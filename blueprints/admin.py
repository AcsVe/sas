"""Tenant administration: users, roles, departments, audit and email logs,
organization settings. Everything here runs inside the logged-in user's
tenant; the tenancy layer filters every query automatically."""
import os
import re

from flask import (Blueprint, render_template, request, redirect, url_for, flash, g, abort,
                   current_app)
from sqlalchemy import func

from authz import permission_required, PERMISSIONS
from extensions import db
from tenancy import get_scoped
from models import (User, Role, RolePermission, UserRole, Department, UserDepartment, AuditLog,
                    EmailLog)
from models.core import utcnow, DEPARTMENT_TYPES
from security import hash_password, password_problems
from utils.audit import audit
from utils.helpers import split_emails, is_valid_email

admin_bp = Blueprint('admin', __name__)

PAGE_SIZE = 50


def _get_or_404(model, obj_id):
    obj = get_scoped(model, obj_id)   # tenant filter applies → other tenants' ids are 404
    if obj is None:
        abort(404)
    return obj


def _ids(field):
    out = set()
    for v in request.form.getlist(field):
        try:
            out.add(int(v))
        except ValueError:
            pass
    return out


def _admins_remaining(excluding_user_id=None):
    users = User.query.filter(User.is_active.is_(True)).all()
    return [u for u in users if u.id != excluding_user_id and u.has_perm('users.manage')]


# ───────────────────────────── Users ─────────────────────────────
@admin_bp.route('/users')
@permission_required('users.manage')
def users():
    """Users as cards (grouped by role, with the tasks their roles give them) or as a table; search,
    filter and sorting run in the browser (static/js/table.js), bulk actions post to users_bulk."""
    role_counts = dict(db.session.query(UserRole.role_id, func.count(UserRole.user_id))
                       .group_by(UserRole.role_id).all())
    return render_template('admin/users.html', users=User.query.order_by(User.full_name).all(),
                           roles=Role.query.order_by(Role.id).all(), role_counts=role_counts,
                           permissions=PERMISSIONS, now=utcnow())


def _apply_user_form(user, is_new):
    errors = []
    f = request.form
    username = (f.get('username') or '').strip()
    full_name = (f.get('full_name') or '').strip()
    email = (f.get('email') or '').strip() or None
    if not re.fullmatch(r'[A-Za-z0-9._@-]{3,80}', username):
        errors.append('اسم المستخدم: 3 أحرف على الأقل، حروف إنجليزية وأرقام و . _ - فقط')
    if not full_name:
        errors.append('الاسم الكامل مطلوب')
    if email and not is_valid_email(email):
        errors.append('البريد الإلكتروني غير صالح')
    dup = User.query.filter(func.lower(User.username) == username.lower(), User.id != (user.id or 0)).first()
    if dup:
        errors.append('اسم المستخدم مستخدم مسبقًا')
    if email:
        dup = User.query.filter(func.lower(User.email) == email.lower(), User.id != (user.id or 0)).first()
        if dup:
            errors.append('البريد مستخدم لحساب آخر')
    role_ids = _ids('roles')
    dept_ids = _ids('departments')
    valid_roles = {r.id for r in Role.query.filter(Role.id.in_(role_ids)).all()} if role_ids else set()
    valid_depts = {d.id for d in Department.query.filter(Department.id.in_(dept_ids)).all()} if dept_ids else set()
    if valid_roles != role_ids or valid_depts != dept_ids:
        errors.append('اختيار غير صالح للأدوار أو الأقسام')
    temp_pw = f.get('temp_password') or ''
    if temp_pw:
        errors += password_problems(temp_pw, username)
    elif is_new and not email:
        errors.append('أدخل كلمة مرور مؤقتة أو بريدًا لإرسال رابط تعيين كلمة المرور')
    if errors:
        return errors, None
    user.username, user.full_name, user.email = username, full_name, email
    user.employee_number = (f.get('employee_number') or '').strip() or None
    if not is_new:
        user.is_active = f.get('is_active') == '1'
    if temp_pw:
        user.password_hash = hash_password(temp_pw)
        user.must_change_password = True
        if not is_new:
            user.session_version += 1
    elif is_new:
        user.password_hash = hash_password(os.urandom(24).hex())  # unusable until reset
    return [], (valid_roles, valid_depts, bool(temp_pw))


