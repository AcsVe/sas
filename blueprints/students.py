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
from utils.i18n import _, label
from utils.tabular import norm

students_bp = Blueprint('students', __name__)

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


def _apply(st, data, allowed_sections):
    errors = []
    name = (data.get('full_name') or '').strip()
    number = (data.get('student_number') or '').strip() or None
    if not name:
        errors.append(_('اسم الطالب مطلوب'))
    sid = int_or_none(data.get('section_id'))
    if sid is None or sid not in allowed_sections:
        errors.append(_('اختر الشعبة'))
    if number and Student.query.filter(Student.student_number == number, Student.id != (st.id or 0)).first():
        errors.append(_('رقم الطالب {number} مستخدم لطالب آخر', number=number))
    emails = (data.get('guardian_email') or '').strip()
    if emails and not split_emails(emails):
        errors.append(_('بريد ولي الأمر غير صالح: {emails}', emails=emails))
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
        return xlsx_response('students.xlsx', FIELD_LABELS + [_('الحالة'), _('أيام الغياب')], [
            [s.student_number, s.full_name, s.full_name_en, _(GENDERS.get(s.gender, '')), label(s.section.grade),
             s.section.name, s.guardian_name, s.guardian_relation, s.guardian_phone, s.guardian_email, s.notes,
             _('فعّال') if s.is_active else _('موقوف'), counts.get(s.id, 0)] for s in students], _('الطلبة'))
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
            flash(_('تم الحفظ'), 'success')
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
    by_kind = {k: sum(1 for r in records if r.kind == k and r.status != 'rejected') for k, _x in RECORD_KINDS}
    return render_template('students/profile.html', s=st, records=records, leaves=leaves, by_kind=by_kind,
                           kinds=RECORD_KINDS, genders=GENDERS, today=today())


def _import_dir():
    import os
    from flask import current_app
    import time
    path = os.path.join(current_app.config['UPLOAD_DIR'], 'tenants', str(g.tenant.id), 'imports')
    os.makedirs(path, exist_ok=True)
    for f in os.listdir(path):                     # previews left unfinished for a day
        full = os.path.join(path, f)
        try:
            if time.time() - os.path.getmtime(full) > 86400:
                os.remove(full)
        except OSError:
            pass
    return path


def _plan_path(token):
    import os
    import re as _re
    if not _re.fullmatch(r'[0-9a-f]{32}', token or ''):
        abort(404)
    return os.path.join(_import_dir(), f'students-{token}-u{g.user.id}.json')


@students_bp.route('/import', methods=['GET', 'POST'])
@permission_required('students.manage')
def import_list():
    """Step 1: the file (or a pasted list). Step 2: the preview — nothing is written until «تنفيذ»."""
    import json
    import uuid
    from utils.tabular import read_sheets
    if request.method == 'GET':
        return render_template('students/import.html', sections=_sections(), plan=None)
    try:
        sheets = read_sheets(request.form.get('text'), request.files.get('file'))
    except ValueError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('students.import_list'))
    except Exception:  # noqa: BLE001 — a damaged spreadsheet
        flash(_('تعذّرت قراءة الملف — تأكد أنه ملف Excel أو CSV سليم'), 'danger')
        return redirect(url_for('students.import_list'))
    token = uuid.uuid4().hex
    with open(_plan_path(token).replace('.json', '.sheets.json'), 'w', encoding='utf-8') as fh:
        json.dump({'sheets': sheets, 'file': (request.files.get('file').filename if request.files.get('file') else '')
                   or _('قائمة ملصقة'), 'update': request.form.get('update') == '1',
                   'section_id': int_or_none(request.form.get('section_id'))}, fh, ensure_ascii=False)
    return _preview(token)


def _preview(token, skip=None):
    """Builds the plan from the stored sheets (again, when sheets are left out) and shows it."""
    import json
    import os
    from services.importing import build_plan, stages_needed, sheet_overview
    src = _plan_path(token).replace('.json', '.sheets.json')
    if not os.path.isfile(src):
        flash(_('انتهت صلاحية المعاينة — ارفع الملف من جديد'), 'warning')
        return redirect(url_for('students.import_list'))
    with open(src, encoding='utf-8') as fh:
        stored = json.load(fh)
    sections = _sections()
    if skip is None:                  # first look: a sheet that only repeats an earlier one is left out
        overview = sheet_overview(stored['sheets'])
        skip = [s['name'] for s in overview if s['copy_of'] and s['overlap'] >= max(1, s['students'] * 0.6)]
    plan = build_plan(stored['sheets'], update=stored['update'], default_section_id=stored['section_id'],
                      allowed_sections={sec.id for sec in sections}, skip_sheets=skip)
    if not plan['rows'] and not plan['problems'] and not skip:
        flash(_('لا توجد بيانات للاستيراد'), 'warning')
        return redirect(url_for('students.import_list'))
    plan['file'] = stored['file']
    with open(_plan_path(token), 'w', encoding='utf-8') as fh:
        json.dump(plan, fh, ensure_ascii=False)
    creates = bool(plan['new_stages'] or plan['new_grades'] or plan['new_sections'])
    return render_template('students/import.html', plan=plan, token=token, need_stage=stages_needed(plan),
                           stages=Stage.query.order_by(Stage.sort_order, Stage.id).all(),
                           can_create=has_perm('structure.manage'), creates=creates, sections=sections)


