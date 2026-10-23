"""Attendance register: the daily sheet of a section (tick the absent students in one go), the records list
(filters, approve / reject / delete / notify the guardians of the ticked rows, Excel export), one record with
its attachment (stored on disk), and the manual guardian e-mail (preview → send).

Every record a user touches goes through scope.can_act(): a supervisor can never read or change another
stage's students by sending other ids."""
import os

from flask import (Blueprint, render_template, request, redirect, url_for, flash, abort, g, send_from_directory)

from authz import permission_required, has_perm
from extensions import db
from tenancy import get_scoped
from models import (AttendanceRecord, Attachment, ClassLeave, Student, Section, Grade, Stage, RECORD_KINDS,
                    STATUSES)
from models.core import utcnow
from services import scope, notify
from services.calendar import holiday_for, approved_holidays
from utils.audit import audit
from utils.export import xlsx_response
from utils.school import (today, form_ids, int_or_none, parse_day, clean_time, save_attachment,
                          delete_attachment_file, attachment_dir)

attendance_bp = Blueprint('attendance', __name__)
KIND_KEYS = [k for k, _ in RECORD_KINDS]


def _sections():
    return scope.sections_query(g.user).join(Grade).join(Stage) \
        .order_by(Stage.sort_order, Grade.sort_order, Grade.id, Section.sort_order, Section.name).all()


def _get_record(rec_id):
    r = get_scoped(AttendanceRecord, rec_id)
    if r is None or not scope.can_act(g.user, r.student):
        abort(404)
    return r


def _initial_status(record):
    if g.user.has_perm('attendance.approve'):
        record.status, record.approved_by, record.approved_at = 'approved', g.user.id, utcnow()
    else:
        record.status = 'pending'


def _apply(rec, data):
    errors = []
    kind = data.get('kind') or 'absent'
    if kind not in KIND_KEYS:
        errors.append('نوع السجل غير صالح')
    day = parse_day(data.get('record_date'))
    if day is None:
        errors.append('التاريخ مطلوب (الصيغة 2026-12-31)')
    all_day = data.get('all_day', '1') == '1'
    t_from, t_to = clean_time(data.get('time_from')), clean_time(data.get('time_to'))
    if not all_day and not t_from:
        errors.append('حدّد الوقت، أو اختر «طوال اليوم»')
    if t_from and t_to and t_to <= t_from:
        errors.append('وقت النهاية يجب أن يكون بعد وقت البداية')
    if errors:
        return errors
    dup = AttendanceRecord.query.filter(AttendanceRecord.student_id == rec.student_id,
                                        AttendanceRecord.record_date == day, AttendanceRecord.kind == kind,
                                        AttendanceRecord.id != (rec.id or 0)).first()
    if dup:
        return [f'يوجد سجل «{dup.kind_label}» للطالب في {day.isoformat()}']
    rec.kind, rec.record_date, rec.all_day = kind, day, all_day
    rec.time_from, rec.time_to = (None, None) if all_day else (t_from, t_to)
    rec.notes = (data.get('notes') or '').strip()[:1000] or None
    return []


