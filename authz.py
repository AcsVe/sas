"""Permissions catalog, default roles and access decorators."""
from functools import wraps

from flask import g, abort, redirect, url_for, request, session

# code -> (Arabic label, group)
PERMISSIONS = {
    'attendance.record':    ('تسجيل الغياب والتأخر والإذن لطلبة مراحله', 'الغياب'),
    'attendance.approve':   ('اعتماد السجلات ورفضها لطلبة مراحله', 'الغياب'),
    'attendance.view_all':  ('كل المراحل والشعب (دون تقييد)', 'الغياب'),
    'class_leave.record':   ('تسجيل الخروج من الحصة والعودة', 'الخروج من الحصة'),
    'class_leave.approve':  ('اعتماد الخروج من الحصة', 'الخروج من الحصة'),
    'guardians.notify':     ('إرسال إشعار الغياب لولي الأمر', 'أولياء الأمور'),
    'holidays.manage':      ('اقتراح العطل وتعديلها', 'العطل'),
    'holidays.approve':     ('اعتماد العطل', 'العطل'),
    'students.view':        ('عرض الطلبة ضمن نطاقه وملفاتهم', 'الطلبة'),
    'students.manage':      ('إدارة الطلبة (إضافة واستيراد ونقل)', 'الطلبة'),
    'structure.manage':     ('السنوات والفصول والمراحل والصفوف والشعب', 'الهيكل المدرسي'),
    'timetable.manage':     ('الحصص وجدول الشعب', 'الهيكل المدرسي'),
    'reports.view':         ('اللوحة والتقارير والمقارنة', 'التقارير'),
    'employees.manage':     ('إدارة سجل الموظفين (إضافة واستيراد وتعديل)', 'الموظفون'),
    'users.manage':         ('إدارة المستخدمين والأدوار', 'الإدارة'),
    'departments.manage':   ('إدارة أقسام الموظفين', 'الإدارة'),
    'settings.manage':      ('إعدادات المدرسة', 'الإدارة'),
    'audit.view':           ('سجل التدقيق', 'الإدارة'),
    'email_log.view':       ('سجل البريد وأخطائه', 'الإدارة'),
}

DEFAULT_ROLES = [
    ('admin', 'مدير النظام', 'كامل الصلاحيات', list(PERMISSIONS)),
    ('principal', 'المدير والإدارة', 'كل المراحل: العرض والاعتماد والعطل والتقارير والإشعارات',
     ['attendance.record', 'attendance.approve', 'attendance.view_all', 'class_leave.record', 'class_leave.approve',
      'guardians.notify', 'students.view', 'holidays.manage', 'holidays.approve', 'students.manage', 'reports.view',
      'employees.manage', 'email_log.view']),
    ('supervisor', 'المشرف', 'مراحله فقط: التسجيل والاعتماد والخروج من الحصة والإشعارات',
     ['attendance.record', 'attendance.approve', 'class_leave.record', 'class_leave.approve', 'guardians.notify',
      'holidays.manage', 'reports.view', 'students.view']),
    ('secretary', 'السكرتير', 'مراحله فقط: التسجيل وإدارة الطلبة والإشعارات، دون الاعتماد',
     ['attendance.record', 'class_leave.record', 'guardians.notify', 'students.view', 'students.manage']),
    ('teacher', 'المعلم', 'شعبه فقط: الخروج من الحصة والعودة، وعرض طلبته', ['class_leave.record', 'students.view']),
]


def seed_default_roles(tenant_id):
    """Create the default roles for a tenant (idempotent). Must run inside
    tenant_context(tenant_id)."""
    from extensions import db
    from models import Role, RolePermission
    existing = {r.key for r in Role.query.all()}
    for key, name, desc, perms in DEFAULT_ROLES:
        if key in existing:
            continue
        role = Role(key=key, name=name, description=desc, is_system=True)
        role.permissions = [RolePermission(permission_code=c) for c in perms]
        db.session.add(role)
    db.session.flush()
    from models import ClassLeaveType, DEFAULT_LEAVE_TYPES
    if not ClassLeaveType.query.first():
        for i, (name, back) in enumerate(DEFAULT_LEAVE_TYPES):
            db.session.add(ClassLeaveType(name=name, needs_return=back, sort_order=i))
        db.session.flush()


def current_user():
    return getattr(g, 'user', None)


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        user = current_user()
        if user is None:
            return redirect(url_for('auth.login', next=request.full_path))
        if user.must_change_password and request.endpoint not in (
                'auth.change_password', 'auth.logout'):
            return redirect(url_for('auth.change_password'))
        return f(*a, **kw)
    return wrapper


def permission_required(*codes):
    """Allow if the user has ANY of the given permission codes."""
    def deco(f):
        @wraps(f)
        @login_required
        def wrapper(*a, **kw):
            user = current_user()
            if not any(user.has_perm(c) for c in codes):
                abort(403)
            return f(*a, **kw)
        return wrapper
    return deco


def can_access_department(user, department_id, own_perm, all_perm=None):
    """Department-scoped check: with ``all_perm`` any department; with
    ``own_perm`` only departments the user is linked to."""
    if user is None:
        return False
    if all_perm and user.has_perm(all_perm):
        return True
    return user.has_perm(own_perm) and department_id in user.department_ids


def require_department(department_id, own_perm, all_perm=None):
    if not can_access_department(current_user(), department_id, own_perm, all_perm):
        abort(403)


def superadmin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if getattr(g, 'superadmin', None) is None:
            return redirect(url_for('super.login'))
        return f(*a, **kw)
    return wrapper


def has_perm(code):
    u = current_user()
    return bool(u and u.has_perm(code))
