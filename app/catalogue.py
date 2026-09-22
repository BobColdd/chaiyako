"""The factory's departments, permissions and roles.

This is the reference data the whole access model stands on. It is loaded once
(automatically, when the app starts) and is safe to load again: it only ever
ADDS what is missing. It never changes a role that already exists, so an
administrator's later edits are not overwritten on the next restart.
"""
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Department, Permission, Role

# ---------------------------------------------------------------- departments

DEPARTMENTS = [
    ("Factory Management", "Oversight of the whole factory"),
    ("Customer Services", "Registers farmers and answers farmer enquiries"),
    ("Field and Farm Registration", "Visits and verifies farms; issues farm numbers"),
    ("Tea Buying and Operations", "Buying centres, weighing, receiving and grading tea"),
    ("Finance", "Farmer statements and financial services"),
    ("Inputs and Fertilizer", "Fertilizer and other farm inputs"),
    ("Farmer Relations", "Complaints and farmer communication"),
    ("IT and Information Systems", "Accounts, access and system governance"),
]

DEPT_MANAGEMENT = "Factory Management"
DEPT_CUSTOMER = "Customer Services"
DEPT_FIELD = "Field and Farm Registration"
DEPT_BUYING = "Tea Buying and Operations"
DEPT_FINANCE = "Finance"
DEPT_INPUTS = "Inputs and Fertilizer"
DEPT_RELATIONS = "Farmer Relations"
DEPT_IT = "IT and Information Systems"

# ---------------------------------------------------------------- permissions

PERMISSIONS = [
    ("VIEW_FARMER", "See farmer and farm records"),
    ("CREATE_FARMER", "Register new farmers and add farms"),
    ("EDIT_FARMER", "Edit a farmer's contact and identity details"),
    ("ASSIGN_BUYING_CENTRE", "Change the buying centre a farmer belongs to"),
    ("VERIFY_FARM", "Carry out farm verification visits and record the result"),
    ("GENERATE_FARM_NUMBER", "Issue farm numbers and farm cards"),
    ("VIEW_TRANSACTION", "See tea transactions and receipts"),
    ("RECORD_TRANSACTION", "Scan farm cards and confirm weighings at a buying centre"),
    ("CORRECT_TRANSACTION", "Void a wrongly recorded tea transaction"),
    ("RECORD_QUALITY", "Record the quality grade of tea received at the factory"),
    ("VIEW_CENTRE_TRENDS", "See kilos and quality per buying centre (never farmer names)"),
    ("GENERATE_REPORTS", "See factory-wide dashboards and reports"),
    ("MANAGE_BUYING_CENTRES", "Add, rename and deactivate buying centres"),
    ("MANAGE_SCALES", "Register weighing scales and issue their access keys"),
    ("PROCESS_FERTILIZER", "Record fertilizer and input distribution"),
    ("HANDLE_COMPLAINT", "Review and resolve farmer complaints"),
    ("POST_NOTICE", "Post notices to your own department"),
    ("PUBLISH_NOTICE", "Post public and all-staff notices, and notices to any department"),
    ("VIEW_STAFF", "See employees, accounts and roles"),
    ("CREATE_USER", "Create employee accounts and reset access"),
    ("MANAGE_PERMISSIONS", "Change which roles an employee holds"),
    ("VIEW_AUDIT_LOG", "Read the audit trail"),
    # Used by the finance screens in a later phase.
    ("GENERATE_STATEMENT", "Prepare farmer statements"),
    ("APPROVE_STATEMENT", "Approve farmer statements"),
]

# ---------------------------------------------------------------------- roles
# (name, home department, description, permissions)
# The home department is only used to place people when demo data is created;
# a role itself is not tied to a department.

_IT_OFFICER = ["VIEW_STAFF", "CREATE_USER", "MANAGE_SCALES", "POST_NOTICE"]
_FINANCE_OFFICER = ["VIEW_FARMER", "VIEW_TRANSACTION", "GENERATE_STATEMENT", "POST_NOTICE"]