def _sync_links(user, role_ids, dept_ids):
    for link in UserRole.query.filter_by(user_id=user.id).all():
        if link.role_id not in role_ids:
            db.session.delete(link)
    have = {l.role_id for l in UserRole.query.filter_by(user_id=user.id).all()}
    for rid in role_ids - have:
        db.session.add(UserRole(user_id=user.id, role_id=rid))
    for link in UserDepartment.query.filter_by(user_id=user.id).all():
        if link.department_id not in dept_ids:
            db.session.delete(link)
    have = {l.department_id for l in UserDepartment.query.filter_by(user_id=user.id).all()}
    for did in dept_ids - have:
        db.session.add(UserDepartment(user_id=user.id, department_id=did))
    _sync_scope(user)
    db.session.flush()
    db.session.expire(user, ['roles', 'departments'])


def _sync_scope(user):
    """Stages (supervisors / secretaries) and sections (teachers) ticked on the user form."""
    from models import Stage, Section, UserStage, UserSection
    if request.form.get('scope_form') != '1':
        return
    want = {int(x) for x in request.form.getlist('stages') if x.isdigit()}
    want = {s.id for s in Stage.query.filter(Stage.id.in_(want or [0])).all()}
    for link in UserStage.query.filter_by(user_id=user.id).all():
        if link.stage_id not in want:
            db.session.delete(link)
        else:
            want.discard(link.stage_id)
    for sid in want:
        db.session.add(UserStage(user_id=user.id, stage_id=sid))
    want = {int(x) for x in request.form.getlist('sections') if x.isdigit()}
    want = {s.id for s in Section.query.filter(Section.id.in_(want or [0])).all()}
    for link in UserSection.query.filter_by(user_id=user.id).all():
        if link.section_id not in want:
            db.session.delete(link)
        else:
            want.discard(link.section_id)
    for sid in want:
        db.session.add(UserSection(user_id=user.id, section_id=sid))


def _user_form_ctx(user, errors=None):
    from models import Stage, UserStage, UserSection
    stage_ids = {l.stage_id for l in UserStage.query.filter_by(user_id=user.id).all()} if user.id else set()
    section_ids = {l.section_id for l in UserSection.query.filter_by(user_id=user.id).all()} if user.id else set()
    return dict(u=user, errors=errors or [], roles=Role.query.order_by(Role.id).all(),
                departments=Department.query.order_by(Department.sort_order, Department.name).all(),
                stages=Stage.query.order_by(Stage.sort_order, Stage.id).all(),
                user_stage_ids=stage_ids, user_section_ids=section_ids)


@admin_bp.route('/users/new', methods=['GET', 'POST'])
@permission_required('users.manage')
def user_new():
    user = User(is_active=True)
    if request.method == 'POST':
        errors, extra = _apply_user_form(user, True)
        if not errors:
            role_ids, dept_ids, has_temp = extra
            db.session.add(user)
            db.session.flush()
            _sync_links(user, role_ids, dept_ids)
            audit('user.created', 'user', user.id, username=user.username,
                  roles=sorted(role_ids), departments=sorted(dept_ids))
            db.session.commit()
            if not has_temp:
                from blueprints.auth import _issue_reset
                res = _issue_reset(user, g.tenant, by_admin=True)
                flash('تم إنشاء المستخدم وإرسال رابط تعيين كلمة المرور' if res else
                      f'تم إنشاء المستخدم، لكن فشل إرسال البريد: {res.error}',
                      'success' if res else 'warning')
            else:
                flash('تم إنشاء المستخدم؛ سيُطلب منه تغيير كلمة المرور عند أول دخول', 'success')
            return redirect(url_for('admin.users'))
        return render_template('admin/user_form.html', **_user_form_ctx(user, errors)), 400
    return render_template('admin/user_form.html', **_user_form_ctx(user))


