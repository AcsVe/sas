from flask import g, has_request_context

from extensions import db
from utils.helpers import client_ip


def audit(action, entity_type=None, entity_id=None, actor=None, **details):
    """Record who did what and when. Added to the current transaction, so it
    is committed (or rolled back) together with the change it describes."""
    from models import AuditLog
    user = getattr(g, 'user', None) if has_request_context() else None
    entry = AuditLog(
        user_id=user.id if user else None,
        actor=actor or (user.full_name if user else 'النظام'),
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        details=details or None,
        ip_address=client_ip(),
    )
    db.session.add(entry)
    return entry
