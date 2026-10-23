"""Platform super-admin: create and manage organizations. All queries here
use tenant_bypass explicitly — this is the only area allowed to do so."""
import re
from datetime import timedelta

from flask import (Blueprint, render_template, request, redirect, url_for, flash, g, session,
                   current_app, abort)
from sqlalchemy import func

from authz import superadmin_required, seed_default_roles
from extensions import db
from models import Tenant, SuperAdmin, User, Role, UserRole, LoginAttempt
from models.core import utcnow
from security import verify_password, hash_password, password_problems
from tenancy import tenant_bypass, tenant_context
from utils.helpers import client_ip, is_valid_email
from utils.audit import audit

super_bp = Blueprint('super', __name__)


@super_bp.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        now = utcnow()
        since = now - timedelta(minutes=current_app.config['LOGIN_LOCK_MINUTES'])
        ip_fails = LoginAttempt.query.filter(LoginAttempt.ip_address == client_ip(),
                                             LoginAttempt.success.is_(False),
                                             LoginAttempt.created_at >= since).count()
        sa = SuperAdmin.query.filter(func.lower(SuperAdmin.username) == username.lower()).first()
        ok = (ip_fails < current_app.config['LOGIN_IP_MAX_FAILURES'] and sa and sa.is_active
              and not (sa.locked_until and sa.locked_until > now)
              and verify_password(sa.password_hash, password))
        if not sa:
            verify_password(None, password)
        if ok:
            sa.failed_logins, sa.locked_until, sa.last_login = 0, None, now
            db.session.add(LoginAttempt(username='super:' + username, ip_address=client_ip(),
                                        success=True, reason='super_ok'))
            db.session.commit()
            session.clear()
            session.permanent = True
            session['super_id'], session['sv'] = sa.id, sa.session_version
            return redirect(url_for('super.tenants'))
        if sa:
            sa.failed_logins += 1
            if sa.failed_logins >= current_app.config['LOGIN_MAX_FAILURES']:
                sa.locked_until = now + timedelta(minutes=current_app.config['LOGIN_LOCK_MINUTES'])
                sa.failed_logins = 0
        db.session.add(LoginAttempt(username='super:' + username, ip_address=client_ip(),
                                    success=False, reason='super_fail'))
        db.session.commit()
        error = 'بيانات الدخول غير صحيحة أو الحساب مقفل مؤقتًا'
    return render_template('super/login.html', error=error), (401 if error else 200)


@super_bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('super.login'))


@super_bp.route('/')
@superadmin_required
def tenants():
    with tenant_bypass():
        rows = Tenant.query.order_by(Tenant.id).all()
        counts = dict(db.session.query(User.tenant_id, func.count(User.id))
                      .group_by(User.tenant_id).all())
    return render_template('super/tenants.html', tenants=rows, counts=counts)


def _tenant_fields(t, errors):
    f = request.form
    slug = (f.get('slug') or '').strip().lower()
    domain = (f.get('domain') or '').strip().lower() or None
    if not re.fullmatch(r'[a-z0-9-]{2,50}', slug):
        errors.append('المعرّف (slug): حروف إنجليزية صغيرة وأرقام وشرطة')
    if not (f.get('name_ar') or '').strip():
        errors.append('الاسم العربي مطلوب')
    if domain and not re.fullmatch(r'[a-z0-9.-]{3,200}', domain):
        errors.append('النطاق غير صالح')
    with tenant_bypass():
        if Tenant.query.filter(Tenant.slug == slug, Tenant.id != (t.id or 0)).first():
            errors.append('المعرّف مستخدم')
        if domain and Tenant.query.filter(Tenant.domain == domain, Tenant.id != (t.id or 0)).first():
            errors.append('النطاق مستخدم لمنظمة أخرى')
    t.slug, t.domain = slug, domain
    t.name_ar = (f.get('name_ar') or '').strip()
    t.name_en = (f.get('name_en') or '').strip() or None
    accent = (f.get('accent_color') or '#3D5A80').strip()
    t.accent_color = accent if re.fullmatch(r'#[0-9A-Fa-f]{6}', accent) else '#3D5A80'


@super_bp.route('/tenants/new', methods=['GET', 'POST'])
@superadmin_required
def tenant_new():
    t = Tenant(is_active=True, accent_color='#3D5A80')
    errors = []
    if request.method == 'POST':
        _tenant_fields(t, errors)
        f = request.form
        a_user = (f.get('admin_username') or '').strip()
        a_name = (f.get('admin_full_name') or '').strip()
        a_email = (f.get('admin_email') or '').strip() or None
        a_pw = f.get('admin_password') or ''
        if not re.fullmatch(r'[A-Za-z0-9._@-]{3,80}', a_user):
            errors.append('اسم مستخدم المدير غير صالح')
        if not a_name:
            errors.append('اسم المدير مطلوب')
        if a_email and not is_valid_email(a_email):
            errors.append('بريد المدير غير صالح')
        errors += password_problems(a_pw, a_user)
        if not errors:
            db.session.add(t)
            db.session.flush()
            with tenant_context(t.id):
                seed_default_roles(t.id)
                admin = User(username=a_user, full_name=a_name, email=a_email,
                             password_hash=hash_password(a_pw), must_change_password=True)
                db.session.add(admin)
                db.session.flush()
                role = Role.query.filter_by(key='admin').one()
                db.session.add(UserRole(user_id=admin.id, role_id=role.id))
                audit('tenant.created', 'tenant', t.id, actor='مدير عام: ' + g.superadmin.username)
                db.session.commit()
            flash('تم إنشاء المنظمة ومديرها؛ سيُطلب منه تغيير كلمة المرور عند أول دخول', 'success')
            return redirect(url_for('super.tenants'))
        db.session.rollback()
    return render_template('super/tenant_form.html', t=t, errors=errors, new=True), \
        (400 if errors else 200)


@super_bp.route('/tenants/<int:tenant_id>', methods=['GET', 'POST'])
@superadmin_required
def tenant_edit(tenant_id):
    t = db.session.get(Tenant, tenant_id) or abort(404)
    errors = []
    if request.method == 'POST':
        _tenant_fields(t, errors)
        if not errors:
            t.is_active = request.form.get('is_active') == '1'
            t.ocr_cloud_enabled = request.form.get('ocr_cloud_enabled') == '1'
            with tenant_context(t.id):
                audit('tenant.updated', 'tenant', t.id, actor='مدير عام: ' + g.superadmin.username,
                      active=t.is_active, ocr_cloud=t.ocr_cloud_enabled)
                db.session.commit()
            flash('تم الحفظ', 'success')
            return redirect(url_for('super.tenants'))
        db.session.rollback()
        t = db.session.get(Tenant, tenant_id)
    return render_template('super/tenant_form.html', t=t, errors=errors, new=False), \
        (400 if errors else 200)