@admin_bp.route('/users/<int:user_id>', methods=['GET', 'POST'])
@permission_required('users.manage')
def user_edit(user_id):
    user = _get_or_404(User, user_id)
    if request.method == 'POST':
        errors, extra = _apply_user_form(user, False)
        if not errors:
            role_ids, dept_ids, _ = extra
            _sync_links(user, role_ids, dept_ids)
            if not _admins_remaining() :
                db.session.rollback()
                errors = ['لا يمكن: يجب أن يبقى مستخدم فعّال واحد على الأقل بصلاحية إدارة المستخدمين']
            else:
                if not user.is_active:
                    user.session_version += 1
                audit('user.updated', 'user', user.id, roles=sorted(role_ids),
                      departments=sorted(dept_ids), active=user.is_active)
                db.session.commit()
                flash('تم الحفظ', 'success')
                return redirect(url_for('admin.users'))
        db.session.rollback()
        user = _get_or_404(User, user_id)
        return render_template('admin/user_form.html', **_user_form_ctx(user, errors)), 400
    return render_template('admin/user_form.html', **_user_form_ctx(user))


@admin_bp.route('/users/<int:user_id>/action', methods=['POST'])
@permission_required('users.manage')
def user_action(user_id):
    user = _get_or_404(User, user_id)
    action = request.form.get('action')
    if action == 'unlock':
        user.locked_until, user.failed_logins = None, 0
        audit('user.unlocked', 'user', user.id)
        db.session.commit()
        flash('تم فك القفل', 'success')
    elif action == 'logout_all':
        user.session_version += 1
        audit('user.sessions_revoked', 'user', user.id)
        db.session.commit()
        flash('تم إنهاء جميع جلسات المستخدم', 'success')
    elif action == 'send_reset':
        if not user.email:
            flash('لا يوجد بريد لهذا المستخدم', 'danger')
        else:
            from blueprints.auth import _issue_reset
            res = _issue_reset(user, g.tenant, by_admin=True)
            flash('تم إرسال رابط إعادة التعيين' if res else f'فشل الإرسال: {res.error}',
                  'success' if res else 'danger')
    else:
        abort(400)
    return redirect(url_for('admin.user_edit', user_id=user.id))


def _bulk_done(n, skipped, verb):
    msg = f'{verb}: {n}'
    if skipped:
        msg += ' — تُرك: ' + '، '.join(skipped)
    flash(msg, 'success' if n else 'warning')


@admin_bp.route('/users/bulk', methods=['POST'])
@permission_required('users.manage')
def users_bulk():
    """Bulk actions on the ticked users: activate / deactivate / delete / logout_all.
    The signed-in user and the last remaining administrator are never deactivated or deleted."""
    action = request.form.get('action')
    if action not in ('activate', 'deactivate', 'delete', 'logout_all'):
        abort(400)
    me = g.user.id
    picked = User.query.filter(User.id.in_(_ids('ids'))).all()
    done, skipped = 0, []
    for u in picked:
        if action in ('deactivate', 'delete'):
            if u.id == me:
                skipped.append(f'{u.full_name} (حسابك)')
                continue
            # earlier rows of this batch are already applied (autoflush), so this sees the batch so far
            if u.has_perm('users.manage') and not _admins_remaining(excluding_user_id=u.id):
                skipped.append(f'{u.full_name} (آخر مدير)')
                continue
        if action == 'activate':
            u.is_active = True
        elif action == 'deactivate':
            u.is_active = False
            u.session_version += 1
        elif action == 'logout_all':
            u.session_version += 1
        else:
            audit('user.deleted', 'user', u.id, username=u.username, name=u.full_name)
            db.session.delete(u)
            done += 1
            continue
        audit(f'user.bulk_{action}', 'user', u.id)
        done += 1
    db.session.commit()
    _bulk_done(done, skipped, {'activate': 'فُعِّل', 'deactivate': 'أُوقف', 'delete': 'حُذف',
                               'logout_all': 'أُنهيت جلسات'}[action])
    return redirect(request.referrer or url_for('admin.users'))


