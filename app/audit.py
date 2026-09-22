"""Write-once audit trail: who did what, to which record, when, and under which authority."""
import json

from flask import g, has_request_context, request
from flask_login import current_user

from app import db
from app.models import AuditLog


def _dumps(value):
    return None if value is None else json.dumps(value, default=str, sort_keys=True)


def log_action(action, entity_type=None, entity_id=None, old=None, new=None, user=None):
    """Add an audit row to the current database transaction.

    It is committed together with the change it describes, so a change can
    never be saved without its audit record (or the other way round).
    """
    ip_address = None
    delegation_id = None
    if has_request_context():
        ip_address = request.remote_addr
        delegation_id = g.get("acting_delegation_id")
        if user is None:
            user = g.get("audit_user")            # set by the mobile API, which has no browser session
        if user is None and current_user.is_authenticated:
            user = current_user._get_current_object()

    entry = AuditLog(
        user_id=user.id if user is not None else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        old_value=_dumps(old),
        new_value=_dumps(new),
        delegation_id=delegation_id,
        ip_address=ip_address,
    )
    db.session.add(entry)
    return entry
