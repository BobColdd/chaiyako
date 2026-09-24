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
from sqlalchemy.orm import selectinload

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
        raise ServiceError("Enter a valid phone number.")
    clash = Farmer.query.filter(Farmer.phone == cleaned_phone, Farmer.id != farmer.id).first()
    if clash:
        raise ServiceError("Another farmer already uses that phone number.")
    old = {"phone": farmer.phone, "email": farmer.email,
           "identification_number": farmer.identification_number, "address": farmer.address}
    farmer.phone = cleaned_phone
    farmer.email = _text(email, 120) or None
    farmer.identification_number = _text(identification_number, 30) or None
    farmer.address = _text(address, 200) or None
    log_action("FARMER_UPDATED", "farmer", farmer.id, old=old,
               new={"phone": farmer.phone, "email": farmer.email,
                    "identification_number": farmer.identification_number, "address": farmer.address})


def change_farmer_centre(actor, farmer, centre_id):
    centre = db.session.get(BuyingCentre, centre_id) if centre_id else None
    if centre is None or not centre.is_active:
        raise ServiceError("Choose an active buying centre.")
    if centre.id == farmer.buying_centre_id:
        raise ServiceError("The farmer already belongs to that buying centre.")
    old = {"buying_centre": farmer.buying_centre.code}
    farmer.buying_centre_id = centre.id
    log_action("FARMER_CENTRE_CHANGED", "farmer", farmer.id, old=old, new={"buying_centre": centre.code})


# ============================================================================
# Weighing: scale -> weighing event -> tea transaction -> receipt
# ============================================================================

def _max_weight():
    return current_app.config["MAX_SINGLE_WEIGHT_KG"]


def _valid_weight(value):
    try:
        return rules.parse_weight(value, _max_weight())
    except ValueError as problem:
        raise ServiceError(str(problem))


def find_scale(scale_identifier, api_key):
    """The scale a bridge program is speaking for, or None if the identifier or key is wrong."""
    scale = WeighingScale.query.filter_by(scale_identifier=(scale_identifier or "").strip()).first()
    if scale is None or scale.status != "ACTIVE":
        return None
    return scale if rules.api_key_matches(api_key, scale.api_key_hash) else None


def record_scale_reading(scale, weight_kg, reference=None):
    """A scale reported a stable weight. It replaces any earlier reading nobody used."""
    weight = _valid_weight(weight_kg)
    WeighingEvent.query.filter_by(scale_id=scale.id, processing_status="PENDING").update(
        {"processing_status": "DISCARDED"}, synchronize_session=False)
    event = WeighingEvent(
        buying_centre_id=scale.buying_centre_id, scale_id=scale.id, weight_kg=weight, source="SCALE",
        raw_reference=_text(reference, 120) or None,
    )
    scale.last_seen_at = utcnow()
    scale.integration_status = "CONNECTED"
    db.session.add(event)
    db.session.flush()
    return event


def create_manual_weight(actor, centre, weight_kg):
    """Fallback until a scale is connected. Flagged MANUAL and audit-logged."""
    if not current_app.config["ALLOW_MANUAL_WEIGHT"]:
        raise ServiceError("Typing a weight is switched off. The weight has to come from the scale.")
    weight = _valid_weight(weight_kg)
    WeighingEvent.query.filter_by(
        buying_centre_id=centre.id, source="MANUAL", created_by_id=actor.employee_id, processing_status="PENDING",
    ).update({"processing_status": "DISCARDED"}, synchronize_session=False)
    event = WeighingEvent(buying_centre_id=centre.id, weight_kg=weight, source="MANUAL",
                          created_by_id=actor.employee_id)
    db.session.add(event)
    db.session.flush()
    log_action("WEIGHT_ENTERED_MANUALLY", "weighing_event", event.id,
               new={"weight_kg": weight, "buying_centre": centre.code})
    return event


