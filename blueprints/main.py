from flask import Blueprint, render_template, g, redirect, url_for
from sqlalchemy import func

from authz import login_required, current_user
from extensions import db

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    if g.get('superadmin'):
        return redirect(url_for('super.tenants'))
    if not g.get('user'):
        return redirect(url_for('auth.login'))
    return redirect(url_for('main.home'))


def _kpi(label, n, url, hint='', tone=''):
    return {'label': label, 'n': n, 'url': url, 'hint': hint, 'tone': tone if n else 'zero'}


def has_perm(code):
    u = current_user()
    return bool(u and u.has_perm(code))


def dashboard_sections():
    """[(section title, [kpi…])] for what the signed-in user may see. Every number links to the list
    behind it, already filtered (query string or ?f=<column>:<value>)."""
    from models import User, EmailLog, UserRole, Employee
    from models import AttendanceRecord, ClassLeave, Holiday, Student
    from models.core import utcnow
    from services import scope
    from utils.school import today
    out = []
    user = current_user()
    t = today()
    vis = scope.visible_section_ids(user)

    def scoped(q):
        return q if vis is None else q.filter(Student.section_id.in_(list(vis) or [0]))

    if has_perm('attendance.record') or has_perm('attendance.approve') or has_perm('reports.view'):
        recs = scoped(AttendanceRecord.query.join(Student).filter(AttendanceRecord.record_date == t)).all()
        R = lambda **kw: url_for('attendance.records', date_from=t.isoformat(), date_to=t.isoformat(), **kw)  # noqa: E731
        live = [r for r in recs if r.status != 'rejected']
        k = [
            _kpi('غياب اليوم', sum(r.kind == 'absent' for r in live), R(kind='absent'), 'اضغط للقائمة', 'bad'),
            _kpi('غياب بعذر', sum(r.kind == 'excused' for r in live), R(kind='excused'), '', 'warn'),
            _kpi('تأخر اليوم', sum(r.kind == 'late' for r in live), R(kind='late'), '', 'warn'),
            _kpi('مغادرة مبكرة', sum(r.kind == 'early_leave' for r in live), R(kind='early_leave'), '', 'warn'),
        ]
        if has_perm('attendance.approve'):
            pend = scoped(AttendanceRecord.query.join(Student).filter(AttendanceRecord.status == 'pending')).count()
            k.append(_kpi('بانتظار الاعتماد', pend, url_for('attendance.records', status='pending',
                                                            date_from='2000-01-01'), 'كل الأيام', 'warn'))
        if has_perm('guardians.notify'):
            n = sum(1 for r in live if r.kind in ('absent', 'late', 'early_leave') and not r.notified_at
                    and r.student.guardian_email)
            k.append(_kpi('لم يُشعر أولياء أمورهم', n, url_for('attendance.notify_today'), 'إرسال يدوي بالبريد', 'bad'))
        out.append(('الغياب اليوم', k))
    if has_perm('class_leave.record') or has_perm('class_leave.approve'):
        lv = scoped(ClassLeave.query.join(Student).filter(ClassLeave.leave_date == t)).all()
        k = [_kpi('خارج الصف الآن', sum(1 for x in lv if x.is_out), url_for('leaves.index', out=1), 'لم يعودوا بعد', 'bad'),
             _kpi('خروج من الحصة اليوم', len(lv), url_for('leaves.index'), '', '')]
        if has_perm('class_leave.approve'):
            k.append(_kpi('خروج بانتظار الاعتماد', sum(1 for x in lv if x.status == 'pending'),
                          url_for('leaves.index', f='الحالة:بانتظار الاعتماد'), '', 'warn'))
        out.append(('الخروج من الحصة', k))
    if has_perm('students.view') or has_perm('students.manage'):
        sts = scope.students_query(user, include_inactive=True).all()
        L = lambda f: url_for('students.index', f=f)  # noqa: E731
        out.append(('الطلبة', [
            _kpi('طلبة فعّالون', sum(s.is_active for s in sts), L('الحالة:فعّال'), '', 'ok'),
            _kpi('بلا بريد لولي الأمر', sum(s.is_active and not s.guardian_email for s in sts), L('البريد:—'),
                 'لن يصلهم إشعار الغياب', 'warn'),
            _kpi('موقوفون', sum(not s.is_active for s in sts), L('الحالة:موقوف'), ''),
        ]))
    if has_perm('holidays.manage') or has_perm('holidays.approve'):
        hs = Holiday.query.filter(Holiday.end_date >= t).all()
        L = lambda f: url_for('holidays.index', f=f)  # noqa: E731
        out.append(('العطل', [
            _kpi('بانتظار الاعتماد', sum(h.status == 'pending' for h in hs), L('الحالة:بانتظار الاعتماد'), '', 'warn'),
            _kpi('عطل قادمة أو جارية', sum(h.status == 'approved' for h in hs), L('الحالة:معتمد'), '', 'ok'),
        ]))
    if has_perm('employees.manage'):
        emps = Employee.query.all()
        L = lambda f: url_for('employees.index', f=f)  # noqa: E731
        out.append(('الموظفون', [
            _kpi('موظفون فعّالون', sum(e.is_active for e in emps), L('الحالة:فعّال'), '', 'ok'),
            _kpi('بلا بريد', sum(not e.email for e in emps), L('البريد:—'), '', 'warn'),
            _kpi('بلا حساب دخول', sum(e.user_id is None for e in emps), L('الحساب:بلا حساب'),
                 'أنشئ حساباتهم من القائمة', ''),
        ]))
    if has_perm('users.manage'):
        users = User.query.all()
        now = utcnow()
        with_role = {uid for (uid,) in db.session.query(UserRole.user_id).distinct()}
        out.append(('المستخدمون', [
            _kpi('الحسابات الفعّالة', sum(u.is_active for u in users), url_for('admin.users', f='الحالة:فعّال'), 'اضغط للقائمة', 'ok'),
            _kpi('الحسابات الموقوفة', sum(not u.is_active for u in users), url_for('admin.users', f='الحالة:موقوف'), '', 'bad'),
            _kpi('مقفلة مؤقتًا', sum(bool(u.is_active and u.locked_until and u.locked_until > now) for u in users),
                 url_for('admin.users', f='الحالة:مقفل مؤقتًا'), 'محاولات دخول خاطئة', 'warn'),
            _kpi('بلا دور', sum(u.id not in with_role for u in users), url_for('admin.users'), 'لا يستطيعون أداء أي مهمة', 'warn'),
            _kpi('لم يدخلوا بعد', sum(u.last_login is None for u in users), url_for('admin.users'), 'أرسل لهم رابط كلمة المرور'),
        ]))
    if has_perm('email_log.view'):
        failed = EmailLog.query.filter(EmailLog.status != 'sent', EmailLog.acknowledged.is_(False)).count()
        sent = EmailLog.query.filter(EmailLog.status == 'sent').count()
        out.append(('البريد', [
            _kpi('أخطاء بريد جديدة', failed, url_for('admin.email_log', failed=1), 'رسائل لم تصل', 'bad'),
            _kpi('رسائل أُرسلت', sent, url_for('admin.email_log', f='الحالة:أُرسل'), '', 'ok'),
        ]))
    return out


@main_bp.route('/home')
@login_required
def home():
    return render_template('home.html', sections=dashboard_sections())
