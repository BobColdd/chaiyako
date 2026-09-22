"""Management and Tea Buying Manager pages: live overview, insights, buying centres, scales,
transactions (with voiding), and the audit trail."""
from datetime import datetime, time, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import or_

from app import db, rules
from app.analytics import (
    active_centres, centre_analysis, centre_trends, compare_periods, daily_series, kilos_by_centre,
    today_by_centre, top_farmers, total_kilos,
)
from app.models import (
    AuditLog, BuyingCentre, Complaint, Farm, FarmVerification, Farmer, QualityRecord, Receipt,
    TeaTransaction, User, WeighingScale,
)
from app.permissions import has_permission, permission_required
from app.services import (
    ServiceError, create_centre, create_scale, rotate_scale_key, toggle_scale, update_centre, void_transaction,
)
from app.timeutil import today_utc, utcnow

management_bp = Blueprint("management", __name__, url_prefix="/management")


# ---------------------------------------------------------------- live overview

@management_bp.route("/")
@permission_required("GENERATE_REPORTS")
def dashboard():
    today = today_utc()
    stats = today_by_centre()
    rows = []
    for centre in active_centres():
        s = stats.get(centre.id, {})
        seen = [sc.last_seen_at for sc in centre.scales if sc.status == "ACTIVE" and sc.last_seen_at]
        rows.append({"centre": centre, "kilos": s.get("kilos", 0.0), "count": s.get("count", 0),
                     "last": s.get("last"), "scale_seen": max(seen) if seen else None,
                     "has_scale": any(sc.status == "ACTIVE" for sc in centre.scales)})

    day_start = datetime.combine(today, time.min)
    recent = (TeaTransaction.query
              .filter(TeaTransaction.transaction_time >= day_start)
              .order_by(TeaTransaction.transaction_time.desc()).limit(15).all())
    return render_template(
        "management/dashboard.html", rows=rows, recent=recent,
        today_total=sum(r["kilos"] for r in rows), today_count=sum(r["count"] for r in rows),
        farmer_count=Farmer.query.filter_by(status="ACTIVE").count(),
        pending_verifications=FarmVerification.query.filter_by(status="PENDING").count(),
        open_complaints=Complaint.query.filter_by(status="open").count(),
    )


# --------------------------------------------------------------------- insights

@management_bp.route("/insights")
@permission_required("GENERATE_REPORTS")
def insights():
    today = today_utc()
    yesterday = today - timedelta(days=1)
    centres = active_centres()
    today_by, yesterday_by, trend_by = kilos_by_centre(today, today), kilos_by_centre(yesterday, yesterday), centre_trends()

    rows = []
    for c in centres:
        direction, pct = trend_by.get(c.id, ("stagnant", 0.0))
        rows.append({"centre": c, "today_kilos": today_by.get(c.id, 0.0), "yesterday_kilos": yesterday_by.get(c.id, 0.0),
                     "direction": direction, "pct_change": pct})
    rows.sort(key=lambda r: r["today_kilos"], reverse=True)

    chart_data = [{"name": r["centre"].name, "today": round(r["today_kilos"], 1), "yesterday": round(r["yesterday_kilos"], 1)}
                  for r in rows]
    quality_counts = {"good": 0, "average": 0, "poor": 0}
    for q in QualityRecord.query.filter(QualityRecord.date >= today - timedelta(days=30)).all():
        if q.grade in quality_counts:
            quality_counts[q.grade] += 1

    return render_template(
        "management/insights.html", centres=centres, trend_rows=rows, chart_data=chart_data,
        quality_counts=quality_counts, top_farmers=top_farmers(15),
        today_total=total_kilos(today, today), yesterday_total=total_kilos(yesterday, yesterday),
        week_total=total_kilos(today - timedelta(days=6), today), month_total=total_kilos(today.replace(day=1), today),
    )


@management_bp.route("/insights/series")
@permission_required("GENERATE_REPORTS")
def insights_series():
    days = max(7, min(request.args.get("days", 30, type=int), 180))
    labels, values = daily_series(days=days, centre_id=request.args.get("centre_id", type=int) or None)
    return {"labels": labels, "values": values}


@management_bp.route("/insights/compare")
@permission_required("GENERATE_REPORTS")
def insights_compare():
    try:
        return compare_periods(request.args.get("mode", "days"), request.args.get("a", ""),
                               request.args.get("b", ""), request.args.get("centre_id", type=int) or None)
    except (ValueError, IndexError, TypeError):
        return {"error": "Enter two valid dates to compare."}, 400


# ---------------------------------------------------------------- buying centres

@management_bp.route("/centres", methods=["GET", "POST"])
@permission_required("MANAGE_BUYING_CENTRES", "GENERATE_REPORTS")
def centres():
    if request.method == "POST":
        if not has_permission(current_user, "MANAGE_BUYING_CENTRES"):
            abort(403)
        try:
            try:
                established = datetime.strptime(request.form.get("established_at", ""), "%Y-%m-%d").date()
            except ValueError:
                established = None
            centre = create_centre(current_user, name=request.form.get("name"), code=request.form.get("code"),
                                   location=request.form.get("location"), established_at=established)
            db.session.commit()
            flash(f"Buying centre '{centre.name}' added.", "success")
        except ServiceError as problem:
            db.session.rollback()
            flash(str(problem), "error")
        return redirect(url_for("management.centres"))
    return render_template("management/centres.html", analysis=centre_analysis())


@management_bp.route("/centres/<int:centre_id>/update", methods=["POST"])
@permission_required("MANAGE_BUYING_CENTRES")
def centre_update(centre_id):
    centre = db.get_or_404(BuyingCentre, centre_id)
    try:
        update_centre(current_user, centre, name=request.form.get("name"), location=request.form.get("location"),
                      status=request.form.get("status"))
        db.session.commit()
        flash("Buying centre updated.", "success")
    except ServiceError as problem:
        db.session.rollback()
        flash(str(problem), "error")
    return redirect(url_for("management.centres"))


