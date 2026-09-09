"""JSON API used by the farmer mobile app (React Native).

Mirrors app/api.py (the clerk app's API) but for the Farmer side: phone +
password login, with the same first-login "verify with a code, then set your
own password" flow the website uses (see app/auth.py). Bearer-token auth,
stateless (itsdangerous-signed farmer id) — see app/api.py's module
docstring for the reasoning, it applies here too.
"""

from functools import wraps
from datetime import date, datetime, timedelta

from flask import Blueprint, request, jsonify, current_app
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app import db
from app.models import Farmer, Farm, PluckingRecord
from app.factory_models import Purchase, Notice, FertilizerDistribution, Complaint

farmer_api_bp = Blueprint("farmer_api", __name__, url_prefix="/api/farmer")

TOKEN_MAX_AGE = 60 * 60 * 24 * 30  # 30 days
TOKEN_SALT = "farmer-api-token"
CODE_VALID_MINUTES = 60 * 24  # matches app/auth.py


def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=TOKEN_SALT)


def make_token(farmer):
    return _serializer().dumps({"id": farmer.id})


def farmer_login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify(error="Missing or invalid Authorization header."), 401

        token = auth.split(" ", 1)[1]
        try:
            data = _serializer().loads(token, max_age=TOKEN_MAX_AGE)
        except SignatureExpired:
            return jsonify(error="Session expired. Please log in again."), 401
        except BadSignature:
            return jsonify(error="Invalid token."), 401

        farmer = Farmer.query.get(data.get("id"))
        if not farmer:
            return jsonify(error="Account not found."), 401

        request.farmer = farmer
        return f(*args, **kwargs)

    return wrapped


def _farm_json(f):
    return {
        "id": f.id,
        "farm_number": f.farm_number,
        "location": f.location,
        "approx_bushes": f.approx_bushes,
        "acreage": f.acreage,
        "buying_center": f.buying_center.name if f.buying_center else None,
    }


def _purchase_json(p):
    return {
        "id": p.id,
        "receipt_number": p.receipt_number,
        "farm_number": p.farm.farm_number,
        "kilos": p.kilos,
        "purchased_at": p.purchased_at.isoformat(),
    }


def _notice_json(n):
    return {
        "id": n.id,
        "category": n.category,
        "title": n.title,
        "content": n.content,
        "created_at": n.created_at.isoformat(),
    }


def _fertilizer_json(d):
    return {
        "id": d.id,
        "fertilizer_type": d.fertilizer_type,
        "quantity_kg": d.quantity_kg,
        "date": d.date.isoformat() if d.date else None,
        "notes": d.notes,
    }


def _complaint_json(c):
    return {
        "id": c.id,
        "category": c.category,
        "description": c.description,
        "status": c.status,
        "manager_note": c.manager_note,
        "created_at": c.created_at.isoformat(),
        "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
        "purchase_receipt": c.purchase.receipt_number if c.purchase else None,
    }


def _select_farm(farmer, farm_id):
    farms = farmer.farms
    if not farms:
        return None
    if farm_id:
        for f in farms:
            if f.id == farm_id:
                return f
    return farms[0]


@farmer_api_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    password = data.get("password") or ""

    farmer = Farmer.query.filter_by(phone=phone).first()
    if not farmer:
        return jsonify(error="No farmer account found with that phone number."), 404

    if farmer.must_set_password or not farmer.password_hash:
        return jsonify(needs_verification=True, phone=farmer.phone)

    if not farmer.check_password(password):
        return jsonify(error="Incorrect password."), 401

    return jsonify(
        token=make_token(farmer),
        farmer={"id": farmer.id, "full_name": farmer.full_name, "phone": farmer.phone},
    )


@farmer_api_bp.route("/verify", methods=["POST"])
def verify():
    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    code = (data.get("code") or "").strip()
    password = data.get("password") or ""
    confirm = data.get("confirm_password") or ""

    farmer = Farmer.query.filter_by(phone=phone).first()
    if not farmer:
        return jsonify(error="No farmer account found with that phone number."), 404

    if not farmer.verification_code or farmer.verification_code != code:
        return jsonify(error="Incorrect verification code."), 400

    if farmer.code_generated_at and datetime.utcnow() - farmer.code_generated_at > timedelta(minutes=CODE_VALID_MINUTES):
        return jsonify(error="That code has expired. Ask your factory to resend a new one."), 400

    if len(password) < 6:
        return jsonify(error="Password must be at least 6 characters."), 400

    if password != confirm:
        return jsonify(error="Passwords do not match."), 400

    farmer.set_password(password)
    db.session.commit()

    return jsonify(
        token=make_token(farmer),
        farmer={"id": farmer.id, "full_name": farmer.full_name, "phone": farmer.phone},
    )


@farmer_api_bp.route("/me", methods=["GET"])
@farmer_login_required
def me():
    farmer = request.farmer
    return jsonify(
        farmer={"id": farmer.id, "full_name": farmer.full_name, "phone": farmer.phone},
        farms=[_farm_json(f) for f in farmer.farms],
    )


