from functools import wraps
from datetime import date, datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user

from app import db
from app.factory_models import FactoryUser, ClerkSession, BuyingCenter, Purchase
from app.models import Farm

clerk_bp = Blueprint("clerk", __name__, url_prefix="/clerk")


def clerk_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not isinstance(current_user, FactoryUser) or not current_user.is_clerk:
            abort(403)
        return f(*args, **kwargs)
    return wrapped


@clerk_bp.route("/")
@login_required
@clerk_required
def home():
    session = current_user.active_session

    if not session:
        route = current_user.current_route
        centers = route.buying_centers if route else []
        return render_template("clerk/select_center.html", centers=centers, route=route)

    today_purchases = (
        Purchase.query.filter_by(clerk_id=current_user.id, buying_center_id=session.buying_center_id)
        .filter(db.func.date(Purchase.purchased_at) == date.today())
        .order_by(Purchase.purchased_at.desc())
        .all()
    )
    today_total = sum(p.kilos for p in today_purchases)

    return render_template(
        "clerk/buy.html",
        session=session,
        center=session.buying_center,
        today_purchases=today_purchases,
        today_total=today_total,
    )


@clerk_bp.route("/select-center", methods=["POST"])
@login_required
@clerk_required
def select_center():
    center_id = request.form.get("buying_center_id", type=int)
    route = current_user.current_route
    valid_ids = {c.id for c in route.buying_centers} if route else set()

    if not center_id or center_id not in valid_ids:
        flash("Please choose a valid buying center from your route.", "error")
        return redirect(url_for("clerk.home"))

    session = ClerkSession(clerk_id=current_user.id, buying_center_id=center_id)
    db.session.add(session)
    db.session.commit()
    flash("You're now buying at this center — it will show as active to farmers and managers.", "success")
    return redirect(url_for("clerk.home"))


@clerk_bp.route("/buy", methods=["POST"])
@login_required
@clerk_required
def buy():
    session = current_user.active_session
    if not session:
        flash("Select a buying center first.", "error")
        return redirect(url_for("clerk.home"))

    farm_number = request.form.get("farm_number", "").strip().upper()
    kilos = request.form.get("kilos", type=float)

    farm = Farm.query.filter_by(farm_number=farm_number).first()

    if not farm:
        flash(f"No farm found for card '{farm_number}'. Check the card and try again.", "error")
        return redirect(url_for("clerk.home"))

    if farm.buying_center_id != session.buying_center_id:
        flash(
            f"This card belongs to a farmer registered at a different buying center "
            f"({farm.buying_center.name if farm.buying_center else 'unknown'}). Purchase not recorded.",
            "error",
        )
        return redirect(url_for("clerk.home"))

    if not kilos or kilos <= 0:
        flash("Enter a valid weight in kilos.", "error")
        return redirect(url_for("clerk.home"))

    purchase = Purchase(
        receipt_number=Purchase.generate_receipt_number(),
        farm_id=farm.id,
        buying_center_id=session.buying_center_id,
        clerk_id=current_user.id,
        kilos=kilos,
    )
    db.session.add(purchase)
    db.session.commit()

    flash(
        f"✅ Recorded {kilos} kg for {farm.farm_number} ({farm.owner.full_name}). "
        f"Receipt {purchase.receipt_number}.",
        "success",
    )
    return redirect(url_for("clerk.home"))
