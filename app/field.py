"""Field officers: visit a farm, confirm the details, and issue its farm number."""
from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app import db
from app.models import FarmVerification
from app.permissions import permission_required
from app.services import ServiceError, verify_farm
from app.timeutil import today_utc

field_bp = Blueprint("field", __name__, url_prefix="/field")


@field_bp.route("/")
@permission_required("VERIFY_FARM")
def queue():
    pending = (FarmVerification.query.filter_by(status="PENDING")
               .order_by(FarmVerification.created_at).all())
    recent = (FarmVerification.query.filter(FarmVerification.status != "PENDING")
              .order_by(FarmVerification.verified_at.desc()).limit(15).all())
    return render_template("field/queue.html", pending=pending, recent=recent)


@field_bp.route("/<int:verification_id>", methods=["GET", "POST"])
@permission_required("VERIFY_FARM")
def verify(verification_id):
    verification = db.get_or_404(FarmVerification, verification_id)

    if request.method == "POST":
        try:
            visit = datetime.strptime(request.form.get("visit_date", ""), "%Y-%m-%d").date()
        except ValueError:
            visit = None
        try:
            farm = verify_farm(
                current_user, verification,
                decision=request.form.get("decision"), visit_date=visit,
                observations=request.form.get("observations"), tea_bushes=request.form.get("tea_bushes"),
            )
            db.session.commit()
        except ServiceError as problem:
            db.session.rollback()
            flash(str(problem), "error")
            return redirect(url_for("field.verify", verification_id=verification_id))
        if farm.is_verified:
            flash(f"Farm verified. Farm number {farm.farm_number} issued.", "success")
            return redirect(url_for("farmers.card", farm_id=farm.id))
        flash("Farm rejected. The reason has been recorded.", "info")
        return redirect(url_for("field.queue"))

    return render_template("field/verify.html", verification=verification, today=today_utc())
