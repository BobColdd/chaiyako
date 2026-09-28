"""Inputs and Fertilizer.

Two pages:
  Info          farmers and what they have taken (0 for those who have not), and the distribution records,
                filterable by buying centre and date, with print and PDF.
  Distribution  type a farm number, the farmer appears, record what they collect, see what they took before.
"""
from datetime import datetime

from flask import Blueprint, Response, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload, selectinload

from app import db, rules
from app.analytics import active_centres
from app.audit import log_action
from app.models import BuyingCentre, Farm, Farmer, FertilizerDistribution
from app.permissions import permission_required
from app.reports import build_fertilizer_report_pdf
from app.timeutil import today_utc

inputs_bp = Blueprint("inputs", __name__, url_prefix="/inputs")

FARMER_ROW_LIMIT = 300
RECORD_SCREEN_LIMIT = 300
RECORD_EXPORT_LIMIT = 2000


def _parse_day(text):
    try:
        return datetime.strptime(text or "", "%Y-%m-%d").date()
    except ValueError:
        return None


def _filters(args):
    """The Info page filters, read from the query string. Used by the screen, the print view and the PDF
    so an export always matches what was on screen."""
    start, end = _parse_day(args.get("from")), _parse_day(args.get("to"))
    if start and end and end < start:
        start, end = end, start
    show = args.get("show", "all")
    return {"start": start, "end": end, "centre_id": args.get("centre", type=int),
            "q": args.get("q", "").strip(), "show": show if show in ("all", "taken", "none") else "all"}


def _filter_distributions(query, f):
    """Date and buying-centre filters (the centre is where the fertilizer was handed out)."""
    if f["start"]:
        query = query.filter(FertilizerDistribution.date >= f["start"])
    if f["end"]:
        query = query.filter(FertilizerDistribution.date <= f["end"])
    if f["centre_id"]:
        query = query.filter(FertilizerDistribution.buying_centre_id == f["centre_id"])
    return query


def _records_query(f):
    query = FertilizerDistribution.query.outerjoin(Farmer, FertilizerDistribution.farmer_id == Farmer.id)
    query = _filter_distributions(query, f)
    for term in rules.split_terms(f["q"]):
        like = f"%{term}%"
        query = query.filter(or_(
            Farmer.first_name.ilike(like), Farmer.last_name.ilike(like), Farmer.farmer_number.ilike(like),
            FertilizerDistribution.fertilizer_type.ilike(like),
            Farmer.farms.any(Farm.farm_number.ilike(like))))
    return query


def _records_for_display(f, limit):
    return (_records_query(f)
            .options(joinedload(FertilizerDistribution.farmer), joinedload(FertilizerDistribution.buying_centre),
                     joinedload(FertilizerDistribution.recorded_by))
            .order_by(FertilizerDistribution.date.desc(), FertilizerDistribution.id.desc())
            .limit(limit).all())


def _records_total_kg(f):
    return float(_records_query(f).order_by(None)
                 .with_entities(func.coalesce(func.sum(FertilizerDistribution.quantity_kg), 0.0)).scalar() or 0)


def _taken_by_farmer(f=None):
    """{farmer_id: {times, kg, last}} for farmers given fertilizer, within the date/centre filters."""
    query = (db.session.query(FertilizerDistribution.farmer_id,
                              func.count(FertilizerDistribution.id),
                              func.coalesce(func.sum(FertilizerDistribution.quantity_kg), 0.0),
                              func.max(FertilizerDistribution.date))
             .filter(FertilizerDistribution.farmer_id.isnot(None)))
    if f:
        query = _filter_distributions(query, f)
    rows = query.group_by(FertilizerDistribution.farmer_id).all()
    return {farmer_id: {"times": times, "kg": float(kg or 0), "last": last}
            for farmer_id, times, kg, last in rows}


def _verified_farms(farmer):
    return [f for f in farmer.farms if f.verification_status == "VERIFIED" and f.farm_number]


def _farm_numbers_for(records):
    """{farmer_id: 'CY..., CY...'} for the farmers in these records."""
    ids = {r.farmer_id for r in records if r.farmer_id}
    if not ids:
        return {}
    farmers = Farmer.query.options(selectinload(Farmer.farms)).filter(Farmer.id.in_(ids)).all()
    return {fm.id: ", ".join(x.farm_number for x in _verified_farms(fm)) for fm in farmers}


