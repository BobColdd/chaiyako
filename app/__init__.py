from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from config import Config

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to access your dashboard."
login_manager.login_message_category = "info"


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)

    from app.models import Farmer
    from app.factory_models import FactoryUser

    @login_manager.user_loader
    def load_user(prefixed_id):
        # Ids are stored as "farmer-<id>" or "staff-<id>" (see get_id() on each
        # model) so one Flask-Login session space can serve both farmers and
        # factory staff without them colliding.
        try:
            kind, raw_id = prefixed_id.split("-", 1)
        except ValueError:
            return None
        if kind == "farmer":
            return Farmer.query.get(int(raw_id))
        if kind == "staff":
            return FactoryUser.query.get(int(raw_id))
        return None

    from app.auth import auth_bp
    from app.main import main_bp
    from app.news import news_bp
    from app.chatbot import chatbot_bp
    from app.factory_auth import factory_auth_bp
    from app.manager import manager_bp
    from app.clerk import clerk_bp
    from app.api import api_bp
    from app.farmer_api import farmer_api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(news_bp)
    app.register_blueprint(chatbot_bp)
    app.register_blueprint(factory_auth_bp)
    app.register_blueprint(manager_bp)
    app.register_blueprint(clerk_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(farmer_api_bp)

    with app.app_context():
        db.create_all()

    @app.cli.command("set-clerk-pin")
    def set_clerk_pin():
        """Set (or reset) the PIN a clerk uses to log into the mobile app.

        Usage: flask set-clerk-pin
        Prompts for username and a numeric PIN (4-6 digits recommended).
        """
        import click
        from app.factory_models import FactoryUser

        username = click.prompt("Clerk username").strip().lower()
        staff = FactoryUser.query.filter_by(username=username).first()
        if not staff:
            click.echo(f"No staff account found for username '{username}'.")
            return
        if not staff.is_clerk:
            click.echo(f"'{username}' is not a clerk account (role: {staff.role}).")
            return

        pin = click.prompt("New PIN", hide_input=True, confirmation_prompt=True)
        staff.set_pin(pin)
        db.session.commit()
        click.echo(f"PIN set for {staff.full_name} ({username}).")

    return app
