"""One central place that answers "may this person do this?".

A person may do something when EITHER
  * one of their roles carries the permission, OR
  * someone has lent them that permission through a valid delegation.

Every page and API endpoint asks this same function, so access rules are not
scattered through the code. Results are cached per request.
"""
from functools import wraps

from flask import abort, g
from flask_login import current_user, login_required

from app.models import Delegation
from app.rules import delegation_is_active
from app.timeutil import utcnow


def role_permission_names(user):
    cache = g.setdefault("_role_permissions", {})
    if user.id not in cache:
        cache[user.id] = {
            permission.name
            for role in user.employee.roles if role.status == "ACTIVE"
            for permission in role.permissions
        }
    return cache[user.id]


def active_delegations(user):
    cache = g.setdefault("_active_delegations", {})
    if user.id not in cache:
        now = utcnow()
        rows = Delegation.query.filter_by(delegate_employee_id=user.employee_id, status="ACTIVE").all()
        cache[user.id] = [
            d for d in rows
            if delegation_is_active(d.status, d.start_at, d.expires_at, d.withdrawn_at, now)
        ]
    return cache[user.id]


def permission_source(user, name):
    """None if not allowed; ('role', None) or ('delegation', delegation_id) if allowed."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    if name in role_permission_names(user):
        return ("role", None)
    for delegation in active_delegations(user):
        if delegation.permission.name == name:
            return ("delegation", delegation.id)
    return None


def has_permission(user, name):
    return permission_source(user, name) is not None


def permission_required(*names):
    """Allow the request if the logged-in person holds ANY of the named permissions."""
    assert names, "permission_required() needs at least one permission name"

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            for name in names:
                source = permission_source(current_user, name)
                if source is not None:
                    # Remembered so the audit log can say "acting under delegation N".
                    g.acting_delegation_id = source[1]
                    return view(*args, **kwargs)
            abort(403)
        return wrapped
    return decorator
