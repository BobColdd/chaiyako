import secrets
from datetime import datetime, date
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app import db


class Farmer(UserMixin, db.Model):
    """A tea farmer. Accounts are created by factory managers (no self-signup).
    On creation, password_hash is left null and a one-time verification_code is
    generated; the farmer uses that code (sent by SMS, see app/sms.py) to set
    their own password on first login."""

    __tablename__ = "farmers"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=True, index=True)
    phone = db.Column(db.String(30), unique=True, nullable=False, index=True)
    national_id = db.Column(db.String(30))
    password_hash = db.Column(db.String(255), nullable=True)
    scale = db.Column(db.String(20), default="small")  # small_scale / large_scale
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # First-login verification (stands in for a real SMS OTP flow)
    verification_code = db.Column(db.String(10))
    code_generated_at = db.Column(db.DateTime)
    must_set_password = db.Column(db.Boolean, default=True)

    farms = db.relationship("Farm", backref="owner", lazy=True, cascade="all, delete-orphan")
    tools = db.relationship("Tool", backref="owner", lazy=True, cascade="all, delete-orphan")
    notes = db.relationship("Note", backref="owner", lazy=True, cascade="all, delete-orphan")

    def get_id(self):
        # Prefixed so the shared Flask-Login user_loader can tell Farmer and
        # FactoryUser ids apart (see app/factory_models.py + app/__init__.py).
        return f"farmer-{self.id}"

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)
        self.must_set_password = False
        self.verification_code = None

    def check_password(self, raw_password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, raw_password)

    def generate_verification_code(self):
        self.verification_code = f"{secrets.randbelow(1000000):06d}"
        self.code_generated_at = datetime.utcnow()
        return self.verification_code

    @property
    def total_bushes(self):
        return sum(f.approx_bushes or 0 for f in self.farms)

    @property
    def total_purchased_kilos(self):
        """Grand total of factory-recorded (official) purchases across all farms."""
        return sum(f.total_purchased_kilos for f in self.farms)


class Farm(db.Model):
    __tablename__ = "farms"

    id = db.Column(db.Integer, primary_key=True)
    farmer_id = db.Column(db.Integer, db.ForeignKey("farmers.id"), nullable=False)
    buying_center_id = db.Column(db.Integer, db.ForeignKey("buying_centers.id"), nullable=True)
    farm_number = db.Column(db.String(50), nullable=False, unique=True)  # e.g. CY039001 - also the card barcode value
    location = db.Column(db.String(150))
    approx_bushes = db.Column(db.Integer, default=0)
    acreage = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    plucking_records = db.relationship("PluckingRecord", backref="farm", lazy=True, cascade="all, delete-orphan")
    pruning_records = db.relationship("PruningRecord", backref="farm", lazy=True, cascade="all, delete-orphan")
    purchases = db.relationship("Purchase", backref="farm", lazy=True, cascade="all, delete-orphan")

    @property
    def total_kilos(self):
        """Self-logged plucking total (the farmer's own diary, not the official factory figure)."""
        return sum(r.kilos for r in self.plucking_records) or 0

    @property
    def total_purchased_kilos(self):
        """Official kilos bought by the factory for this specific farm/plot."""
        return sum(p.kilos for p in self.purchases) or 0


class PluckingRecord(db.Model):
    __tablename__ = "plucking_records"

    id = db.Column(db.Integer, primary_key=True)
    farm_id = db.Column(db.Integer, db.ForeignKey("farms.id"), nullable=False)
    date = db.Column(db.Date, nullable=False, default=date.today)
    kilos = db.Column(db.Float, nullable=False)
    pluckers_count = db.Column(db.Integer)  # optional: number of people who plucked that day
    notes = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class PruningRecord(db.Model):
    __tablename__ = "pruning_records"

    id = db.Column(db.Integer, primary_key=True)
    farm_id = db.Column(db.Integer, db.ForeignKey("farms.id"), nullable=False)
    date = db.Column(db.Date, nullable=False, default=date.today)
    bushes_pruned = db.Column(db.Integer, nullable=False)
    prune_type = db.Column(db.String(50))  # e.g. skiffing, medium pruning, deep pruning, tipping
    notes = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Tool(db.Model):
    __tablename__ = "tools"

    id = db.Column(db.Integer, primary_key=True)
    farmer_id = db.Column(db.Integer, db.ForeignKey("farmers.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)  # e.g. Pruning shears, Panga, Plucking baskets
    quantity = db.Column(db.Integer, default=1)
    cost = db.Column(db.Float)
    purchase_date = db.Column(db.Date, default=date.today)
    status = db.Column(db.String(30), default="good")  # good / needs_repair / damaged / lost
    notes = db.Column(db.String(300))


class Note(db.Model):
    __tablename__ = "notes"

    id = db.Column(db.Integer, primary_key=True)
    farmer_id = db.Column(db.Integer, db.ForeignKey("farmers.id"), nullable=False)
    farm_id = db.Column(db.Integer, db.ForeignKey("farms.id"), nullable=True)
    title = db.Column(db.String(150))
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
