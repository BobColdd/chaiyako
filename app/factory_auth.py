from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from app import db
from app.factory_models import FactoryUser, ClerkSession

factory_auth_bp = Blueprint("factory_auth", __name__, url_prefix="/factory")


@factory_auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated and isinstance(current_user, FactoryUser):
        return redirect(url_for("manager.dashboard" if current_user.is_manager else "clerk.home"))

    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")

        staff = FactoryUser.query.filter_by(username=username).first()

        if not staff or not staff.check_password(password):
            flash("Invalid username or password.", "error")
            return render_template("factory/login.html")

        if not staff.is_active_staff:
            flash("This staff account has been deactivated. Contact your manager.", "error")
            return render_template("factory/login.html")

        login_user(staff)

        if staff.is_manager:
            return redirect(url_for("manager.dashboard"))
        return redirect(url_for("clerk.home"))

    return render_template("factory/login.html")


@factory_auth_bp.route("/logout")
@login_required
def logout():
    if isinstance(current_user, FactoryUser) and current_user.is_clerk:
        # Closing the session turns off that buying center's live "green mark".
        open_session = ClerkSession.query.filter_by(clerk_id=current_user.id, logout_at=None).first()
        if open_session:
            open_session.logout_at = datetime.utcnow()
            db.session.commit()

    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("factory_auth.login"))