@students_bp.route('/import/<token>/sheets', methods=['POST'])
@permission_required('students.manage')
def import_sheets(token):
    """Preview again with only the ticked sheets."""
    import json
    import os
    src = _plan_path(token).replace('.json', '.sheets.json')
    if not os.path.isfile(src):
        return _preview(token)
    with open(src, encoding='utf-8') as fh:
        names = [n for n, _rows in json.load(fh)['sheets']]
    keep = set(request.form.getlist('sheet'))
    return _preview(token, [n for n in names if n not in keep])


@students_bp.route('/import/<token>/apply', methods=['POST'])
@permission_required('students.manage')
def import_apply(token):
    import json
    import os
    from services.importing import apply_plan
    path = _plan_path(token)
    if request.form.get('refresh'):
        return import_sheets(token)
    if not os.path.isfile(path):
        flash(_('انتهت صلاحية المعاينة — ارفع الملف من جديد'), 'warning')
        return redirect(url_for('students.import_list'))
    with open(path, encoding='utf-8') as fh:
        plan = json.load(fh)
    if (plan.get('new_stages') or plan['new_grades'] or plan['new_sections']) and not has_perm('structure.manage'):
        abort(403)
    allowed = {sec.id for sec in _sections()}
    for r in plan['rows']:
        if r['place'].startswith('sec:') and int(r['place'][4:]) not in allowed:
            abort(403)
    choice = {}
    for gk in plan['new_grades']:
        v = request.form.get(f'stage_{gk["key"]}') or ''
        choice[gk['key']] = 'new' if v == 'new' else int_or_none(v)
    try:
        res = apply_plan(plan, choice, (request.form.get('new_stage') or '').strip() or None)
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'danger')
        return redirect(url_for('students.import_list'))
    audit('student.imported', file=plan.get('file'), **res)
    db.session.commit()
    for f in (path, path.replace('.json', '.sheets.json')):
        if os.path.isfile(f):
            os.remove(f)
    flash(_('تم الاستيراد: أُضيف {added} طالباً، ونُقل {moved}. أُنشئت {stages} مراحل و{grades} صفوف و{sections} شعب.', **res),
          'success')
    return redirect(url_for('students.index'))


@students_bp.route('/template.xlsx')
@permission_required('students.manage')
def template_xlsx():
    """The import template: one row per student; the class may also be written as one code (4A, 7 CS-A)."""
    heads = [_('المرحلة'), _('الصف'), _('الشعبة'), _('الاسم'), _('الاسم بالإنجليزية'), _('رقم الطالب'), _('الجنس'),
             _('مربي الصف'), _('ولي الأمر'), _('صلة القرابة'), _('هاتف ولي الأمر'), _('بريد ولي الأمر'), _('ملاحظات')]
    return xlsx_response('students_template.xlsx', heads, [
        [_('المرحلة الأساسية 4-6'), _('الصف الرابع'), 'A', _('أحمد محمد علي'), 'Ahmad Mohammad Ali', '20231', _('ذكر'),
         _('سارة أحمد'), _('محمد علي'), _('الأب'), '0790000000', 'parent@example.org', ''],
        ['', '4CPA', '', _('سلمى خالد'), '', '', _('أنثى'), '', '', '', '', '', ''],
    ], _('الطلبة'))


@students_bp.route('/template.csv')
@permission_required('students.manage')
def template():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(FIELD_LABELS)
    w.writerow(['20231', _('أحمد محمد علي'), 'Ahmad Mohammad Ali', _('ذكر'), _('الصف السابع'), _('أ'), _('محمد علي'), _('الأب'),
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
            flash(_('اختر الشعبة التي يُنقل إليها الطلبة'), 'danger')
            return redirect(request.referrer or url_for('students.index'))
        for s in picked:
            s.section_id = target
            done += 1
    elif action == 'delete':
        for s in picked:
            if AttendanceRecord.query.filter_by(student_id=s.id).first() or \
                    ClassLeave.query.filter_by(student_id=s.id).first():
                skipped.append(_('{full_name} (له سجلات — أوقفه بدلًا من حذفه)', full_name=s.full_name))
                continue
            audit('student.deleted', 'student', s.id, name=s.full_name)
            db.session.delete(s)
            done += 1
    else:
        abort(400)
    if action != 'delete':
        audit(f'student.bulk_{action}', count=done)
    db.session.commit()
    verb = {'activate': _('فُعِّل'), 'deactivate': _('أُوقف'), 'move': _('نُقل'), 'delete': _('حُذف')}[action]
    flash(f'{verb}: {done}' + ((_(' — تُرك: ') + _('، ').join(skipped[:10])) if skipped else ''),
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