def latest_pending_weight(centre_id, actor):
    """The weight currently waiting at this centre, or None.

    Scale readings are shared by everyone at the centre; a manually typed
    weight belongs to the clerk who typed it. Readings older than
    WEIGHT_MAX_AGE_SECONDS are ignored so a forgotten weight can't be used later.
    """
    cutoff = utcnow() - timedelta(seconds=current_app.config["WEIGHT_MAX_AGE_SECONDS"])
    events = (
        WeighingEvent.query
        .filter(WeighingEvent.buying_centre_id == centre_id,
                WeighingEvent.processing_status == "PENDING",
                WeighingEvent.captured_at >= cutoff)
        .order_by(WeighingEvent.captured_at.desc(), WeighingEvent.id.desc())
        .all()
    )
    for event in events:
        if event.source == "SCALE" or event.created_by_id == actor.employee_id:
            return event
    return None


def lookup_farm_for_buying(farm_number, centre):
    """Check a scanned card. Returns the farm, or explains in plain words why tea can't be bought."""
    number = (farm_number or "").strip().upper()
    if not number:
        raise ServiceError("Scan the farmer's card first.")
    farm = Farm.query.filter_by(farm_number=number).first()
    if farm is None:
        raise ServiceError(f"No farm found for card '{number}'. Check the card and try again.")
    if farm.verification_status != "VERIFIED":
        raise ServiceError("This farm has not been verified yet, so tea cannot be bought against it.")
    farmer = farm.farmer
    if farmer.status != "ACTIVE":
        raise ServiceError("This farmer's account is not active.")
    if farmer.buying_centre_id != centre.id:
        raise ServiceError(f"This card belongs to a farmer registered at {farmer.buying_centre.name}. "
                           "Nothing was recorded.")
    return farm


def lookup_farm_by_farmer_number(farmer_number, centre):
    """Fallback for when the card won't scan: find the farm from the farmer's number instead.

    Same checks as lookup_farm_for_buying, just keyed by the farmer rather than the card.
    """
    number = (farmer_number or "").strip().upper()
    if not number:
        raise ServiceError("Type the farmer's number first.")
    farmer = Farmer.query.filter_by(farmer_number=number).first()
    if farmer is None:
        raise ServiceError(f"No farmer found with number '{number}'.")
    if farmer.status != "ACTIVE":
        raise ServiceError("This farmer's account is not active.")
    if farmer.buying_centre_id != centre.id:
        raise ServiceError(f"This farmer is registered at {farmer.buying_centre.name}. Nothing was recorded.")
    verified = [farm for farm in farmer.farms if farm.verification_status == "VERIFIED" and farm.farm_number]
    if not verified:
        raise ServiceError(f"{farmer.full_name} has no verified farm yet, so tea cannot be bought against it.")
    if len(verified) > 1:
        options = ", ".join(farm.farm_number for farm in verified)
        raise ServiceError(f"{farmer.full_name} has more than one farm ({options}) — scan the specific card instead.")
    return verified[0]


def farmers_for_buying(centre):
    """Farmer number + name for this centre: the list the buying screen filters as the clerk types.

    'verified' says whether the farmer has a farm that tea can be bought against yet.
    """
    farmers = (Farmer.query.options(selectinload(Farmer.farms))
               .filter_by(buying_centre_id=centre.id, status="ACTIVE")
               .order_by(Farmer.farmer_number).all())
    return [{"farmer_number": f.farmer_number, "name": f.full_name,
             "verified": any(farm.verification_status == "VERIFIED" and farm.farm_number for farm in f.farms)}
            for f in farmers]


