import secrets
from functools import wraps
from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user

from app import db
from app.models import Farmer, Farm
from app.factory_models import (
    FactoryUser, BuyingCenter, Route, RouteAssignment, ClerkSession,
    Purchase, Notice, FertilizerDistribution, Complaint,
)
from app.sms import send_sms
from app.analytics import total_kilos, daily_series, center_leaderboard, center_trend, compare_periods

manager_bp = Blueprint("manager", __name__, url_prefix="/manager")


def manager_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not isinstance(current_user, FactoryUser) or not current_user.is_manager:
            abort(403)
        return f(*args, **kwargs)
    return wrapped


def _factory_id():
    return current_user.factory_id


# ---------- Dashboard ----------

@manager_bp.route("/")
@login_required
@manager_required
def dashboard():
    fid = _factory_id()
    centers = BuyingCenter.query.filter_by(factory_id=fid).order_by(BuyingCenter.name).all()

    today_purchases = (
        Purchase.query.join(BuyingCenter)
        .filter(BuyingCenter.factory_id == fid, db.func.date(Purchase.purchased_at) == date.today())
        .order_by(Purchase.purchased_at.desc())
        .all()
    )
    today_total = sum(p.kilos for p in today_purchases)

    center_rows = []
    for c in centers:
        c_today = [p for p in today_purchases if p.buying_center_id == c.id]
        center_rows.append({
            "center": c,
            "is_buying_now": c.is_buying_now,
            "active_clerk": c.active_clerk,
            "today_kilos": sum(p.kilos for p in c_today),
            "today_count": len(c_today),
        })

    open_complaints = Complaint.query.join(Farmer).filter(Complaint.status == "open").count()
    farmer_count = Farmer.query.join(Farm).join(BuyingCenter).filter(BuyingCenter.factory_id == fid).distinct().count()

    return render_template(
        "manager/dashboard.html",
        center_rows=center_rows,
        today_total=today_total,
        today_purchase_count=len(today_purchases),
        recent_purchases=today_purchases[:15],
        open_complaints=open_complaints,
        farmer_count=farmer_count,
        centers=centers,
    )


# ---------- Insights (analytics) ----------

@manager_bp.route("/insights")
@login_required
@manager_required
def insights():
    fid = _factory_id()
    today = date.today()
    yesterday = today - timedelta(days=1)

    centers = BuyingCenter.query.filter_by(factory_id=fid).order_by(BuyingCenter.name).all()

    today_by_center = {row["center"].id: row["kilos"] for row in center_leaderboard(fid, today)}
    yesterday_by_center = {row["center"].id: row["kilos"] for row in center_leaderboard(fid, yesterday)}

    trend_rows = []
    for c in centers:
        direction, pct = center_trend(fid, c.id)
        trend_rows.append({
            "center": c,
            "today_kilos": today_by_center.get(c.id, 0),
            "yesterday_kilos": yesterday_by_center.get(c.id, 0),
            "direction": direction,
            "pct_change": pct,
        })
    trend_rows.sort(key=lambda r: r["today_kilos"], reverse=True)

    all_farmers = (
        Farmer.query.join(Farm).join(BuyingCenter).filter(BuyingCenter.factory_id == fid).distinct().all()
    )
    top_farmers = sorted(all_farmers, key=lambda f: f.total_purchased_kilos, reverse=True)[:15]

    return render_template(
        "manager/insights.html",
        centers=centers,
        trend_rows=trend_rows,
        top_farmers=top_farmers,
        today_total=total_kilos(fid, today, today),
        yesterday_total=total_kilos(fid, yesterday, yesterday),
        week_total=total_kilos(fid, today - timedelta(days=6), today),
        month_total=total_kilos(fid, today.replace(day=1), today),
    )


@manager_bp.route("/insights/series")
@login_required
@manager_required
def insights_series():
    fid = _factory_id()
    days = max(7, min(request.args.get("days", 30, type=int), 180))
    center_id = request.args.get("center_id", type=int) or None
    labels, values = daily_series(fid, days=days, center_id=center_id)
    return {"labels": labels, "values": values}


@manager_bp.route("/insights/compare")
@login_required
@manager_required
def insights_compare():
    fid = _factory_id()
    center_id = request.args.get("center_id", type=int) or None
    mode = request.args.get("mode", "days")
    try:
        return compare_periods(fid, mode, request.args.get("a", ""), request.args.get("b", ""), center_id)
    except (ValueError, IndexError, TypeError):
        return {"error": "Enter two valid dates to compare."}, 400


