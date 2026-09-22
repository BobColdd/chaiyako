"""Inputs and Fertilizer: record what is handed out, per farmer or centre-wide."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app import db
from app.analytics import active_centres
from app.audit import log_action
from app.models import BuyingCentre, FertilizerDistribution, Farmer
from app.permissions import permission_required

inputs_bp = Blueprint("inputs", __name__, url_prefix="/inputs")


@inputs_bp.route("/", methods=["GET", "POST"])
@permission_required("PROCESS_FERTILIZER")
def index():
    if request.method == "POST":
        centre = db.session.get(BuyingCentre, request.form.get("buying_centre_id", type=int) or 0)
        fertilizer_type = request.form.get("fertilizer_type", "").strip()
        farmer_number = request.form.get("farmer_number", "").strip().upper()
        quantity = request.form.get("quantity_kg", type=float)
        farmer = Farmer.query.filter_by(farmer_number=farmer_number).first() if farmer_number else None

        if centre is None or not fertilizer_type:
            flash("A buying centre and the fertilizer type are required.", "error")
        elif farmer_number and farmer is None:
            flash(f"No farmer has the number '{farmer_number}'. Leave it blank for a centre-wide entry.", "error")
        elif quantity is not None and quantity <= 0:
            flash("The quantity must be more than zero.", "error")
        else:
            record = FertilizerDistribution(
                buying_centre_id=centre.id, farmer_id=farmer.id if farmer else None,
                recorded_by_id=current_user.employee_id, fertilizer_type=fertilizer_type[:80],
                quantity_kg=quantity, notes=request.form.get("notes", "").strip()[:300] or None)
            db.session.add(record)
            db.session.flush()
            log_action("FERTILIZER_RECORDED", "fertilizer_distribution", record.id,
                       new={"centre": centre.code, "farmer": farmer.farmer_number if farmer else None,
                            "type": fertilizer_type, "quantity_kg": quantity})
            db.session.commit()
            flash("Distribution recorded.", "success")
        return redirect(url_for("inputs.index"))

    recent = FertilizerDistribution.query.order_by(FertilizerDistribution.date.desc(),
                                                   FertilizerDistribution.id.desc()).limit(40).all()
    return render_template("inputs/index.html", centres=active_centres(), records=recent)