def confirm_weighing(actor, centre, farm_number, event_id):
    """The clerk's single click: turn the weight on the screen into a transaction AND a receipt.

    Everything happens in one database transaction. If any step fails nothing
    is saved, and the weight stays waiting so the clerk can try again.
    """
    if not centre.is_active:
        raise ServiceError("This buying centre is not active.")
    farm = lookup_farm_for_buying(farm_number, centre)

    event = db.session.get(WeighingEvent, event_id) if event_id else None
    if event is None or event.buying_centre_id != centre.id:
        raise ServiceError("There is no weight to record. Put the tea on the scale first.")
    if event.processing_status != "PENDING":
        raise ServiceError("That weight was already used or replaced. Weigh the tea again.")
    if event.captured_at < utcnow() - timedelta(seconds=current_app.config["WEIGHT_MAX_AGE_SECONDS"]):
        raise ServiceError("That weight is too old. Weigh the tea again.")
    if event.source == "MANUAL" and event.created_by_id != actor.employee_id:
        raise ServiceError("That weight was typed by another clerk.")

    # Claim the weight with one conditional UPDATE. If two requests race (a
    # double click, or two screens), exactly one gets a row back.
    claimed = WeighingEvent.query.filter(
        WeighingEvent.id == event.id, WeighingEvent.processing_status == "PENDING",
    ).update({"processing_status": "PROCESSED", "farmer_id": farm.farmer_id}, synchronize_session=False)
    if claimed != 1:
        raise ServiceError("That weight was just recorded by someone else. Weigh the tea again.")

    now = utcnow()
    day = now.strftime("%Y%m%d")
    transaction = TeaTransaction(
        transaction_number=rules.format_transaction_number(now, next_sequence(f"transaction:{day}")),
        farmer_id=farm.farmer_id, farm_id=farm.id, buying_centre_id=centre.id,
        clerk_employee_id=actor.employee_id, weight_kg=event.weight_kg,
        transaction_time=now, source=event.source,
    )
    db.session.add(transaction)
    db.session.flush()

    receipt = Receipt(
        receipt_number=rules.format_receipt_number(now, next_sequence(f"receipt:{day}")),
        transaction_id=transaction.id, farmer_id=farm.farmer_id, issued_at=now,
    )
    db.session.add(receipt)

    event.processing_status = "PROCESSED"      # keep the in-memory object in step with the UPDATE above
    event.farmer_id = farm.farmer_id
    event.transaction_id = transaction.id
    db.session.flush()

    log_action("TRANSACTION_RECORDED", "tea_transaction", transaction.id,
               new={"transaction_number": transaction.transaction_number, "receipt_number": receipt.receipt_number,
                    "farm_number": farm.farm_number, "weight_kg": transaction.weight_kg,
                    "buying_centre": centre.code, "source": transaction.source})
    return transaction, receipt


def void_transaction(actor, transaction, reason):
    """Cancel a wrongly recorded transaction. The original weight stays on file; it just stops counting."""
    reason = _text(reason, 300)
    if len(reason) < 5:
        raise ServiceError("Give a reason for voiding (a few words).")
    if transaction.status != "VALID":
        raise ServiceError("This transaction is already voided.")
    transaction.status = "VOIDED"
    transaction.void_reason = reason
    transaction.voided_by_id = actor.employee_id
    transaction.voided_at = utcnow()
    if transaction.receipt is not None:
        transaction.receipt.status = "VOID"
    log_action("TRANSACTION_VOIDED", "tea_transaction", transaction.id,
               old={"status": "VALID", "weight_kg": transaction.weight_kg},
               new={"status": "VOIDED", "reason": reason})


def register_receipt_print(receipt):
    """Count a print. The second and later prints are marked DUPLICATE on paper."""
    receipt.print_count = (receipt.print_count or 0) + 1
    receipt.last_printed_at = utcnow()
    log_action("RECEIPT_PRINTED" if receipt.print_count == 1 else "RECEIPT_REPRINTED",
               "receipt", receipt.id, new={"print_count": receipt.print_count})


# ============================================================================
# Notices: department, staff-wide and public
# ============================================================================

def _live_notices():
    return Notice.query.filter(
        Notice.is_active.is_(True),
        or_(Notice.expires_at.is_(None), Notice.expires_at > utcnow()),
    )


def visible_notices(user, limit=None):
    """What one employee may read: public notices, all-staff notices, and their own department's."""
    query = _live_notices().filter(or_(
        Notice.audience.in_(("PUBLIC", "STAFF")),
        and_(Notice.audience == "DEPARTMENT", Notice.department_id == user.employee.department_id),
    )).order_by(Notice.created_at.desc())
    return query.limit(limit).all() if limit else query.all()


def all_live_notices():
    """Every live notice in the factory (for people who publish notices and need the whole picture)."""
    return _live_notices().order_by(Notice.created_at.desc()).all()


