from datetime import timedelta
from urllib.parse import urlparse

from flask import (Blueprint, render_template, request, redirect, url_for, session, flash,
                   current_app, g, abort)
from sqlalchemy import func

from authz import login_required
from extensions import db, csrf
from models import Tenant, User, LoginAttempt, PasswordResetToken
from models.core import utcnow
from security import verify_password, hash_password, password_problems
from tenancy import set_current_tenant, tenant_bypass
from utils.audit import audit
from utils.helpers import client_ip, new_token, hash_token
from utils.i18n import _

auth_bp = Blueprint('auth', __name__)

GENERIC_LOGIN_ERROR = 'اسم المستخدم أو كلمة المرور غير صحيحة'


def resolve_tenant_from_request(slug=None):
    """Which organization is this request for? By configured domain first,
    then an explicit slug from the login form, then — if this server hosts a
    single active organization — that one. Never derived from user input
    beyond choosing between existing, active tenants."""
    host = (request.host or '').split(':')[0].lower()
    q = Tenant.query.filter(Tenant.is_active.is_(True))
    if host:
        t = q.filter(func.lower(Tenant.domain) == host).first()
        if t:
            return t
    if slug:
        return q.filter(func.lower(Tenant.slug) == slug.strip().lower()).first()
    active = q.limit(2).all()
    return active[0] if len(active) == 1 else None


def _safe_next(target):
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not target.startswith('/') or target.startswith('//'):
        return None
    return target


def _ip_blocked():
    ip = client_ip()
    since = utcnow() - timedelta(minutes=current_app.config['LOGIN_LOCK_MINUTES'])
    fails = LoginAttempt.query.filter(LoginAttempt.ip_address == ip,
                                      LoginAttempt.success.is_(False),
                                      LoginAttempt.created_at >= since).count()
    return fails >= current_app.config['LOGIN_IP_MAX_FAILURES']