# ------------------------------------------------------------------------ scales

def _render_scales(new_key=None, key_for=None):
    scales = WeighingScale.query.order_by(WeighingScale.scale_identifier).all()
    centres_list = BuyingCentre.query.filter_by(status="ACTIVE").order_by(BuyingCentre.name).all()
    return render_template("management/scales.html", scales=scales, centres=centres_list,
                           new_key=new_key, key_for=key_for)


@management_bp.route("/scales", methods=["GET", "POST"])
@permission_required("MANAGE_SCALES")
def scales():
    if request.method == "POST":
        try:
            scale, key = create_scale(current_user, centre_id=request.form.get("buying_centre_id", type=int),
                                      scale_identifier=request.form.get("scale_identifier"),
                                      model=request.form.get("model"))
            db.session.commit()
        except ServiceError as problem:
            db.session.rollback()
            flash(str(problem), "error")
            return redirect(url_for("management.scales"))
        return _render_scales(new_key=key, key_for=scale.scale_identifier)   # shown once, right here
    return _render_scales()


@management_bp.route("/scales/<int:scale_id>/rotate", methods=["POST"])
@permission_required("MANAGE_SCALES")
def scale_rotate(scale_id):
    scale = db.get_or_404(WeighingScale, scale_id)
    key = rotate_scale_key(current_user, scale)
    db.session.commit()
    return _render_scales(new_key=key, key_for=scale.scale_identifier)


@management_bp.route("/scales/<int:scale_id>/toggle", methods=["POST"])
@permission_required("MANAGE_SCALES")
def scale_toggle(scale_id):
    scale = db.get_or_404(WeighingScale, scale_id)
    status = toggle_scale(current_user, scale)
    db.session.commit()
    flash(f"Scale {scale.scale_identifier} is now {status.lower()}.", "success")
    return redirect(url_for("management.scales"))


# ------------------------------------------------------------------ transactions

def _parse_day(text, fallback):
    try:
        return datetime.strptime(text or "", "%Y-%m-%d").date()
    except ValueError:
        return fallback


@management_bp.route("/transactions")
@permission_required("VIEW_TRANSACTION")
def transactions():
    today = today_utc()
    start = _parse_day(request.args.get("from"), today)
    end = _parse_day(request.args.get("to"), today)
    if end < start:
        start, end = end, start
    centre_id = request.args.get("centre_id", type=int)
    status = request.args.get("status", "")
    q = request.args.get("q", "").strip()

    query = (TeaTransaction.query
             .join(Farmer, Farmer.id == TeaTransaction.farmer_id)
             .join(Farm, Farm.id == TeaTransaction.farm_id)
             .outerjoin(Receipt, Receipt.transaction_id == TeaTransaction.id)
             .filter(TeaTransaction.transaction_time >= datetime.combine(start, time.min),
                     TeaTransaction.transaction_time < datetime.combine(end + timedelta(days=1), time.min)))
    if centre_id:
        query = query.filter(TeaTransaction.buying_centre_id == centre_id)
    if status in ("VALID", "VOIDED"):
        query = query.filter(TeaTransaction.status == status)
    for term in rules.split_terms(q):
        like = f"%{term}%"
        query = query.filter(or_(
            TeaTransaction.transaction_number.ilike(like), Receipt.receipt_number.ilike(like),
            Farm.farm_number.ilike(like), Farmer.first_name.ilike(like), Farmer.last_name.ilike(like),
            Farmer.phone.ilike(like),
        ))

    valid_total = float(query.filter(TeaTransaction.status == "VALID")
                        .with_entities(db.func.coalesce(db.func.sum(TeaTransaction.weight_kg), 0.0)).scalar() or 0)
    rows = query.order_by(TeaTransaction.transaction_time.desc()).limit(200).all()
    centres_list = BuyingCentre.query.order_by(BuyingCentre.name).all()
    return render_template("management/transactions.html", rows=rows, valid_total=valid_total, centres=centres_list,
                           start=start, end=end, centre_id=centre_id, status=status, q=q, capped=len(rows) == 200)


@management_bp.route("/transactions/<int:transaction_id>/void", methods=["POST"])
@permission_required("CORRECT_TRANSACTION")
def void(transaction_id):
    transaction = db.get_or_404(TeaTransaction, transaction_id)
    try:
        void_transaction(current_user, transaction, request.form.get("reason"))
        db.session.commit()
        flash(f"{transaction.transaction_number} voided. The tea can now be weighed again.", "success")
    except ServiceError as problem:
        db.session.rollback()
        flash(str(problem), "error")
    back = request.form.get("back") or ""
    return redirect(back if back.startswith("/management/") else url_for("management.transactions"))


# ------------------------------------------------------------------------- audit

@management_bp.route("/audit")
@permission_required("VIEW_AUDIT_LOG")
def audit():
    action = request.args.get("action", "").strip().upper()
    entity = request.args.get("entity", "").strip()
    query = AuditLog.query.outerjoin(User, User.id == AuditLog.user_id)
    if action:
        query = query.filter(AuditLog.action.like(f"%{action}%"))
    if entity:
        query = query.filter(AuditLog.entity_type == entity)
    entries = query.order_by(AuditLog.id.desc()).limit(200).all()
    entity_types = [row[0] for row in db.session.query(AuditLog.entity_type).distinct().order_by(AuditLog.entity_type)
                    if row[0]]
    return render_template("management/audit.html", entries=entries, action=action, entity=entity,
                           entity_types=entity_types)