# ── daily sheet ──────────────────────────────────────────────────────────────────────────────────────────
@attendance_bp.route('/daily', methods=['GET', 'POST'])
@permission_required('attendance.record')
def daily():
    sections = _sections()
    sec_id = int_or_none(request.values.get('section'))
    section = next((s for s in sections if s.id == sec_id), None)
    if sec_id and section is None:
        abort(404)                       # a section outside the user's scope
    if section is None and len(sections) == 1 and request.method == 'GET':
        section = sections[0]
    day = parse_day(request.values.get('date'), today())
    if request.method == 'POST':
        if section is None:
            abort(404)
        kind = request.form.get('kind') or 'absent'
        picked = {int(x) for x in request.form.getlist('student_ids') if x.isdigit()}
        students = [s for s in Student.query.filter(Student.id.in_(picked or {0}),
                                                    Student.section_id == section.id).all()]
        made, dup, errors = 0, 0, []
        for st in students:
            rec = AttendanceRecord(student_id=st.id, created_by=g.user.id)
            errs = _apply(rec, {'kind': kind, 'record_date': day.isoformat(),
                                'all_day': request.form.get('all_day', '1'),
                                'time_from': request.form.get('time_from'), 'time_to': request.form.get('time_to'),
                                'notes': request.form.get('notes')})
            if errs:
                if errs[0].startswith('يوجد سجل'):
                    dup += 1
                    continue
                errors = errs
                break
            _initial_status(rec)
            db.session.add(rec)
            made += 1
        if errors:
            db.session.rollback()
            for e in errors:
                flash(e, 'danger')
        else:
            audit('attendance.daily', 'section', section.id, date=day.isoformat(), kind=kind, count=made)
            db.session.commit()
            msg = f'سُجّل {made}' + (f' — {dup} مسجّل مسبقًا' if dup else '')
            flash(msg, 'success' if made else 'warning')
        return redirect(url_for('attendance.daily', section=section.id, date=day.isoformat()))
    students, existing, holiday, leaves_out = [], {}, None, {}
    if section is not None:
        students = Student.query.filter_by(section_id=section.id, is_active=True).order_by(Student.full_name).all()
        for r in AttendanceRecord.query.filter(AttendanceRecord.record_date == day,
                                               AttendanceRecord.student_id.in_([s.id for s in students] or [0])):
            existing.setdefault(r.student_id, []).append(r)
        holiday = holiday_for(day, section, approved_holidays(day, day))
        for lv in ClassLeave.query.filter(ClassLeave.leave_date == day,
                                          ClassLeave.student_id.in_([s.id for s in students] or [0])).all():
            if lv.is_out:
                leaves_out[lv.student_id] = lv
    school_day = section is None or day.weekday() in section.grade.stage.day_list
    return render_template('attendance/daily.html', sections=sections, section=section, day=day,
                           students=students, existing=existing, holiday=holiday, school_day=school_day,
                           leaves_out=leaves_out, kinds=RECORD_KINDS)


# ── records list ─────────────────────────────────────────────────────────────────────────────────────────
def _records_query():
    a = request.args
    q = AttendanceRecord.query.join(Student)
    ids = scope.visible_section_ids(g.user)
    if ids is not None:
        q = q.filter(Student.section_id.in_(list(ids) or [0]))
    d_from = parse_day(a.get('date_from'), today())
    d_to = parse_day(a.get('date_to')) if a.get('date_from') else parse_day(a.get('date_to'), today())
    q = q.filter(AttendanceRecord.record_date >= d_from)
    if d_to:
        q = q.filter(AttendanceRecord.record_date <= d_to)
    if a.get('kind') in KIND_KEYS:
        q = q.filter(AttendanceRecord.kind == a['kind'])
    if a.get('status') in dict(STATUSES):
        q = q.filter(AttendanceRecord.status == a['status'])
    if int_or_none(a.get('student')):
        q = q.filter(AttendanceRecord.student_id == int(a['student']))
    if int_or_none(a.get('section')):
        q = q.filter(Student.section_id == int(a['section']))
    if int_or_none(a.get('stage')):
        q = q.join(Section, Student.section_id == Section.id).join(Grade).filter(Grade.stage_id == int(a['stage']))
    if a.get('notified') == '0':
        q = q.filter(AttendanceRecord.notified_at.is_(None))
    return q.order_by(AttendanceRecord.record_date.desc(), Student.full_name), d_from, d_to