def _describe(f):
    """The filters in words, for the top of a printout."""
    parts = []
    if f["start"] or f["end"]:
        parts.append(f"{f['start'].strftime('%d %b %Y') if f['start'] else 'Start'} – "
                     f"{f['end'].strftime('%d %b %Y') if f['end'] else 'today'}")
    else:
        parts.append("All dates")
    centre = db.session.get(BuyingCentre, f["centre_id"]) if f["centre_id"] else None
    parts.append(centre.name if centre else "All buying centres")
    if f["q"]:
        parts.append(f'Search: "{f["q"]}"')
    return " · ".join(parts)


# ---------------------------------------------------------------- Info

@inputs_bp.route("/")
@permission_required("PROCESS_FERTILIZER")
def info():
    f = _filters(request.args)
    taken = _taken_by_farmer(f)

    took_ids = _filter_distributions(
        db.session.query(FertilizerDistribution.farmer_id).filter(FertilizerDistribution.farmer_id.isnot(None)), f)
    scope = Farmer.query.filter(Farmer.status == "ACTIVE")
    if f["centre_id"]:
        scope = scope.filter(Farmer.buying_centre_id == f["centre_id"])

    query = scope.options(selectinload(Farmer.farms), selectinload(Farmer.buying_centre))
    for term in rules.split_terms(f["q"]):
        like = f"%{term}%"
        query = query.filter(or_(
            Farmer.first_name.ilike(like), Farmer.last_name.ilike(like), Farmer.phone.ilike(like),
            Farmer.farmer_number.ilike(like), Farmer.farms.any(Farm.farm_number.ilike(like))))
    if f["show"] == "taken":
        query = query.filter(Farmer.id.in_(took_ids))
    elif f["show"] == "none":
        query = query.filter(~Farmer.id.in_(took_ids))

    matched = query.count()
    rows = []
    for farmer in query.order_by(Farmer.farmer_number).limit(FARMER_ROW_LIMIT).all():
        farms = _verified_farms(farmer)
        record = taken.get(farmer.id, {"times": 0, "kg": 0.0, "last": None})
        rows.append({"farmer": farmer, "farm_numbers": ", ".join(x.farm_number for x in farms),
                     "bushes": sum(x.tea_bushes or 0 for x in farms), **record})

    total_farmers = scope.count()
    served = scope.filter(Farmer.id.in_(took_ids)).count()
    total_kg = _records_total_kg(f)
    records = _records_for_display(f, RECORD_SCREEN_LIMIT)
    record_count = _records_query(f).order_by(None).count()

    return render_template(
        "inputs/info.html", rows=rows, matched=matched, farmer_limit=FARMER_ROW_LIMIT,
        stats={"farmers": total_farmers, "served": served, "not_served": total_farmers - served, "kg": total_kg},
        records=records, record_count=record_count, record_limit=RECORD_SCREEN_LIMIT,
        farm_numbers=_farm_numbers_for(records), centres=active_centres(),
        q=f["q"], show=f["show"], centre_id=f["centre_id"],
        start=f["start"].isoformat() if f["start"] else "", end=f["end"].isoformat() if f["end"] else "")


@inputs_bp.route("/print")
@permission_required("PROCESS_FERTILIZER")
def print_view():
    f = _filters(request.args)
    records = _records_for_display(f, RECORD_EXPORT_LIMIT)
    return render_template("inputs/print.html", records=records, farm_numbers=_farm_numbers_for(records),
                           total_kg=_records_total_kg(f), description=_describe(f),
                           capped=_records_query(f).order_by(None).count() > RECORD_EXPORT_LIMIT,
                           limit=RECORD_EXPORT_LIMIT)