def public_notices(centre_code=None, limit=None):
    """Notices for farmers and visitors. With a centre code: notices for everyone plus that centre's own."""
    query = _live_notices().filter(Notice.audience == "PUBLIC")
    if centre_code:
        centre = BuyingCentre.query.filter_by(code=centre_code).first()
        if centre is None:
            return []
        query = query.filter(or_(Notice.buying_centre_id.is_(None), Notice.buying_centre_id == centre.id))
    query = query.order_by(Notice.created_at.desc())
    return query.limit(limit).all() if limit else query.all()


def create_notice(actor, *, audience, category, title, content,
                  department_id=None, buying_centre_id=None, expires_at=None):
    title, content = _text(title, 160), (content or "").strip()
    if not title or not content:
        raise ServiceError("A notice needs a title and some text.")
    if category not in rules.NOTICE_CATEGORIES:
        raise ServiceError("Choose a category.")

    problem = rules.notice_post_problem(
        audience,
        can_post=has_permission(actor, "POST_NOTICE"),
        can_publish=has_permission(actor, "PUBLISH_NOTICE"),
        actor_department_id=actor.employee.department_id,
        target_department_id=department_id,
    )
    if problem:
        raise ServiceError(problem)

    if audience == "DEPARTMENT":
        if db.session.get(Department, department_id) is None:
            raise ServiceError("Choose a department.")
        buying_centre_id = None
    elif audience == "STAFF":
        department_id = buying_centre_id = None
    else:  # PUBLIC
        department_id = None
        if buying_centre_id and db.session.get(BuyingCentre, buying_centre_id) is None:
            raise ServiceError("Choose a valid buying centre.")

    notice = Notice(
        audience=audience, department_id=department_id, buying_centre_id=buying_centre_id or None,
        category=category, title=title, content=content, posted_by_id=actor.employee_id, expires_at=expires_at,
    )
    db.session.add(notice)
    db.session.flush()
    log_action("NOTICE_POSTED", "notice", notice.id,
               new={"audience": audience, "department_id": department_id, "title": title})
    return notice


def deactivate_notice(actor, notice):
    if notice.posted_by_id != actor.employee_id and not has_permission(actor, "PUBLISH_NOTICE"):
        raise ServiceError("You can only take down notices you posted.")
    notice.is_active = False
    log_action("NOTICE_REMOVED", "notice", notice.id, old={"title": notice.title})


# ============================================================================
# Staff accounts
# ============================================================================

def _username(value):
    return (value or "").strip().lower()


def create_staff(actor, *, first_name, last_name, username, password, department_id, role_ids,
                 phone="", email="", pin=""):
    first_name, last_name = _text(first_name, 80), _text(last_name, 80)
    username = _username(username)
    if not first_name or not last_name or not username:
        raise ServiceError("First name, last name and username are required.")
    if User.query.filter_by(username=username).first():
        raise ServiceError("That username is already taken.")
    problem = rules.password_problem(password)
    if problem:
        raise ServiceError(problem)
    pin = (pin or "").strip()
    if pin and not rules.pin_is_valid(pin):
        raise ServiceError("A PIN must be 4 to 6 digits.")
    department = db.session.get(Department, department_id) if department_id else None
    if department is None:
        raise ServiceError("Choose a department.")
    roles = Role.query.filter(Role.id.in_(role_ids or [0]), Role.status == "ACTIVE").all()
    if not roles:
        raise ServiceError("Choose at least one role.")

    employee = Employee(
        employee_number=rules.format_employee_number(next_sequence("employee")),
        first_name=first_name, last_name=last_name, phone=_text(phone, 30) or None,
        email=_text(email, 120) or None, department_id=department.id, roles=roles,
    )
    db.session.add(employee)
    db.session.flush()
    user = User(employee_id=employee.id, username=username)
    user.set_password(password)
    if pin:
        user.set_pin(pin)
    db.session.add(user)
    db.session.flush()
    log_action("USER_CREATED", "user", user.id,
               new={"username": username, "employee_number": employee.employee_number,
                    "department": department.name, "roles": sorted(r.name for r in roles)})
    return user