# ---------- Farmers ----------

@manager_bp.route("/farmers", methods=["GET"])
@login_required
@manager_required
def farmers():
    fid = _factory_id()
    q = request.args.get("q", "").strip()
    query = Farmer.query.join(Farm).join(BuyingCenter).filter(BuyingCenter.factory_id == fid)
    if q:
        query = query.filter(db.or_(Farmer.full_name.ilike(f"%{q}%"), Farmer.phone.ilike(f"%{q}%"), Farm.farm_number.ilike(f"%{q}%")))
    all_farmers = query.distinct().order_by(Farmer.created_at.desc()).all()
    centers = BuyingCenter.query.filter_by(factory_id=fid).order_by(BuyingCenter.name).all()
    return render_template("manager/farmers.html", farmers=all_farmers, centers=centers, q=q)


@manager_bp.route("/farmers/register", methods=["POST"])
@login_required
@manager_required
def register_farmer():
    fid = _factory_id()
    full_name = request.form.get("full_name", "").strip()
    phone = request.form.get("phone", "").strip()
    national_id = request.form.get("national_id", "").strip()
    scale = request.form.get("scale", "small")
    buying_center_id = request.form.get("buying_center_id", type=int)
    approx_bushes = request.form.get("approx_bushes", type=int) or 0
    location = request.form.get("location", "").strip()

    center = BuyingCenter.query.filter_by(id=buying_center_id, factory_id=fid).first()

    if not full_name or not phone or not center:
        flash("Full name, phone, and a valid buying center are required.", "error")
        return redirect(url_for("manager.farmers"))

    if Farmer.query.filter_by(phone=phone).first():
        flash("A farmer with that phone number already exists. Use 'Add another farm' instead.", "error")
        return redirect(url_for("manager.farmers"))

    farmer = Farmer(full_name=full_name, phone=phone, national_id=national_id or None, scale=scale)
    db.session.add(farmer)
    db.session.flush()

    farm_number = center.generate_farm_number()
    farm = Farm(
        farmer_id=farmer.id,
        buying_center_id=center.id,
        farm_number=farm_number,
        location=location,
        approx_bushes=approx_bushes,
    )
    db.session.add(farm)
    db.session.commit()

    send_sms(phone, f"Welcome to {center.name}. Your farm number is {farm_number}. "
                     f"Keep your farm card safe — this number identifies your tea at the buying center.")

    flash(f"Farmer registered. Farm number {farm_number} created.", "success")
    return redirect(url_for("manager.farmer_card", farm_id=farm.id))


@manager_bp.route("/farmers/<int:farmer_id>/add-farm", methods=["POST"])
@login_required
@manager_required
def add_farm_to_farmer(farmer_id):
    fid = _factory_id()
    farmer = Farmer.query.get_or_404(farmer_id)
    buying_center_id = request.form.get("buying_center_id", type=int)
    center = BuyingCenter.query.filter_by(id=buying_center_id, factory_id=fid).first()

    if not center:
        flash("Choose a valid buying center.", "error")
        return redirect(url_for("manager.farmers"))

    farm_number = center.generate_farm_number()
    farm = Farm(farmer_id=farmer.id, buying_center_id=center.id, farm_number=farm_number)
    db.session.add(farm)
    db.session.commit()

    flash(f"New farm {farm_number} added to {farmer.full_name}'s account.", "success")
    return redirect(url_for("manager.farmer_card", farm_id=farm.id))


@manager_bp.route("/farmers/card/<int:farm_id>")
@login_required
@manager_required
def farmer_card(farm_id):
    farm = Farm.query.get_or_404(farm_id)
    return render_template("manager/farmer_card.html", farm=farm)


# ---------- Buying centers ----------

@manager_bp.route("/centers", methods=["GET", "POST"])
@login_required
@manager_required
def centers():
    fid = _factory_id()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        code = request.form.get("code", "").strip()
        route_id = request.form.get("route_id", type=int)
        location_notes = request.form.get("location_notes", "").strip()

        if not name or not code:
            flash("Center name and code are required.", "error")
        elif BuyingCenter.query.filter_by(factory_id=fid, code=code).first():
            flash("A buying center with that code already exists.", "error")
        else:
            center = BuyingCenter(factory_id=fid, name=name, code=code, route_id=route_id or None, location_notes=location_notes)
            db.session.add(center)
            db.session.commit()
            flash(f"Buying center '{name}' added.", "success")
        return redirect(url_for("manager.centers"))

    all_centers = BuyingCenter.query.filter_by(factory_id=fid).order_by(BuyingCenter.name).all()
    all_routes = Route.query.filter_by(factory_id=fid).order_by(Route.name).all()
    return render_template("manager/centers.html", centers=all_centers, routes=all_routes)


