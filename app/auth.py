from datetime import datetime, timedelta
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from app import db
from app.models import Farmer

auth_bp = Blueprint("auth", __name__)

CODE_VALID_MINUTES = 60 * 24  # a verification code is valid for 24 hours


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Farmer login. Farmer accounts are created by the factory (see the
    manager side) — there is no self-signup. A brand-new farmer has no
    password yet, so logging in with just their phone number routes them to
    the one-time verification step instead."""
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")

        farmer = Farmer.query.filter_by(phone=phone).first()

        if not farmer:
            flash("No farmer account found with that phone number. Ask your factory to register you.", "error")
            return render_template("login.html")

        if farmer.must_set_password or not farmer.password_hash:
            flash("You need to set up your password first using the code sent to your phone.", "info")
            return redirect(url_for("auth.verify", phone=phone))

        if farmer.check_password(password):
            login_user(farmer)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("main.dashboard"))

        flash("Incorrect password.", "error")

    return render_template("login.html")


@auth_bp.route("/verify", methods=["GET", "POST"])
def verify():
    """First-login flow: farmer enters the code the factory sent (currently
    via the SMS stub in app/sms.py) and sets their own password."""
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    prefill_phone = request.args.get("phone", "")

    if request.method == "POST":
        phone = request.form.get("phone", "").strip()
        code = request.form.get("code", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        farmer = Farmer.query.filter_by(phone=phone).first()

        if not farmer:
            flash("No farmer account found with that phone number.", "error")
            return render_template("verify.html", phone=phone)

        if not farmer.verification_code or farmer.verification_code != code:
            flash("Incorrect verification code.", "error")
            return render_template("verify.html", phone=phone)

        if farmer.code_generated_at and datetime.utcnow() - farmer.code_generated_at > timedelta(minutes=CODE_VALID_MINUTES):
            flash("That code has expired. Ask your factory to resend a new one.", "error")
            return render_template("verify.html", phone=phone)

        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return render_template("verify.html", phone=phone)

        if password != confirm:
            flash("Passwords do not match.", "error")
            return render_template("verify.html", phone=phone)

        farmer.set_password(password)
        db.session.commit()
        login_user(farmer)
        flash(f"Welcome, {farmer.full_name.split(' ')[0]}! Your password is set.", "success")
        return redirect(url_for("main.dashboard"))

    return render_template("verify.html", phone=prefill_phone)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("main.landing"))
