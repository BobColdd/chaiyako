from datetime import datetime
from app import db


class Farmer(db.Model):
    """A tea farmer. Accounts are created by factory managers — farmers do not
    log in to this system (that lives in a separate farmer-facing app). This
    model exists here purely as the record the factory keeps: who owns which
    farm/farm card, so clerks can look them up when buying tea."""

    __tablename__ = "farmers"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(30), unique=True, nullable=False, index=True)
    national_id = db.Column(db.String(30))
    scale = db.Column(db.String(20), default="small")  # small / large
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    farms = db.relationship("Farm", backref="owner", lazy=True, cascade="all, delete-orphan")

    @property
    def total_bushes(self):
        return sum(f.approx_bushes or 0 for f in self.farms)

    @property
    def total_purchased_kilos(self):
        """Grand total of factory-recorded purchases across all farms."""
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

    purchases = db.relationship("Purchase", backref="farm", lazy=True, cascade="all, delete-orphan")

    @property
    def total_purchased_kilos(self):
        """Official kilos bought by the factory for this specific farm/plot."""
        return sum(p.kilos for p in self.purchases) or 0
