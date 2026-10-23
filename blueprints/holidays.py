"""Holidays: a period off for the whole school, some stages, grades or sections. Proposed (pending), then
approved by holder of holidays.approve; only approved holidays affect the daily sheet and the reports.
A supervisor proposes for the stages in their own scope only."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, g

from authz import permission_required, has_perm
from extensions import db
from tenancy import get_scoped
from models import Holiday, Stage, Grade, Section, STATUSES
from services import scope
from utils.audit import audit
from utils.school import form_ids, parse_day, today

holidays_bp = Blueprint('holidays', __name__)
SCOPES = [('school', 'المدرسة كلها'), ('stages', 'مراحل'), ('grades', 'صفوف'), ('sections', 'شعب')]


def _choices():
    """What this user may pick: stages / grades / sections inside their scope."""
    vis = scope.visible_section_ids(g.user)
    secs = Section.query.join(Grade).join(Stage).order_by(Stage.sort_order, Grade.sort_order, Grade.id, Section.sort_order)
    if vis is not None:
        secs = secs.filter(Section.id.in_(list(vis) or [0]))
    secs = secs.all()
    grade_ids = {s.grade_id for s in secs}
    stage_ids = {s.grade.stage_id for s in secs}
    stages = [st for st in Stage.query.order_by(Stage.sort_order).all() if vis is None or st.id in stage_ids]
    grades = [gr for gr in Grade.query.order_by(Grade.sort_order).all() if vis is None or gr.id in grade_ids]
    return {'stages': stages, 'grades': grades, 'sections': secs}


def _names(h, choices=None):
    if h.scope_type == 'school':
        return 'المدرسة كلها'
    model = {'stages': Stage, 'grades': Grade, 'sections': Section}[h.scope_type]
    objs = model.query.filter(model.id.in_(h.ids or {0})).all()
    return '، '.join((o.full_name if isinstance(o, Section) else o.name) for o in objs) or '—'


def _can_touch(h):
    if scope.sees_all(g.user):
        return True
    if h.scope_type == 'school':
        return False
    ch = _choices()
    return h.ids <= {o.id for o in ch[h.scope_type]}


def _apply(h, data):
    errors = []
    title = (data.get('title') or '').strip()
    start, end = parse_day(data.get('start_date')), parse_day(data.get('end_date'))
    end = end or start
    st = data.get('scope_type') or 'school'
    if not title:
        errors.append('عنوان العطلة مطلوب')
    if start is None:
        errors.append('تاريخ البداية مطلوب')
    elif end < start:
        errors.append('تاريخ النهاية قبل البداية')
    if st not in dict(SCOPES):
        errors.append('النطاق غير صالح')
    if errors:
        return errors
    if st == 'school':
        if not scope.sees_all(g.user):
            return ['عطلة المدرسة كلها يقترحها من له صلاحية كل المراحل؛ اختر مراحلك']
        ids = set()
    else:
        allowed = {o.id for o in _choices()[st]}
        ids = {int(x) for x in data.getlist('scope_ids_' + st) if x.isdigit()} & allowed
        if not ids:
            return ['اختر عنصرًا واحدًا على الأقل من النطاق']
    h.title, h.start_date, h.end_date, h.scope_type = title[:200], start, end, st
    h.scope_ids = ','.join(str(i) for i in sorted(ids))
    return []


@holidays_bp.route('/')
@permission_required('holidays.manage', 'holidays.approve')
def index():
    rows = Holiday.query.order_by(Holiday.start_date.desc()).all()
    if not scope.sees_all(g.user):
        rows = [h for h in rows if h.scope_type == 'school' or _can_touch(h)]
    return render_template('holidays/index.html', rows=rows, names={h.id: _names(h) for h in rows},
                           choices=_choices(), scopes=SCOPES, statuses=STATUSES, today=today(),
                           sees_all=scope.sees_all(g.user), can_touch={h.id: _can_touch(h) for h in rows})


@holidays_bp.route('/new', methods=['POST'])
@holidays_bp.route('/<int:hid>', methods=['GET', 'POST'])
@permission_required('holidays.manage')
def form(hid=None):
    h = get_scoped(Holiday, hid) if hid else Holiday(status='pending')
    if h is None or (hid and not _can_touch(h)):
        abort(404)
    errors = []
    if request.method == 'POST':
        errors = _apply(h, request.form)
        if not errors:
            if hid and h.status == 'approved' and not has_perm('holidays.approve'):
                h.status = 'pending'          # a change to an approved holiday is approved again
            if not hid:
                h.created_by = g.user.id
                if has_perm('holidays.approve') and request.form.get('approve_now') == '1':
                    h.status, h.approved_by = 'approved', g.user.id
                db.session.add(h)
            db.session.flush()
            audit('holiday.created' if not hid else 'holiday.updated', 'holiday', h.id, title=h.title)
            db.session.commit()
            flash('تم الحفظ' + ('' if h.status == 'approved' else ' — بانتظار الاعتماد'), 'success')
            return redirect(url_for('holidays.index'))
        db.session.rollback()
        if not hid:
            for e in errors:
                flash(e, 'danger')
            return redirect(url_for('holidays.index'))
        h = get_scoped(Holiday, hid)
    return render_template('holidays/form.html', h=h, choices=_choices(), scopes=SCOPES, errors=errors,
                           sees_all=scope.sees_all(g.user)), (400 if errors else 200)


@holidays_bp.route('/bulk', methods=['POST'])
@permission_required('holidays.manage', 'holidays.approve')
def bulk():
    action = request.form.get('action')
    picked = [h for h in Holiday.query.filter(Holiday.id.in_(form_ids() or {0})).all() if _can_touch(h)]
    done = 0
    if action in ('approve', 'reject'):
        if not has_perm('holidays.approve'):
            abort(403)
        for h in picked:
            h.status = 'approved' if action == 'approve' else 'rejected'
            h.approved_by = g.user.id
            done += 1
    elif action == 'delete':
        if not has_perm('holidays.manage'):
            abort(403)
        for h in picked:
            if h.status == 'approved' and not has_perm('holidays.approve'):
                continue
            audit('holiday.deleted', 'holiday', h.id, title=h.title)
            db.session.delete(h)
            done += 1
    else:
        abort(400)
    if action != 'delete':
        audit(f'holiday.bulk_{action}', count=done)
    db.session.commit()
    verb = {'approve': 'اعتُمد', 'reject': 'رُفض', 'delete': 'حُذف'}[action]
    flash(f'{verb}: {done}', 'success' if done else 'warning')
    return redirect(url_for('holidays.index'))
