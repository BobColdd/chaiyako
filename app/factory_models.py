from datetime import datetime, date
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app import db


class Factory(db.Model):
    """A single tea factory (e.g. a KTDA factory). The system currently
    assumes one factory owns the whole deployment, but keeping this as its
    own table makes multi-factory support possible later without a rebuild."""

    __tablename__ = "factories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    code = db.Column(db.String(10), nullable=False, unique=True)  # e.g. "CY" for Chai Yako
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    buying_centers = db.relationship("BuyingCenter", backref="factory", lazy=True)
    staff = db.relationship("FactoryUser", backref="factory", lazy=True)
    routes = db.relationship("Route", backref="factory", lazy=True)
    notices = db.relationship("Notice", backref="factory", lazy=True)


class FactoryUser(UserMixin, db.Model):
    """Factory staff: clerks/tallyboys (buy tea at a center) and managers
    (see everything, manage centers/routes/staff, resolve complaints)."""

    __tablename__ = "factory_users"

    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), nullable=False)
    full_name = db.Column(db.String(150), nullable=False)
    username = db.Column(db.String(80), nullable=False, unique=True, index=True)
    phone = db.Column(db.String(30))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="clerk")  # 'clerk' or 'manager'
    is_active_staff = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Short numeric PIN for the mobile clerk app (field use — faster than typing
    # a full password on a phone). Separate from password_hash, which still
    # guards the web login. Null until a manager/admin sets one.
    pin_hash = db.Column(db.String(255), nullable=True)

    def get_id(self):
        return f"staff-{self.id}"

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    def set_pin(self, raw_pin):
        self.pin_hash = generate_password_hash(raw_pin)

    def check_pin(self, raw_pin):
        return bool(self.pin_hash) and check_password_hash(self.pin_hash, raw_pin)

    @property
    def is_manager(self):
        return self.role == "manager"

    @property
    def is_clerk(self):
        return self.role == "clerk"

    @property
    def active_session(self):
        """The clerk's currently open (not logged out) buying session, if any."""
        return (
            ClerkSession.query.filter_by(clerk_id=self.id, logout_at=None)
            .order_by(ClerkSession.login_at.desc())
            .first()
        )

    @property
    def current_route(self):
        assignment = (
            RouteAssignment.query.filter_by(clerk_id=self.id, end_date=None)
            .order_by(RouteAssignment.start_date.desc())
            .first()
        )
        return assignment.route if assignment else None


class Route(db.Model):
    """An ordered group of buying centers a tallyboy's lorry visits. Routes are
    stable for years; the tallyboy assigned to a route rotates periodically."""

    __tablename__ = "routes"

    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)  # e.g. "Route A - Kapsuser Loop"
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    buying_centers = db.relationship("BuyingCenter", backref="route", lazy=True)
    assignments = db.relationship("RouteAssignment", backref="route", lazy=True, cascade="all, delete-orphan")

    @property
    def current_assignment(self):
        return (
            RouteAssignment.query.filter_by(route_id=self.id, end_date=None)
            .order_by(RouteAssignment.start_date.desc())
            .first()
        )

    @property
    def current_tallyboy(self):
        assignment = self.current_assignment
        return assignment.clerk if assignment else None


class RouteAssignment(db.Model):
    """History of which clerk/tallyboy has been assigned to a route.
    end_date is null for the current, active assignment."""

    __tablename__ = "route_assignments"

    id = db.Column(db.Integer, primary_key=True)
    route_id = db.Column(db.Integer, db.ForeignKey("routes.id"), nullable=False)
    clerk_id = db.Column(db.Integer, db.ForeignKey("factory_users.id"), nullable=False)
    start_date = db.Column(db.Date, nullable=False, default=date.today)
    end_date = db.Column(db.Date, nullable=True)

    clerk = db.relationship("FactoryUser", backref="route_assignments")


class BuyingCenter(db.Model):
    __tablename__ = "buying_centers"

    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), nullable=False)
    route_id = db.Column(db.Integer, db.ForeignKey("routes.id"), nullable=True)
    name = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(10), nullable=False)  # short code used in farm numbers, e.g. "039"
    location_notes = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    farms = db.relationship("Farm", backref="buying_center", lazy=True)
    purchases = db.relationship("Purchase", backref="buying_center", lazy=True)
    sessions = db.relationship("ClerkSession", backref="buying_center", lazy=True)

    __table_args__ = (db.UniqueConstraint("factory_id", "code", name="uq_factory_center_code"),)

    @property
    def is_buying_now(self):
        """The 'green mark' - true while any clerk has an open session here."""
        return ClerkSession.query.filter_by(buying_center_id=self.id, logout_at=None).first() is not None

    @property
    def active_clerk(self):
        session = ClerkSession.query.filter_by(buying_center_id=self.id, logout_at=None).first()
        return session.clerk if session else None

    def next_farm_sequence(self):
        return Farm.query.filter_by(buying_center_id=self.id).count() + 1

    def generate_farm_number(self):
        seq = self.next_farm_sequence()
        return f"{self.factory.code}{self.code}{seq:03d}"