@attendance_bp.route('/records')
@permission_required('attendance.record', 'attendance.approve', 'reports.view')
def records():
    q, d_from, d_to = _records_query()
    rows = q.all()
    if request.args.get('export') == 'xlsx':
        return xlsx_response('attendance.xlsx',
                             ['التاريخ', 'الطالب', 'رقم الطالب', 'المرحلة', 'الصف', 'الشعبة', 'النوع', 'الوقت',
                              'الحالة', 'ملاحظات', 'سجّله', 'إشعار ولي الأمر'],
                             [[r.record_date.isoformat(), r.student.full_name, r.student.student_number,
                               r.student.section.grade.stage.name, r.student.section.grade.name,
                               r.student.section.name, r.kind_label,
                               'طوال اليوم' if r.all_day else f'{r.time_from or ""} {r.time_to or ""}'.strip(),
                               r.status_label, r.notes, r.creator.full_name if r.creator else '',
                               r.notified_at.strftime('%Y-%m-%d %H:%M') if r.notified_at else ''] for r in rows],
                             'السجلات')
    return render_template('attendance/records.html', rows=rows, d_from=d_from, d_to=d_to, kinds=RECORD_KINDS,
                           statuses=STATUSES, sections=_sections(), args=request.args)


@attendance_bp.route('/records/bulk', methods=['POST'])
@permission_required('attendance.record', 'attendance.approve')
def records_bulk():
    action = request.form.get('action')
    picked = [r for r in AttendanceRecord.query.filter(AttendanceRecord.id.in_(form_ids() or {0})).all()
              if scope.can_act(g.user, r.student)]
    back = request.referrer or url_for('attendance.records')
    if action == 'notify':
        if not has_perm('guardians.notify'):
            abort(403)
        return _notify_preview(picked)
    done, skipped = 0, []
    if action in ('approve', 'reject'):
        if not has_perm('attendance.approve'):
            abort(403)
        for r in picked:
            r.status = 'approved' if action == 'approve' else 'rejected'
            r.approved_by, r.approved_at = g.user.id, utcnow()
            done += 1
    elif action == 'delete':
        for r in picked:
            if r.status == 'approved' and not has_perm('attendance.approve'):
                skipped.append(f'{r.student.full_name} (معتمد — يحذفه من يملك الاعتماد)')
                continue
            att = r.attachment
            audit('attendance.deleted', 'attendance', r.id, student=r.student_id, date=r.record_date.isoformat())
            db.session.delete(r)
            if att is not None and not ClassLeave.query.filter_by(attachment_id=att.id).first():
                delete_attachment_file(att)
                db.session.delete(att)
            done += 1
    else:
        abort(400)
    if action != 'delete':
        audit(f'attendance.bulk_{action}', count=done)
    db.session.commit()
    verb = {'approve': 'اعتُمد', 'reject': 'رُفض', 'delete': 'حُذف'}[action]
    flash(f'{verb}: {done}' + ((' — تُرك: ' + '، '.join(skipped[:10])) if skipped else ''),
          'success' if done else 'warning')
    return redirect(back)


# ── one record ───────────────────────────────────────────────────────────────────────────────────────────
@attendance_bp.route('/record/new', methods=['GET', 'POST'])
@attendance_bp.route('/record/<int:rec_id>', methods=['GET', 'POST'])
@permission_required('attendance.record')
def record_form(rec_id=None):
    if rec_id:
        rec = _get_record(rec_id)
        student = rec.student
    else:
        student = get_scoped(Student, int_or_none(request.values.get('student')) or 0)
        if student is None or not scope.can_act(g.user, student):
            abort(404)
        rec = AttendanceRecord(student_id=student.id, record_date=parse_day(request.args.get('date'), today()),
                               kind=request.args.get('kind') or 'absent', all_day=True)
    if rec_id and rec.status == 'approved' and not has_perm('attendance.approve'):
        flash('السجل معتمد؛ يعدّله من يملك صلاحية الاعتماد', 'warning')
        return redirect(url_for('students.profile', student_id=student.id))
    errors = []
    if request.method == 'POST':
        errors = _apply(rec, request.form)
        if not errors:
            try:
                att = save_attachment(request.files.get('attachment'))
            except ValueError as exc:
                errors = [str(exc)]
                att = None
        if not errors:
            old = rec.attachment if (att is not None or request.form.get('remove_attachment') == '1') else None
            if att is not None:
                rec.attachment_id = att.id
            elif request.form.get('remove_attachment') == '1':
                rec.attachment_id = None
            if not rec_id:
                rec.created_by = g.user.id
                _initial_status(rec)
                db.session.add(rec)
            db.session.flush()
            if old is not None and not ClassLeave.query.filter_by(attachment_id=old.id).first() and \
                    not AttendanceRecord.query.filter_by(attachment_id=old.id).first():
                delete_attachment_file(old)
                db.session.delete(old)
            audit('attendance.created' if not rec_id else 'attendance.updated', 'attendance', rec.id,
                  student=student.id, kind=rec.kind, date=rec.record_date.isoformat())
            db.session.commit()
            flash('تم الحفظ', 'success')
            return redirect(request.form.get('next') or url_for('students.profile', student_id=student.id))
        db.session.rollback()
        if rec_id:
            rec = _get_record(rec_id)
    return render_template('attendance/record_form.html', r=rec, s=student, errors=errors, kinds=RECORD_KINDS,
                           next=request.values.get('next', '')), (400 if errors else 200)