# ─────────────────────────── Departments ───────────────────────────
@admin_bp.route('/departments')
@permission_required('departments.manage')
def departments():
    depts = Department.query.order_by(Department.sort_order, Department.name).all()
    return render_template('admin/departments.html', departments=depts, types=dict(DEPARTMENT_TYPES))


def _apply_dept_form(dept):
    f = request.form
    errors = []
    code = (f.get('code') or '').strip()
    name = (f.get('name') or '').strip()
    dtype = f.get('dept_type') or 'normal'
    raw_emails = f.get('emails') or ''
    emails = split_emails(raw_emails)
    if not code or len(code) > 50:
        errors.append('الكود مطلوب (حتى 50 حرفًا)')
    if not name:
        errors.append('اسم القسم مطلوب')
    if dtype not in dict(DEPARTMENT_TYPES):
        errors.append('نوع القسم غير صالح')
    bad = [p for p in re.split(r'[;,\s]+', raw_emails) if p.strip() and p.strip() not in emails
           and p.strip().lower() not in {e.lower() for e in emails}]
    if bad:
        errors.append('بريد غير صالح: ' + '، '.join(bad))
    if Department.query.filter(Department.code == code, Department.id != (dept.id or 0)).first():
        errors.append('الكود مستخدم لقسم آخر')
    active = f.get('is_active', '1') == '1'
    if dtype in ('HR', 'Finance') and active:
        other = Department.query.filter(Department.dept_type == dtype, Department.is_active.is_(True),
                                        Department.id != (dept.id or 0)).first()
        if other:
            errors.append(f'يوجد قسم فعّال آخر من النوع نفسه: {other.name}')
    if errors:
        return errors
    dept.code, dept.name, dept.dept_type = code, name, dtype
    dept.icon = (f.get('icon') or '🏢').strip()[:20]
    dept.manager_name = (f.get('manager_name') or '').strip() or None
    dept.emails = ', '.join(emails) or None
    try:
        dept.sort_order = int(f.get('sort_order') or 0)
    except ValueError:
        dept.sort_order = 0
    dept.is_active = active
    return []


@admin_bp.route('/departments/new', methods=['GET', 'POST'])
@admin_bp.route('/departments/<int:dept_id>', methods=['GET', 'POST'])
@permission_required('departments.manage')
def department_form(dept_id=None):
    dept = _get_or_404(Department, dept_id) if dept_id else Department(is_active=True, dept_type='normal')
    errors = []
    if request.method == 'POST':
        errors = _apply_dept_form(dept)
        if not errors:
            if not dept_id:
                db.session.add(dept)
            db.session.flush()
            audit('department.created' if not dept_id else 'department.updated', 'department',
                  dept.id, code=dept.code, name=dept.name, type=dept.dept_type)
            db.session.commit()
            flash('تم الحفظ', 'success')
            return redirect(url_for('admin.departments'))
        db.session.rollback()
    return render_template('admin/department_form.html', d=dept, errors=errors,
                           types=DEPARTMENT_TYPES), (400 if errors else 200)


def department_in_use(dept):
    """Why a department cannot be deleted (records that depend on it), or '' when it is free."""
    reasons = []
    try:
        from models import Employee
        if Employee.query.filter_by(department_id=dept.id).first():
            reasons.append('مرتبط بموظفين')
    except ImportError:
        pass
    return '، '.join(reasons)


@admin_bp.route('/departments/bulk', methods=['POST'])
@permission_required('departments.manage')
def departments_bulk():
    action = request.form.get('action')
    if action not in ('activate', 'deactivate', 'delete'):
        abort(400)
    done, skipped = 0, []
    for d in Department.query.filter(Department.id.in_(_ids('ids'))).all():
        if action == 'activate':
            if d.dept_type in ('HR', 'Finance') and Department.query.filter(
                    Department.dept_type == d.dept_type, Department.is_active.is_(True),
                    Department.id != d.id).first():
                skipped.append(f'{d.name} (يوجد قسم فعّال من النوع نفسه)')
                continue
            d.is_active = True
        elif action == 'deactivate':
            d.is_active = False
        else:
            why = department_in_use(d)
            if why:
                skipped.append(f'{d.name} ({why} — أوقفه بدلاً من حذفه)')
                continue
            audit('department.deleted', 'department', d.id, code=d.code, name=d.name)
            db.session.delete(d)
            done += 1
            continue
        audit(f'department.bulk_{action}', 'department', d.id)
        done += 1
    db.session.commit()
    _bulk_done(done, skipped, {'activate': 'فُعِّل', 'deactivate': 'أُوقف', 'delete': 'حُذف'}[action])
    return redirect(url_for('admin.departments'))


