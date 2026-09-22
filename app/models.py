"""Every database table, in one place.

Two structures, deliberately kept separate (see the design document):

1. Organisation and access
   departments -> employees -> users, roles, permissions, delegations, audit_logs

2. Tea flow
   buying_centres -> farmers -> farms -> weighing_events -> tea_transactions -> receipts

Plus the services that hang off them: notices, quality_records, complaints,
fertilizer_distributions, and number_sequences (safe, collision-free numbering).

Conventions
* Timestamps are stored as naive UTC (see app/timeutil.py).
* Nothing is ever cascade-deleted. Farmers, farms and transactions are
  deactivated or voided, never removed, so the purchase history survives.
* Totals are never stored. Kilograms are always calculated from tea_transactions.
"""
from flask_login import UserMixin
from sqlalchemy import event
from werkzeug.security import generate_password_hash, check_password_hash

from app import db
from app.timeutil import utcnow, today_utc


# ============================================================================
# 1. ORGANISATION AND ACCESS
# ============================================================================

employee_roles = db.Table(
    "employee_roles",
    db.Column("employee_id", db.Integer, db.ForeignKey("employees.id"), primary_key=True),
    db.Column("role_id", db.Integer, db.ForeignKey("roles.id"), primary_key=True),
)

role_permissions = db.Table(
    "role_permissions",
    db.Column("role_id", db.Integer, db.ForeignKey("roles.id"), primary_key=True),
    db.Column("permission_id", db.Integer, db.ForeignKey("permissions.id"), primary_key=True),
)


class Department(db.Model):
    __tablename__ = "departments"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(255))
    status = db.Column(db.String(20), nullable=False, default="ACTIVE")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    employees = db.relationship("Employee", back_populates="department")


class Employee(db.Model):
    __tablename__ = "employees"

    id = db.Column(db.Integer, primary_key=True)
    employee_number = db.Column(db.String(30), unique=True, nullable=False)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    phone = db.Column(db.String(30))
    email = db.Column(db.String(120))
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="ACTIVE")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    department = db.relationship("Department", back_populates="employees")
    roles = db.relationship("Role", secondary=employee_roles, back_populates="employees")
    user = db.relationship("User", back_populates="employee", uselist=False)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"


class Role(db.Model):
    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.String(255))
    status = db.Column(db.String(20), nullable=False, default="ACTIVE")

    permissions = db.relationship("Permission", secondary=role_permissions, back_populates="roles")
    employees = db.relationship("Employee", secondary=employee_roles, back_populates="roles")


class Permission(db.Model):
    __tablename__ = "permissions"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), unique=True, nullable=False)
    description = db.Column(db.String(255))

    roles = db.relationship("Role", secondary=role_permissions, back_populates="permissions")


class User(UserMixin, db.Model):
    """The login identity of an employee. One login system for everyone —
    what a person can do comes from their roles, not from where they log in."""
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), unique=True, nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    # Optional 4-6 digit PIN, used ONLY by the clerk mobile app's /api/login.
    pin_hash = db.Column(db.String(255))
    status = db.Column(db.String(20), nullable=False, default="ACTIVE")
    last_login_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    employee = db.relationship("Employee", back_populates="user")

    # Flask-Login
    def get_id(self):
        return f"user-{self.id}"

    @property
    def is_active(self):
        return self.status == "ACTIVE" and self.employee.status == "ACTIVE"

    @property
    def full_name(self):
        return self.employee.full_name

    @property
    def department(self):
        return self.employee.department

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def set_pin(self, pin):
        self.pin_hash = generate_password_hash(pin)

    def check_pin(self, pin):
        return bool(self.pin_hash) and check_password_hash(self.pin_hash, pin)


