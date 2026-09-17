from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from app import db
from app.factory_models import FactoryUser, ClerkSession

factory_auth_bp = Blueprint("factory_auth", __name__, url_prefix="/factory")


def _redirect_home(staff):
    return redirect(url_for("manager.dashboard" if staff.is_manager else "clerk.home"))


@factory_auth_bp.route("/login")
def login():
    """Role chooser — sends the person to the right login form."""
    if current_user.is_authenticated:
        return _redirect_home(current_user)
    return render_template("factory/login.html")


@factory_auth_bp.route("/manager/login", methods=["GET", "POST"])
def manager_login():
    if current_user.is_authenticated:
        return _redirect_home(current_user)

    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")

        staff = FactoryUser.query.filter_by(username=username).first()

        if not staff or not staff.check_password(password):
            flash("Invalid username or password.", "error")
            return render_template("factory/manager_login.html")

        if not staff.is_manager:
            flash("That account isn't a manager account. Clerks log in from the clerk login page.", "error")
            return render_template("factory/manager_login.html")

        if not staff.is_active_staff:
            flash("This account has been deactivated.", "error")
            return render_template("factory/manager_login.html")

        login_user(staff)
        return redirect(url_for("manager.dashboard"))

    return render_template("factory/manager_login.html")


@factory_auth_bp.route("/clerk/login", methods=["GET", "POST"])
def clerk_login():
    if current_user.is_authenticated:
        return _redirect_home(current_user)

    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        pin = request.form.get("pin", "").strip()

        staff = FactoryUser.query.filter_by(username=username).first()

        if not staff or not staff.check_pin(pin):
            flash("Invalid username or PIN.", "error")
            return render_template("factory/clerk_login.html")

        if not staff.is_clerk:
            flash("That account isn't a clerk account. Managers log in from the manager login page.", "error")
            return render_template("factory/clerk_login.html")

        if not staff.is_active_staff:
            flash("This account has been deactivated. Contact your manager.", "error")
            return render_template("factory/clerk_login.html")

        login_user(staff)
        return redirect(url_for("clerk.home"))

    return render_template("factory/clerk_login.html")


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
