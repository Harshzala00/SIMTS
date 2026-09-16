from functools import wraps

from flask import abort, request, current_app
from flask_login import current_user

from extensions import db
from models import AuditLog


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_active:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def client_ip():
    # Only trust X-Forwarded-For when the application is explicitly configured
    # behind a trusted reverse proxy. Otherwise it is user-controlled input.
    if current_app.config.get('TRUST_PROXY'):
        forwarded = request.headers.get('X-Forwarded-For', '')
        if forwarded:
            return forwarded.split(',')[0].strip()[:64]
    return (request.remote_addr or '')[:64]


def audit(action, target=''):
    try:
        db.session.add(
            AuditLog(
                admin_id=current_user.id if current_user.is_authenticated else None,
                action=str(action)[:200],
                target=str(target)[:200],
                ip_address=client_ip(),
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
