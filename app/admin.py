"""IT: employee accounts, access and roles."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app import db
from app.models import Department, Employee, Role, User
from app.permissions import permission_required
from app.services import (
    ServiceError, create_staff, reset_password, set_employee_roles, set_user_pin, set_user_status,
)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _done(action, success_message):
    """Run one staff action, commit, and flash the outcome."""
    try:
        action()
        db.session.commit()
        flash(success_message, "success")
    except ServiceError as problem:
        db.session.rollback()
        flash(str(problem), "error")
    return redirect(url_for("admin.staff"))


@admin_bp.route("/staff")
@permission_required("VIEW_STAFF")
def staff():
    users = (User.query
             .join(Employee, Employee.id == User.employee_id)
             .join(Department, Department.id == Employee.department_id)
             .order_by(Department.name, Employee.last_name, Employee.first_name).all())
    return render_template("admin/staff.html", users=users,
                           departments=Department.query.order_by(Department.name).all(),
                           roles=Role.query.order_by(Role.name).all())


@admin_bp.route("/staff/create", methods=["POST"])
@permission_required("CREATE_USER")
def create():
    form = request.form
    return _done(
        lambda: create_staff(
            current_user, first_name=form.get("first_name"), last_name=form.get("last_name"),
            username=form.get("username"), password=form.get("password"),
            department_id=form.get("department_id", type=int), role_ids=form.getlist("role_ids", type=int),
            phone=form.get("phone"), email=form.get("email"), pin=form.get("pin")),
        "Account created.")


@admin_bp.route("/staff/<int:user_id>/status", methods=["POST"])
@permission_required("CREATE_USER")
def user_status(user_id):
    user = db.get_or_404(User, user_id)
    active = request.form.get("active") == "1"
    return _done(lambda: set_user_status(current_user, user, active),
                 "Account reactivated." if active else "Account deactivated.")


@admin_bp.route("/staff/<int:user_id>/password", methods=["POST"])
@permission_required("CREATE_USER")
def user_password(user_id):
    user = db.get_or_404(User, user_id)
    return _done(lambda: reset_password(current_user, user, request.form.get("password")), "Password updated.")


@admin_bp.route("/staff/<int:user_id>/pin", methods=["POST"])
@permission_required("CREATE_USER")
def user_pin(user_id):
    user = db.get_or_404(User, user_id)
    return _done(lambda: set_user_pin(current_user, user, request.form.get("pin")), "PIN updated.")


@admin_bp.route("/staff/<int:user_id>/roles", methods=["POST"])
@permission_required("MANAGE_PERMISSIONS")
def user_roles(user_id):
    user = db.get_or_404(User, user_id)
    return _done(lambda: set_employee_roles(current_user, user.employee, request.form.getlist("role_ids", type=int)),
                 "Roles updated.")
