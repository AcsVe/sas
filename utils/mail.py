"""Email via Microsoft Graph (same app registration settings as venues).

Unlike the Apps Script (empty ``catch(e) {}``), every attempt is written to
``email_logs`` with its error, and the result is returned to the caller so
the UI can say "saved, but the email to X failed". Call ``send_email`` only
after the business change has been committed: it commits the log row.
"""
import time

import requests
from flask import current_app, render_template

from extensions import db
from utils.helpers import split_emails
from utils.i18n import _

_token_cache = {'token': None, 'expires_at': 0}


class MailResult:
    def __init__(self, ok, status, error=None, recipients=None):
        self.ok, self.status, self.error, self.recipients = ok, status, error, recipients or []

    def __bool__(self):
        return self.ok


def _graph_token():
    cfg = current_app.config
    tenant, cid, secret = cfg.get('MS_TENANT_ID'), cfg.get('MS_CLIENT_ID'), cfg.get('MS_CLIENT_SECRET')
    if not (tenant and cid and secret):
        raise RuntimeError(_('إعدادات Microsoft Graph ناقصة (MS_TENANT_ID / MS_CLIENT_ID / MS_CLIENT_SECRET)'))
    now = time.time()
    if _token_cache['token'] and now < _token_cache['expires_at'] - 60:
        return _token_cache['token']
    r = requests.post(
        f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token',
        data={'client_id': cid, 'client_secret': secret,
              'scope': 'https://graph.microsoft.com/.default',
              'grant_type': 'client_credentials'},
        timeout=15)
    if r.status_code != 200:
        raise RuntimeError(_('فشل الحصول على رمز Graph: HTTP {status_code} {v}', status_code=r.status_code, v=r.text[:300]))
    data = r.json()
    _token_cache['token'] = data['access_token']
    _token_cache['expires_at'] = now + int(data.get('expires_in', 3600))
    return _token_cache['token']


def _graph_send(sender, to_list, subject, html):
    token = _graph_token()
    message = {
        'subject': subject,
        'body': {'contentType': 'HTML', 'content': html},
        'toRecipients': [{'emailAddress': {'address': e}} for e in to_list],
    }
    r = requests.post(
        f'https://graph.microsoft.com/v1.0/users/{sender}/sendMail',
        headers={'Authorization': f'Bearer {token}'},
        json={'message': message, 'saveToSentItems': True}, timeout=20)
    if r.status_code not in (200, 201, 202):
        raise RuntimeError(f'Graph sendMail HTTP {r.status_code}: {r.text[:300]}')


def render_email(template, tenant, **ctx):
    """Render an email body. Templates extend emails/base.html, which
    reproduces the script's unified layout (RTL, blue gradient, footer).
    Jinja autoescaping protects every value inserted."""
    return render_template(template, tenant=tenant, **ctx)


def send_email(recipients, subject, template, tenant, kind=None, entity=None, **ctx):
    from models import EmailLog
    to_list = split_emails(','.join(recipients) if isinstance(recipients, (list, tuple, set))
                           else recipients)
    entity_type, entity_id = (entity if entity else (None, None))
    log = EmailLog(recipients=', '.join(to_list) or '-', subject=subject[:500], kind=kind,
                   entity_type=entity_type,
                   entity_id=str(entity_id) if entity_id is not None else None,
                   status='failed')
    result = None
    if not to_list:
        log.status, log.error = 'skipped', _('لا يوجد بريد صالح للمستلم')
        result = MailResult(False, 'skipped', log.error)
    else:
        try:
            html = render_email(template, tenant, subject=subject, **ctx)
            backend = current_app.config.get('MAIL_BACKEND', 'graph')
            if backend == 'memory':
                current_app.config.setdefault('MAIL_OUTBOX', []).append(
                    {'to': to_list, 'subject': subject, 'html': html, 'kind': kind})
            else:
                sender = (tenant.mail_sender if tenant and tenant.mail_sender
                          else current_app.config.get('MS_SENDER_EMAIL'))
                if not sender:
                    raise RuntimeError(_('MS_SENDER_EMAIL غير مضبوط'))
                _graph_send(sender, to_list, subject, html)
            log.status = 'sent'
            result = MailResult(True, 'sent', recipients=to_list)
        except Exception as e:  # recorded and returned, never swallowed
            log.status, log.error = 'failed', str(e)[:2000]
            current_app.logger.error('[mail] %s -> %s: %s', subject, to_list, e)
            result = MailResult(False, 'failed', str(e), to_list)
    db.session.add(log)
    db.session.commit()
    return result