class Delegation(db.Model):
    """A manager lends ONE permission to another employee for a while.
    The delegate does not take on the delegator's role — only that permission."""
    __tablename__ = "delegations"

    id = db.Column(db.Integer, primary_key=True)
    delegator_employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    delegate_employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    permission_id = db.Column(db.Integer, db.ForeignKey("permissions.id"), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"))
    # Cases arrive in Phase 2; until then this is a plain number (the foreign key is added with the cases table).
    case_id = db.Column(db.Integer)
    delegation_type = db.Column(db.String(20), nullable=False, default="TIME_BOUND")  # ONE_TIME / TIME_BOUND / RECURRING
    start_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime)
    status = db.Column(db.String(20), nullable=False, default="ACTIVE")
    reason = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    withdrawn_at = db.Column(db.DateTime)

    delegator = db.relationship("Employee", foreign_keys=[delegator_employee_id])
    delegate = db.relationship("Employee", foreign_keys=[delegate_employee_id])
    permission = db.relationship("Permission")
    department = db.relationship("Department")


class AuditLog(db.Model):
    """Who did what, to which record, when — and whether under a delegation.
    Rows are write-once: the ORM refuses to update or delete them."""
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(60), nullable=False)
    entity_type = db.Column(db.String(60))
    entity_id = db.Column(db.Integer)
    old_value = db.Column(db.Text)   # JSON
    new_value = db.Column(db.Text)   # JSON
    delegation_id = db.Column(db.Integer, db.ForeignKey("delegations.id"))
    ip_address = db.Column(db.String(45))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    user = db.relationship("User")
    delegation = db.relationship("Delegation")

    __table_args__ = (
        db.Index("ix_audit_entity", "entity_type", "entity_id"),
        db.Index("ix_audit_created", "created_at"),
    )


@event.listens_for(AuditLog, "before_update")
def _audit_rows_are_write_once(mapper, connection, target):
    raise RuntimeError("Audit records cannot be changed.")


@event.listens_for(AuditLog, "before_delete")
def _audit_rows_cannot_be_deleted(mapper, connection, target):
    raise RuntimeError("Audit records cannot be deleted.")


class NumberSequence(db.Model):
    """One counter per name (e.g. 'farmer', 'farm:7', 'receipt:20260921').
    Incremented with a single UPDATE so two clerks can never get the same number."""
    __tablename__ = "number_sequences"

    name = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Integer, nullable=False, default=0)


# ============================================================================
# 2. TEA FLOW
# ============================================================================

class BuyingCentre(db.Model):
    """A place farmers deliver tea. Established by the factory and the farmers;
    the farmer record only points at it."""
    __tablename__ = "buying_centres"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    location = db.Column(db.String(200))
    status = db.Column(db.String(20), nullable=False, default="ACTIVE")
    established_at = db.Column(db.Date)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    farmers = db.relationship("Farmer", back_populates="buying_centre")
    scales = db.relationship("WeighingScale", back_populates="buying_centre")

    @property
    def is_active(self):
        return self.status == "ACTIVE"


