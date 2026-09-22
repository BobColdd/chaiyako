"""JSON API.

Three audiences:

1. The clerk mobile app  - bearer-token login, then the same scan / weigh / confirm steps as the web screen.
2. Weighing scales       - a small bridge program at the buying centre posts each stable weight to
                           /api/scale/reading using that scale's own key.
3. The farmer app/public - /api/public/notices needs no login.

Everything else in the system is server-rendered HTML for browsers.

The token is a signed user id (no token table). It is stateless, so /api/logout can't revoke
it — but every request re-checks that the account is still active and still allowed to
record tea, so deactivating an account locks a lost phone out immediately.
"""
from datetime import timedelta
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app import db
from app.models import BuyingCentre, TeaTransaction, User
from app.permissions import has_permission, permission_source
from app.services import (
    ServiceError, confirm_weighing, create_manual_weight, find_scale, latest_pending_weight,
    lookup_farm_for_buying, public_notices, record_scale_reading,
)
from app.timeutil import utcnow

api_bp = Blueprint("api", __name__, url_prefix="/api")

TOKEN_MAX_AGE = 60 * 60 * 24 * 30  # 30 days
TOKEN_SALT = "clerk-api-token"


def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=TOKEN_SALT)


def _iso(moment):
    """Stored times are UTC; the trailing Z says so."""
    return moment.isoformat() + "Z" if moment else None


def _staff_json(user):
    return {"id": user.id, "full_name": user.full_name, "username": user.username}


def api_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return jsonify(error="Missing or invalid Authorization header."), 401
        try:
            data = _serializer().loads(header.split(" ", 1)[1], max_age=TOKEN_MAX_AGE)
        except SignatureExpired:
            return jsonify(error="Session expired. Please log in again."), 401
        except BadSignature:
            return jsonify(error="Invalid token."), 401

        user_id = data.get("uid") if isinstance(data, dict) else None
        user = db.session.get(User, user_id) if isinstance(user_id, int) else None
        source = permission_source(user, "RECORD_TRANSACTION") if user is not None and user.is_active else None
        if source is None:
            return jsonify(error="Account not found, deactivated, or not allowed to record tea."), 401

        g.api_user = user
        g.audit_user = user
        g.acting_delegation_id = source[1]
        return view(*args, **kwargs)
    return wrapped


def _centre_from_request():
    data = request.get_json(silent=True) if request.is_json else None
    raw = request.args.get("buying_centre_id") or (data or {}).get("buying_centre_id")
    centre = db.session.get(BuyingCentre, int(raw)) if str(raw or "").isdigit() else None
    if centre is None or not centre.is_active:
        return None, (jsonify(error="Choose a valid, active buying centre (buying_centre_id)."), 400)
    return centre, None


def _event_json(event):
    if event is None:
        return None
    return {"event_id": event.id, "weight_kg": event.weight_kg, "source": event.source,
            "captured_at": _iso(event.captured_at)}


# ------------------------------------------------------------------ clerk app

@api_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip().lower()
    pin = (data.get("pin") or "").strip()
    password = data.get("password") or ""

    user = User.query.filter_by(username=username).first()
    if user is None:
        credentials_ok = False
    elif pin:
        credentials_ok = user.check_pin(pin)
    else:
        credentials_ok = bool(password) and user.check_password(password)

    if not credentials_ok:
        return jsonify(error="Invalid username or PIN."), 401
    if not user.is_active:
        return jsonify(error="This account has been deactivated."), 403
    if not has_permission(user, "RECORD_TRANSACTION"):
        return jsonify(error="This app is for tea buying clerks."), 403
    return jsonify(token=_serializer().dumps({"uid": user.id}), staff=_staff_json(user))


@api_bp.route("/me")
@api_login_required
def me():
    centres = BuyingCentre.query.filter_by(status="ACTIVE").order_by(BuyingCentre.name).all()
    return jsonify(
        staff=_staff_json(g.api_user),
        centres=[{"id": c.id, "code": c.code, "name": c.name, "location": c.location} for c in centres],
        manual_weight_allowed=bool(current_app.config["ALLOW_MANUAL_WEIGHT"]),
    )


@api_bp.route("/farm/<farm_number>")
@api_login_required
def lookup_farm(farm_number):
    centre, error = _centre_from_request()
    if error:
        return error
    try:
        farm = lookup_farm_for_buying(farm_number, centre)
    except ServiceError as problem:
        return jsonify(error=str(problem)), 400
    farmer = farm.farmer
    return jsonify(farm_number=farm.farm_number, farmer_name=farmer.full_name,
                   farmer_number=farmer.farmer_number, tea_bushes=farm.tea_bushes)