def set_user_status(actor, user, active):
    if user.id == actor.id and not active:
        raise ServiceError("You cannot deactivate your own account.")
    old = user.status
    user.status = "ACTIVE" if active else "DISABLED"
    log_action("USER_ACTIVATED" if active else "USER_DEACTIVATED", "user", user.id,
               old={"status": old}, new={"status": user.status})


def reset_password(actor, user, new_password):
    problem = rules.password_problem(new_password)
    if problem:
        raise ServiceError(problem)
    user.set_password(new_password)
    log_action("PASSWORD_RESET", "user", user.id)


def set_user_pin(actor, user, pin):
    pin = (pin or "").strip()
    if not rules.pin_is_valid(pin):
        raise ServiceError("A PIN must be 4 to 6 digits.")
    user.set_pin(pin)
    log_action("PIN_SET", "user", user.id)


def set_employee_roles(actor, employee, role_ids):
    roles = Role.query.filter(Role.id.in_(role_ids or [0]), Role.status == "ACTIVE").all()
    if not roles:
        raise ServiceError("An employee needs at least one role.")
    old = sorted(r.name for r in employee.roles)
    employee.roles = roles
    log_action("ROLES_CHANGED", "employee", employee.id, old={"roles": old},
               new={"roles": sorted(r.name for r in roles)})


# ============================================================================
# Buying centres and scales
# ============================================================================

def create_centre(actor, *, name, code, location="", established_at=None):
    name, code = _text(name, 120), _text(code, 10)
    if not name or not code:
        raise ServiceError("A buying centre needs a name and a code.")
    if BuyingCentre.query.filter_by(code=code).first():
        raise ServiceError("A buying centre with that code already exists.")
    centre = BuyingCentre(name=name, code=code, location=_text(location, 200) or None, established_at=established_at)
    db.session.add(centre)
    db.session.flush()
    log_action("CENTRE_CREATED", "buying_centre", centre.id, new={"code": code, "name": name})
    return centre


def update_centre(actor, centre, *, name, location, status):
    name = _text(name, 120)
    if not name:
        raise ServiceError("A buying centre needs a name.")
    if status not in ("ACTIVE", "INACTIVE"):
        raise ServiceError("Choose a status.")
    old = {"name": centre.name, "location": centre.location, "status": centre.status}
    centre.name, centre.location, centre.status = name, _text(location, 200) or None, status
    log_action("CENTRE_UPDATED", "buying_centre", centre.id, old=old,
               new={"name": centre.name, "location": centre.location, "status": centre.status})


def create_scale(actor, *, centre_id, scale_identifier, model=""):
    """Register a scale. Returns (scale, api_key); the key is shown once and never stored in the clear."""
    centre = db.session.get(BuyingCentre, centre_id) if centre_id else None
    identifier = _text(scale_identifier, 60)
    if centre is None or not identifier:
        raise ServiceError("Choose a buying centre and give the scale an identifier.")
    if WeighingScale.query.filter_by(scale_identifier=identifier).first():
        raise ServiceError("A scale with that identifier already exists.")
    key = rules.generate_api_key()
    scale = WeighingScale(buying_centre_id=centre.id, scale_identifier=identifier,
                          model=_text(model, 80) or None, api_key_hash=rules.hash_api_key(key))
    db.session.add(scale)
    db.session.flush()
    log_action("SCALE_REGISTERED", "weighing_scale", scale.id, new={"identifier": identifier, "centre": centre.code})
    return scale, key


def rotate_scale_key(actor, scale):
    key = rules.generate_api_key()
    scale.api_key_hash = rules.hash_api_key(key)
    scale.integration_status = "NOT_CONNECTED"
    log_action("SCALE_KEY_ROTATED", "weighing_scale", scale.id)
    return key


def toggle_scale(actor, scale):
    scale.status = "INACTIVE" if scale.status == "ACTIVE" else "ACTIVE"
    log_action("SCALE_STATUS_CHANGED", "weighing_scale", scale.id, new={"status": scale.status})
    return scale.status
