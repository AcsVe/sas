"""The timetable of a section: for each school day of its stage, the subject and teacher of every period.
Linking a teacher account to a period gives that teacher the section's students (scope.section_ids).
The bell periods themselves are managed in «الهيكل المدرسي ← الحصص». Import from AcsVe/Timetable comes later."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, g

from authz import permission_required, login_required
from extensions import db
from tenancy import get_scoped
from models import Period, TimetableEntry, Section, User, Role, UserRole, WEEKDAYS
from utils.audit import audit
from utils.school import int_or_none
from utils.i18n import _

timetable_bp = Blueprint('timetable', __name__)


def _teachers():
    teacher = Role.query.filter_by(key='teacher').first()
    ids = {ur.user_id for ur in UserRole.query.filter_by(role_id=teacher.id).all()} if teacher else set()
    users = User.query.filter(User.is_active.is_(True)).order_by(User.full_name).all()
    return sorted(users, key=lambda u: (u.id not in ids, u.full_name))


@timetable_bp.route('/section/<int:section_id>', methods=['GET', 'POST'])
@permission_required('timetable.manage')
def section(section_id):
    sec = get_scoped(Section, section_id)
    if sec is None:
        abort(404)
    from services.periods import grade_week
    stage = sec.grade.stage
    week = grade_week(sec.grade)             # the grade's own periods where it has them, else the stage's
    periods = [p for d in week.values() for p in d]
    entries = {e.period_id: e for e in TimetableEntry.query.filter_by(section_id=sec.id).all()}
    if request.method == 'POST':
        teachers = {u.id: u for u in _teachers()}
        changed = 0
        for p in periods:
            if p.kind != 'period':
                continue
            subject = (request.form.get(f'subject_{p.id}') or '').strip()[:200]
            tid = int_or_none(request.form.get(f'teacher_{p.id}'))
            tid = tid if tid in teachers else None
            tname = (request.form.get(f'tname_{p.id}') or '').strip()[:200] or \
                (teachers[tid].full_name if tid else '')
            e = entries.get(p.id)
            if not subject and not tid and not tname:
                if e is not None:
                    db.session.delete(e)
                    changed += 1
                continue
            if e is None:
                e = TimetableEntry(section_id=sec.id, period_id=p.id)
                db.session.add(e)
            if (e.subject, e.teacher_user_id, e.teacher_name) != (subject or None, tid, tname or None):
                e.subject, e.teacher_user_id, e.teacher_name = subject or None, tid, tname or None
                changed += 1
        audit('timetable.saved', 'section', sec.id, changes=changed)
        db.session.commit()
        flash(_('حُفظ الجدول ({changed} تغيير)', changed=changed), 'success')
        return redirect(url_for('timetable.section', section_id=sec.id))
    days = [(d, n) for d, n in WEEKDAYS if d in stage.day_list]
    by_day = week
    return render_template('timetable/section.html', sec=sec, days=days, by_day=by_day, entries=entries,
                           teachers=_teachers())


@timetable_bp.route('/mine')
@login_required
def mine():
    rows = TimetableEntry.query.filter_by(teacher_user_id=g.user.id).all()
    periods = {p.id: p for p in Period.query.filter(Period.id.in_([e.period_id for e in rows] or [0])).all()}
    rows.sort(key=lambda e: ([d for d, _x in WEEKDAYS].index(periods[e.period_id].weekday),
                             periods[e.period_id].start_time))
    sections = {s.id: s for s in Section.query.filter(Section.id.in_([e.section_id for e in rows] or [0])).all()}
    return render_template('timetable/mine.html', rows=rows, periods=periods, sections=sections,
                           day_names=dict(WEEKDAYS))