# ---------- Routes & tallyboy assignment ----------

@manager_bp.route("/routes", methods=["GET", "POST"])
@login_required
@manager_required
def routes():
    fid = _factory_id()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Route name is required.", "error")
        else:
            db.session.add(Route(factory_id=fid, name=name))
            db.session.commit()
            flash(f"Route '{name}' created.", "success")
        return redirect(url_for("manager.routes"))

    all_routes = Route.query.filter_by(factory_id=fid).order_by(Route.name).all()
    clerks = FactoryUser.query.filter_by(factory_id=fid, role="clerk", is_active_staff=True).order_by(FactoryUser.full_name).all()
    return render_template("manager/routes.html", routes=all_routes, clerks=clerks)


@manager_bp.route("/routes/<int:route_id>/assign", methods=["POST"])
@login_required
@manager_required
def assign_route(route_id):
    fid = _factory_id()
    route = Route.query.filter_by(id=route_id, factory_id=fid).first_or_404()
    clerk_id = request.form.get("clerk_id", type=int)
    clerk = FactoryUser.query.filter_by(id=clerk_id, factory_id=fid, role="clerk").first()

    if not clerk:
        flash("Choose a valid tallyboy to assign.", "error")
        return redirect(url_for("manager.routes"))

    # End any current assignment for this route
    current = route.current_assignment
    if current:
        current.end_date = date.today()

    db.session.add(RouteAssignment(route_id=route.id, clerk_id=clerk.id))
    db.session.commit()

    notice = Notice(
        factory_id=fid,
        posted_by_id=current_user.id,
        category="route_change",
        title=f"New tallyboy on {route.name}",
        content=f"{clerk.full_name} is now the tallyboy for {route.name}, effective {date.today().strftime('%d %b %Y')}.",
    )
    db.session.add(notice)
    db.session.commit()

    flash(f"{clerk.full_name} assigned to {route.name}. Farmers on this route have been notified.", "success")
    return redirect(url_for("manager.routes"))


# ---------- Staff ----------

@manager_bp.route("/staff", methods=["GET", "POST"])
@login_required
@manager_required
def staff():
    fid = _factory_id()
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        role = request.form.get("role", "clerk")
        password = request.form.get("password", "")
        pin = request.form.get("pin", "").strip()

        if not full_name or not username:
            flash("Name and username are required.", "error")
            return redirect(url_for("manager.staff"))

        if FactoryUser.query.filter_by(username=username).first():
            flash("That username is already taken.", "error")
            return redirect(url_for("manager.staff"))

        if role not in ("clerk", "receiver", "manager"):
            flash("Invalid role.", "error")
            return redirect(url_for("manager.staff"))

        if role == "manager" and (not password or len(password) < 6):
            flash("Managers need a password of at least 6 characters.", "error")
            return redirect(url_for("manager.staff"))

        if role in ("clerk", "receiver") and (not pin or not pin.isdigit() or not (4 <= len(pin) <= 6)):
            flash("Clerks and reception staff need a 4-6 digit PIN.", "error")
            return redirect(url_for("manager.staff"))

        new_staff = FactoryUser(factory_id=fid, full_name=full_name, username=username, phone=phone, role=role)
        # Clerks and reception staff log in with a PIN, not a password — a
        # password is still stored on the account (the model requires one),
        # so generate a random one behind the scenes when not set explicitly.
        new_staff.set_password(password or secrets.token_hex(16))
        if role in ("clerk", "receiver"):
            new_staff.set_pin(pin)
        db.session.add(new_staff)
        db.session.commit()
        flash(f"{role.title()} account created for {full_name}.", "success")
        return redirect(url_for("manager.staff"))

    all_staff = FactoryUser.query.filter_by(factory_id=fid).order_by(FactoryUser.role, FactoryUser.full_name).all()
    return render_template("manager/staff.html", staff=all_staff)


@manager_bp.route("/staff/<int:staff_id>/toggle", methods=["POST"])
@login_required
@manager_required
def toggle_staff(staff_id):
    staff_member = FactoryUser.query.filter_by(id=staff_id, factory_id=_factory_id()).first_or_404()
    staff_member.is_active_staff = not staff_member.is_active_staff
    db.session.commit()
    return redirect(url_for("manager.staff"))


