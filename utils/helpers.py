import hashlib
import re
import secrets

from flask import request, has_request_context

_EMAIL_RE = re.compile(r'^[^@\s,;<>"]+@[^@\s,;<>"]+\.[^@\s,;<>"]+$')


def split_emails(value):
    """'a@x.com; b@x.com ,c@x.com' -> ['a@x.com', 'b@x.com', 'c@x.com'].
    Same cleanup the Apps Script did (';' -> ',', strip spaces), plus
    validation and de-duplication."""
    if not value:
        return []
    seen, out = set(), []
    for part in re.split(r'[;,\s]+', str(value)):
        part = part.strip()
        if part and _EMAIL_RE.match(part) and part.lower() not in seen:
            seen.add(part.lower())
            out.append(part)
    return out


def is_valid_email(value):
    return bool(value) and bool(_EMAIL_RE.match(value.strip()))


def new_token():
    """URL-safe random token (256 bits)."""
    return secrets.token_urlsafe(32)


def hash_token(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def client_ip():
    if not has_request_context():
        return None
    return request.remote_addr


def short_code(length=6):
    """Upper-case code like the Apps Script generateId(), but from a CSPRNG."""
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    return ''.join(secrets.choice(alphabet) for _ in range(length))