def _record_attempt(tenant_id, username, success, reason):
    db.session.add(LoginAttempt(tenant_id=tenant_id, username=(username or '')[:200],
                                ip_address=client_ip(), success=success, reason=reason))


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if g.get('user'):
        return redirect(url_for('main.home'))
    need_org = g.get('tenant') is None
    error = None
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        tenant = g.get('tenant') or resolve_tenant_from_request(request.form.get('org'))

        if _ip_blocked():
            error = _('محاولات دخول كثيرة من هذا الجهاز، حاول لاحقًا')
            _record_attempt(tenant.id if tenant else None, username, False, 'ip_blocked')
            db.session.commit()
            return render_template('auth/login.html', error=error, need_org=need_org), 429

        if tenant is None:
            verify_password(None, password)
            _record_attempt(None, username, False, 'no_tenant')
            db.session.commit()
            return render_template('auth/login.html', error=GENERIC_LOGIN_ERROR,
                                   need_org=True), 401

        set_current_tenant(tenant.id)
        g.tenant = tenant
        user = User.query.filter(
            (func.lower(User.username) == username.lower()) |
            (func.lower(User.email) == username.lower())).first() if username else None

        now = utcnow()
        if user and user.locked_until and user.locked_until > now:
            verify_password(None, password)
            _record_attempt(tenant.id, username, False, 'locked')
            db.session.commit()
            mins = int((user.locked_until - now).total_seconds() // 60) + 1
            return render_template('auth/login.html', need_org=need_org,
                                   error=_('الحساب مقفل مؤقتًا بسبب محاولات فاشلة. حاول بعد {mins} دقيقة', mins=mins)), 423

        if not user or not user.is_active or not verify_password(user.password_hash if user else None,
                                                                 password):
            reason = 'bad_password' if user else 'unknown_user'
            if user:
                user.failed_logins += 1
                if user.failed_logins >= current_app.config['LOGIN_MAX_FAILURES']:
                    user.locked_until = now + timedelta(minutes=current_app.config['LOGIN_LOCK_MINUTES'])
                    user.failed_logins = 0
                    audit('auth.locked', 'user', user.id, actor=user.full_name)
            _record_attempt(tenant.id, username, False, reason)
            db.session.commit()
            return render_template('auth/login.html', error=GENERIC_LOGIN_ERROR,
                                   need_org=need_org), 401

        user.failed_logins = 0
        user.locked_until = None
        user.last_login = now
        _record_attempt(tenant.id, username, True, 'ok')
        g.user = user
        audit('auth.login', 'user', user.id)
        db.session.commit()

        session.clear()  # new session id contents: no fixation
        session.permanent = True
        session['uid'], session['tid'], session['sv'] = user.id, tenant.id, user.session_version
        return redirect(_safe_next(request.args.get('next')) or url_for('main.home'))

    return render_template('auth/login.html', error=error, need_org=need_org)


@auth_bp.route('/logout', methods=['POST'])
@csrf.exempt
def logout():
    """Signing out never fails on an old page (another tab signed in again, the page stayed open across a
    restart…): it only ends this browser's session. A form posted from another site is still refused."""
    from urllib.parse import urlparse
    origin = request.headers.get('Origin') or request.headers.get('Referer') or ''
    if origin and urlparse(origin).netloc != request.host:
        abort(400)
    if g.get('user'):
        audit('auth.logout', 'user', g.user.id)
        db.session.commit()
    session.clear()
    return redirect(url_for('auth.login'))


@auth_bp.route('/forgot', methods=['GET', 'POST'])
def forgot():
    need_org = g.get('tenant') is None
    if request.method == 'POST':
        ident = (request.form.get('username') or '').strip().lower()
        tenant = g.get('tenant') or resolve_tenant_from_request(request.form.get('org'))
        if tenant and ident:
            set_current_tenant(tenant.id)
            g.tenant = tenant
            user = User.query.filter((func.lower(User.username) == ident) |
                                     (func.lower(User.email) == ident)).first()
            if user and user.is_active and user.email:
                recent = PasswordResetToken.query.filter(
                    PasswordResetToken.user_id == user.id,
                    PasswordResetToken.created_at >= utcnow() - timedelta(hours=1)).count()
                if recent < 3:
                    _issue_reset(user, tenant)
        flash(_('إن كان الحساب موجودًا وله بريد مسجّل، فقد أرسلنا رابط إعادة التعيين إليه.'), 'info')
        return redirect(url_for('auth.login'))
    return render_template('auth/forgot.html', need_org=need_org)


def _issue_reset(user, tenant, by_admin=False):
    base = current_app.config.get('BASE_URL')
    if not base:
        # Links are never built from request headers.
        raise RuntimeError(_('BASE_URL غير مضبوط؛ لا يمكن بناء رابط إعادة التعيين'))
    token = new_token()
    db.session.add(PasswordResetToken(
        user_id=user.id, token_hash=hash_token(token),
        expires_at=utcnow() + timedelta(hours=current_app.config['RESET_TOKEN_HOURS'])))
    audit('auth.reset_requested', 'user', user.id, by_admin=by_admin)
    db.session.commit()
    from utils.mail import send_email
    return send_email([user.email], _('إعادة تعيين كلمة المرور'), 'emails/password_reset.html',
                      tenant, kind='password_reset', entity=('user', user.id),
                      user=user, link=f'{base}/reset/{token}',
                      hours=current_app.config['RESET_TOKEN_HOURS'])


def _load_reset_token(token):
    th = hash_token(token)
    with tenant_bypass():   # token is the only thing that tells us the tenant
        row = PasswordResetToken.query.filter_by(token_hash=th).first()
        tenant_id = row.tenant_id if row else None
    if not row or row.used_at or row.expires_at < utcnow():
        return None, None
    set_current_tenant(tenant_id)
    g.tenant = db.session.get(Tenant, tenant_id)
    row = PasswordResetToken.query.filter_by(token_hash=th).first()
    user = db.session.get(User, row.user_id) if row else None
    if not user or not user.is_active or not g.tenant.is_active:
        return None, None
    return row, user


@auth_bp.route('/reset/<token>', methods=['GET', 'POST'])
def reset(token):
    row, user = _load_reset_token(token)
    if not row:
        return render_template('auth/reset.html', invalid=True), 400
    errors = []
    if request.method == 'POST':
        pw, pw2 = request.form.get('password') or '', request.form.get('confirm') or ''
        errors = password_problems(pw, user.username)
        if pw != pw2:
            errors.append(_('كلمتا المرور غير متطابقتين'))
        if not errors:
            now = utcnow()
            user.password_hash = hash_password(pw)
            user.session_version += 1
            user.failed_logins, user.locked_until = 0, None
            user.must_change_password = False
            for t in PasswordResetToken.query.filter_by(user_id=user.id, used_at=None).all():
                t.used_at = now
            audit('auth.password_reset', 'user', user.id, actor=user.full_name)
            db.session.commit()
            session.clear()
            flash(_('تم تغيير كلمة المرور. سجّل الدخول بالكلمة الجديدة.'), 'success')
            return redirect(url_for('auth.login'))
    return render_template('auth/reset.html', invalid=False, errors=errors)


@auth_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    user = g.user
    errors = []
    if request.method == 'POST':
        cur = request.form.get('current') or ''
        pw, pw2 = request.form.get('password') or '', request.form.get('confirm') or ''
        if not verify_password(user.password_hash, cur):
            errors.append(_('كلمة المرور الحالية غير صحيحة'))
        errors += password_problems(pw, user.username)
        if pw != pw2:
            errors.append(_('كلمتا المرور غير متطابقتين'))
        if not errors:
            user.password_hash = hash_password(pw)
            user.must_change_password = False
            user.session_version += 1        # logs out every other device
            audit('auth.password_changed', 'user', user.id)
            db.session.commit()
            session['sv'] = user.session_version
            flash(_('تم تغيير كلمة المرور'), 'success')
            return redirect(url_for('main.home'))
    return render_template('auth/change_password.html', errors=errors)