@manager_bp.route("/staff/<int:staff_id>/reset-pin", methods=["POST"])
@login_required
@manager_required
def reset_pin(staff_id):
    staff_member = FactoryUser.query.filter_by(id=staff_id, factory_id=_factory_id()).first_or_404()
    if staff_member.role not in ("clerk", "receiver"):
        abort(403)
    pin = request.form.get("pin", "").strip()

    if not pin or not pin.isdigit() or not (4 <= len(pin) <= 6):
        flash("Enter a 4-6 digit PIN.", "error")
        return redirect(url_for("manager.staff"))

    staff_member.set_pin(pin)
    db.session.commit()
    flash(f"PIN updated for {staff_member.full_name}.", "success")
    return redirect(url_for("manager.staff"))


# ---------- Notices ----------

@manager_bp.route("/notices", methods=["GET", "POST"])
@login_required
@manager_required
def notices():
    fid = _factory_id()
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        category = request.form.get("category", "general")
        buying_center_id = request.form.get("buying_center_id", type=int)

        if not title or not content:
            flash("Title and content are required.", "error")
        else:
            db.session.add(Notice(
                factory_id=fid, posted_by_id=current_user.id, category=category,
                title=title, content=content, buying_center_id=buying_center_id or None,
            ))
            db.session.commit()
            flash("Notice posted.", "success")
        return redirect(url_for("manager.notices"))

    all_notices = Notice.query.filter_by(factory_id=fid).order_by(Notice.created_at.desc()).all()
    all_centers = BuyingCenter.query.filter_by(factory_id=fid).order_by(BuyingCenter.name).all()
    return render_template("manager/notices.html", notices=all_notices, centers=all_centers)


# ---------- Fertilizer ----------

@manager_bp.route("/fertilizer", methods=["GET", "POST"])
@login_required
@manager_required
def fertilizer():
    fid = _factory_id()
    if request.method == "POST":
        buying_center_id = request.form.get("buying_center_id", type=int)
        farmer_id = request.form.get("farmer_id", type=int) or None
        fertilizer_type = request.form.get("fertilizer_type", "").strip()
        quantity_kg = request.form.get("quantity_kg", type=float)
        notes = request.form.get("notes", "").strip()

        center = BuyingCenter.query.filter_by(id=buying_center_id, factory_id=fid).first()
        if not center or not fertilizer_type:
            flash("Buying center and fertilizer type are required.", "error")
        else:
            db.session.add(FertilizerDistribution(
                buying_center_id=center.id, farmer_id=farmer_id, recorded_by_id=current_user.id,
                fertilizer_type=fertilizer_type, quantity_kg=quantity_kg, notes=notes,
            ))
            db.session.commit()
            flash("Fertilizer distribution recorded.", "success")
        return redirect(url_for("manager.fertilizer"))

    all_centers = BuyingCenter.query.filter_by(factory_id=fid).order_by(BuyingCenter.name).all()
    recent = (
        FertilizerDistribution.query.join(BuyingCenter)
        .filter(BuyingCenter.factory_id == fid)
        .order_by(FertilizerDistribution.date.desc())
        .limit(40)
        .all()
    )
    return render_template("manager/fertilizer.html", centers=all_centers, records=recent)


# ---------- Complaints ----------

@manager_bp.route("/complaints", methods=["GET"])
@login_required
@manager_required
def complaints():
    fid = _factory_id()
    status_filter = request.args.get("status", "open")
    query = (
        Complaint.query.join(Farmer)
        .join(Farm, Farm.farmer_id == Farmer.id)
        .join(BuyingCenter, BuyingCenter.id == Farm.buying_center_id)
        .filter(BuyingCenter.factory_id == fid)
        .distinct()
    )
    if status_filter in ("open", "resolved"):
        query = query.filter(Complaint.status == status_filter)
    all_complaints = query.order_by(Complaint.created_at.desc()).all()
    return render_template("manager/complaints.html", complaints=all_complaints, status_filter=status_filter)


@manager_bp.route("/complaints/<int:complaint_id>/resolve", methods=["POST"])
@login_required
@manager_required
def resolve_complaint(complaint_id):
    complaint = Complaint.query.get_or_404(complaint_id)
    complaint.status = "resolved"
    complaint.manager_note = request.form.get("manager_note", "").strip()
    complaint.resolved_at = datetime.utcnow()
    db.session.commit()
    flash("Complaint marked resolved.", "success")
    return redirect(url_for("manager.complaints"))
