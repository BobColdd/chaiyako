"""Escalations: a case handed to another department's officer, and the desk that deals with it."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app import db
from app.models import Escalation
from app.permissions import has_permission, permission_required
from app.services import (
    ServiceError, escalations_for_desk, escalations_raised_by, mark_outcomes_seen,
    raise_escalation, resolve_escalation, take_escalation,
)

escalations_bp = Blueprint("escalations", __name__, url_prefix="/escalations")

_FILTERS = ("active", "open", "taken", "resolved", "all")


def _safe_next(target):
    """Send people back to the page they escalated from, but only to a page on this site."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("escalations.index")


@escalations_bp.route("/")
@permission_required("RAISE_ESCALATION", "HANDLE_ESCALATION")
def index():
    can_handle = has_permission(current_user, "HANDLE_ESCALATION")
    can_raise = has_permission(current_user, "RAISE_ESCALATION")
    status = request.args.get("status", "active")
    if status not in _FILTERS:
        status = "active"

    desk = escalations_for_desk(current_user, None if status == "all" else status) if can_handle else []
    mine = escalations_raised_by(current_user)
    # Remember which outcomes are new BEFORE marking them read, so they can be tagged this once.
    new_outcomes = {e.id for e in mine if e.status == "resolved" and not e.outcome_seen}
    mark_outcomes_seen(current_user)
    db.session.commit()

    return render_template("escalations/index.html", desk=desk, mine=mine, new_outcomes=new_outcomes,
                           can_handle=can_handle, can_raise=can_raise, status_filter=status, filters=_FILTERS)


@escalations_bp.route("/", methods=["POST"])
@permission_required("RAISE_ESCALATION")
def create():
    form = request.form
    try:
        escalation = raise_escalation(
            current_user, department_id=form.get("department_id", type=int),
            description=form.get("description"), farmer_id=form.get("farmer_id", type=int))
        db.session.commit()
        flash(f"Escalated to the {escalation.to_department.name} desk.", "success")
    except ServiceError as problem:
        db.session.rollback()
        flash(str(problem), "error")
    return redirect(_safe_next(form.get("next")))


@escalations_bp.route("/<int:escalation_id>/take", methods=["POST"])
@permission_required("HANDLE_ESCALATION")
def take(escalation_id):
    escalation = db.get_or_404(Escalation, escalation_id)
    try:
        take_escalation(current_user, escalation)
        db.session.commit()
        flash("You are handling this case.", "success")
    except ServiceError as problem:
        db.session.rollback()
        flash(str(problem), "error")
    return redirect(url_for("escalations.index"))


@escalations_bp.route("/<int:escalation_id>/resolve", methods=["POST"])
@permission_required("HANDLE_ESCALATION")
def resolve(escalation_id):
    escalation = db.get_or_404(Escalation, escalation_id)
    try:
        resolve_escalation(current_user, escalation, request.form.get("resolution_note"))
        db.session.commit()
        flash("Case resolved. The person who raised it will see your note.", "success")
    except ServiceError as problem:
        db.session.rollback()
        flash(str(problem), "error")
    return redirect(url_for("escalations.index"))
