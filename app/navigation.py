"""What each account sees in the sidebar, and which page it lands on after login.

The landing page is the page the account exists for: Buy tea for a clerk,
Staff and access for IT, Live overview for management, and so on. It is always
the first item in that person's sidebar. Notices are reached through the bell
icon, not the sidebar list.
"""
from flask import url_for

from app.permissions import has_permission

# (endpoint, label, rule). The rule gets a can(permission) function.
_NAV = [
    ("buying.home", "Buy tea", lambda can: can("RECORD_TRANSACTION")),
    ("receiver.home", "Reception desk", lambda can: can("RECORD_QUALITY")),
    ("receiver.trends", "Centre trends", lambda can: can("VIEW_CENTRE_TRENDS") and not can("GENERATE_REPORTS")),
    ("farmers.index", "Farmers", lambda can: can("VIEW_FARMER") or can("CREATE_FARMER")),
    ("complaints.index", "Complaints", lambda can: can("HANDLE_COMPLAINT") or can("GENERATE_REPORTS")),
    ("field.queue", "Farm verification", lambda can: can("VERIFY_FARM")),
    ("management.dashboard", "Live overview", lambda can: can("GENERATE_REPORTS")),
    ("management.insights", "Insights and trends", lambda can: can("GENERATE_REPORTS")),
    ("management.transactions", "Transactions", lambda can: can("VIEW_TRANSACTION")),
    ("management.centres", "Buying centres", lambda can: can("MANAGE_BUYING_CENTRES") or can("GENERATE_REPORTS")),
    ("management.scales", "Scales", lambda can: can("MANAGE_SCALES")),
    ("inputs.index", "Fertilizer", lambda can: can("PROCESS_FERTILIZER")),
    ("admin.staff", "Staff and access", lambda can: can("VIEW_STAFF")),
    ("management.audit", "Audit trail", lambda can: can("VIEW_AUDIT_LOG")),
]

# First match wins. The permission that says what the account is about, and the page that serves it.
_HOME = [
    ("RECORD_TRANSACTION", "buying.home"),
    ("RECORD_QUALITY", "receiver.home"),
    ("VERIFY_FARM", "field.queue"),
    ("PROCESS_FERTILIZER", "inputs.index"),
    ("HANDLE_COMPLAINT", "complaints.index"),
    ("GENERATE_REPORTS", "management.dashboard"),
    ("VIEW_STAFF", "admin.staff"),
    ("CREATE_FARMER", "farmers.index"),
    ("VIEW_TRANSACTION", "management.transactions"),
    ("VIEW_FARMER", "farmers.index"),
    ("VIEW_CENTRE_TRENDS", "receiver.trends"),
]


def home_endpoint(user):
    for permission, endpoint in _HOME:
        if has_permission(user, permission):
            return endpoint
    return "notices.board"          # an account with no working role still gets somewhere


def nav_items(user):
    """[(endpoint, label)] for this person, with their landing page first."""
    allowed = [(endpoint, label) for endpoint, label, rule in _NAV
               if rule(lambda name: has_permission(user, name))]
    home = home_endpoint(user)
    allowed.sort(key=lambda item: item[0] != home)      # stable: only the home item moves up
    return allowed


def home_url(user):
    return url_for(home_endpoint(user))
