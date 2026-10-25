from flask import Blueprint, render_template, g, redirect, url_for, request, session
from sqlalchemy import func

from authz import login_required, current_user
from extensions import db
from utils.i18n import _

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    if g.get('superadmin'):
        return redirect(url_for('super.tenants'))
    if not g.get('user'):
        return redirect(url_for('auth.login'))
    return redirect(url_for('main.home'))


@main_bp.route('/lang/<code>')
def set_lang(code):
    """Switch the interface language (kept in the session and a one-year cookie)."""
    from urllib.parse import urlparse
    code = code if code in ('ar', 'en') else 'ar'
    session['lang'] = code
    back = request.referrer or ''
    # only back to a page of this site (no open redirect)
    target = back if back and urlparse(back).netloc == request.host else url_for('main.index')
    resp = redirect(target)
    resp.set_cookie('lang', code, max_age=365 * 24 * 3600, samesite='Lax',
                    secure=request.is_secure, httponly=True)
    return resp


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
            _kpi(_('غياب اليوم'), sum(r.kind == 'absent' for r in live), R(kind='absent'), _('اضغط للقائمة'), 'bad'),
            _kpi(_('غياب بعذر'), sum(r.kind == 'excused' for r in live), R(kind='excused'), '', 'warn'),
            _kpi(_('تأخر اليوم'), sum(r.kind == 'late' for r in live), R(kind='late'), '', 'warn'),
            _kpi(_('مغادرة مبكرة'), sum(r.kind == 'early_leave' for r in live), R(kind='early_leave'), '', 'warn'),
        ]
        if has_perm('attendance.approve'):
            pend = scoped(AttendanceRecord.query.join(Student).filter(AttendanceRecord.status == 'pending')).count()
            k.append(_kpi(_('بانتظار الاعتماد'), pend, url_for('attendance.records', status='pending',
                                                            date_from='2000-01-01'), _('كل الأيام'), 'warn'))
        if has_perm('guardians.notify'):
            n = sum(1 for r in live if r.kind in ('absent', 'late', 'early_leave') and not r.notified_at
                    and r.student.guardian_email)
            k.append(_kpi(_('لم يُشعر أولياء أمورهم'), n, url_for('attendance.notify_today'), _('إرسال يدوي بالبريد'), 'bad'))
        out.append((_('الغياب اليوم'), k))
    if has_perm('class_leave.record') or has_perm('class_leave.approve'):
        lv = scoped(ClassLeave.query.join(Student).filter(ClassLeave.leave_date == t)).all()
        k = [_kpi(_('خارج الصف الآن'), sum(1 for x in lv if x.is_out), url_for('leaves.index', out=1), _('لم يعودوا بعد'), 'bad'),
             _kpi(_('خروج من الحصة اليوم'), len(lv), url_for('leaves.index'), '', '')]
        if has_perm('class_leave.approve'):
            k.append(_kpi(_('خروج بانتظار الاعتماد'), sum(1 for x in lv if x.status == 'pending'),
                          url_for('leaves.index', f=_('الحالة:بانتظار الاعتماد')), '', 'warn'))
        out.append((_('الخروج من الحصة'), k))
    if has_perm('students.view') or has_perm('students.manage'):
        sts = scope.students_query(user, include_inactive=True).all()
        L = lambda f: url_for('students.index', f=f)  # noqa: E731
        out.append((_('الطلبة'), [
            _kpi(_('طلبة فعّالون'), sum(s.is_active for s in sts), L(_('الحالة:فعّال')), '', 'ok'),
            _kpi(_('بلا بريد لولي الأمر'), sum(s.is_active and not s.guardian_email for s in sts), L(_('البريد:—')),
                 _('لن يصلهم إشعار الغياب'), 'warn'),
            _kpi(_('موقوفون'), sum(not s.is_active for s in sts), L(_('الحالة:موقوف')), ''),
        ]))
    if has_perm('holidays.manage') or has_perm('holidays.approve'):
        hs = Holiday.query.filter(Holiday.end_date >= t).all()
        L = lambda f: url_for('holidays.index', f=f)  # noqa: E731
        out.append((_('العطل'), [
            _kpi(_('بانتظار الاعتماد'), sum(h.status == 'pending' for h in hs), L(_('الحالة:بانتظار الاعتماد')), '', 'warn'),
            _kpi(_('عطل قادمة أو جارية'), sum(h.status == 'approved' for h in hs), L(_('الحالة:معتمد')), '', 'ok'),
        ]))
    if has_perm('employees.manage'):
        emps = Employee.query.all()
        L = lambda f: url_for('employees.index', f=f)  # noqa: E731
        out.append((_('الموظفون'), [
            _kpi(_('موظفون فعّالون'), sum(e.is_active for e in emps), L(_('الحالة:فعّال')), '', 'ok'),
            _kpi(_('بلا بريد'), sum(not e.email for e in emps), L(_('البريد:—')), '', 'warn'),
            _kpi(_('بلا حساب دخول'), sum(e.user_id is None for e in emps), L(_('الحساب:بلا حساب')),
                 _('أنشئ حساباتهم من القائمة'), ''),
        ]))
    if has_perm('users.manage'):
        users = User.query.all()
        now = utcnow()
        with_role = {uid for (uid,) in db.session.query(UserRole.user_id).distinct()}
        out.append((_('المستخدمون'), [
            _kpi(_('الحسابات الفعّالة'), sum(u.is_active for u in users), url_for('admin.users', f=_('الحالة:فعّال')), _('اضغط للقائمة'), 'ok'),
            _kpi(_('الحسابات الموقوفة'), sum(not u.is_active for u in users), url_for('admin.users', f=_('الحالة:موقوف')), '', 'bad'),
            _kpi(_('مقفلة مؤقتًا'), sum(bool(u.is_active and u.locked_until and u.locked_until > now) for u in users),
                 url_for('admin.users', f=_('الحالة:مقفل مؤقتًا')), _('محاولات دخول خاطئة'), 'warn'),
            _kpi(_('بلا دور'), sum(u.id not in with_role for u in users), url_for('admin.users'), _('لا يستطيعون أداء أي مهمة'), 'warn'),
            _kpi(_('لم يدخلوا بعد'), sum(u.last_login is None for u in users), url_for('admin.users'), _('أرسل لهم رابط كلمة المرور')),
        ]))
    if has_perm('email_log.view'):
        failed = EmailLog.query.filter(EmailLog.status != 'sent', EmailLog.acknowledged.is_(False)).count()
        sent = EmailLog.query.filter(EmailLog.status == 'sent').count()
        out.append((_('البريد'), [
            _kpi(_('أخطاء بريد جديدة'), failed, url_for('admin.email_log', failed=1), _('رسائل لم تصل'), 'bad'),
            _kpi(_('رسائل أُرسلت'), sent, url_for('admin.email_log', f=_('الحالة:أُرسل')), '', 'ok'),
        ]))
    return out


@main_bp.route('/home')
@login_required
def home():
    return render_template('home.html', sections=dashboard_sections())
