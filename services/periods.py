"""The bell periods of a section's day, and loading them from a file (the «export for SAS» of the bell
system, or any sheet with the same columns).

A day belongs to the stage (grade_id empty) unless the grade has its own rows for that weekday, which then
replace the stage's day for that grade only — like the bell programs, where grade 4 rings on the «3+4»
program and grades 5–6 of the same stage on the «5+6» one.

Import: one row per period — stage, grade (optional), weekday, order, kind, name (Arabic / English), start,
end. For each stage and weekday the most common timing among its grades becomes the stage's day and the
grades that differ keep their own rows. Existing periods are updated in place (same order), so the
timetable entries already linked to them are kept; a surplus period is removed only when no timetable entry
uses it.
"""
import re
from collections import Counter

from extensions import db
from models import Stage, Grade, Period, TimetableEntry, WEEKDAYS
from utils.i18n import _
from utils.school import clean_time
from utils.tabular import norm, to_records

FIELDS = [
    ('stage', ['المرحلة', 'stage', 'stage_ar']),
    ('stage_en', ['المرحلة بالإنجليزية', 'stage_en', 'stage (english)']),
    ('grade', ['الصف', 'grade', 'class', 'grade_ar']),
    ('grade_en', ['الصف بالإنجليزية', 'grade_en']),
    ('weekday', ['اليوم', 'day', 'weekday', 'رقم اليوم']),
    ('order', ['الترتيب', 'الرقم', 'order', 'number', 'n', 'period number']),
    ('kind', ['النوع', 'kind', 'type']),
    ('label', ['الاسم', 'اسم الحصة', 'label', 'name', 'label_ar']),
    ('label_en', ['الاسم بالإنجليزية', 'label_en', 'name (english)']),
    ('start', ['تبدأ', 'البداية', 'من', 'start', 'start_time', 'from']),
    ('end', ['تنتهي', 'النهاية', 'إلى', 'end', 'end_time', 'to']),
]
_DAYS = {}
for _n, _ar in WEEKDAYS:
    _DAYS[norm(_ar)] = _n
for _i, _en in enumerate(['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']):
    _DAYS[_en] = _i
    _DAYS[_en[:3]] = _i
_BREAK = re.compile(r'break|استراح|فرص|فسح', re.I)


def day_periods(grade, weekday):
    """[Period] of a grade on one weekday: the grade's own rows, else the stage's."""
    q = Period.query.filter_by(weekday=weekday)
    own = q.filter(Period.grade_id == grade.id).order_by(Period.sort_order, Period.start_time).all()
    if own:
        return own
    return Period.query.filter_by(weekday=weekday, stage_id=grade.stage_id).filter(Period.grade_id.is_(None)) \
        .order_by(Period.sort_order, Period.start_time).all()


def grade_week(grade):
    """{weekday: [Period]} for the school days of the grade's stage."""
    return {d: day_periods(grade, d) for d, _n in WEEKDAYS if d in grade.stage.day_list}


def _weekday(v):
    v = (v or '').strip()
    if v.lstrip('-').isdigit():
        n = int(v)
        return n if 0 <= n <= 6 else None
    return _DAYS.get(norm(v))


def _grade_no(text):
    from services.importing import parse_grade_number
    return parse_grade_number(text or '')[0]


def read_rows(sheets):
    out = []
    for sheet, rows in sheets:
        recs, _hdr = to_records(rows, FIELDS, keep_row=True)
        for r in recs:
            r['_sheet'] = sheet
            out.append(r)
    return out


def _match_stage(rec, stages):
    for key in ('stage', 'stage_en'):
        v = norm(rec.get(key))
        if v:
            for st in stages:
                if v in (norm(st.name), norm(st.name_en)):
                    return st
    return None


