"""Farmer Relations: complaints farmers have filed (through the farmer app), and resolving them."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app import db
from app.audit import log_action
from app.models import Complaint
from app.permissions import permission_required
from app.timeutil import utcnow

complaints_bp = Blueprint("complaints", __name__, url_prefix="/complaints")


@complaints_bp.route("/")
@permission_required("HANDLE_COMPLAINT")
def index():
    status = request.args.get("status", "open")
    query = Complaint.query
    if status in ("open", "resolved"):
        query = query.filter(Complaint.status == status)
    return render_template("complaints/index.html", complaints=query.order_by(Complaint.created_at.desc()).all(),
                           status_filter=status)


@complaints_bp.route("/<int:complaint_id>/resolve", methods=["POST"])
@permission_required("HANDLE_COMPLAINT")
def resolve(complaint_id):
    complaint = db.get_or_404(Complaint, complaint_id)
    if complaint.status == "resolved":
        flash("That complaint is already resolved.", "info")
        return redirect(url_for("complaints.index"))
    complaint.status = "resolved"
    complaint.resolution_note = request.form.get("resolution_note", "").strip() or None
    complaint.resolved_at = utcnow()
    complaint.resolved_by_id = current_user.employee_id
    log_action("COMPLAINT_RESOLVED", "complaint", complaint.id, old={"status": "open"},
               new={"status": "resolved", "note": complaint.resolution_note})
    db.session.commit()
    flash("Complaint marked resolved.", "success")
    return redirect(url_for("complaints.index"))
