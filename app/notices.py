"""Notices — one board, three audiences: your department, all staff, and the public."""
from datetime import datetime, time

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db, rules
from app.analytics import active_centres
from app.models import Department, Notice
from app.permissions import has_permission
from app.services import ServiceError, all_live_notices, create_notice, deactivate_notice, visible_notices

notices_bp = Blueprint("notices", __name__, url_prefix="/notices")


@notices_bp.route("/")
@login_required
def board():
    can_post = has_permission(current_user, "POST_NOTICE")
    can_publish = has_permission(current_user, "PUBLISH_NOTICE")
    show_all = can_publish and request.args.get("all") == "1"

    if can_publish:
        departments = Department.query.order_by(Department.name).all()
    elif can_post:
        departments = [current_user.employee.department]
    else:
        departments = []

    return render_template(
        "notices/board.html",
        notices=all_live_notices() if show_all else visible_notices(current_user),
        show_all=show_all, can_post=can_post, can_publish=can_publish,
        departments=departments, centres=active_centres(), categories=rules.NOTICE_CATEGORIES,
    )


@notices_bp.route("/", methods=["POST"])
@login_required
def create():
    form = request.form
    expires_at = None
    if form.get("expires_on"):
        try:
            # Notices end at the close of that day in Kenya (24:00 EAT is 21:00 UTC).
            expires_at = datetime.combine(datetime.strptime(form["expires_on"], "%Y-%m-%d").date(), time(21, 0))
        except ValueError:
            flash("That expiry date isn't valid.", "error")
            return redirect(url_for("notices.board"))
    try:
        create_notice(
            current_user, audience=form.get("audience", ""), category=form.get("category", ""),
            title=form.get("title"), content=form.get("content"),
            department_id=form.get("department_id", type=int), buying_centre_id=form.get("buying_centre_id", type=int),
            expires_at=expires_at)
        db.session.commit()
        flash("Notice posted.", "success")
    except ServiceError as problem:
        db.session.rollback()
        flash(str(problem), "error")
    return redirect(url_for("notices.board"))


@notices_bp.route("/<int:notice_id>/remove", methods=["POST"])
@login_required
def remove(notice_id):
    notice = db.get_or_404(Notice, notice_id)
    try:
        deactivate_notice(current_user, notice)
        db.session.commit()
        flash("Notice taken down.", "success")
    except ServiceError as problem:
        db.session.rollback()
        flash(str(problem), "error")
    return redirect(url_for("notices.board"))
