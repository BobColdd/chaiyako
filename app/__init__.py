from flask import Flask, render_template
from flask_login import LoginManager, current_user
from flask_sqlalchemy import SQLAlchemy

from config import Config

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to continue."
login_manager.login_message_category = "info"


def create_app(config_overrides=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)

    if app.config.get("TRUST_PROXY"):
        # Behind Render's proxy, the real visitor address arrives in X-Forwarded-For.
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    login_manager.init_app(app)

    from app import models  # noqa: F401  (registers every table before create_all)
    from app.permissions import has_permission
    from app.timeutil import to_eat

    @login_manager.user_loader
    def load_user(user_id):
        # Ids look like "user-12". Returning None for a deactivated account
        # logs them out on their very next request.
        kind, _, raw = (user_id or "").partition("-")
        if kind != "user" or not raw.isdigit():
            return None
        user = db.session.get(models.User, int(raw))
        return user if user is not None and user.is_active else None

    # ---- blueprints ---------------------------------------------------
    from app.auth import auth_bp
    from app.work import work_bp
    from app.farmers import farmers_bp
    from app.field import field_bp
    from app.buying import buying_bp
    from app.receiver import receiver_bp
    from app.management import management_bp
    from app.admin import admin_bp
    from app.notices import notices_bp
    from app.complaints import complaints_bp
    from app.inputs import inputs_bp
    from app.api import api_bp

    for blueprint in (auth_bp, work_bp, farmers_bp, field_bp, buying_bp, receiver_bp,
                      management_bp, admin_bp, notices_bp, complaints_bp, inputs_bp, api_bp):
        app.register_blueprint(blueprint)

    # ---- templates ----------------------------------------------------
    @app.context_processor
    def template_globals():
        def can(permission):
            return current_user.is_authenticated and has_permission(current_user, permission)
        return {"can": can, "factory_name": app.config["FACTORY_NAME"]}

    @app.template_filter("eat")
    def eat_filter(moment, fmt="%d %b %Y, %H:%M"):
        """Show a stored (UTC) time in East Africa Time."""
        return to_eat(moment).strftime(fmt) if moment else "—"

    @app.template_filter("kg")
    def kg_filter(value):
        return f"{(value or 0):,.1f} kg"

    def _error(code, title, message):
        return render_template("errors/error.html", code=code, title=title, message=message), code

    @app.errorhandler(403)
    def forbidden(_error_):
        return _error(403, "Not allowed", "Your role doesn't include this page. Ask your manager if you need access.")

    @app.errorhandler(404)
    def not_found(_error_):
        return _error(404, "Page not found", "That page doesn't exist, or the record was removed.")

    # ---- database -----------------------------------------------------
    with app.app_context():
        from app import dbtools
        problems = dbtools.schema_problems()
        if problems:
            raise RuntimeError(
                "This database was created by an older version of the app and doesn't match the new tables:\n  - "
                + "\n  - ".join(problems)
                + "\nFor demo data, rebuild it with:  python demo_seed.py --reset"
                + "\n(that drops every table in the database first). Real data needs a migration instead."
            )
        db.create_all()
        from app.catalogue import ensure_reference_data
        ensure_reference_data()

    register_commands(app)
    return app


def register_commands(app):
    """Command-line helpers: `flask --app run set-pin <username>` etc."""
    import click

    @app.cli.command("set-pin")
    @click.argument("username")
    @click.argument("pin")
    def set_pin_command(username, pin):
        """Set the 4-6 digit PIN a clerk uses in the mobile app."""
        from app import rules
        from app.models import User
        user = User.query.filter_by(username=username.strip().lower()).first()
        if user is None:
            raise click.ClickException(f"No account with username '{username}'.")
        if not rules.pin_is_valid(pin):
            raise click.ClickException("A PIN must be 4 to 6 digits.")
        user.set_pin(pin)
        db.session.commit()
        click.echo(f"PIN updated for {username}.")
