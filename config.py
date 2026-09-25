import os
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, ".env"))


def _flag(name, default):
    """Read an on/off environment variable ('1', 'true', 'yes', 'on')."""
    fallback = "1" if default else "0"
    return os.environ.get(name, fallback).strip().lower() in ("1", "true", "yes", "on")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    # Render (and most hosts) hand out postgres:// URLs; SQLAlchemy needs postgresql://
    # Also force the psycopg2 driver explicitly, since an unspecified postgresql://
    # URL can resolve to the psycopg (v3) dialect, which isn't installed here.
    db_url = os.environ.get("DATABASE_URL")
    if db_url and db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    if db_url and db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    SQLALCHEMY_DATABASE_URI = db_url or "sqlite:///" + os.path.join(basedir, "teafarm.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Browsers won't send the login cookie on cross-site POSTs — this is what
    # protects the forms from cross-site request forgery.
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _flag("SESSION_COOKIE_SECURE", False)  # set to 1 on HTTPS hosts
    TRUST_PROXY = _flag("TRUST_PROXY", False)  # set to 1 behind Render's proxy so audit IPs are real

    # Factory identity — shown on the login page, receipts and farm cards, and
    # used as the prefix of farm numbers (e.g. CY + 039 + 001 = CY039001).
    FACTORY_NAME = os.environ.get("FACTORY_NAME", "Chai Yako Tea Factory")
    FACTORY_CODE = os.environ.get("FACTORY_CODE", "CY")

    # Weighing. The official weight always comes from the scale. Until a scale
    # is connected, ALLOW_MANUAL_WEIGHT lets a clerk key the weight in; every
    # such weighing is flagged MANUAL and audit-logged. Set it to 0 once the
    # scale bridge is live.
    ALLOW_MANUAL_WEIGHT = _flag("ALLOW_MANUAL_WEIGHT", True)
    WEIGHT_MAX_AGE_SECONDS = int(os.environ.get("WEIGHT_MAX_AGE_SECONDS", "600"))
    MAX_SINGLE_WEIGHT_KG = float(os.environ.get("MAX_SINGLE_WEIGHT_KG", "1000"))
