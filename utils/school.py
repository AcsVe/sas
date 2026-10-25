"""Small helpers shared by the SAS blueprints: school-local date/time, ticked ids, attachments on disk."""
import os
import uuid
from datetime import datetime, date
from zoneinfo import ZoneInfo

from flask import current_app, request, g, abort

from extensions import db
from utils.i18n import _

ALLOWED_EXT = {'.pdf', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.doc', '.docx'}


def local_now():
    return datetime.now(ZoneInfo(current_app.config.get('TIMEZONE', 'Asia/Amman')))


def today():
    return local_now().date()


def now_hhmm():
    return local_now().strftime('%H:%M')


def parse_day(value, default=None):
    try:
        return date.fromisoformat((value or '').strip())
    except ValueError:
        return default


def clean_time(value):
    """'7:5' / '07:05' / '07:05:00' → '07:05', or None."""
    v = (value or '').strip()
    if not v:
        return None
    parts = v.split(':')
    try:
        h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return None
    if not (0 <= h < 24 and 0 <= m < 60):
        return None
    return f'{h:02d}:{m:02d}'


def form_ids(name='ids'):
    return {int(v) for v in request.form.getlist(name) if v.isdigit()}


def int_or_none(v):
    v = (v or '').strip() if isinstance(v, str) else v
    try:
        return int(v) if v not in (None, '') else None
    except (TypeError, ValueError):
        return None


def attachment_dir(tenant_id=None):
    tid = tenant_id or g.tenant.id
    path = os.path.join(current_app.config['UPLOAD_DIR'], 'tenants', str(tid), 'attachments')
    os.makedirs(path, exist_ok=True)
    return path


def save_attachment(file_storage):
    """Stores an uploaded file under UPLOAD_DIR (never in the database). → Attachment or None.
    Raises ValueError with an Arabic message for a refused file."""
    from models import Attachment
    if file_storage is None or not file_storage.filename:
        return None
    name = os.path.basename(file_storage.filename)
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_EXT:
        raise ValueError(_('نوع المرفق غير مسموح — المسموح: PDF أو صورة أو Word'))
    stored = f'{uuid.uuid4().hex}{ext}'
    path = os.path.join(attachment_dir(), stored)
    file_storage.save(path)
    att = Attachment(original_name=name[:255], stored_name=stored, mime=file_storage.mimetype,
                     size=os.path.getsize(path), uploaded_by=g.user.id if g.get('user') else None)
    db.session.add(att)
    db.session.flush()
    return att


def delete_attachment_file(att):
    if att is None:
        return
    try:
        os.remove(os.path.join(attachment_dir(att.tenant_id), att.stored_name))
    except OSError:
        pass


def require(cond):
    if not cond:
        abort(403)