class Farmer(db.Model):
    __tablename__ = "farmers"

    id = db.Column(db.Integer, primary_key=True)
    farmer_number = db.Column(db.String(30), unique=True, nullable=False)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    phone = db.Column(db.String(30), unique=True, nullable=False)
    email = db.Column(db.String(120))
    identification_number = db.Column(db.String(30))
    address = db.Column(db.String(200))
    buying_centre_id = db.Column(db.Integer, db.ForeignKey("buying_centres.id"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="ACTIVE")
    # PENDING_VERIFICATION -> VERIFIED (or REJECTED). The farmer app reads this to show the farm-card status.
    registration_status = db.Column(db.String(30), nullable=False, default="PENDING_VERIFICATION")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    buying_centre = db.relationship("BuyingCentre", back_populates="farmers")
    farms = db.relationship("Farm", back_populates="farmer", order_by="Farm.id")

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def total_bushes(self):
        return sum(f.tea_bushes or 0 for f in self.farms)

    @property
    def total_kilos(self):
        """Calculated from valid transactions, never stored."""
        value = (
            db.session.query(db.func.coalesce(db.func.sum(TeaTransaction.weight_kg), 0.0))
            .filter(TeaTransaction.farmer_id == self.id, TeaTransaction.status == "VALID")
            .scalar()
        )
        return float(value or 0)


class Farm(db.Model):
    """A farm has no farm number until a field officer has verified it."""
    __tablename__ = "farms"

    id = db.Column(db.Integer, primary_key=True)
    farmer_id = db.Column(db.Integer, db.ForeignKey("farmers.id"), nullable=False, index=True)
    farm_number = db.Column(db.String(50), unique=True)   # NULL until verified
    tea_bushes = db.Column(db.Integer, nullable=False, default=0)
    location = db.Column(db.String(200))
    verification_status = db.Column(db.String(20), nullable=False, default="PENDING")  # PENDING / VERIFIED / REJECTED
    verified_by_id = db.Column(db.Integer, db.ForeignKey("employees.id"))
    verified_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    farmer = db.relationship("Farmer", back_populates="farms")
    verified_by = db.relationship("Employee", foreign_keys=[verified_by_id])
    verifications = db.relationship("FarmVerification", back_populates="farm", order_by="FarmVerification.id")

    __table_args__ = (
        db.CheckConstraint("tea_bushes >= 0", name="ck_farm_bushes_not_negative"),
    )

    @property
    def is_verified(self):
        return self.verification_status == "VERIFIED"


class FarmVerification(db.Model):
    """One row per verification task. The history of visits is kept."""
    __tablename__ = "farm_verifications"

    id = db.Column(db.Integer, primary_key=True)
    farm_id = db.Column(db.Integer, db.ForeignKey("farms.id"), nullable=False, index=True)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("employees.id"))
    status = db.Column(db.String(20), nullable=False, default="PENDING")  # PENDING / VERIFIED / REJECTED
    visit_date = db.Column(db.Date)
    observations = db.Column(db.Text)
    decision = db.Column(db.String(20))
    verified_by_id = db.Column(db.Integer, db.ForeignKey("employees.id"))
    verified_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    farm = db.relationship("Farm", back_populates="verifications")
    assigned_to = db.relationship("Employee", foreign_keys=[assigned_to_id])
    verified_by = db.relationship("Employee", foreign_keys=[verified_by_id])


class WeighingScale(db.Model):
    __tablename__ = "weighing_scales"

    id = db.Column(db.Integer, primary_key=True)
    buying_centre_id = db.Column(db.Integer, db.ForeignKey("buying_centres.id"), nullable=False, index=True)
    scale_identifier = db.Column(db.String(60), unique=True, nullable=False)
    model = db.Column(db.String(80))
    api_key_hash = db.Column(db.String(64))
    integration_status = db.Column(db.String(20), nullable=False, default="NOT_CONNECTED")  # NOT_CONNECTED / CONNECTED
    last_seen_at = db.Column(db.DateTime)
    status = db.Column(db.String(20), nullable=False, default="ACTIVE")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    buying_centre = db.relationship("BuyingCentre", back_populates="scales")


