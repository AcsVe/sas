"""Leaving the class (library, clinic, counselling…) and coming back. A teacher records it for the students of
their sections; «خارج الصف الآن» lists who has not come back yet; tick rows → returned now / approve /
reject / delete."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, g

from authz import permission_required, has_perm
from extensions import db
from tenancy import get_scoped
from models import ClassLeave, ClassLeaveType, Student, Section, Grade, Stage, STATUSES
from services import scope
from utils.audit import audit
from utils.export import xlsx_response
from utils.school import today, now_hhmm, form_ids, int_or_none, parse_day, clean_time
from utils.i18n import _

leaves_bp = Blueprint('leaves', __name__)


def _sections():
    return scope.sections_query(g.user).join(Grade).join(Stage) \
        .order_by(Stage.sort_order, Grade.sort_order, Grade.id, Section.sort_order, Section.name).all()


def _types():
    return ClassLeaveType.query.filter_by(is_active=True).order_by(ClassLeaveType.sort_order,
                                                                  ClassLeaveType.id).all()


def _get(leave_id):
    lv = get_scoped(ClassLeave, leave_id)
    if lv is None or not scope.can_act(g.user, lv.student):
        abort(404)
    return lv


def _query(day, d_to=None):
    q = ClassLeave.query.join(Student).filter(ClassLeave.leave_date >= day, ClassLeave.leave_date <= (d_to or day))
    ids = scope.visible_section_ids(g.user)
    if ids is not None:
        q = q.filter(Student.section_id.in_(list(ids) or [0]))
    return q


@leaves_bp.route('/')
@permission_required('class_leave.record', 'class_leave.approve')
def index():
    day = parse_day(request.args.get('date'), today())
    d_to = parse_day(request.args.get('date_to'))
    sections = _sections()
    sec_id = int_or_none(request.args.get('section'))
    section = next((s for s in sections if s.id == sec_id), None)
    q = _query(day, d_to)
    if section:
        q = q.filter(Student.section_id == section.id)
    rows = q.order_by(ClassLeave.leave_date.desc(), ClassLeave.leave_time.desc()).all()
    if request.args.get('out') == '1':
        rows = [r for r in rows if r.is_out]
    if request.args.get('export') == 'xlsx':
        return xlsx_response('class_leaves.xlsx',
                             [_('التاريخ'), _('الطالب'), _('الشعبة'), _('النوع'), _('الخروج'), _('العودة'), _('الحالة'), _('ملاحظات'), _('سجّله')],
                             [[r.leave_date.isoformat(), r.student.full_name, r.student.section.full_name,
                               r.leave_type.name, r.leave_time, r.return_time or (_('خارج الصف') if r.is_out else ''),
                               r.status_label, r.notes, r.creator.full_name if r.creator else ''] for r in rows],
                             _('الخروج من الحصة'))
    students = Student.query.filter_by(section_id=section.id, is_active=True).order_by(Student.full_name).all() \
        if section else []
    return render_template('leaves/index.html', rows=rows, day=day, d_to=d_to, sections=sections,
                           section=section, students=students, types=_types(), now=now_hhmm(),
                           out_only=request.args.get('out') == '1')


@leaves_bp.route('/new', methods=['POST'])
@permission_required('class_leave.record')
def create():
    section_id = int_or_none(request.form.get('section'))
    if section_id not in {s.id for s in _sections()}:
        abort(404)
    picked = [s for s in Student.query.filter(Student.id.in_(form_ids('student_ids') or {0}),
                                              Student.section_id == section_id).all()]
    lt = get_scoped(ClassLeaveType, int_or_none(request.form.get('leave_type_id')) or 0)
    t = clean_time(request.form.get('leave_time')) or now_hhmm()
    day = parse_day(request.form.get('leave_date'), today())
    back = url_for('leaves.index', section=section_id, date=day.isoformat())
    if lt is None or not picked:
        flash(_('اختر نوع الخروج وطالبًا واحدًا على الأقل'), 'danger')
        return redirect(back)
    approve = has_perm('class_leave.approve')
    for st in picked:
        lv = ClassLeave(student_id=st.id, leave_type_id=lt.id, leave_date=day, leave_time=t,
                        notes=(request.form.get('notes') or '').strip()[:500] or None, created_by=g.user.id,
                        status='approved' if approve else 'pending', approved_by=g.user.id if approve else None)
        if not lt.needs_return:
            lv.return_time = None
        db.session.add(lv)
    audit('class_leave.created', 'section', section_id, count=len(picked), type=lt.name)
    db.session.commit()
    flash(_('سُجّل خروج {v}: {name}', v=len(picked), name=lt.name), 'success')
    return redirect(back)


@leaves_bp.route('/bulk', methods=['POST'])
@permission_required('class_leave.record', 'class_leave.approve')
def bulk():
    action = request.form.get('action')
    picked = [lv for lv in ClassLeave.query.filter(ClassLeave.id.in_(form_ids() or {0})).all()
              if scope.can_act(g.user, lv.student)]
    done, skipped = 0, []
    if action == 'return':
        t = now_hhmm()
        for lv in picked:
            if lv.is_out:
                lv.return_time = t if t > lv.leave_time else lv.leave_time
                done += 1
    elif action in ('approve', 'reject'):
        if not has_perm('class_leave.approve'):
            abort(403)
        for lv in picked:
            lv.status = 'approved' if action == 'approve' else 'rejected'
            lv.approved_by = g.user.id
            done += 1
    elif action == 'delete':
        for lv in picked:
            if lv.status == 'approved' and not has_perm('class_leave.approve'):
                skipped.append(_('{full_name} (معتمد)', full_name=lv.student.full_name))
                continue
            audit('class_leave.deleted', 'class_leave', lv.id, student=lv.student_id)
            db.session.delete(lv)
            done += 1
    else:
        abort(400)
    if action != 'delete':
        audit(f'class_leave.bulk_{action}', count=done)
    db.session.commit()
    verb = {'return': _('سُجّلت العودة'), 'approve': _('اعتُمد'), 'reject': _('رُفض'), 'delete': _('حُذف')}[action]
    flash(f'{verb}: {done}' + ((_(' — تُرك: ') + _('، ').join(skipped[:10])) if skipped else ''),
          'success' if done else 'warning')
    return redirect(request.referrer or url_for('leaves.index'))


@leaves_bp.route('/<int:leave_id>', methods=['GET', 'POST'])
@permission_required('class_leave.record', 'class_leave.approve')
def edit(leave_id):
    lv = _get(leave_id)
    errors = []
    if request.method == 'POST':
        lt = get_scoped(ClassLeaveType, int_or_none(request.form.get('leave_type_id')) or 0)
        t = clean_time(request.form.get('leave_time'))
        back = clean_time(request.form.get('return_time'))
        day = parse_day(request.form.get('leave_date'))
        if lt is None or t is None or day is None:
            errors.append(_('النوع والتاريخ ووقت الخروج مطلوبة'))
        elif back and back < t:
            errors.append(_('وقت العودة قبل وقت الخروج'))
        if not errors:
            lv.leave_type_id, lv.leave_time, lv.return_time, lv.leave_date = lt.id, t, back, day
            lv.notes = (request.form.get('notes') or '').strip()[:500] or None
            if has_perm('class_leave.approve') and request.form.get('status') in dict(STATUSES):
                lv.status = request.form['status']
            audit('class_leave.updated', 'class_leave', lv.id)
            db.session.commit()
            flash(_('تم الحفظ'), 'success')
            return redirect(url_for('leaves.index', section=lv.student.section_id, date=lv.leave_date.isoformat()))
        db.session.rollback()
    return render_template('leaves/form.html', lv=lv, types=ClassLeaveType.query.order_by(
        ClassLeaveType.sort_order).all(), statuses=STATUSES, errors=errors), (400 if errors else 200)
