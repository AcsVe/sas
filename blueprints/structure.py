"""School structure, managed in SAS: academic years → semesters, stages → grades → sections, the class-leave
types and the bell periods of each stage. One small engine serves every list with the same behaviour:
add (one, or several names at once), edit, tick rows → activate / deactivate / delete, sticky headers,
a filter per column and sorting. A row that has dependants is never deleted (deactivate it instead)."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort

from authz import permission_required, has_perm
from extensions import db
from tenancy import get_scoped
from models import (AcademicYear, Semester, Stage, Grade, Section, Student, ClassLeaveType, ClassLeave,
                    Period, TimetableEntry, AttendanceRecord, WEEKDAYS)
from utils.audit import audit
from utils.school import parse_day, clean_time, form_ids, int_or_none

structure_bp = Blueprint('structure', __name__)

DAY_NAMES = dict(WEEKDAYS)
YES_NO = [('1', 'نعم'), ('0', 'لا')]


def F(name, label, kind='text', required=False, options=None, default=None, hint=''):
    return {'name': name, 'label': label, 'kind': kind, 'required': required, 'options': options,
            'default': default, 'hint': hint}


def C(label, value, filt='text', sort=None):
    return {'label': label, 'value': value, 'filter': filt, 'sort': sort}


def _years():
    return [(y.id, y.name) for y in AcademicYear.query.order_by(AcademicYear.name.desc()).all()]


def _stages():
    return [(s.id, s.name) for s in Stage.query.order_by(Stage.sort_order, Stage.id).all()]


def _grades():
    return [(gr.id, f'{gr.stage.name} — {gr.name}') for gr in
            Grade.query.join(Stage).order_by(Stage.sort_order, Grade.sort_order, Grade.id).all()]


def _active(o):
    return 'فعّال' if o.is_active else 'متوقف'


def _count(model, **kw):
    return model.query.filter_by(**kw).count()


def _year_in_use(y):
    return 'له فصول دراسية' if Semester.query.filter_by(year_id=y.id).first() else ''


def _stage_in_use(s):
    r = []
    if Grade.query.filter_by(stage_id=s.id).first():
        r.append('لها صفوف')
    if Period.query.filter_by(stage_id=s.id).first():
        r.append('لها حصص')
    return '، '.join(r)


def _grade_in_use(gr):
    return 'له شعب' if Section.query.filter_by(grade_id=gr.id).first() else ''


def _section_in_use(sec):
    r = []
    if Student.query.filter_by(section_id=sec.id).first():
        r.append('لها طلبة')
    if TimetableEntry.query.filter_by(section_id=sec.id).first():
        r.append('لها جدول حصص')
    return '، '.join(r)


def _leave_type_in_use(t):
    return 'استُخدم في سجلات خروج' if ClassLeave.query.filter_by(leave_type_id=t.id).first() else ''


def _period_in_use(p):
    return 'مستخدمة في جدول الشعب' if TimetableEntry.query.filter_by(period_id=p.id).first() else ''


ENTITIES = {
    'years': dict(
        title='السنوات الدراسية', singular='سنة دراسية', model=AcademicYear, perm='structure.manage',
        order=lambda q: q.order_by(AcademicYear.name.desc()), in_use=_year_in_use,
        fields=[F('name', 'السنة', required=True, hint='مثل 2026-2027'), F('start_date', 'تبدأ', 'date'),
                F('end_date', 'تنتهي', 'date'), F('is_current', 'السنة الحالية', 'bool', default=False),
                F('is_active', 'الحالة', 'active', default=True)],
        columns=[C('السنة', lambda o: o.name), C('تبدأ', lambda o: o.start_date or '—', 'text', 'date'),
                 C('تنتهي', lambda o: o.end_date or '—', 'text', 'date'),
                 C('الحالية', lambda o: 'الحالية' if o.is_current else '—', 'select'),
                 C('الفصول', lambda o: len(o.semesters), None, 'num'), C('الحالة', _active, 'select')]),
    'semesters': dict(
        title='الفصول الدراسية', singular='فصل دراسي', model=Semester, perm='structure.manage', parent='year_id',
        order=lambda q: q.join(AcademicYear).order_by(AcademicYear.name.desc(), Semester.sort_order),
        in_use=lambda s: '', names='name',
        fields=[F('year_id', 'السنة الدراسية', 'select', True, _years), F('name', 'الفصل', required=True),
                F('start_date', 'يبدأ', 'date'), F('end_date', 'ينتهي', 'date'),
                F('sort_order', 'الترتيب', 'int', default=0), F('is_active', 'الحالة', 'active', default=True)],
        columns=[C('السنة', lambda o: o.year.name, 'select'), C('الفصل', lambda o: o.name),
                 C('يبدأ', lambda o: o.start_date or '—', 'text', 'date'),
                 C('ينتهي', lambda o: o.end_date or '—', 'text', 'date'), C('الحالة', _active, 'select')]),
    'stages': dict(
        title='المراحل', singular='مرحلة', model=Stage, perm='structure.manage',
        order=lambda q: q.order_by(Stage.sort_order, Stage.id), in_use=_stage_in_use, names='name',
        fields=[F('name', 'المرحلة', required=True, hint='مثل الأساسية الدنيا'), F('name_en', 'الاسم بالإنجليزية'),
                F('school_days', 'أيام الدوام', 'days', default='6,0,1,2,3'),
                F('sort_order', 'الترتيب', 'int', default=0), F('is_active', 'الحالة', 'active', default=True)],
        columns=[C('المرحلة', lambda o: o.name), C('بالإنجليزية', lambda o: o.name_en or '—'),
                 C('أيام الدوام', lambda o: '، '.join(DAY_NAMES.get(d, '') for d in o.day_list)),
                 C('الصفوف', lambda o: len(o.grades), None, 'num'),
                 C('الطلبة', lambda o: Student.query.join(Section).join(Grade)
                   .filter(Grade.stage_id == o.id, Student.is_active.is_(True)).count(), None, 'num'),
                 C('الحالة', _active, 'select')],
        child=('grades', 'الصفوف')),
    'grades': dict(
        title='الصفوف', singular='صف', model=Grade, perm='structure.manage', parent='stage_id',
        order=lambda q: q.join(Stage).order_by(Stage.sort_order, Grade.sort_order, Grade.id),
        in_use=_grade_in_use, names='name',
        fields=[F('stage_id', 'المرحلة', 'select', True, _stages), F('name', 'الصف', required=True,
                                                                   hint='مثل الصف السابع'),
                F('name_en', 'الاسم بالإنجليزية'), F('sort_order', 'الترتيب', 'int', default=0),
                F('is_active', 'الحالة', 'active', default=True)],
        columns=[C('المرحلة', lambda o: o.stage.name, 'select'), C('الصف', lambda o: o.name),
                 C('الشعب', lambda o: len(o.sections), None, 'num'), C('الحالة', _active, 'select')],
        child=('sections', 'الشعب')),
    'sections': dict(
        title='الشعب', singular='شعبة', model=Section, perm='structure.manage', parent='grade_id',
        order=lambda q: q.join(Grade).join(Stage).order_by(Stage.sort_order, Grade.sort_order, Grade.id, Section.sort_order,
                                                             Section.name),
        in_use=_section_in_use, names='name',
        fields=[F('grade_id', 'الصف', 'select', True, _grades), F('name', 'الشعبة', required=True, hint='مثل أ'),
                F('sort_order', 'الترتيب', 'int', default=0), F('is_active', 'الحالة', 'active', default=True)],
        columns=[C('المرحلة', lambda o: o.grade.stage.name, 'select'), C('الصف', lambda o: o.grade.name, 'select'),
                 C('الشعبة', lambda o: o.name),
                 C('الطلبة', lambda o: _count(Student, section_id=o.id, is_active=True), None, 'num'),
                 C('الحالة', _active, 'select')]),
    'leave_types': dict(
        title='أنواع الخروج من الحصة', singular='نوع خروج', model=ClassLeaveType, perm='structure.manage',
        order=lambda q: q.order_by(ClassLeaveType.sort_order, ClassLeaveType.id), in_use=_leave_type_in_use,
        names='name',
        fields=[F('name', 'النوع', required=True), F('needs_return', 'يعود الطالب إلى الحصة', 'bool', default=True),
                F('sort_order', 'الترتيب', 'int', default=0), F('is_active', 'الحالة', 'active', default=True)],
        columns=[C('النوع', lambda o: o.name),
                 C('يعود إلى الحصة', lambda o: 'نعم' if o.needs_return else 'لا', 'select'),
                 C('مرات الاستخدام', lambda o: _count(ClassLeave, leave_type_id=o.id), None, 'num'),
                 C('الحالة', _active, 'select')]),
    'periods': dict(
        title='الحصص والاستراحات', singular='حصة', model=Period, perm='timetable.manage', parent='stage_id',
        order=lambda q: q.join(Stage).order_by(Stage.sort_order, Period.weekday, Period.sort_order, Period.start_time),
        in_use=_period_in_use, no_active=True,
        fields=[F('stage_id', 'المرحلة', 'select', True, _stages),
                F('weekday', 'اليوم', 'select', True, lambda: WEEKDAYS),
                F('label', 'الاسم', required=True, hint='مثل الحصة الأولى'),
                F('kind', 'النوع', 'select', True, lambda: [('period', 'حصة'), ('break', 'استراحة')], 'period'),
                F('start_time', 'تبدأ', 'time', True), F('end_time', 'تنتهي', 'time', True),
                F('sort_order', 'الترتيب', 'int', default=0)],
        columns=[C('المرحلة', lambda o: (get_scoped(Stage, o.stage_id).name if o.stage_id else '—'), 'select'),
                 C('اليوم', lambda o: DAY_NAMES.get(o.weekday, ''), 'select'), C('الاسم', lambda o: o.label),
                 C('النوع', lambda o: 'استراحة' if o.kind == 'break' else 'حصة', 'select'),
                 C('تبدأ', lambda o: o.start_time, 'text'), C('تنتهي', lambda o: o.end_time, 'text')]),
}
TABS = [('years', 'السنوات'), ('semesters', 'الفصول'), ('stages', 'المراحل'), ('grades', 'الصفوف'),
        ('sections', 'الشعب'), ('leave_types', 'أنواع الخروج'), ('periods', 'الحصص')]


def _spec(key):
    spec = ENTITIES.get(key)
    if spec is None:
        abort(404)
    if not has_perm(spec['perm']):
        abort(403)
    return spec


def _options(field):
    opts = field['options']() if callable(field['options']) else (field['options'] or [])
    return [(str(v), lab) for v, lab in opts]


def _apply(spec, obj, data, only=None):
    """Writes the posted fields into obj. → list of errors (Arabic)."""
    errors = []
    for f in spec['fields']:
        n, kind = f['name'], f['kind']
        if only is not None and n not in only:
            continue
        raw = data.get(n)
        if kind == 'days':
            val = ','.join(sorted({d for d in data.getlist(n) if d.lstrip('-').isdigit()}, key=int)) \
                if hasattr(data, 'getlist') else (raw or '')
            if not val:
                errors.append('اختر يوم دوام واحدًا على الأقل')
                continue
        elif kind in ('bool', 'active'):
            val = str(raw) == '1'
        elif kind == 'int':
            val = int_or_none(raw)
            if val is None:
                val = f['default'] or 0
        elif kind == 'date':
            val = parse_day(raw)
            if raw and val is None:
                errors.append(f'{f["label"]}: تاريخ غير صالح (الصيغة 2026-12-31)')
                continue
        elif kind == 'time':
            val = clean_time(raw)
            if raw and val is None:
                errors.append(f'{f["label"]}: وقت غير صالح (الصيغة 07:45)')
                continue
        elif kind == 'select':
            allowed = {v for v, _ in _options(f)}
            val = (raw or '').strip()
            if val and val not in allowed:
                errors.append(f'{f["label"]}: اختيار غير صالح')
                continue
            val = int(val) if val.lstrip('-').isdigit() else (val or None)
        else:
            val = (raw or '').strip()[:200] or None
        if f['required'] and val in (None, ''):
            errors.append(f'{f["label"]} مطلوب')
            continue
        setattr(obj, n, val)
    if not errors and isinstance(obj, (AcademicYear, Semester)) and obj.start_date and obj.end_date \
            and obj.end_date < obj.start_date:
        errors.append('تاريخ النهاية قبل تاريخ البداية')
    if not errors and isinstance(obj, Period) and obj.end_time <= obj.start_time:
        errors.append('وقت نهاية الحصة يجب أن يكون بعد بدايتها')
    return errors


def _after_save(obj):
    if isinstance(obj, AcademicYear) and obj.is_current:
        for y in AcademicYear.query.filter(AcademicYear.id != obj.id, AcademicYear.is_current.is_(True)).all():
            y.is_current = False


@structure_bp.route('/')
@permission_required('structure.manage', 'timetable.manage')
def index():
    return redirect(url_for('structure.items', key='stages' if has_perm('structure.manage') else 'periods'))


@structure_bp.route('/<key>/')
@permission_required('structure.manage', 'timetable.manage')
def items(key):
    spec = _spec(key)
    q = spec['order'](spec['model'].query)
    parent_id = int_or_none(request.args.get('parent'))
    if spec.get('parent') and parent_id:
        q = q.filter(getattr(spec['model'], spec['parent']) == parent_id)
    rows = q.all()
    return render_template('structure/list.html', key=key, spec=spec, rows=rows, tabs=TABS,
                           options={f['name']: _options(f) for f in spec['fields'] if f['kind'] == 'select'},
                           weekdays=WEEKDAYS, parent_id=parent_id)


@structure_bp.route('/<key>/new', methods=['POST'])
@permission_required('structure.manage', 'timetable.manage')
def create(key):
    spec = _spec(key)
    names_field = spec.get('names')
    names = []
    if names_field:
        raw = request.form.get(names_field) or ''
        names = [n.strip() for n in raw.replace('،', '\n').replace(',', '\n').splitlines() if n.strip()]
    if not names:
        names = [request.form.get(names_field)] if names_field else [None]
    added, errors = 0, []
    base_order = int_or_none(request.form.get('sort_order')) or 0
    for i, name in enumerate(names):
        obj = spec['model']()
        data = request.form.copy()
        if names_field:
            data[names_field] = name or ''
            if len(names) > 1:
                data['sort_order'] = str(base_order + i)
        errs = _apply(spec, obj, data)
        if errs:
            errors.extend(errs)
            break
        db.session.add(obj)
        db.session.flush()
        _after_save(obj)
        added += 1
        audit(f'{key}.created', key, obj.id)
    if errors:
        db.session.rollback()
        for e in errors:
            flash(e, 'danger')
    else:
        db.session.commit()
        flash(f'أُضيف {added}: {spec["singular"]}' if added == 1 else f'أُضيف {added}', 'success')
    return redirect(request.referrer or url_for('structure.items', key=key))


@structure_bp.route('/<key>/<int:obj_id>', methods=['GET', 'POST'])
@permission_required('structure.manage', 'timetable.manage')
def edit(key, obj_id):
    spec = _spec(key)
    obj = get_scoped(spec['model'], obj_id)
    if obj is None:
        abort(404)
    errors = []
    if request.method == 'POST':
        errors = _apply(spec, obj, request.form)
        if not errors:
            _after_save(obj)
            audit(f'{key}.updated', key, obj.id)
            db.session.commit()
            flash('تم الحفظ', 'success')
            return redirect(url_for('structure.items', key=key))
        db.session.rollback()
        obj = get_scoped(spec['model'], obj_id)
    return render_template('structure/form.html', key=key, spec=spec, o=obj, errors=errors, tabs=TABS,
                           options={f['name']: _options(f) for f in spec['fields'] if f['kind'] == 'select'},
                           weekdays=WEEKDAYS), (400 if errors else 200)


@structure_bp.route('/<key>/bulk', methods=['POST'])
@permission_required('structure.manage', 'timetable.manage')
def bulk(key):
    spec = _spec(key)
    action = request.form.get('action')
    model = spec['model']
    picked = model.query.filter(model.id.in_(form_ids() or {0})).all()
    done, skipped = 0, []
    if action in ('activate', 'deactivate') and not spec.get('no_active'):
        for o in picked:
            o.is_active = action == 'activate'
            done += 1
    elif action == 'delete':
        for o in picked:
            why = spec['in_use'](o)
            if why:
                skipped.append(f'{getattr(o, "name", None) or getattr(o, "label", "")} ({why}'
                               + ('' if spec.get('no_active') else ' — أوقفه بدلًا من حذفه') + ')')
                continue
            audit(f'{key}.deleted', key, o.id)
            db.session.delete(o)
            done += 1
    elif action == 'current' and key == 'years' and len(picked) == 1:
        picked[0].is_current = True
        _after_save(picked[0])
        done = 1
    elif action == 'copy_day' and key == 'periods':
        targets = {int(d) for d in request.form.getlist('to_days') if d.lstrip('-').isdigit()}
        for o in picked:
            for d in targets - {o.weekday}:
                exists = Period.query.filter_by(stage_id=o.stage_id, weekday=d, start_time=o.start_time).first()
                if exists:
                    continue
                db.session.add(Period(stage_id=o.stage_id, weekday=d, sort_order=o.sort_order, kind=o.kind,
                                      label=o.label, start_time=o.start_time, end_time=o.end_time))
                done += 1
    else:
        abort(400)
    if action != 'delete':
        audit(f'{key}.bulk_{action}', count=done)
    db.session.commit()
    verb = {'activate': 'فُعِّل', 'deactivate': 'أُوقف', 'delete': 'حُذف', 'current': 'عُيِّنت السنة الحالية',
            'copy_day': 'نُسخت حصص'}[action]
    flash(f'{verb}: {done}' + ((' — تُرك: ' + '، '.join(skipped[:10])) if skipped else ''),
          'success' if done else 'warning')
    return redirect(request.referrer or url_for('structure.items', key=key))


def in_use_any(obj):
    """Used by tests: the reason a structure row cannot be deleted."""
    for spec in ENTITIES.values():
        if isinstance(obj, spec['model']):
            return spec['in_use'](obj)
    return ''


__all__ = ['structure_bp', 'ENTITIES', 'in_use_any', 'AttendanceRecord']