class WeighingEvent(db.Model):
    """The physical event: tea on the scale showed this weight. It waits
    (PENDING) until the clerk confirms it against a scanned farm card, and is
    then PROCESSED into a tea transaction. The clerk never types the weight
    (except the flagged MANUAL fallback)."""
    __tablename__ = "weighing_events"

    id = db.Column(db.Integer, primary_key=True)
    buying_centre_id = db.Column(db.Integer, db.ForeignKey("buying_centres.id"), nullable=False)
    scale_id = db.Column(db.Integer, db.ForeignKey("weighing_scales.id"))       # NULL for MANUAL entries
    farmer_id = db.Column(db.Integer, db.ForeignKey("farmers.id"))              # filled in when confirmed
    weight_kg = db.Column(db.Float, nullable=False)
    captured_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    source = db.Column(db.String(10), nullable=False, default="SCALE")         # SCALE / MANUAL
    raw_reference = db.Column(db.String(120))
    processing_status = db.Column(db.String(12), nullable=False, default="PENDING")  # PENDING / PROCESSED / DISCARDED
    transaction_id = db.Column(db.Integer, db.ForeignKey("tea_transactions.id"), unique=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("employees.id"))       # who keyed a MANUAL weight

    buying_centre = db.relationship("BuyingCentre")
    scale = db.relationship("WeighingScale")
    farmer = db.relationship("Farmer")
    created_by = db.relationship("Employee")
    transaction = db.relationship("TeaTransaction", back_populates="weighing_event")

    __table_args__ = (
        db.CheckConstraint("weight_kg > 0", name="ck_event_weight_positive"),
        db.CheckConstraint("processing_status IN ('PENDING','PROCESSED','DISCARDED')", name="ck_event_status"),
        db.Index("ix_event_centre_status", "buying_centre_id", "processing_status"),
    )


class TeaTransaction(db.Model):
    """The authoritative record of tea delivered. Never edited: a wrong one is
    VOIDED (with a reason, by someone allowed to) and the tea is weighed again."""
    __tablename__ = "tea_transactions"

    id = db.Column(db.Integer, primary_key=True)
    transaction_number = db.Column(db.String(30), unique=True, nullable=False)
    farmer_id = db.Column(db.Integer, db.ForeignKey("farmers.id"), nullable=False)
    farm_id = db.Column(db.Integer, db.ForeignKey("farms.id"), nullable=False, index=True)
    buying_centre_id = db.Column(db.Integer, db.ForeignKey("buying_centres.id"), nullable=False)
    clerk_employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    weight_kg = db.Column(db.Float, nullable=False)
    transaction_time = db.Column(db.DateTime, nullable=False, default=utcnow)
    source = db.Column(db.String(10), nullable=False, default="SCALE")          # SCALE / MANUAL
    status = db.Column(db.String(10), nullable=False, default="VALID")         # VALID / VOIDED
    void_reason = db.Column(db.String(300))
    voided_by_id = db.Column(db.Integer, db.ForeignKey("employees.id"))
    voided_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    farmer = db.relationship("Farmer")
    farm = db.relationship("Farm")
    buying_centre = db.relationship("BuyingCentre")
    clerk = db.relationship("Employee", foreign_keys=[clerk_employee_id])
    voided_by = db.relationship("Employee", foreign_keys=[voided_by_id])
    weighing_event = db.relationship("WeighingEvent", back_populates="transaction", uselist=False)
    receipt = db.relationship("Receipt", back_populates="transaction", uselist=False)

    __table_args__ = (
        db.CheckConstraint("weight_kg > 0", name="ck_tx_weight_positive"),
        db.CheckConstraint("status IN ('VALID','VOIDED')", name="ck_tx_status"),
        db.Index("ix_tx_centre_time", "buying_centre_id", "transaction_time"),
        db.Index("ix_tx_farmer_time", "farmer_id", "transaction_time"),
        db.Index("ix_tx_time", "transaction_time"),
    )


class Receipt(db.Model):
    __tablename__ = "receipts"

    id = db.Column(db.Integer, primary_key=True)
    receipt_number = db.Column(db.String(30), unique=True, nullable=False)
    transaction_id = db.Column(db.Integer, db.ForeignKey("tea_transactions.id"), unique=True, nullable=False)
    farmer_id = db.Column(db.Integer, db.ForeignKey("farmers.id"), nullable=False)
    issued_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    status = db.Column(db.String(10), nullable=False, default="ISSUED")   # ISSUED / VOID
    # Every time the print view opens it counts as a print; the second and later prints say "DUPLICATE".
    print_count = db.Column(db.Integer, nullable=False, default=0)
    last_printed_at = db.Column(db.DateTime)

    transaction = db.relationship("TeaTransaction", back_populates="receipt")
    farmer = db.relationship("Farmer")