def build_plan(sheets):
    """→ {'targets': [...], 'problems': [...], 'counts': {...}} — nothing is written."""
    from services.importing import stage_range, _number_of
    stages = Stage.query.order_by(Stage.sort_order, Stage.id).all()
    grades = Grade.query.all()
    by_num = {}
    for gr in grades:
        n = _number_of(gr.name, gr.name_en)
        if n:
            by_num.setdefault(n, gr)
    problems = []
    missing = {}    # a grade / stage of the file that SAS does not have → its rows are left out
    days = {}       # (stage id, grade id | None, weekday) → [period dict]
    for rec in read_rows(sheets):
        where = f'{rec["_sheet"]} · {rec["_row"]}' if rec.get('_sheet') else str(rec['_row'])
        start, end = clean_time(rec.get('start')), clean_time(rec.get('end'))
        wd = _weekday(rec.get('weekday'))
        if not start or not end or wd is None:
            if any(rec.get(k) for k in ('start', 'end', 'label')):
                problems.append({'where': where, 'why': _('اليوم أو الوقت غير صالح')})
            continue
        if end <= start:
            problems.append({'where': where, 'why': _('وقت النهاية قبل البداية')})
            continue
        n = _grade_no(rec.get('grade')) or _grade_no(rec.get('grade_en'))
        gr = by_num.get(n) if n else None
        st = _match_stage(rec, stages) or (gr.stage if gr else None)
        if st is None and n:
            st = next((s for s in stages if (stage_range(s) or (0, -1))[0] <= n <= (stage_range(s) or (0, -1))[1]),
                      None)
        if n and gr is None:
            missing[rec.get('grade') or rec.get('grade_en')] = missing.get(rec.get('grade') or rec.get('grade_en'), 0) + 1
            continue
        if st is None:
            missing[rec.get('stage') or '—'] = missing.get(rec.get('stage') or '—', 0) + 1
            continue
        if gr is not None and gr.stage_id != st.id:
            st = gr.stage
        label = (rec.get('label') or '').strip()
        kind = 'break' if _BREAK.search(f'{rec.get("kind") or ""} {label} {rec.get("label_en") or ""}') else 'period'
        days.setdefault((st.id, gr.id if gr else None, wd), []).append({
            'order': int(rec['order']) if str(rec.get('order') or '').isdigit() else 0, 'kind': kind,
            'label': label or (_('استراحة') if kind == 'break' else _('حصة')),
            'label_en': (rec.get('label_en') or '').strip() or None, 'start': start, 'end': end})
    for lst in days.values():
        lst.sort(key=lambda p: (p['start'], p['order']))
        for i, p in enumerate(lst, 1):
            p['order'] = i
    # compaction: per stage and weekday, the most common timing of its grades is the stage's day
    targets = {}
    stage_days = {(s, w) for (s, _g, w) in days}
    for sid, wd in stage_days:
        own_stage = days.get((sid, None, wd))
        per_grade = {g: lst for (s, g, w), lst in days.items() if s == sid and w == wd and g is not None}
        sig = lambda lst: tuple((p['kind'], p['start'], p['end'], p['label']) for p in lst)  # noqa: E731
        base = own_stage
        if base is None and per_grade:
            common = Counter(sig(lst) for lst in per_grade.values()).most_common(1)[0][0]
            base = next(lst for lst in per_grade.values() if sig(lst) == common)
        targets[(sid, None, wd)] = base
        for g, lst in per_grade.items():
            if sig(lst) != sig(base):
                targets[(sid, g, wd)] = lst
    stage_names = {s.id: s for s in stages}
    grade_names = {g.id: g for g in grades}
    out = []
    for (sid, gid, wd), lst in sorted(targets.items(), key=lambda kv: (
            stage_names[kv[0][0]].sort_order, kv[0][0], kv[0][1] or 0, [d for d, _n in WEEKDAYS].index(kv[0][2]))):
        current = Period.query.filter_by(stage_id=sid, weekday=wd).filter(
            Period.grade_id == gid if gid else Period.grade_id.is_(None)).count()
        out.append({'stage_id': sid, 'grade_id': gid, 'weekday': wd, 'periods': lst, 'current': current,
                    'stage': stage_names[sid].name, 'grade': grade_names[gid].name if gid else None,
                    'day': dict(WEEKDAYS)[wd]})
    school_days = {}
    for (sid, _g, wd) in days:
        school_days.setdefault(sid, set()).add(wd)
    return {'targets': out, 'problems': problems,
            'missing': [{'name': k, 'rows': v} for k, v in missing.items()],
            'school_days': {str(k): sorted(v, key=[d for d, _n in WEEKDAYS].index) for k, v in school_days.items()},
            'counts': {'days': len(out), 'periods': sum(len(t['periods']) for t in out),
                       'grades': len({t['grade_id'] for t in out if t['grade_id']}), 'problems': len(problems)}}


def apply_plan(plan, set_school_days=True):
    """Writes the plan: updates periods in place by order, adds the missing ones, removes the surplus ones
    that no timetable entry uses. Grade days the file no longer separates from the stage are removed the
    same way. The caller commits. → counts."""
    added = updated = removed = kept = 0
    touched_stage_days = set()
    for t in plan['targets']:
        q = Period.query.filter_by(stage_id=t['stage_id'], weekday=t['weekday'])
        q = q.filter(Period.grade_id == t['grade_id']) if t['grade_id'] else q.filter(Period.grade_id.is_(None))
        cur = q.order_by(Period.sort_order, Period.start_time).all()
        touched_stage_days.add((t['stage_id'], t['weekday']))
        for i, p in enumerate(t['periods']):
            if i < len(cur):
                row = cur[i]
                if (row.kind, row.label, row.label_en, row.start_time, row.end_time, row.sort_order) != \
                        (p['kind'], p['label'], p['label_en'], p['start'], p['end'], p['order']):
                    updated += 1
            else:
                row = Period(stage_id=t['stage_id'], grade_id=t['grade_id'], weekday=t['weekday'])
                db.session.add(row)
                added += 1
            row.kind, row.label, row.label_en = p['kind'], p['label'][:100], (p['label_en'] or None)
            row.start_time, row.end_time, row.sort_order = p['start'], p['end'], p['order']
        for row in cur[len(t['periods']):]:
            if TimetableEntry.query.filter_by(period_id=row.id).first():
                kept += 1
            else:
                db.session.delete(row)
                removed += 1
    planned = {(t['stage_id'], t['grade_id'], t['weekday']) for t in plan['targets']}
    for sid, wd in touched_stage_days:
        for row in Period.query.filter_by(stage_id=sid, weekday=wd).filter(Period.grade_id.isnot(None)).all():
            if (sid, row.grade_id, wd) not in planned:
                if TimetableEntry.query.filter_by(period_id=row.id).first():
                    kept += 1
                else:
                    db.session.delete(row)
                    removed += 1
    if set_school_days:
        for sid, wds in (plan.get('school_days') or {}).items():
            st = db.session.get(Stage, int(sid))
            if st is not None and wds:
                st.school_days = ','.join(str(d) for d in sorted(wds))
    db.session.flush()
    return {'added': added, 'updated': updated, 'removed': removed, 'kept': kept}