@api_bp.route("/weight/latest")
@api_login_required
def weight_latest():
    centre, error = _centre_from_request()
    if error:
        return error
    return jsonify(event=_event_json(latest_pending_weight(centre.id, g.api_user)))


@api_bp.route("/weight/manual", methods=["POST"])
@api_login_required
def weight_manual():
    centre, error = _centre_from_request()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    try:
        event = create_manual_weight(g.api_user, centre, data.get("kilos"))
        db.session.commit()
    except ServiceError as problem:
        db.session.rollback()
        return jsonify(error=str(problem)), 400
    return jsonify(event=_event_json(event))


@api_bp.route("/buy", methods=["POST"])
@api_login_required
def buy():
    centre, error = _centre_from_request()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    raw_event_id = str(data.get("weighing_event_id", ""))
    try:
        transaction, receipt = confirm_weighing(
            g.api_user, centre, data.get("farm_number"), int(raw_event_id) if raw_event_id.isdigit() else None)
        db.session.commit()
    except ServiceError as problem:
        db.session.rollback()
        return jsonify(error=str(problem)), 400
    return jsonify(transaction={
        "id": transaction.id, "transaction_number": transaction.transaction_number,
        "receipt_id": receipt.id, "receipt_number": receipt.receipt_number,
        "farm_number": transaction.farm.farm_number, "farmer_name": transaction.farmer.full_name,
        "buying_centre": centre.name, "weight_kg": transaction.weight_kg,
        "transaction_time": _iso(transaction.transaction_time), "clerk": g.api_user.full_name,
    })


@api_bp.route("/transactions/today")
@api_login_required
def transactions_today():
    centre, error = _centre_from_request()
    if error:
        return error
    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = (TeaTransaction.query
            .filter(TeaTransaction.clerk_employee_id == g.api_user.employee_id,
                    TeaTransaction.buying_centre_id == centre.id,
                    TeaTransaction.transaction_time >= start, TeaTransaction.transaction_time < start + timedelta(days=1))
            .order_by(TeaTransaction.transaction_time.desc()).all())
    return jsonify(
        transactions=[{"transaction_number": t.transaction_number, "farm_number": t.farm.farm_number,
                       "farmer_name": t.farmer.full_name, "weight_kg": t.weight_kg, "status": t.status,
                       "transaction_time": _iso(t.transaction_time)} for t in rows],
        total_kg=round(sum(t.weight_kg for t in rows if t.status == "VALID"), 1),
    )


@api_bp.route("/logout", methods=["POST"])
@api_login_required
def logout():
    return jsonify(ok=True)   # nothing to end: the token is stateless (see the module note)


# ----------------------------------------------------------------- scale bridge

@api_bp.route("/scale/reading", methods=["POST"])
def scale_reading():
    """A scale (via its bridge program) reports a stable weight.

    Header:  X-Scale-Key: <the key shown once when the scale was registered>
    Body:    {"scale_identifier": "SC-001", "weight_kg": 18.4, "reference": "optional"}
    Send only STABLE weights: each reading replaces the previous unused one.
    """
    data = request.get_json(silent=True) or {}
    scale = find_scale(data.get("scale_identifier"), request.headers.get("X-Scale-Key", ""))
    if scale is None:
        return jsonify(error="Unknown scale, wrong key, or the scale is switched off."), 401
    try:
        event = record_scale_reading(scale, data.get("weight_kg"), data.get("reference"))
        db.session.commit()
    except ServiceError as problem:
        db.session.rollback()
        return jsonify(error=str(problem)), 400
    return jsonify(ok=True, event_id=event.id, weight_kg=event.weight_kg)


# ----------------------------------------------------------------------- public

@api_bp.route("/public/notices")
def public_notices_feed():
    """Notices for farmers. Optional ?centre=<code> adds that centre's own notices to the general ones."""
    notices = public_notices(centre_code=(request.args.get("centre") or "").strip() or None, limit=50)
    return jsonify(notices=[{
        "id": n.id, "category": n.category, "title": n.title, "content": n.content,
        "buying_centre": {"code": n.buying_centre.code, "name": n.buying_centre.name} if n.buying_centre else None,
        "posted_at": _iso(n.created_at), "expires_at": _iso(n.expires_at),
    } for n in notices])
