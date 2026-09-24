"""Customer Services: registering farmers and looking after their records."""
from datetime import datetime, time

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import and_, or_

from app import db, rules
from app.models import BuyingCentre, Farm, Farmer, TeaTransaction
from app.navigation import home_endpoint
from app.permissions import has_permission, permission_required
from app.services import (
    ServiceError, add_farm, change_farmer_centre, register_farmer, update_farmer_details,
)
from app.timeutil import today_utc

farmers_bp = Blueprint("farmers", __name__, url_prefix="/farmers")


def _search(query_text):
    query = Farmer.query.outerjoin(Farm, Farm.farmer_id == Farmer.id)
    filters = []
    for term in rules.split_terms(query_text):
        like = f"%{term}%"
        filters.append(or_(
            Farmer.first_name.ilike(like), Farmer.last_name.ilike(like), Farmer.phone.ilike(like),
            Farmer.farmer_number.ilike(like), Farm.farm_number.ilike(like),
        ))
    if filters:
        query = query.filter(and_(*filters))
    return query.distinct().order_by(Farmer.created_at.desc())


@farmers_bp.route("/")
@permission_required("VIEW_FARMER", "CREATE_FARMER")
def index():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    query = _search(q)
    if status == "pending":
        query = query.filter(Farmer.registration_status == "PENDING_VERIFICATION")
    farmers = query.limit(100).all() if has_permission(current_user, "VIEW_FARMER") else []
    centres = BuyingCentre.query.filter_by(status="ACTIVE").order_by(BuyingCentre.name).all()
    return render_template("farmers/index.html", farmers=farmers, centres=centres, q=q, status=status)


@farmers_bp.route("/register", methods=["POST"])
@permission_required("CREATE_FARMER")
def register():
    form = request.form
    try:
        farmer, farm = register_farmer(
            current_user,
            first_name=form.get("first_name"), last_name=form.get("last_name"), phone=form.get("phone"),
            email=form.get("email"), identification_number=form.get("identification_number"),
            address=form.get("address"), buying_centre_id=form.get("buying_centre_id", type=int),
            tea_bushes=form.get("tea_bushes"), location=form.get("location"),
        )
        db.session.commit()
    except ServiceError as problem:
        db.session.rollback()
        flash(str(problem), "error")
        return redirect(url_for("farmers.index"))
    flash(f"{farmer.full_name} registered as {farmer.farmer_number}. "
          "The farm now waits for a field officer's verification visit.", "success")
    if has_permission(current_user, "VIEW_FARMER"):
        return redirect(url_for("farmers.detail", farmer_id=farmer.id))
    return redirect(url_for("farmers.index"))


@farmers_bp.route("/<int:farmer_id>")
@permission_required("VIEW_FARMER")
def detail(farmer_id):
    farmer = db.get_or_404(Farmer, farmer_id)
    centres = BuyingCentre.query.filter_by(status="ACTIVE").order_by(BuyingCentre.name).all()

    # Money-side history is only for roles that may see transactions (a field officer, for example, may not).
    transactions, month_kg = [], 0.0
    if has_permission(current_user, "VIEW_TRANSACTION"):
        transactions = (TeaTransaction.query.filter_by(farmer_id=farmer.id)
                        .order_by(TeaTransaction.transaction_time.desc()).limit(30).all())
        month_start = datetime.combine(today_utc().replace(day=1), time.min)
        month_kg = float(
            db.session.query(db.func.coalesce(db.func.sum(TeaTransaction.weight_kg), 0.0))
            .filter(TeaTransaction.farmer_id == farmer.id, TeaTransaction.status == "VALID",
                    TeaTransaction.transaction_time >= month_start)
            .scalar() or 0)
    return render_template("farmers/detail.html", farmer=farmer, centres=centres,
                           transactions=transactions, month_kg=month_kg)


@farmers_bp.route("/<int:farmer_id>/edit", methods=["POST"])
@permission_required("EDIT_FARMER")
def edit(farmer_id):
    farmer = db.get_or_404(Farmer, farmer_id)
    form = request.form
    try:
        update_farmer_details(current_user, farmer, phone=form.get("phone"), email=form.get("email"),
                              identification_number=form.get("identification_number"), address=form.get("address"))
        db.session.commit()
        flash("Farmer details updated.", "success")
    except ServiceError as problem:
        db.session.rollback()
        flash(str(problem), "error")
    return redirect(url_for("farmers.detail", farmer_id=farmer_id))


@farmers_bp.route("/<int:farmer_id>/centre", methods=["POST"])
@permission_required("ASSIGN_BUYING_CENTRE")
def set_centre(farmer_id):
    farmer = db.get_or_404(Farmer, farmer_id)
    try:
        change_farmer_centre(current_user, farmer, request.form.get("buying_centre_id", type=int))
        db.session.commit()
        flash("Buying centre changed.", "success")
    except ServiceError as problem:
        db.session.rollback()
        flash(str(problem), "error")
    return redirect(url_for("farmers.detail", farmer_id=farmer_id))


@farmers_bp.route("/<int:farmer_id>/add-farm", methods=["POST"])
@permission_required("CREATE_FARMER")
def add_farm_route(farmer_id):
    farmer = db.get_or_404(Farmer, farmer_id)
    try:
        add_farm(current_user, farmer, tea_bushes=request.form.get("tea_bushes"), location=request.form.get("location"))
        db.session.commit()
        flash("Farm added. It now waits for a verification visit.", "success")
    except ServiceError as problem:
        db.session.rollback()
        flash(str(problem), "error")
    return redirect(url_for("farmers.detail", farmer_id=farmer_id))


@farmers_bp.route("/farm/<int:farm_id>/card")
@permission_required("VIEW_FARMER", "GENERATE_FARM_NUMBER")
def card(farm_id):
    farm = db.get_or_404(Farm, farm_id)
    if not farm.is_verified or not farm.farm_number:
        flash("This farm has no farm number yet — it has to be verified first.", "error")
        return redirect(url_for("farmers.detail", farmer_id=farm.farmer_id)
                        if has_permission(current_user, "VIEW_FARMER") else url_for(home_endpoint(current_user)))
    return render_template("farmers/card.html", farm=farm)
