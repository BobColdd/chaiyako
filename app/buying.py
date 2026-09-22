"""Tea buying: scan the farm card, see the weight from the scale, confirm once.

One click on Confirm records the tea against the farmer (so it appears on the
farmer's side) AND issues the receipt for printing, in a single all-or-nothing step.
The clerk never types the weight: it comes from the scale.
"""
from datetime import datetime, time, timedelta

from flask import (
    Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for,
)
from flask_login import current_user

from app import db
from app.models import BuyingCentre, Receipt, TeaTransaction
from app.permissions import has_permission, permission_required
from app.services import (
    ServiceError, confirm_weighing, create_manual_weight, latest_pending_weight,
    lookup_farm_for_buying, register_receipt_print,
)
from app.timeutil import today_utc

buying_bp = Blueprint("buying", __name__, url_prefix="/buying")

SESSION_KEY = "buying_centre_id"


def _current_centre():
    """The buying centre this clerk chose for this browser session (None if not chosen or no longer active)."""
    centre_id = session.get(SESSION_KEY)
    centre = db.session.get(BuyingCentre, centre_id) if centre_id else None
    return centre if centre is not None and centre.is_active else None


def _centre_or_400():
    centre = _current_centre()
    if centre is None:
        return None, (jsonify(ok=False, error="Choose a buying centre first."), 400)
    return centre, None


def _weight_json(event):
    if event is None:
        return None
    return {"event_id": event.id, "weight_kg": event.weight_kg, "source": event.source,
            "captured_at": event.captured_at.isoformat()}


def _today_range():
    start = datetime.combine(today_utc(), time.min)
    return start, start + timedelta(days=1)


@buying_bp.route("/")
@permission_required("RECORD_TRANSACTION")
def home():
    centre = _current_centre()
    if centre is None:
        centres = BuyingCentre.query.filter_by(status="ACTIVE").order_by(BuyingCentre.name).all()
        return render_template("buying/select_centre.html", centres=centres)

    lo, hi = _today_range()
    todays = (TeaTransaction.query
              .filter(TeaTransaction.clerk_employee_id == current_user.employee_id,
                      TeaTransaction.buying_centre_id == centre.id,
                      TeaTransaction.transaction_time >= lo, TeaTransaction.transaction_time < hi)
              .order_by(TeaTransaction.transaction_time.desc()).all())
    total = sum(t.weight_kg for t in todays if t.status == "VALID")
    scales = [s for s in centre.scales if s.status == "ACTIVE"]
    return render_template("buying/home.html", centre=centre, transactions=todays, total=total,
                           scales=scales, allow_manual=current_app.config["ALLOW_MANUAL_WEIGHT"])


@buying_bp.route("/centre", methods=["POST"])
@permission_required("RECORD_TRANSACTION")
def set_centre():
    centre = db.session.get(BuyingCentre, request.form.get("buying_centre_id", type=int) or 0)
    if centre is None or not centre.is_active:
        flash("Choose a buying centre from the list.", "error")
    else:
        session[SESSION_KEY] = centre.id
    return redirect(url_for("buying.home"))


@buying_bp.route("/centre/clear", methods=["POST"])
@permission_required("RECORD_TRANSACTION")
def clear_centre():
    session.pop(SESSION_KEY, None)
    return redirect(url_for("buying.home"))


@buying_bp.route("/farm-lookup")
@permission_required("RECORD_TRANSACTION")
def farm_lookup():
    """Called when a card is scanned: shows who the farm belongs to, or why it can't be used."""
    centre, error = _centre_or_400()
    if error:
        return error
    try:
        farm = lookup_farm_for_buying(request.args.get("farm_number"), centre)
    except ServiceError as problem:
        return jsonify(ok=False, error=str(problem))
    farmer = farm.farmer
    return jsonify(ok=True, farm_number=farm.farm_number, farmer_name=farmer.full_name,
                   farmer_number=farmer.farmer_number, tea_bushes=farm.tea_bushes)


@buying_bp.route("/weight/latest")
@permission_required("RECORD_TRANSACTION")
def weight_latest():
    """Polled by the buying screen every second or two for the weight the scale is showing."""
    centre, error = _centre_or_400()
    if error:
        return error
    return jsonify(ok=True, event=_weight_json(latest_pending_weight(centre.id, current_user)))


@buying_bp.route("/weight/manual", methods=["POST"])
@permission_required("RECORD_TRANSACTION")
def weight_manual():
    centre, error = _centre_or_400()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    try:
        event = create_manual_weight(current_user, centre, data.get("kilos"))
        db.session.commit()
    except ServiceError as problem:
        db.session.rollback()
        return jsonify(ok=False, error=str(problem)), 400
    return jsonify(ok=True, event=_weight_json(event))


@buying_bp.route("/confirm", methods=["POST"])
@permission_required("RECORD_TRANSACTION")
def confirm():
    centre, error = _centre_or_400()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    raw_event_id = str(data.get("event_id", ""))
    event_id = int(raw_event_id) if raw_event_id.isdigit() else None
    try:
        transaction, receipt = confirm_weighing(current_user, centre, data.get("farm_number"), event_id)
        db.session.commit()
    except ServiceError as problem:
        db.session.rollback()
        return jsonify(ok=False, error=str(problem)), 400
    return jsonify(
        ok=True,
        transaction_number=transaction.transaction_number, receipt_number=receipt.receipt_number,
        weight_kg=transaction.weight_kg, farmer_name=transaction.farmer.full_name,
        time=transaction.transaction_time.isoformat(),
        print_url=url_for("buying.receipt_print", receipt_id=receipt.id, auto=1),
    )


@buying_bp.route("/receipt/<int:receipt_id>/print")
@permission_required("RECORD_TRANSACTION", "VIEW_TRANSACTION")
def receipt_print(receipt_id):
    receipt = db.get_or_404(Receipt, receipt_id)
    transaction = receipt.transaction
    # A clerk can only print receipts they issued; people who may view transactions can print any.
    if not has_permission(current_user, "VIEW_TRANSACTION") and transaction.clerk_employee_id != current_user.employee_id:
        abort(403)
    register_receipt_print(receipt)
    db.session.commit()
    return render_template("buying/receipt.html", receipt=receipt, transaction=transaction,
                           auto=request.args.get("auto") == "1")