# ───────────────────────────── Roles ─────────────────────────────
@admin_bp.route('/roles')
@permission_required('users.manage')
def roles():
    role_counts = dict(db.session.query(UserRole.role_id, func.count(UserRole.user_id))
                       .group_by(UserRole.role_id).all())
    return render_template('admin/roles.html', roles=Role.query.order_by(Role.id).all(),
                           permissions=PERMISSIONS, role_counts=role_counts)


@admin_bp.route('/roles/new', methods=['GET', 'POST'])
@admin_bp.route('/roles/<int:role_id>', methods=['GET', 'POST'])
@permission_required('users.manage')
def role_form(role_id=None):
    role = _get_or_404(Role, role_id) if role_id else Role(is_active=True)
    errors = []
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        codes = {c for c in request.form.getlist('perms') if c in PERMISSIONS}
        if not name:
            errors.append('اسم الدور مطلوب')
        if not errors:
            role.name = name
            role.description = (request.form.get('description') or '').strip() or None
            if not role_id:
                role.key = 'custom_' + os.urandom(4).hex()
                db.session.add(role)
                db.session.flush()
            current = {p.permission_code: p for p in role.permissions}
            for code, rp in current.items():
                if code not in codes:
                    role.permissions.remove(rp)
            for code in codes - set(current):
                role.permissions.append(RolePermission(permission_code=code))
            db.session.flush()
            db.session.expire_all()
            if not _admins_remaining():
                db.session.rollback()
                errors.append('لا يمكن إزالة صلاحية إدارة المستخدمين من آخر مدير')
            else:
                audit('role.saved', 'role', role.id, name=name, permissions=sorted(codes))
                db.session.commit()
                flash('تم الحفظ', 'success')
                return redirect(url_for('admin.roles'))
        if role_id:
            role = _get_or_404(Role, role_id)
    return render_template('admin/role_form.html', role=role, errors=errors,
                           permissions=PERMISSIONS), (400 if errors else 200)


@admin_bp.route('/roles/bulk', methods=['POST'])
@permission_required('users.manage')
def roles_bulk():
    if request.form.get('action') != 'delete':
        abort(400)
    done, skipped = 0, []
    for r in Role.query.filter(Role.id.in_(_ids('ids'))).all():
        if r.is_system:
            skipped.append(f'{r.name} (دور أساسي)')
            continue
        if UserRole.query.filter_by(role_id=r.id).first():
            skipped.append(f'{r.name} (مسند إلى مستخدمين)')
            continue
        audit('role.deleted', 'role', r.id, name=r.name)
        db.session.delete(r)
        done += 1
    db.session.commit()
    _bulk_done(done, skipped, 'حُذف')
    return redirect(url_for('admin.roles'))


# ─────────────────────────── Logs ───────────────────────────
@admin_bp.route('/audit')
@permission_required('audit.view')
def audit_log():
    page = max(int(request.args.get('page', 1) or 1), 1)
    action = (request.args.get('action') or '').strip()
    q = AuditLog.query
    if action:
        q = q.filter(AuditLog.action.like(f'{action}%'))
    rows = q.order_by(AuditLog.id.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE + 1).all()
    return render_template('admin/audit.html', rows=rows[:PAGE_SIZE], page=page,
                           has_next=len(rows) > PAGE_SIZE, action=action)


