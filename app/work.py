"""'My Work' — where everyone lands after logging in: what needs doing now, and what's been announced."""
from datetime import timedelta

from flask import Blueprint, render_template, url_for
from flask_login import current_user, login_required

from app import db
from app.analytics import active_centres, total_kilos
from app.models import Complaint, FarmVerification, QualityRecord, TeaTransaction
from app.permissions import has_permission
from app.services import visible_notices
from app.timeutil import today_utc, utcnow

work_bp = Blueprint("work", __name__, url_prefix="/work")


def _tasks(user):
    """The things this person is responsible for right now, based on what they're allowed to do."""
    tasks = []
    today = today_utc()

    if has_permission(user, "VERIFY_FARM"):
        pending = FarmVerification.query.filter_by(status="PENDING").count()
        tasks.append({"title": "Farms waiting for verification", "count": pending,
                      "detail": "Visit the farm, confirm the details, and issue a farm number.",
                      "url": url_for("field.queue")})

    if has_permission(user, "RECORD_TRANSACTION"):
        start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        mine = (db.session.query(db.func.coalesce(db.func.sum(TeaTransaction.weight_kg), 0.0))
                .filter(TeaTransaction.clerk_employee_id == user.employee_id, TeaTransaction.status == "VALID",
                        TeaTransaction.transaction_time >= start,
                        TeaTransaction.transaction_time < start + timedelta(days=1)).scalar())
        tasks.append({"title": "Buy tea", "count": None,
                      "detail": f"You have recorded {float(mine or 0):,.1f} kg today.",
                      "url": url_for("buying.home")})

    if has_permission(user, "RECORD_QUALITY"):
        graded = QualityRecord.query.filter_by(date=today).count()
        left = max(len(active_centres()) - graded, 0)
        tasks.append({"title": "Buying centres not graded today", "count": left,
                      "detail": "Record the quality of each centre's tea as it arrives.",
                      "url": url_for("receiver.home")})

    if has_permission(user, "HANDLE_COMPLAINT"):
        tasks.append({"title": "Open complaints", "count": Complaint.query.filter_by(status="open").count(),
                      "detail": "Farmers are waiting for a response.", "url": url_for("complaints.index")})

    if has_permission(user, "GENERATE_REPORTS"):
        tasks.append({"title": "Kilos bought today", "count": None,
                      "detail": f"{total_kilos(today, today):,.1f} kg across all buying centres.",
                      "url": url_for("management.dashboard")})
    return tasks


@work_bp.route("/")
@login_required   # everyone who is logged in
def home():
    return render_template("work/home.html", tasks=_tasks(current_user), notices=visible_notices(current_user, limit=10))