class QualityRecord(db.Model):
    """The reception clerk's grade for the tea a buying centre delivered on a day."""
    __tablename__ = "quality_records"
    GRADE_SCORES = {"good": 3, "average": 2, "poor": 1}

    id = db.Column(db.Integer, primary_key=True)
    buying_centre_id = db.Column(db.Integer, db.ForeignKey("buying_centres.id"), nullable=False)
    date = db.Column(db.Date, nullable=False, default=today_utc)
    grade = db.Column(db.String(10), nullable=False)
    notes = db.Column(db.Text)
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    recorded_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    buying_centre = db.relationship("BuyingCentre")
    recorded_by = db.relationship("Employee")

    __table_args__ = (
        db.UniqueConstraint("buying_centre_id", "date", name="uq_quality_centre_day"),
        db.CheckConstraint("grade IN ('good','average','poor')", name="ck_quality_grade"),
    )


class Notice(db.Model):
    """A notice has one audience:
       PUBLIC     - farmers and visitors (landing page + the public API the farmer app reads);
                    optionally for one buying centre only
       STAFF      - every employee
       DEPARTMENT - only the employees of one department"""
    __tablename__ = "notices"

    id = db.Column(db.Integer, primary_key=True)
    audience = db.Column(db.String(12), nullable=False, default="PUBLIC")
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"))
    buying_centre_id = db.Column(db.Integer, db.ForeignKey("buying_centres.id"))
    category = db.Column(db.String(20), nullable=False, default="general")
    title = db.Column(db.String(160), nullable=False)
    content = db.Column(db.Text, nullable=False)
    posted_by_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    expires_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    department = db.relationship("Department")
    buying_centre = db.relationship("BuyingCentre")
    posted_by = db.relationship("Employee")

    __table_args__ = (
        db.CheckConstraint("audience IN ('PUBLIC','STAFF','DEPARTMENT')", name="ck_notice_audience"),
        db.CheckConstraint("audience <> 'DEPARTMENT' OR department_id IS NOT NULL", name="ck_notice_department"),
        db.Index("ix_notice_audience_created", "audience", "created_at"),
    )


class Complaint(db.Model):
    """Filed by a farmer (through the farmer app) and handled by Farmer Relations.
    Becomes a case in Phase 2."""
    __tablename__ = "complaints"

    id = db.Column(db.Integer, primary_key=True)
    farmer_id = db.Column(db.Integer, db.ForeignKey("farmers.id"), nullable=False, index=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey("tea_transactions.id"))
    category = db.Column(db.String(30), nullable=False, default="other")
    description = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(12), nullable=False, default="open")   # open / resolved
    resolution_note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    resolved_at = db.Column(db.DateTime)
    resolved_by_id = db.Column(db.Integer, db.ForeignKey("employees.id"))

    farmer = db.relationship("Farmer")
    transaction = db.relationship("TeaTransaction")
    resolved_by = db.relationship("Employee")


class FertilizerDistribution(db.Model):
    """A fertilizer/input handout. farmer_id empty = a centre-wide 'available now' entry.
    Becomes a fertilizer request case in Phase 2."""
    __tablename__ = "fertilizer_distributions"

    id = db.Column(db.Integer, primary_key=True)
    buying_centre_id = db.Column(db.Integer, db.ForeignKey("buying_centres.id"), nullable=False)
    farmer_id = db.Column(db.Integer, db.ForeignKey("farmers.id"))
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    fertilizer_type = db.Column(db.String(80), nullable=False)
    quantity_kg = db.Column(db.Float)
    date = db.Column(db.Date, nullable=False, default=today_utc)
    notes = db.Column(db.String(300))

    buying_centre = db.relationship("BuyingCentre")
    farmer = db.relationship("Farmer")
    recorded_by = db.relationship("Employee")
