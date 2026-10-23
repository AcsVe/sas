"""Reports: absence summary per section (students, school days, absence days, lateness, early leave, absence
rate — zero-safe), top absentees, comparison of two periods per stage, print view and Excel export.
Only the sections inside the user's scope are counted."""
from datetime import timedelta

from flask import Blueprint, render_template, request, g

from authz import permission_required
from extensions import db
from models import AttendanceRecord, Student, Section, Grade, Stage, Semester
from services import scope
from services.calendar import approved_holidays, school_days
from utils.export import xlsx_response
from utils.school import today, parse_day

reports_bp = Blueprint('reports', __name__)
ABSENCE = ('absent', 'excused')


def rate(part, whole):
    """Percentage with one decimal; 0 when there is nothing to divide by."""
    return round(100.0 * part / whole, 1) if whole else 0.0


def default_range():
    t = today()
    sem = Semester.query.filter(Semester.start_date <= t, Semester.end_date >= t).first()
    if sem:
        return sem.start_date, t
    return t.replace(day=1), t


def summary(d_from, d_to, stage_id=None):
    """→ (rows per section, totals per stage, grand total, top absentees)."""
    secs = scope.sections_query(g.user).join(Grade).join(Stage) \
        .order_by(Stage.sort_order, Grade.sort_order, Grade.id, Section.sort_order, Section.name)
    if stage_id:
        secs = secs.filter(Grade.stage_id == stage_id)
    secs = secs.all()
    sec_ids = [s.id for s in secs] or [0]
    hols = approved_holidays(d_from, d_to)
    students = {}
    for sid, n in db.session.query(Student.section_id, db.func.count(Student.id)) \
            .filter(Student.section_id.in_(sec_ids), Student.is_active.is_(True)).group_by(Student.section_id):
        students[sid] = n
    recs = db.session.query(Student.section_id, AttendanceRecord.kind, db.func.count(AttendanceRecord.id)) \
        .join(Student, AttendanceRecord.student_id == Student.id) \
        .filter(Student.section_id.in_(sec_ids), AttendanceRecord.record_date >= d_from,
                AttendanceRecord.record_date <= d_to, AttendanceRecord.status != 'rejected') \
        .group_by(Student.section_id, AttendanceRecord.kind).all()
    counts = {}
    for sid, kind, n in recs:
        counts.setdefault(sid, {})[kind] = n
    rows, stages = [], {}
    for s in secs:
        c = counts.get(s.id, {})
        n_st, days = students.get(s.id, 0), school_days(d_from, d_to, s, hols)
        absent = sum(c.get(k, 0) for k in ABSENCE)
        row = {'section': s, 'students': n_st, 'days': days, 'absent': absent, 'excused': c.get('excused', 0),
               'late': c.get('late', 0), 'early': c.get('early_leave', 0), 'possible': n_st * days}
        row['rate'] = rate(absent, row['possible'])
        rows.append(row)
        st = stages.setdefault(s.grade.stage_id, {'stage': s.grade.stage, 'students': 0, 'absent': 0, 'late': 0,
                                                  'early': 0, 'excused': 0, 'possible': 0})
        for k in ('students', 'absent', 'late', 'early', 'excused', 'possible'):
            st[k] += row[k]
    for st in stages.values():
        st['rate'] = rate(st['absent'], st['possible'])
    total = {k: sum(r[k] for r in rows) for k in ('students', 'absent', 'late', 'early', 'excused', 'possible')}
    total['rate'] = rate(total['absent'], total['possible'])
    top = db.session.query(Student, db.func.count(AttendanceRecord.id).label('n')) \
        .join(AttendanceRecord, AttendanceRecord.student_id == Student.id) \
        .filter(Student.section_id.in_(sec_ids), AttendanceRecord.kind.in_(ABSENCE),
                AttendanceRecord.status != 'rejected', AttendanceRecord.record_date >= d_from,
                AttendanceRecord.record_date <= d_to) \
        .group_by(Student.id).order_by(db.text('n DESC')).limit(20).all()
    return rows, list(stages.values()), total, top


def _stages():
    vis = scope.visible_section_ids(g.user)
    stages = Stage.query.filter_by(is_active=True).order_by(Stage.sort_order).all()
    if vis is None:
        return stages
    keep = {s.grade.stage_id for s in Section.query.filter(Section.id.in_(list(vis) or [0])).all()}
    return [s for s in stages if s.id in keep]


@reports_bp.route('/')
@permission_required('reports.view')
def index():
    dd_from, dd_to = default_range()
    d_from = parse_day(request.args.get('date_from'), dd_from)
    d_to = parse_day(request.args.get('date_to'), dd_to)
    if d_to < d_from:
        d_from, d_to = d_to, d_from
    stage_id = request.args.get('stage', type=int)
    rows, stages, total, top = summary(d_from, d_to, stage_id)
    if request.args.get('export') == 'xlsx':
        return xlsx_response(f'absence_{d_from}_{d_to}.xlsx',
                             ['المرحلة', 'الصف', 'الشعبة', 'الطلبة', 'أيام الدوام', 'أيام الغياب', 'منها بعذر',
                              'التأخر', 'المغادرة المبكرة', 'نسبة الغياب %'],
                             [[r['section'].grade.stage.name, r['section'].grade.name, r['section'].name,
                               r['students'], r['days'], r['absent'], r['excused'], r['late'], r['early'], r['rate']]
                              for r in rows], 'الملخص')
    return render_template('reports/index.html', rows=rows, stages=stages, total=total, top=top, d_from=d_from,
                           d_to=d_to, stage_id=stage_id, all_stages=_stages(),
                           printing=request.args.get('print') == '1')


@reports_bp.route('/compare')
@permission_required('reports.view')
def compare():
    t = today()
    b_to = parse_day(request.args.get('b_to'), t)
    b_from = parse_day(request.args.get('b_from'), b_to - timedelta(days=29))
    span = (b_to - b_from).days
    a_to = parse_day(request.args.get('a_to'), b_from - timedelta(days=1))
    a_from = parse_day(request.args.get('a_from'), a_to - timedelta(days=span))
    _, sa, ta, _ = summary(a_from, a_to)
    _, sb, tb, _ = summary(b_from, b_to)
    a_by = {x['stage'].id: x for x in sa}
    rows = []
    for x in sb:
        prev = a_by.get(x['stage'].id, {'rate': 0.0, 'absent': 0, 'late': 0})
        rows.append({'stage': x['stage'], 'a': prev, 'b': x, 'diff': round(x['rate'] - prev['rate'], 1)})
    return render_template('reports/compare.html', rows=rows, ta=ta, tb=tb, a_from=a_from, a_to=a_to,
                           b_from=b_from, b_to=b_to, diff=round(tb['rate'] - ta['rate'], 1))
