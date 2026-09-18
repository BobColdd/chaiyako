from functools import wraps
from datetime import date, datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user

from app import db
from app.factory_models import FactoryUser, BuyingCenter, Purchase, QualityRecord
from app.analytics import total_kilos, daily_series, center_leaderboard, center_trend, compare_periods

receiver_bp = Blueprint("receiver", __name__, url_prefix="/receiver")


def receiver_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not isinstance(current_user, FactoryUser) or not current_user.is_receiver:
            abort(403)
        return f(*args, **kwargs)
    return wrapped


def _factory_id():
    return current_user.factory_id


# ---------- Reception desk: record quality, see today's intake ----------

@receiver_bp.route("/", methods=["GET", "POST"])
@login_required
@receiver_required
def home():
    fid = _factory_id()
    centers = BuyingCenter.query.filter_by(factory_id=fid).order_by(BuyingCenter.name).all()

    if request.method == "POST":
        center_ids = request.form.getlist("buying_center_ids", type=int)
        grade = request.form.get("grade", "")
        notes = request.form.get("notes", "").strip()
        date_str = request.form.get("date", "")

        try:
            record_date = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else date.today()
        except ValueError:
            record_date = date.today()

        valid_ids = {c.id for c in centers}
        chosen = [cid for cid in center_ids if cid in valid_ids]

        if not chosen or grade not in QualityRecord.GRADE_SCORES:
            flash("Choose at least one buying center and a quality grade.", "error")
            return redirect(url_for("receiver.home"))

        for cid in chosen:
            existing = QualityRecord.query.filter_by(buying_center_id=cid, date=record_date).first()
            if existing:
                existing.grade = grade
                existing.notes = notes
                existing.recorded_by_id = current_user.id
                existing.recorded_at = datetime.utcnow()
            else:
                db.session.add(QualityRecord(
                    buying_center_id=cid, date=record_date, grade=grade,
                    notes=notes, recorded_by_id=current_user.id,
                ))
        db.session.commit()
        flash(f"Quality recorded for {len(chosen)} buying center(s).", "success")
        return redirect(url_for("receiver.home"))

    today = date.today()
    leaderboard = center_leaderboard(fid, today)
    quality_today = {q.buying_center_id: q for q in QualityRecord.query.filter_by(date=today).all()}

    center_rows = [
        {"center": row["center"], "today_kilos": row["kilos"], "quality_today": quality_today.get(row["center"].id)}
        for row in leaderboard
    ]

    recent_quality = (
        QualityRecord.query.join(BuyingCenter)
        .filter(BuyingCenter.factory_id == fid)
        .order_by(QualityRecord.recorded_at.desc())
        .limit(20)
        .all()
    )

    return render_template(
        "receiver/dashboard.html",
        centers=centers,
        center_rows=center_rows,
        today_total=sum(r["today_kilos"] for r in center_rows),
        recent_quality=recent_quality,
        today=today,
    )


# ---------- Trends: same center-level analytics the manager sees, minus farmers ----------

@receiver_bp.route("/trends")
@login_required
@receiver_required
def trends():
    fid = _factory_id()
    today = date.today()

    centers = BuyingCenter.query.filter_by(factory_id=fid).order_by(BuyingCenter.name).all()
    today_by_center = {row["center"].id: row["kilos"] for row in center_leaderboard(fid, today)}

    trend_rows = []
    for c in centers:
        direction, pct = center_trend(fid, c.id)
        trend_rows.append({
            "center": c,
            "today_kilos": today_by_center.get(c.id, 0),
            "direction": direction,
            "pct_change": pct,
        })
    trend_rows.sort(key=lambda r: r["today_kilos"], reverse=True)

    return render_template(
        "receiver/trends.html",
        centers=centers,
        trend_rows=trend_rows,
        today_total=total_kilos(fid, today, today),
    )


@receiver_bp.route("/trends/series")
@login_required
@receiver_required
def trends_series():
    fid = _factory_id()
    days = max(7, min(request.args.get("days", 30, type=int), 180))
    center_id = request.args.get("center_id", type=int) or None
    labels, values = daily_series(fid, days=days, center_id=center_id)
    return {"labels": labels, "values": values}


@receiver_bp.route("/trends/compare")
@login_required
@receiver_required
def trends_compare():
    fid = _factory_id()
    center_id = request.args.get("center_id", type=int) or None
    mode = request.args.get("mode", "days")
    try:
        return compare_periods(fid, mode, request.args.get("a", ""), request.args.get("b", ""), center_id)
    except (ValueError, IndexError, TypeError):
        return {"error": "Enter two valid dates to compare."}, 400