ROLES = [
    ("Factory Manager", DEPT_MANAGEMENT, "Broad oversight; changes still need the right authority",
     ["VIEW_FARMER", "VIEW_TRANSACTION", "GENERATE_REPORTS", "VIEW_CENTRE_TRENDS", "MANAGE_BUYING_CENTRES",
      "POST_NOTICE", "PUBLISH_NOTICE", "VIEW_STAFF", "VIEW_AUDIT_LOG"]),
    ("Customer Service Officer", DEPT_CUSTOMER, "Registers farmers and helps them",
     ["VIEW_FARMER", "CREATE_FARMER", "EDIT_FARMER", "ASSIGN_BUYING_CENTRE", "VIEW_TRANSACTION",
      "POST_NOTICE", "PUBLISH_NOTICE"]),
    ("Field Officer", DEPT_FIELD, "Verifies farms and issues farm numbers",
     ["VIEW_FARMER", "VERIFY_FARM", "GENERATE_FARM_NUMBER", "POST_NOTICE"]),
    ("Tea Buying Clerk", DEPT_BUYING, "Weighs and records tea at a buying centre",
     ["RECORD_TRANSACTION"]),
    ("Tea Buying Manager", DEPT_BUYING, "Runs buying centres, scales and corrections",
     ["VIEW_FARMER", "VIEW_TRANSACTION", "CORRECT_TRANSACTION", "VIEW_CENTRE_TRENDS", "GENERATE_REPORTS",
      "MANAGE_BUYING_CENTRES", "MANAGE_SCALES", "POST_NOTICE", "PUBLISH_NOTICE"]),
    ("Tea Receiver", DEPT_BUYING, "Grades the tea received at the factory",
     ["RECORD_QUALITY", "VIEW_CENTRE_TRENDS"]),
    ("Finance Officer", DEPT_FINANCE, "Prepares farmer statements", _FINANCE_OFFICER),
    ("Finance Manager", DEPT_FINANCE, "Approves farmer statements", _FINANCE_OFFICER + ["APPROVE_STATEMENT"]),
    ("Inputs Officer", DEPT_INPUTS, "Handles fertilizer and inputs",
     ["VIEW_FARMER", "PROCESS_FERTILIZER", "POST_NOTICE", "PUBLISH_NOTICE"]),
    ("Farmer Relations Officer", DEPT_RELATIONS, "Handles farmer complaints",
     ["VIEW_FARMER", "VIEW_TRANSACTION", "HANDLE_COMPLAINT", "POST_NOTICE", "PUBLISH_NOTICE"]),
    ("IT Officer", DEPT_IT, "Creates accounts and registers scales", _IT_OFFICER),
    ("IT Manager", DEPT_IT, "Governs access and reads the audit trail",
     _IT_OFFICER + ["MANAGE_PERMISSIONS", "VIEW_AUDIT_LOG"]),
]


def _check_catalogue():
    """A typo in a role's permission list should fail loudly, not silently grant nothing."""
    known = {name for name, _ in PERMISSIONS}
    for name, _dept, _desc, perms in ROLES:
        unknown = set(perms) - known
        assert not unknown, f"Role '{name}' lists unknown permissions: {sorted(unknown)}"
    assert len(known) == len(PERMISSIONS), "Duplicate permission name in the catalogue"
    dept_names = {name for name, _ in DEPARTMENTS}
    for name, dept, _desc, _perms in ROLES:
        assert dept in dept_names, f"Role '{name}' names an unknown department '{dept}'"


_check_catalogue()


def ensure_reference_data():
    """Create any missing departments, permissions and roles. Safe to run repeatedly."""
    try:
        have_departments = {d.name for d in Department.query.all()}
        for name, description in DEPARTMENTS:
            if name not in have_departments:
                db.session.add(Department(name=name, description=description))

        permissions = {p.name: p for p in Permission.query.all()}
        for name, description in PERMISSIONS:
            if name not in permissions:
                permission = Permission(name=name, description=description)
                db.session.add(permission)
                permissions[name] = permission
        db.session.flush()

        have_roles = {r.name for r in Role.query.all()}
        for name, _dept, description, perm_names in ROLES:
            if name not in have_roles:
                db.session.add(Role(
                    name=name, description=description,
                    permissions=[permissions[p] for p in perm_names],
                ))
        db.session.commit()
    except IntegrityError:
        # Another worker started at the same moment and got there first.
        db.session.rollback()


def role_home_department(role_name):
    for name, dept, _desc, _perms in ROLES:
        if name == role_name:
            return dept
    return None
