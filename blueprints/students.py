"""Students: list (only the sections the user may see), add one, paste a list or import Excel/CSV, edit,
tick rows → activate / deactivate / move to a section / delete, Excel export, and the student's profile.
A student with attendance records or class leaves is never deleted (deactivate instead)."""
import csv
import io

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, g, Response
from sqlalchemy import func

from authz import permission_required, has_perm
from extensions import db
from tenancy import get_scoped
from models import (Student, Section, Grade, Stage, AttendanceRecord, ClassLeave, RECORD_KINDS)
from services import scope
from utils.audit import audit
from utils.export import xlsx_response
from utils.helpers import split_emails
from utils.school import form_ids, int_or_none, today
from utils.tabular import read_table, to_records, norm

students_bp = Blueprint('students', __name__)

FIELDS = [
    ('student_number', ['رقم الطالب', 'الرقم', 'الرقم المدرسي', 'student_number', 'number', 'id', 'code']),
    ('full_name', ['الاسم', 'اسم الطالب', 'الاسم الكامل', 'name', 'student', 'full_name']),
    ('full_name_en', ['الاسم بالإنجليزية', 'name_en', 'english name', 'full_name_en']),
    ('gender', ['الجنس', 'gender', 'sex']),
    ('grade', ['الصف', 'grade', 'class']),
    ('section', ['الشعبة', 'section', 'division']),
    ('guardian_name', ['ولي الأمر', 'اسم ولي الأمر', 'guardian', 'guardian_name', 'parent']),
    ('guardian_relation', ['صلة القرابة', 'القرابة', 'relation']),
    ('guardian_phone', ['هاتف ولي الأمر', 'الهاتف', 'الجوال', 'phone', 'mobile']),
    ('guardian_email', ['بريد ولي الأمر', 'البريد', 'البريد الإلكتروني', 'email', 'guardian_email']),
    ('notes', ['ملاحظات', 'notes']),
]
FIELD_LABELS = ['رقم الطالب', 'الاسم', 'الاسم بالإنجليزية', 'الجنس', 'الصف', 'الشعبة', 'ولي الأمر', 'صلة القرابة',
                'هاتف ولي الأمر', 'بريد ولي الأمر', 'ملاحظات']
GENDERS = {'m': 'ذكر', 'f': 'أنثى'}


def _gender(v):
    n = norm(v)
    if n in ('m', 'male', 'ذكر', 'ولد', 'طالب', 'boy'):
        return 'm'
    if n in ('f', 'female', 'انثي', 'بنت', 'طالبه', 'girl'):
        return 'f'
    return None


def get_student(student_id):
    s = get_scoped(Student, student_id)
    if s is None or not scope.can_act(g.user, s):
        abort(404)
    return s


def _sections():
    return scope.sections_query(g.user).join(Grade).join(Stage) \
        .order_by(Stage.sort_order, Grade.sort_order, Grade.id, Section.sort_order, Section.name).all()


def section_resolver(sections):
    """'السابع / أ', 'السابع أ', or grade + section columns → Section (Arabic-insensitive)."""
    idx = {}
    for s in sections:
        for key in (s.full_name, f'{s.grade.name} {s.name}', f'{s.grade.name}{s.name}', f'{s.grade.name}-{s.name}'):
            idx.setdefault(norm(key), s)
    return lambda grade, section: idx.get(norm(f'{grade or ""}{section or ""}')) or \
        (idx.get(norm(section)) if section else None)


def _apply(st, data, allowed_sections):
    errors = []
    name = (data.get('full_name') or '').strip()
    number = (data.get('student_number') or '').strip() or None
    if not name:
        errors.append('اسم الطالب مطلوب')
    sid = int_or_none(data.get('section_id'))
    if sid is None or sid not in allowed_sections:
        errors.append('اختر الشعبة')
    if number and Student.query.filter(Student.student_number == number, Student.id != (st.id or 0)).first():
        errors.append(f'رقم الطالب {number} مستخدم لطالب آخر')
    emails = (data.get('guardian_email') or '').strip()
    if emails and not split_emails(emails):
        errors.append(f'بريد ولي الأمر غير صالح: {emails}')
    if errors:
        return errors
    st.full_name, st.student_number, st.section_id = name[:200], number, sid
    st.full_name_en = (data.get('full_name_en') or '').strip()[:200] or None
    st.gender = _gender(data.get('gender')) if data.get('gender') else st.gender
    for k in ('guardian_name', 'guardian_relation', 'guardian_phone', 'notes'):
        if k in data:
            setattr(st, k, (data.get(k) or '').strip()[:500] or None)
    if 'guardian_email' in data:
        st.guardian_email = ', '.join(split_emails(emails)) or None
    if 'is_active' in data:
        st.is_active = data.get('is_active') == '1'
    return []


