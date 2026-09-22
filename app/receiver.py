"""The reception desk: grade the tea each buying centre delivers, and watch centre-level trends.

Trends are aggregated per buying centre and per day. The receiver never sees whose tea it was.
"""
from datetime import date, datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app import db
from app.analytics import (
    active_centres, centre_leaderboard, centre_trends, compare_periods, daily_series, total_kilos,
)
from app.audit import log_action
from app.models import QualityRecord
from app.permissions import permission_required
from app.timeutil import today_utc, utcnow

receiver_bp = Blueprint("receiver", __name__, url_prefix="/receiver")


@receiver_bp.route("/", methods=["GET", "POST"])
@permission_required("RECORD_QUALITY")
def home():
    centres = active_centres()
    today = today_utc()

    if request.method == "POST":
        centre_ids = request.form.getlist("buying_centre_ids", type=int)
        grade = request.form.get("grade", "")
        notes = request.form.get("notes", "").strip()
        try:
            record_date = datetime.strptime(request.form.get("date", ""), "%Y-%m-%d").date()
        except ValueError:
            record_date = today
        if record_date > today:
            record_date = today

        valid_ids = {c.id for c in centres}
        chosen = [cid for cid in centre_ids if cid in valid_ids]
        if not chosen or grade not in QualityRecord.GRADE_SCORES:
            flash("Choose at least one buying centre and a quality grade.", "error")
            return redirect(url_for("receiver.home"))

        for centre_id in chosen:
            existing = QualityRecord.query.filter_by(buying_centre_id=centre_id, date=record_date).first()
            if existing:
                old = {"grade": existing.grade, "notes": existing.notes}
                existing.grade, existing.notes = grade, notes or None
                existing.recorded_by_id, existing.recorded_at = current_user.employee_id, utcnow()
                log_action("QUALITY_UPDATED", "quality_record", existing.id, old=old, new={"grade": grade})
            else:
                record = QualityRecord(buying_centre_id=centre_id, date=record_date, grade=grade,
                                       notes=notes or None, recorded_by_id=current_user.employee_id)
                db.session.add(record)
                db.session.flush()
                log_action("QUALITY_RECORDED", "quality_record", record.id,
                           new={"grade": grade, "date": record_date.isoformat()})
        db.session.commit()
        flash(f"Quality recorded for {len(chosen)} buying centre(s).", "success")
        return redirect(url_for("receiver.home"))

    quality_today = {q.buying_centre_id: q for q in QualityRecord.query.filter_by(date=today).all()}
    rows = [{"centre": r["centre"], "today_kilos": r["kilos"], "quality_today": quality_today.get(r["centre"].id)}
            for r in centre_leaderboard(today)]
    recent = QualityRecord.query.order_by(QualityRecord.recorded_at.desc()).limit(20).all()
    return render_template("receiver/dashboard.html", centres=centres, rows=rows,
                           today_total=sum(r["today_kilos"] for r in rows), recent_quality=recent, today=today)


@receiver_bp.route("/trends")
@permission_required("VIEW_CENTRE_TRENDS")
def trends():
    today = today_utc()
    centres = active_centres()
    today_by_centre = {r["centre"].id: r["kilos"] for r in centre_leaderboard(today)}
    trend_by_centre = centre_trends()
    rows = []
    for c in centres:
        direction, pct = trend_by_centre.get(c.id, ("stagnant", 0.0))
        rows.append({"centre": c, "today_kilos": today_by_centre.get(c.id, 0.0), "direction": direction, "pct_change": pct})
    rows.sort(key=lambda r: r["today_kilos"], reverse=True)
    return render_template("receiver/trends.html", centres=centres, trend_rows=rows, today_total=total_kilos(today, today))


@receiver_bp.route("/trends/series")
@permission_required("VIEW_CENTRE_TRENDS")
def trends_series():
    days = max(7, min(request.args.get("days", 30, type=int), 180))
    labels, values = daily_series(days=days, centre_id=request.args.get("centre_id", type=int) or None)
    return {"labels": labels, "values": values}


@receiver_bp.route("/trends/compare")
@permission_required("VIEW_CENTRE_TRENDS")
def trends_compare():
    try:
        return compare_periods(request.args.get("mode", "days"), request.args.get("a", ""),
                               request.args.get("b", ""), request.args.get("centre_id", type=int) or None)
    except (ValueError, IndexError, TypeError):
        return {"error": "Enter two valid dates to compare."}, 400