@farmer_api_bp.route("/dashboard", methods=["GET"])
@farmer_login_required
def dashboard():
    farmer = request.farmer
    farm_id = request.args.get("farm_id", type=int)
    farm = _select_farm(farmer, farm_id)

    if not farm:
        return jsonify(
            farm=None,
            farms=[],
            total_kilos=0,
            avg_daily=0,
            last_30_kilos=0,
            recent_purchases=[],
            open_complaints_count=Complaint.query.filter_by(farmer_id=farmer.id, status="open").count(),
            grand_total_purchased=0,
        )

    cutoff = date.today() - timedelta(days=30)
    last_30 = (
        PluckingRecord.query.filter(PluckingRecord.farm_id == farm.id, PluckingRecord.date >= cutoff)
        .order_by(PluckingRecord.date.asc())
        .all()
    )
    last_30_kilos = sum(r.kilos for r in last_30)
    days_with_records = len({r.date for r in last_30}) or 1
    avg_daily = round(last_30_kilos / days_with_records, 1)

    recent_purchases = (
        Purchase.query.filter_by(farm_id=farm.id).order_by(Purchase.purchased_at.desc()).limit(8).all()
    )

    tallyboy = None
    if farm.buying_center and farm.buying_center.route:
        clerk = farm.buying_center.route.current_tallyboy
        tallyboy = clerk.full_name if clerk else None

    return jsonify(
        farm=_farm_json(farm),
        farms=[_farm_json(f) for f in farmer.farms],
        total_kilos=farm.total_kilos,
        avg_daily=avg_daily,
        last_30_kilos=last_30_kilos,
        recent_purchases=[_purchase_json(p) for p in recent_purchases],
        buying_center=farm.buying_center.name if farm.buying_center else None,
        tallyboy=tallyboy,
        open_complaints_count=Complaint.query.filter_by(farmer_id=farmer.id, status="open").count(),
        grand_total_purchased=farmer.total_purchased_kilos,
    )


@farmer_api_bp.route("/plucking", methods=["POST"])
@farmer_login_required
def add_plucking():
    farmer = request.farmer
    data = request.get_json(silent=True) or {}

    farm_id = data.get("farm_id")
    farm = Farm.query.filter_by(id=farm_id, farmer_id=farmer.id).first()
    if not farm:
        return jsonify(error="Select one of your farms."), 400

    try:
        kilos = float(data.get("kilos"))
    except (TypeError, ValueError):
        kilos = None
    if not kilos or kilos <= 0:
        return jsonify(error="Enter a valid number of kilos."), 400

    rec_date_raw = data.get("date")
    try:
        rec_date = date.fromisoformat(rec_date_raw) if rec_date_raw else date.today()
    except ValueError:
        rec_date = date.today()

    record = PluckingRecord(
        farm_id=farm.id,
        date=rec_date,
        kilos=kilos,
        pluckers_count=data.get("pluckers_count"),
        notes=(data.get("notes") or "").strip() or None,
    )
    db.session.add(record)
    db.session.commit()

    return jsonify(ok=True, total_kilos=farm.total_kilos)


@farmer_api_bp.route("/receipts", methods=["GET"])
@farmer_login_required
def receipts():
    farmer = request.farmer
    farm_ids = [f.id for f in farmer.farms]
    purchases = (
        Purchase.query.filter(Purchase.farm_id.in_(farm_ids)).order_by(Purchase.purchased_at.desc()).all()
        if farm_ids else []
    )
    return jsonify(
        purchases=[_purchase_json(p) for p in purchases],
        grand_total=farmer.total_purchased_kilos,
    )


@farmer_api_bp.route("/notices", methods=["GET"])
@farmer_login_required
def notices():
    farmer = request.farmer
    center_ids = [f.buying_center_id for f in farmer.farms if f.buying_center_id]
    all_notices = (
        Notice.query.filter(db.or_(Notice.buying_center_id.is_(None), Notice.buying_center_id.in_(center_ids)))
        .order_by(Notice.created_at.desc())
        .all()
    )
    return jsonify(notices=[_notice_json(n) for n in all_notices])


@farmer_api_bp.route("/fertilizer", methods=["GET"])
@farmer_login_required
def fertilizer():
    farmer = request.farmer
    center_ids = [f.buying_center_id for f in farmer.farms if f.buying_center_id]
    personal = (
        FertilizerDistribution.query.filter_by(farmer_id=farmer.id)
        .order_by(FertilizerDistribution.date.desc())
        .all()
    )
    center_wide = (
        FertilizerDistribution.query.filter(
            FertilizerDistribution.farmer_id.is_(None), FertilizerDistribution.buying_center_id.in_(center_ids)
        )
        .order_by(FertilizerDistribution.date.desc())
        .all()
        if center_ids else []
    )
    return jsonify(
        personal=[_fertilizer_json(d) for d in personal],
        center_wide=[_fertilizer_json(d) for d in center_wide],
    )


@farmer_api_bp.route("/complaints", methods=["GET"])
@farmer_login_required
def complaints():
    farmer = request.farmer
    farm_ids = [f.id for f in farmer.farms]
    recent_purchases = (
        Purchase.query.filter(Purchase.farm_id.in_(farm_ids)).order_by(Purchase.purchased_at.desc()).limit(20).all()
        if farm_ids else []
    )
    my_complaints = Complaint.query.filter_by(farmer_id=farmer.id).order_by(Complaint.created_at.desc()).all()
    return jsonify(
        complaints=[_complaint_json(c) for c in my_complaints],
        recent_purchases=[_purchase_json(p) for p in recent_purchases],
    )


@farmer_api_bp.route("/complaints", methods=["POST"])
@farmer_login_required
def file_complaint():
    farmer = request.farmer
    data = request.get_json(silent=True) or {}

    category = data.get("category", "other")
    description = (data.get("description") or "").strip()
    purchase_id = data.get("purchase_id")

    if not description:
        return jsonify(error="Please describe the issue."), 400

    farm_ids = [f.id for f in farmer.farms]
    valid_purchase = None
    if purchase_id:
        valid_purchase = Purchase.query.filter(Purchase.id == purchase_id, Purchase.farm_id.in_(farm_ids)).first()

    complaint = Complaint(
        farmer_id=farmer.id,
        purchase_id=valid_purchase.id if valid_purchase else None,
        category=category,
        description=description,
    )
    db.session.add(complaint)
    db.session.commit()

    return jsonify(complaint=_complaint_json(complaint))