@students_bp.route('/')
@permission_required('students.view', 'students.manage')
def index():
    q = scope.students_query(g.user, include_inactive=True)
    sec = int_or_none(request.args.get('section'))
    if sec:
        q = q.filter(Student.section_id == sec)
    students = q.join(Section).join(Grade).join(Stage) \
        .order_by(Stage.sort_order, Grade.sort_order, Grade.id, Section.sort_order, Student.full_name).all()
    since = today().replace(day=1) if request.args.get('month') else None
    counts = dict(db.session.query(AttendanceRecord.student_id, func.count(AttendanceRecord.id))
                  .filter(AttendanceRecord.kind.in_(('absent', 'excused')), AttendanceRecord.status != 'rejected',
                          *( [AttendanceRecord.record_date >= since] if since else []))
                  .group_by(AttendanceRecord.student_id).all())
    if request.args.get('export') == 'xlsx':
        return xlsx_response('students.xlsx', FIELD_LABELS + ['الحالة', 'أيام الغياب'], [
            [s.student_number, s.full_name, s.full_name_en, GENDERS.get(s.gender, ''), s.section.grade.name,
             s.section.name, s.guardian_name, s.guardian_relation, s.guardian_phone, s.guardian_email, s.notes,
             'فعّال' if s.is_active else 'موقوف', counts.get(s.id, 0)] for s in students], 'الطلبة')
    return render_template('students/index.html', students=students, sections=_sections(), counts=counts,
                           section_id=sec, field_labels=FIELD_LABELS, genders=GENDERS)


@students_bp.route('/new', methods=['POST'])
@students_bp.route('/<int:student_id>/edit', methods=['GET', 'POST'])
@permission_required('students.manage')
def form(student_id=None):
    st = get_student(student_id) if student_id else Student(is_active=True)
    sections = _sections()
    errors = []
    if request.method == 'POST':
        errors = _apply(st, request.form, {s.id for s in sections})
        if not errors:
            if not student_id:
                db.session.add(st)
            db.session.flush()
            audit('student.created' if not student_id else 'student.updated', 'student', st.id, name=st.full_name)
            db.session.commit()
            flash('تم الحفظ', 'success')
            return redirect(url_for('students.profile', student_id=st.id) if student_id
                            else (request.referrer or url_for('students.index')))
        db.session.rollback()
        if not student_id:
            for e in errors:
                flash(e, 'danger')
            return redirect(request.referrer or url_for('students.index'))
        st = get_student(student_id)
    return render_template('students/form.html', s=st, sections=sections, errors=errors, genders=GENDERS), \
        (400 if errors else 200)


@students_bp.route('/<int:student_id>')
@permission_required('students.view', 'students.manage', 'attendance.record')
def profile(student_id):
    st = get_student(student_id)
    records = AttendanceRecord.query.filter_by(student_id=st.id) \
        .order_by(AttendanceRecord.record_date.desc()).all()
    leaves = ClassLeave.query.filter_by(student_id=st.id).order_by(ClassLeave.leave_date.desc(),
                                                                   ClassLeave.leave_time.desc()).limit(200).all()
    by_kind = {k: sum(1 for r in records if r.kind == k and r.status != 'rejected') for k, _ in RECORD_KINDS}
    return render_template('students/profile.html', s=st, records=records, leaves=leaves, by_kind=by_kind,
                           kinds=RECORD_KINDS, genders=GENDERS, today=today())