@admin_bp.route('/email-log')
@permission_required('email_log.view')
def email_log():
    only_failed = request.args.get('failed') == '1'
    q = EmailLog.query
    if only_failed:
        q = q.filter(EmailLog.status != 'sent')
    rows = q.order_by(EmailLog.id.desc()).limit(200).all()
    return render_template('admin/email_log.html', rows=rows, only_failed=only_failed)


@admin_bp.route('/email-log/ack', methods=['POST'])
@permission_required('email_log.view')
def email_log_ack():
    n = EmailLog.query.filter(EmailLog.status != 'sent', EmailLog.acknowledged.is_(False)) \
        .update({'acknowledged': True}, synchronize_session=False)
    audit('email_log.acknowledged', count=n)
    db.session.commit()
    return redirect(url_for('admin.email_log', failed=1))


@admin_bp.route('/email-log/bulk', methods=['POST'])
@permission_required('email_log.view')
def email_log_bulk():
    action = request.form.get('action')
    q = EmailLog.query.filter(EmailLog.id.in_(_ids('ids')))
    if action == 'ack':
        n = q.update({'acknowledged': True}, synchronize_session=False)
        verb = 'عُلِّم كمقروء'
    elif action == 'delete':
        n = 0
        for row in q.all():
            db.session.delete(row)
            n += 1
        verb = 'حُذف'
    else:
        abort(400)
    audit(f'email_log.bulk_{action}', count=n)
    db.session.commit()
    _bulk_done(n, [], verb)
    return redirect(request.referrer or url_for('admin.email_log'))


# ─────────────────────────── Settings ───────────────────────────
_IMAGE_MAGIC = {b'\x89PNG': '.png', b'\xff\xd8\xff': '.jpg', b'RIFF': '.webp'}


def _save_logo(file_storage, which):
    head = file_storage.stream.read(12)
    file_storage.stream.seek(0)
    ext = next((e for magic, e in _IMAGE_MAGIC.items() if head.startswith(magic)), None)
    if ext == '.webp' and head[8:12] != b'WEBP':
        ext = None
    if not ext:
        raise ValueError('الشعار يجب أن يكون PNG أو JPG أو WEBP')
    folder = os.path.join(current_app.config['UPLOAD_DIR'], 'tenants', str(g.tenant.id))
    os.makedirs(folder, exist_ok=True)
    name = f'logo_{which}_{os.urandom(4).hex()}{ext}'
    file_storage.save(os.path.join(folder, name))
    return name


@admin_bp.route('/settings', methods=['GET', 'POST'])
@permission_required('settings.manage')
def settings():
    from models import Tenant
    t = db.session.get(Tenant, g.tenant.id)
    errors = []
    if request.method == 'POST':
        f = request.form
        name_ar = (f.get('name_ar') or '').strip()
        accent = (f.get('accent_color') or '').strip()
        sender = (f.get('mail_sender') or '').strip()
        if not name_ar:
            errors.append('الاسم العربي مطلوب')
        if not re.fullmatch(r'#[0-9A-Fa-f]{6}', accent):
            errors.append('اللون يجب أن يكون بصيغة ‎#RRGGBB')
        if sender and not is_valid_email(sender):
            errors.append('بريد المرسل غير صالح')
        logos = {}
        for which in ('primary', 'secondary'):
            fs = request.files.get(f'logo_{which}')
            if fs and fs.filename:
                try:
                    logos[which] = _save_logo(fs, which)
                except ValueError as e:
                    errors.append(str(e))
        if not errors:
            t.name_ar, t.accent_color = name_ar, accent
            t.name_en = (f.get('name_en') or '').strip() or None
            t.mail_sender = sender or None
            t.email_footer = (f.get('email_footer') or '').strip() or None
            t.form_footer = (f.get('form_footer') or '').strip() or None
            if 'primary' in logos:
                t.logo_primary = logos['primary']
            if 'secondary' in logos:
                t.logo_secondary = logos['secondary']
            audit('settings.updated', 'tenant', t.id, logos=sorted(logos))
            db.session.commit()
            flash('تم حفظ الإعدادات', 'success')
            return redirect(url_for('admin.settings'))
    return render_template('admin/settings.html', t=t, errors=errors), (400 if errors else 200)