@attendance_bp.route('/attachment/<int:att_id>')
@permission_required('attendance.record', 'attendance.approve', 'class_leave.record', 'students.view')
def attachment(att_id):
    att = get_scoped(Attachment, att_id)
    if att is None:
        abort(404)
    owners = [r.student for r in AttendanceRecord.query.filter_by(attachment_id=att.id).all()] + \
             [lv.student for lv in ClassLeave.query.filter_by(attachment_id=att.id).all()]
    if not any(scope.can_act(g.user, s) for s in owners):
        abort(404)
    return send_from_directory(attachment_dir(att.tenant_id), att.stored_name, download_name=att.original_name,
                               as_attachment=not (att.mime or '').startswith(('image/', 'application/pdf')))


# ── guardian e-mail (manual) ─────────────────────────────────────────────────────────────────────────────
def _notify_preview(records, resend=False):
    p = notify.plan(records, resend=resend)
    return render_template('attendance/notify.html', plan=p, ids=[r.id for r in records], resend=resend)


@attendance_bp.route('/notify', methods=['GET', 'POST'])
@permission_required('guardians.notify')
def notify_today():
    """All of today's absences (within the user's scope) → preview."""
    day = parse_day(request.values.get('date'), today())
    q = AttendanceRecord.query.join(Student).filter(AttendanceRecord.record_date == day,
                                                    AttendanceRecord.kind.in_(('absent', 'late', 'early_leave')))
    ids = scope.visible_section_ids(g.user)
    if ids is not None:
        q = q.filter(Student.section_id.in_(list(ids) or [0]))
    return _notify_preview(q.all(), resend=request.values.get('resend') == '1')


@attendance_bp.route('/notify/send', methods=['POST'])
@permission_required('guardians.notify')
def notify_send():
    resend = request.form.get('resend') == '1'
    records = [r for r in AttendanceRecord.query.filter(AttendanceRecord.id.in_(form_ids() or {0})).all()
               if scope.can_act(g.user, r.student)]
    p = notify.plan(records, resend=resend)
    if request.form.get('preview') == '1':
        return _notify_preview(records, resend=resend)
    sent, failed = notify.send(p)
    audit('guardians.notified', count=sent, failed=len(failed))
    db.session.commit()
    if sent:
        flash(f'أُرسل الإشعار إلى أولياء أمور {sent} من الطلبة', 'success')
    for name, err in failed[:10]:
        flash(f'تعذّر الإرسال لولي أمر {name}: {err}', 'danger')
    if not sent and not failed:
        flash('لا يوجد ما يُرسل (الكل بلا بريد أو أُشعر مسبقًا)', 'warning')
    return redirect(url_for('attendance.records', notified='0' if failed else None))


__all__ = ['attendance_bp', 'os']
