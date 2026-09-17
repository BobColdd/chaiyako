import os
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, ".env"))  # actually load .env — was missing before, so .env edits were silently ignored


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    db_url = os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(basedir, 'teafarm.db')}")
    # Render (and most hosts) hand out "postgres://" but SQLAlchemy 1.4+/psycopg2 wants "postgresql://"
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    SQLALCHEMY_DATABASE_URI = db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False
