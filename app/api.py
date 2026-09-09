"""JSON API used by the clerk/tallyboy mobile app (React Native).

Everything else in this app is server-rendered HTML for the browser; this
blueprint is the one part of the surface meant to be called by a phone. Auth
here is a bearer token (not the Flask-Login cookie session), since a mobile
client can't rely on cookies the way a browser can.

Token = itsdangerous-signed staff id, no separate token table needed. It's
stateless, so "logging out" of the app only ends the ClerkSession (closing
the buying-center green mark) — it does not revoke the token itself. That's
an acceptable trade-off for a field tool; if a phone is lost, deactivate the
staff account (is_active_staff) to lock the token out immediately, since
every endpoint re-checks that flag on every request.
"""

from functools import wraps
from datetime import date, datetime

from flask import Blueprint, request, jsonify, current_app
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app import db
from app.factory_models import FactoryUser, ClerkSession, Purchase
from app.models import Farm

api_bp = Blueprint("api", __name__, url_prefix="/api")

TOKEN_MAX_AGE = 60 * 60 * 24 * 30  # 30 days
TOKEN_SALT = "clerk-api-token"


def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=TOKEN_SALT)


def make_token(staff):
    return _serializer().dumps({"id": staff.id})


def api_login_required(f):
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

        staff = FactoryUser.query.get(data.get("id"))
        if not staff or not staff.is_clerk or not staff.is_active_staff:
            return jsonify(error="Account not found or not an active clerk."), 401

        request.clerk = staff
        return f(*args, **kwargs)

    return wrapped


def _center_json(c):
    return {
        "id": c.id,
        "name": c.name,
        "code": c.code,
        "location_notes": c.location_notes,
        "is_buying_now": c.is_buying_now,
    }


def _session_json(s):
    return {
        "id": s.id,
        "buying_center": _center_json(s.buying_center),
        "login_at": s.login_at.isoformat(),
    }


def _purchase_json(p):
    return {
        "id": p.id,
        "receipt_number": p.receipt_number,
        "farm_number": p.farm.farm_number,
        "farmer_name": p.farm.owner.full_name,
        "kilos": p.kilos,
        "purchased_at": p.purchased_at.isoformat(),
    }


@api_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip().lower()
    pin = (data.get("pin") or "").strip()

    staff = FactoryUser.query.filter_by(username=username).first()

    if not staff or not staff.check_pin(pin):
        return jsonify(error="Invalid username or PIN."), 401

    if not staff.is_active_staff:
        return jsonify(error="This staff account has been deactivated."), 403

    if not staff.is_clerk:
        return jsonify(error="This app is for clerks/tallyboys only."), 403

    return jsonify(
        token=make_token(staff),
        staff={"id": staff.id, "full_name": staff.full_name, "username": staff.username},
    )


@api_bp.route("/me", methods=["GET"])
@api_login_required
def me():
    staff = request.clerk
    route = staff.current_route
    session = staff.active_session
    return jsonify(
        staff={"id": staff.id, "full_name": staff.full_name, "username": staff.username},
        route={"id": route.id, "name": route.name} if route else None,
        centers=[_center_json(c) for c in (route.buying_centers if route else [])],
        active_session=_session_json(session) if session else None,
    )


@api_bp.route("/select-center", methods=["POST"])
@api_login_required
def select_center():
    staff = request.clerk
    if staff.active_session:
        return jsonify(error="You already have an active buying session. Log out to switch centers."), 400

    data = request.get_json(silent=True) or {}
    center_id = data.get("buying_center_id")

    route = staff.current_route
    valid_ids = {c.id for c in route.buying_centers} if route else set()
    if not center_id or center_id not in valid_ids:
        return jsonify(error="Choose a valid buying center from your route."), 400

    session = ClerkSession(clerk_id=staff.id, buying_center_id=center_id)
    db.session.add(session)
    db.session.commit()
    return jsonify(active_session=_session_json(session))


@api_bp.route("/farm/<farm_number>", methods=["GET"])
@api_login_required
def lookup_farm(farm_number):
    staff = request.clerk
    session = staff.active_session
    if not session:
        return jsonify(error="Select a buying center first."), 400

    farm = Farm.query.filter_by(farm_number=farm_number.strip().upper()).first()
    if not farm:
        return jsonify(error="No farm found for that card."), 404
    if farm.buying_center_id != session.buying_center_id:
        return jsonify(error="This card belongs to a different buying center."), 400

    return jsonify(farm_number=farm.farm_number, farmer_name=farm.owner.full_name)


@api_bp.route("/buy", methods=["POST"])
@api_login_required
def buy():
    staff = request.clerk
    session = staff.active_session
    if not session:
        return jsonify(error="Select a buying center first."), 400

    data = request.get_json(silent=True) or {}
    farm_number = (data.get("farm_number") or "").strip().upper()

    try:
        kilos = float(data.get("kilos"))
    except (TypeError, ValueError):
        kilos = None

    farm = Farm.query.filter_by(farm_number=farm_number).first()
    if not farm:
        return jsonify(error=f"No farm found for card '{farm_number}'."), 404

    if farm.buying_center_id != session.buying_center_id:
        center_name = farm.buying_center.name if farm.buying_center else "unknown"
        return jsonify(error=f"This card belongs to a different buying center ({center_name})."), 400

    if not kilos or kilos <= 0:
        return jsonify(error="Enter a valid weight in kilos."), 400

    purchase = Purchase(
        receipt_number=Purchase.generate_receipt_number(),
        farm_id=farm.id,
        buying_center_id=session.buying_center_id,
        clerk_id=staff.id,
        kilos=kilos,
    )
    db.session.add(purchase)
    db.session.commit()

    return jsonify(purchase=_purchase_json(purchase))


@api_bp.route("/purchases/today", methods=["GET"])
@api_login_required
def purchases_today():
    staff = request.clerk
    session = staff.active_session
    if not session:
        return jsonify(purchases=[], total_kilos=0)

    purchases = (
        Purchase.query.filter_by(clerk_id=staff.id, buying_center_id=session.buying_center_id)
        .filter(db.func.date(Purchase.purchased_at) == date.today())
        .order_by(Purchase.purchased_at.desc())
        .all()
    )
    return jsonify(
        purchases=[_purchase_json(p) for p in purchases],
        total_kilos=sum(p.kilos for p in purchases),
    )


@api_bp.route("/logout", methods=["POST"])
@api_login_required
def logout():
    staff = request.clerk
    session = staff.active_session
    if session:
        session.logout_at = datetime.utcnow()
        db.session.commit()
    return jsonify(ok=True)