@students_bp.route('/import', methods=['POST'])
@permission_required('students.manage')
def import_list():
    """Paste or file. Matching on the student number: existing students are updated only when
    «تحديث الموجودين» is ticked, otherwise skipped. The section comes from the grade + section columns,
    or the default section chosen on the form."""
    try:
        rows = read_table(request.form.get('text'), request.files.get('file'))
    except ValueError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('students.index'))
    except Exception:  # noqa: BLE001 — a damaged spreadsheet
        flash('تعذّرت قراءة الملف — تأكد أنه ملف Excel أو CSV سليم', 'danger')
        return redirect(url_for('students.index'))
    records, _ = to_records(rows, FIELDS)
    if not records:
        flash('لا توجد بيانات للاستيراد', 'warning')
        return redirect(url_for('students.index'))
    sections = _sections()
    allowed = {s.id for s in sections}
    find = section_resolver(sections)
    default_sec = int_or_none(request.form.get('section_id'))
    update = request.form.get('update') == '1'
    added = updated = skipped = 0
    problems = []
    for i, rec in enumerate(records, 1):
        if not rec.get('full_name'):
            problems.append(f'سطر {i}: بلا اسم')
            continue
        st = Student.query.filter_by(student_number=rec['student_number']).first() \
            if rec.get('student_number') else None
        if st is not None and not update:
            skipped += 1
            continue
        if st is not None and not scope.can_act(g.user, st):
            problems.append(f'سطر {i} ({rec["full_name"]}): الطالب في شعبة خارج نطاقك')
            continue
        data = dict(rec)
        sec = find(rec.get('grade'), rec.get('section')) if (rec.get('grade') or rec.get('section')) else None
        data['section_id'] = sec.id if sec else (st.section_id if st else default_sec)
        if st is not None:
            for k in ('full_name_en', 'guardian_name', 'guardian_relation', 'guardian_phone', 'guardian_email',
                      'notes'):
                if not data.get(k):
                    data[k] = getattr(st, k) or ''
        target = st or Student(is_active=True)
        errs = _apply(target, data, allowed)
        if errs:
            problems.append(f'سطر {i} ({rec.get("full_name")}): ' + '، '.join(errs))
            continue
        if st is None:
            db.session.add(target)
            added += 1
        else:
            updated += 1
        db.session.flush()
    audit('student.imported', added=added, updated=updated, skipped=skipped, problems=len(problems))
    db.session.commit()
    flash(f'أُضيف {added}، حُدِّث {updated}، تُرك {skipped} (موجود مسبقًا)', 'success' if added or updated else 'warning')
    for p in problems[:15]:
        flash(p, 'danger')
    if len(problems) > 15:
        flash(f'… و{len(problems) - 15} أخطاء أخرى', 'danger')
    return redirect(url_for('students.index'))


@students_bp.route('/template.csv')
@permission_required('students.manage')
def template():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(FIELD_LABELS)
    w.writerow(['20231', 'أحمد محمد علي', 'Ahmad Mohammad Ali', 'ذكر', 'الصف السابع', 'أ', 'محمد علي', 'الأب',
                '0790000000', 'parent@example.org', ''])
    return Response('﻿' + buf.getvalue(), mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename=students_template.csv'})


@students_bp.route('/bulk', methods=['POST'])
@permission_required('students.manage')
def bulk():
    action = request.form.get('action')
    picked = [s for s in Student.query.filter(Student.id.in_(form_ids() or {0})).all() if scope.can_act(g.user, s)]
    done, skipped = 0, []
    if action in ('activate', 'deactivate'):
        for s in picked:
            s.is_active = action == 'activate'
            done += 1
    elif action == 'move':
        target = int_or_none(request.form.get('section_id'))
        if target not in {s.id for s in _sections()}:
            flash('اختر الشعبة التي يُنقل إليها الطلبة', 'danger')
            return redirect(request.referrer or url_for('students.index'))
        for s in picked:
            s.section_id = target
            done += 1
    elif action == 'delete':
        for s in picked:
            if AttendanceRecord.query.filter_by(student_id=s.id).first() or \
                    ClassLeave.query.filter_by(student_id=s.id).first():
                skipped.append(f'{s.full_name} (له سجلات — أوقفه بدلًا من حذفه)')
                continue
            audit('student.deleted', 'student', s.id, name=s.full_name)
            db.session.delete(s)
            done += 1
    else:
        abort(400)
    if action != 'delete':
        audit(f'student.bulk_{action}', count=done)
    db.session.commit()
    verb = {'activate': 'فُعِّل', 'deactivate': 'أُوقف', 'move': 'نُقل', 'delete': 'حُذف'}[action]
    flash(f'{verb}: {done}' + ((' — تُرك: ' + '، '.join(skipped[:10])) if skipped else ''),
          'success' if done else 'warning')
    return redirect(request.referrer or url_for('students.index'))


@students_bp.route('/search.json')
@permission_required('students.view', 'attendance.record', 'class_leave.record')
def search_json():
    from flask import jsonify
    q = (request.args.get('q') or '').strip().lower()
    query = scope.students_query(g.user)
    sec = int_or_none(request.args.get('section'))
    if sec:
        query = query.filter(Student.section_id == sec)
    if q:
        like = f'%{q}%'
        query = query.filter(func.lower(Student.full_name).like(like) |
                             func.lower(func.coalesce(Student.student_number, '')).like(like))
    return jsonify([{'id': s.id, 'name': s.full_name, 'number': s.student_number or '',
                     'section': s.section.full_name} for s in query.order_by(Student.full_name).limit(30)])


__all__ = ['students_bp', 'get_student', 'has_perm']
