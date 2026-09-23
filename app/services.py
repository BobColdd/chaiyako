"""The business operations of the factory.

Web pages and the mobile API both call these functions, so a rule is written
once. Each function does its work inside the caller's database transaction and
adds its own audit record; the CALLER commits. That way a change and its audit
entry are saved together, or not at all.

A broken rule raises ServiceError. Its message is written for the person using
the screen, so pages can show it as is.
"""
from datetime import timedelta

from flask import current_app
from sqlalchemy import and_, or_

from app import db, rules
from app.audit import log_action
from app.models import (
    BuyingCentre, Department, Employee, Farm, FarmVerification, Farmer, Notice, Receipt,
    Role, TeaTransaction, User, WeighingEvent, WeighingScale,
)
from app.permissions import has_permission
from app.sms import send_sms
from app.timeutil import utcnow


class ServiceError(Exception):
    """A rule was broken. The message is safe to show to the user."""


def _text(value, limit):
    return (value or "").strip()[:limit]


# ============================================================================
# Numbering
# ============================================================================

def next_sequence(name):
    """Next number for a named counter, without ever repeating one.

    One UPDATE bumps the counter, and the database locks that row until the
    surrounding transaction ends, so two clerks saving at the same instant get
    different numbers. (The old count()+1 approach repeated numbers after a delete.)
    """
    params = {"n": name}
    db.session.execute(
        db.text("INSERT INTO number_sequences (name, value) VALUES (:n, 0) ON CONFLICT (name) DO NOTHING"), params)
    db.session.execute(db.text("UPDATE number_sequences SET value = value + 1 WHERE name = :n"), params)
    return db.session.execute(db.text("SELECT value FROM number_sequences WHERE name = :n"), params).scalar()


# ============================================================================
# Farmer registration and farm verification
# ============================================================================

def _bushes(value):
    try:
        bushes = int(value)
    except (TypeError, ValueError):
        raise ServiceError("Enter the number of tea bushes as a whole number.")
    if bushes < 1:
        raise ServiceError("A farm needs at least one tea bush.")
    return bushes


def _new_pending_farm(farmer, bushes, location):
    farm = Farm(farmer_id=farmer.id, tea_bushes=bushes, location=_text(location, 200) or None,
                verification_status="PENDING")
    db.session.add(farm)
    db.session.flush()
    db.session.add(FarmVerification(farm_id=farm.id, status="PENDING"))
    db.session.flush()
    return farm


def register_farmer(actor, *, first_name, last_name, phone, buying_centre_id, tea_bushes,
                    email="", identification_number="", address="", location=""):
    """Customer Services captures a farmer. The farm waits for a field officer's visit."""
    first_name, last_name = _text(first_name, 80), _text(last_name, 80)
    if not first_name or not last_name:
        raise ServiceError("First name and last name are required.")
    cleaned_phone = rules.clean_phone(phone)
    if not cleaned_phone:
        raise ServiceError("Enter a valid phone number.")
    if Farmer.query.filter_by(phone=cleaned_phone).first():
        raise ServiceError("A farmer with that phone number is already registered.")
    centre = db.session.get(BuyingCentre, buying_centre_id) if buying_centre_id else None
    if centre is None or not centre.is_active:
        raise ServiceError("Choose an active buying centre.")
    bushes = _bushes(tea_bushes)

    farmer = Farmer(
        farmer_number=rules.format_farmer_number(next_sequence("farmer")),
        first_name=first_name, last_name=last_name, phone=cleaned_phone,
        email=_text(email, 120) or None,
        identification_number=_text(identification_number, 30) or None,
        address=_text(address, 200) or None,
        buying_centre_id=centre.id,
    )
    db.session.add(farmer)
    db.session.flush()
    farm = _new_pending_farm(farmer, bushes, location)
    log_action("FARMER_REGISTERED", "farmer", farmer.id,
               new={"farmer_number": farmer.farmer_number, "buying_centre": centre.code, "tea_bushes": bushes})
    return farmer, farm


def add_farm(actor, farmer, *, tea_bushes, location=""):
    """A farmer with a second plot: a new farm, waiting for its own verification."""
    farm = _new_pending_farm(farmer, _bushes(tea_bushes), location)
    if farmer.registration_status == "REJECTED":     # a new farm gives a rejected farmer a fresh chance
        farmer.registration_status = "PENDING_VERIFICATION"
    log_action("FARM_ADDED", "farm", farm.id, new={"farmer_id": farmer.id, "tea_bushes": farm.tea_bushes})
    return farm


def _issue_farm_number(farmer):
    centre = farmer.buying_centre
    for _ in range(10):
        number = rules.format_farm_number(
            current_app.config["FACTORY_CODE"], centre.code, next_sequence(f"farm:{centre.id}"))
        if not Farm.query.filter_by(farm_number=number).first():
            return number
    raise ServiceError("Could not issue a unique farm number. Ask IT to check the number counters.")


def verify_farm(actor, verification, *, decision, visit_date, observations, tea_bushes):
    """A field officer records the result of a visit. Only a VERIFIED farm gets a number and a card."""
    if decision not in ("VERIFIED", "REJECTED"):
        raise ServiceError("Choose whether the farm is verified or rejected.")
    if verification.status != "PENDING":
        raise ServiceError("This verification has already been completed.")
    if visit_date is None:
        raise ServiceError("Enter the date of the visit.")
    observations = (observations or "").strip()
    if decision == "REJECTED" and not observations:
        raise ServiceError("Say why the farm was rejected.")

    farm = verification.farm
    farmer = farm.farmer
    old = {"verification_status": farm.verification_status, "tea_bushes": farm.tea_bushes,
           "farm_number": farm.farm_number}

    bushes = _bushes(tea_bushes) if decision == "VERIFIED" else farm.tea_bushes
    now = utcnow()

    if decision == "VERIFIED":
        farm.tea_bushes = bushes
        farm.farm_number = farm.farm_number or _issue_farm_number(farmer)
        farm.verification_status = "VERIFIED"
        farm.verified_by_id = actor.employee_id
        farm.verified_at = now
        farmer.registration_status = "VERIFIED"
    else:
        farm.verification_status = "REJECTED"
        if not any(f.verification_status == "VERIFIED" for f in farmer.farms if f.id != farm.id):
            farmer.registration_status = "REJECTED"

    verification.status = decision
    verification.decision = decision
    verification.visit_date = visit_date
    verification.observations = observations or None
    verification.assigned_to_id = verification.assigned_to_id or actor.employee_id
    verification.verified_by_id = actor.employee_id
    verification.verified_at = now

    log_action(f"FARM_{decision}", "farm", farm.id, old=old,
               new={"verification_status": farm.verification_status, "tea_bushes": farm.tea_bushes,
                    "farm_number": farm.farm_number})

    if decision == "VERIFIED":
        send_sms(farmer.phone, f"Your farm is verified. Farm number {farm.farm_number}. "
                               f"Collect your farm card from {current_app.config['FACTORY_NAME']}.")
    return farm


def update_farmer_details(actor, farmer, *, phone, email, identification_number, address):
    cleaned_phone = rules.clean_phone(phone)
    if not cleaned_phone:
        raise ServiceError("Enter a valid phone