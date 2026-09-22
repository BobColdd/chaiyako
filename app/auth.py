from urllib.parse import urlparse

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app import db
from app.audit import log_action
from app.models import User
from app.timeutil import utcnow

auth_bp = Blueprint("auth", __name__)


def _safe_next(target):
    """Only follow a 'next' address that stays on this site."""
    if not target:
        return None
    parts = urlparse(target)
    return target if not parts.netloc and not parts.scheme and target.startswith("/") else None


@auth_bp.route("/", methods=["GET", "POST"])
def login():
    """The home page IS the login page, and the one login for everyone.
    What a person sees afterwards depends on their roles."""
    if current_user.is_authenticated:
        return redirect(url_for("work.home"))

    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()

        if user is None or not user.check_password(password):
            log_action("LOGIN_FAILED", "user", user.id if user else None, new={"username": username[:80]})
            db.session.commit()
            # One message for every failure, so it never hints at which part was wrong.
            return render_template("auth/login.html", username=username, notice="Wrong username or password.")

        if not user.is_active:
            return render_template("auth/login.html", username=username,
                                   notice="This account has been deactivated. Contact IT.")

        login_user(user)
        user.last_login_at = utcnow()
        log_action("LOGIN", "user", user.id, user=user)
        db.session.commit()
        return redirect(_safe_next(request.args.get("next")) or url_for("work.home"))

    return render_template("auth/login.html", username="", notice=None)


@auth_bp.route("/login")
def login_alias():
    """The old /login address still works and lands on the home page."""
    return redirect(url_for("auth.login", next=request.args.get("next")))


@auth_bp.route("/factory/login")
def legacy_login():
    """Old bookmarks pointed here."""
    return redirect(url_for("auth.login"))


@auth_bp.route("/logout")
@login_required
def logout():
    log_action("LOGOUT", "user", current_user.id)
    db.session.commit()
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