@inputs_bp.route("/pdf")
@permission_required("PROCESS_FERTILIZER")
def records_pdf():
    f = _filters(request.args)
    records = _records_for_display(f, RECORD_EXPORT_LIMIT)
    pdf_bytes = build_fertilizer_report_pdf(
        current_app.config["FACTORY_NAME"], current_user, _describe(f), records, _farm_numbers_for(records),
        _records_total_kg(f))
    filename = f"fertilizer-distribution-{today_utc().isoformat()}.pdf"
    return Response(pdf_bytes, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ---------------------------------------------------------------- Distribution

@inputs_bp.route("/distribution", methods=["GET", "POST"])
@permission_required("PROCESS_FERTILIZER")
def distribution():
    if request.method == "POST":
        farm_number = request.form.get("farm_number", "").strip().upper()
        farm = Farm.query.filter_by(farm_number=farm_number).first() if farm_number else None
        centre = db.session.get(BuyingCentre, request.form.get("buying_centre_id", type=int) or 0)
        fertilizer_type = request.form.get("fertilizer_type", "").strip()
        quantity = request.form.get("quantity_kg", type=float)

        if farm is None:
            flash("Enter a farm number and pick the farmer from the list.", "error")
        elif farm.verification_status != "VERIFIED" or farm.farmer.status != "ACTIVE":
            flash("Fertilizer can only be given against a verified farm of an active farmer.", "error")
        elif centre is None or not fertilizer_type:
            flash("The buying centre and the fertilizer type are required.", "error")
        elif quantity is None or quantity <= 0:
            flash("Enter the quantity in kg. It must be more than zero.", "error")
        else:
            farmer = farm.farmer
            record = FertilizerDistribution(
                buying_centre_id=centre.id, farmer_id=farmer.id, recorded_by_id=current_user.employee_id,
                fertilizer_type=fertilizer_type[:80], quantity_kg=quantity,
                notes=request.form.get("notes", "").strip()[:300] or None)
            db.session.add(record)
            db.session.flush()
            log_action("FERTILIZER_RECORDED", "fertilizer_distribution", record.id,
                       new={"centre": centre.code, "farmer": farmer.farmer_number, "farm": farm.farm_number,
                            "type": fertilizer_type, "quantity_kg": quantity})
            db.session.commit()
            flash(f"Recorded {quantity:g} kg of {fertilizer_type} for {farmer.full_name}.", "success")
        return redirect(url_for("inputs.distribution", farm=farm.farm_number if farm else None))

    types = [t for (t,) in db.session.query(FertilizerDistribution.fertilizer_type).distinct()
             .order_by(FertilizerDistribution.fertilizer_type).all()]
    return render_template("inputs/distribution.html", centres=active_centres(), types=types,
                           preselect=request.args.get("farm", "").strip().upper())


@inputs_bp.route("/distribution/farms")
@permission_required("PROCESS_FERTILIZER")
def farms_list():
    """Every verified farm of an active farmer: the list the farm-number box filters as you type."""
    taken = _taken_by_farmer()
    farms = (Farm.query.join(Farmer, Farm.farmer_id == Farmer.id)
             .options(joinedload(Farm.farmer).joinedload(Farmer.buying_centre))
             .filter(Farm.verification_status == "VERIFIED", Farm.farm_number.isnot(None),
                     Farmer.status == "ACTIVE")
             .order_by(Farm.farm_number).all())
    return jsonify(ok=True, farms=[{
        "farm_number": f.farm_number, "farmer_id": f.farmer_id, "farmer_number": f.farmer.farmer_number,
        "name": f.farmer.full_name, "centre_id": f.farmer.buying_centre_id,
        "centre": f.farmer.buying_centre.name, "bushes": f.tea_bushes or 0,
        "kg": taken.get(f.farmer_id, {}).get("kg", 0.0), "times": taken.get(f.farmer_id, {}).get("times", 0),
    } for f in farms])


@inputs_bp.route("/distribution/history/<int:farmer_id>")
@permission_required("PROCESS_FERTILIZER")
def history(farmer_id):
    """Everything this farmer has been given, newest first."""
    farmer = db.get_or_404(Farmer, farmer_id)
    records = (FertilizerDistribution.query.filter_by(farmer_id=farmer.id)
               .options(joinedload(FertilizerDistribution.buying_centre),
                        joinedload(FertilizerDistribution.recorded_by))
               .order_by(FertilizerDistribution.date.desc(), FertilizerDistribution.id.desc()).all())
    return jsonify(ok=True, total_kg=sum(r.quantity_kg or 0 for r in records), records=[{
        "date": r.date.strftime("%d %b %Y"), "type": r.fertilizer_type, "kg": r.quantity_kg,
        "centre": r.buying_centre.name, "by": r.recorded_by.full_name if r.recorded_by else "",
        "notes": r.notes or "",
    } for r in records])