class ClerkSession(db.Model):
    """A clerk's login session at a specific buying center. While logout_at is
    null, that center shows the live 'buying in progress' green mark."""

    __tablename__ = "clerk_sessions"

    id = db.Column(db.Integer, primary_key=True)
    clerk_id = db.Column(db.Integer, db.ForeignKey("factory_users.id"), nullable=False)
    buying_center_id = db.Column(db.Integer, db.ForeignKey("buying_centers.id"), nullable=False)
    login_at = db.Column(db.DateTime, default=datetime.utcnow)
    logout_at = db.Column(db.DateTime, nullable=True)

    clerk = db.relationship("FactoryUser", backref="sessions")


class Purchase(db.Model):
    """A single tea-weighing transaction: the source of truth for kilos sold.
    Created the instant a clerk records a weighing; immediately visible on the
    farmer's dashboard and the manager's live view, and doubles as the digital
    receipt record."""

    __tablename__ = "purchases"

    id = db.Column(db.Integer, primary_key=True)
    receipt_number = db.Column(db.String(30), nullable=False, unique=True)
    farm_id = db.Column(db.Integer, db.ForeignKey("farms.id"), nullable=False)
    buying_center_id = db.Column(db.Integer, db.ForeignKey("buying_centers.id"), nullable=False)
    clerk_id = db.Column(db.Integer, db.ForeignKey("factory_users.id"), nullable=False)
    kilos = db.Column(db.Float, nullable=False)
    purchased_at = db.Column(db.DateTime, default=datetime.utcnow)

    clerk = db.relationship("FactoryUser", backref="purchases_made")

    @staticmethod
    def generate_receipt_number():
        stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        return f"RCPT-{stamp}-{Purchase.query.count() + 1:04d}"


class Notice(db.Model):
    """Factory-to-farmer announcements: prices, bonuses, fertilizer availability,
    route/tallyboy changes, general messages. buying_center_id null = visible to
    all farmers; set = visible only to farmers at that center."""

    __tablename__ = "notices"

    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), nullable=False)
    buying_center_id = db.Column(db.Integer, db.ForeignKey("buying_centers.id"), nullable=True)
    posted_by_id = db.Column(db.Integer, db.ForeignKey("factory_users.id"), nullable=False)
    category = db.Column(db.String(30), default="general")  # price, bonus, fertilizer, route_change, general
    title = db.Column(db.String(150), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    buying_center = db.relationship("BuyingCenter")
    posted_by = db.relationship("FactoryUser")


class FertilizerDistribution(db.Model):
    """Per-farmer fertilizer receipt log, scoped to a buying center.
    farmer_id is null for a general 'available at this center' style entry."""

    __tablename__ = "fertilizer_distributions"

    id = db.Column(db.Integer, primary_key=True)
    buying_center_id = db.Column(db.Integer, db.ForeignKey("buying_centers.id"), nullable=False)
    farmer_id = db.Column(db.Integer, db.ForeignKey("farmers.id"), nullable=True)
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("factory_users.id"), nullable=False)
    fertilizer_type = db.Column(db.String(100), nullable=False)
    quantity_kg = db.Column(db.Float)
    date = db.Column(db.Date, default=date.today)
    notes = db.Column(db.String(300))

    buying_center = db.relationship("BuyingCenter")
    farmer = db.relationship("Farmer")
    recorded_by = db.relationship("FactoryUser")


class Complaint(db.Model):
    """A farmer-raised dispute: harassment, short-weighing, unfair terms, or
    anything else. Optionally tied to a specific purchase/receipt."""

    __tablename__ = "complaints"

    id = db.Column(db.Integer, primary_key=True)
    farmer_id = db.Column(db.Integer, db.ForeignKey("farmers.id"), nullable=False)
    purchase_id = db.Column(db.Integer, db.ForeignKey("purchases.id"), nullable=True)
    category = db.Column(db.String(30), default="other")  # harassment, short_weight, unfair_terms, other
    description = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="open")  # open / resolved
    manager_note = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime, nullable=True)

    farmer = db.relationship("Farmer", backref="complaints")
    purchase = db.relationship("Purchase")


# Import placed at the bottom to avoid a circular import at module load time —
# Farm (in models.py) is referenced above only via relationship() strings,
# so this is solely to make Farm queryable from generate_farm_number().
from app.models import Farm  # noqa: E402
