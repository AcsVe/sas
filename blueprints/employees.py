"""Staff roster: add one, paste a list or import CSV/Excel, edit, bulk activate/deactivate/move/delete,
and create login accounts for the ticked employees (role «المعلم» or another), with the stages they follow."""
import io
import csv
import os
import re

from flask import Blueprint, render_template, request, redirect, url_for, flash, g, abort, Response
from sqlalchemy import func

from authz import permission_required
from extensions import db
from tenancy import get_scoped
from models import Employee, Department, User, Role, UserRole, Stage, UserStage
from security import hash_password
from utils.audit import audit
from utils.helpers import is_valid_email
from utils.tabular import read_table, to_records, parse_date, norm
from utils.i18n import _

employees_bp = Blueprint('employees', __name__)

FIELDS = [
    ('full_name', ['الاسم', 'اسم الموظف', 'الاسم الكامل', 'name', 'employeeName', 'الموظف']),
    ('employee_number', ['الرقم الوظيفي', 'رقم الموظف', 'الرقم', 'كود الموظف', 'empCode', 'number', 'code', 'employeeId']),
    ('email', ['البريد', 'البريد الإلكتروني', 'الايميل', 'email', 'employeeEmail', 'e-mail']),
    ('phone', ['الهاتف', 'الجوال', 'رقم الهاتف', 'phone', 'mobile']),
    ('department', ['القسم', 'department', 'dept']),
    ('job_title', ['المسمى الوظيفي', 'الوظيفة', 'المسمى', 'job', 'job_title', 'title', 'position']),
    ('specialty', ['التخصص', 'المادة', 'specialty', 'subject']),
    ('hire_date', ['تاريخ التعيين', 'hire_date', 'hired']),
]
FIELD_LABELS = ['الاسم', 'الرقم الوظيفي', 'البريد', 'الهاتف', 'القسم', 'المسمى الوظيفي', 'تاريخ التعيين']


def _get(emp_id):
    e = get_scoped(Employee, emp_id)
    if e is None:
        abort(404)
    return e


def _ids():
    out = set()
    for v in request.form.getlist('ids'):
        if v.isdigit():
            out.add(int(v))
    return out


def _depts():
    return Department.query.order_by(Department.sort_order, Department.name).all()


def dept_resolver():
    """Text (name, code or icon+name) → Department, Arabic-insensitive."""
    index = {}
    for d in Department.query.all():
        for key in (d.name, d.code, f'{d.icon} {d.name}', d.legacy_id):
            if key:
                index.setdefault(norm(key), d)
    return lambda text: index.get(norm(text)) if text else None


@employees_bp.route('/')
@permission_required('employees.manage')
def index():
    emps = Employee.query.order_by(Employee.full_name).all()
    stage_names = {s.id: s.name for s in Stage.query.all()}
    scopes = {}
    for us in UserStage.query.all():
        scopes.setdefault(us.user_id, []).append(stage_names.get(us.stage_id, ''))
    return render_template('employees/index.html', employees=emps, departments=_depts(),
                           scopes=scopes, roles=Role.query.order_by(Role.id).all(),
                           stages=Stage.query.filter_by(is_active=True).order_by(Stage.sort_order).all(),
                           field_labels=FIELD_LABELS)


def _apply(emp, data):
    errors = []
    name = (data.get('full_name') or '').strip()
    number = (data.get('employee_number') or '').strip() or None
    email = (data.get('email') or '').strip() or None
    if not name:
        errors.append(_('الاسم مطلوب'))
    if email and not is_valid_email(email):
        errors.append(_('بريد غير صالح: {email}', email=email))
    if number and Employee.query.filter(Employee.employee_number == number, Employee.id != (emp.id or 0)).first():
        errors.append(_('الرقم الوظيفي {number} مستخدم لموظف آخر', number=number))
    if errors:
        return errors
    emp.full_name, emp.employee_number, emp.email = name, number, email
    emp.phone = (data.get('phone') or '').strip() or None
    emp.job_title = (data.get('job_title') or '').strip() or None
    emp.notes = (data.get('notes') or '').strip()[:500] or None
    if 'hire_date' in data:
        emp.hire_date = parse_date(data.get('hire_date'))
    did = data.get('department_id')
    if did not in (None, ''):
        d = get_scoped(Department, int(did)) if str(did).isdigit() else None
        if d is None:
            return [_('القسم غير موجود')]
        emp.department_id = d.id
    elif 'department_id' in data:
        emp.department_id = None
    if 'is_active' in data:
        emp.is_active = data.get('is_active') == '1'
    return []


@employees_bp.route('/new', methods=['POST'])
@employees_bp.route('/<int:emp_id>', methods=['GET', 'POST'])
@permission_required('employees.manage')
def form(emp_id=None):
    emp = _get(emp_id) if emp_id else Employee(is_active=True)
    errors = []
    if request.method == 'POST':
        errors = _apply(emp, request.form)
        if not errors:
            if not emp_id:
                db.session.add(emp)
            db.session.flush()
            audit('employee.created' if not emp_id else 'employee.updated', 'employee', emp.id, name=emp.full_name)
            db.session.commit()
            flash(_('تم الحفظ'), 'success')
            return redirect(url_for('employees.index'))
        db.session.rollback()
        if not emp_id:
            for e in errors:
                flash(e, 'danger')
            return redirect(url_for('employees.index'))
    return render_template('employees/form.html', e=emp, errors=errors, departments=_depts(),
                           stage_names=_stage_names(emp.user_id)), (400 if errors else 200)


def _stage_names(user_id):
    if not user_id:
        return []
    ids = [us.stage_id for us in UserStage.query.filter_by(user_id=user_id).all()]
    return [st.name for st in Stage.query.filter(Stage.id.in_(ids or [0])).order_by(Stage.sort_order).all()]


@employees_bp.route('/import', methods=['POST'])
@permission_required('employees.manage')
def import_list():
    """Paste or file. Matching on employee number, then e-mail: existing employees are updated only when
    «تحديث الموجودين» is ticked, otherwise skipped."""
    try:
        rows = read_table(request.form.get('text'), request.files.get('file'))
    except ValueError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('employees.index'))
    except Exception:  # noqa: BLE001 — a damaged spreadsheet
        flash(_('تعذّرت قراءة الملف — تأكد أنه ملف Excel أو CSV سليم'), 'danger')
        return redirect(url_for('employees.index'))
    records, _hdr = to_records(rows, FIELDS)
    if not records:
        flash(_('لا توجد بيانات للاستيراد'), 'warning')
        return redirect(url_for('employees.index'))
    update = request.form.get('update') == '1'
    by_name = {}
    for e in Employee.query.all():
        by_name.setdefault(norm(e.full_name), e)
    completed = 0
    default_dept = request.form.get('department_id') or ''
    find_dept = dept_resolver()
    added = updated = skipped = 0
    problems, unknown_depts = [], set()
    for i, rec in enumerate(records, 1):
        if not rec.get('full_name'):
            problems.append(_('سطر {i}: بلا اسم', i=i))
            continue
        emp = None
        if rec.get('employee_number'):
            emp = Employee.query.filter_by(employee_number=rec['employee_number']).first()
        if emp is None and rec.get('email'):
            emp = Employee.query.filter(func.lower(Employee.email) == rec['email'].lower()).first()
        if emp is None:                   # the same teacher from another system (venues, tickets), by name
            emp = by_name.get(norm(rec['full_name']))
        if rec.get('specialty'):
            rec['job_title'] = f"{rec.get('job_title') or ''} ({rec['specialty']})".strip() \
                if rec.get('job_title') else rec['specialty']
        if emp is not None and not update:
            filled = False                # never overwrite — only fill what the employee does not have yet
            for k in ('email', 'phone', 'job_title', 'employee_number'):
                if rec.get(k) and not getattr(emp, k):
                    if k == 'email' and (not is_valid_email(rec[k]) or Employee.query.filter(
                            func.lower(Employee.email) == rec[k].lower()).first()):
                        continue
                    if k == 'employee_number' and Employee.query.filter_by(employee_number=rec[k]).first():
                        continue
                    setattr(emp, k, rec[k].strip()[:200])
                    filled = True
            if filled:
                completed += 1
            else:
                skipped += 1
            continue
        data = dict(rec)
        if rec.get('department'):
            d = find_dept(rec['department'])
            if d:
                data['department_id'] = d.id
            else:
                unknown_depts.add(rec['department'])
        if 'department_id' not in data and default_dept and (emp is None or emp.department_id is None):
            data['department_id'] = default_dept
        is_new = emp is None
        target = emp or Employee(is_active=True)
        if not is_new:   # keep fields the row leaves empty
            for k in ('phone', 'job_title', 'email', 'employee_number'):
                if not data.get(k):
                    data[k] = getattr(target, k)
            data['notes'] = target.notes
        errs = _apply(target, data)
        if errs:
            problems.append(_('سطر {i} ({v}): ', i=i, v=rec.get("full_name")) + _('، ').join(errs))
            continue
        if is_new:
            db.session.add(target)
            by_name.setdefault(norm(target.full_name), target)
            added += 1
        else:
            updated += 1
        db.session.flush()
    audit('employee.imported', added=added, updated=updated, completed=completed, skipped=skipped,
          problems=len(problems))
    db.session.commit()
    msg = _('أُضيف {added}، حُدِّث {updated}، أُكملت بيانات {completed}، تُرك {skipped} (موجود مسبقًا)', added=added, updated=updated, completed=completed, skipped=skipped)
    flash(msg, 'success' if added or updated else 'warning')
    if unknown_depts:
        flash(_('أقسام غير معروفة (أُضيف الموظفون بلا قسم): ') + _('، ').join(sorted(unknown_depts)), 'warning')
    for p in problems[:15]:
        flash(p, 'danger')
    if len(problems) > 15:
        flash(_('… و{v} أخطاء أخرى', v=len(problems) - 15), 'danger')
    return redirect(url_for('employees.index'))


@employees_bp.route('/template.csv')
@permission_required('employees.manage')
def template():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(FIELD_LABELS)
    w.writerow([_('محمد أحمد'), '1023', 'm.ahmad@example.org', '0790000000', _('تقنية المعلومات'), _('فني'), '2024-09-01'])
    return Response('﻿' + buf.getvalue(), mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename=employees_template.csv'})


def _make_username(emp):
    base = (emp.email.split('@')[0] if emp.email else (emp.employee_number or 'emp'))
    base = re.sub(r'[^A-Za-z0-9._-]', '', base).lower() or 'emp'
    base = (base + 'xx')[:3] if len(base) < 3 else base[:70]
    cand, n = base, 1
    while User.query.filter(func.lower(User.username) == cand).first():
        n += 1
        cand = f'{base}{n}'
    return cand


@employees_bp.route('/bulk', methods=['POST'])
@permission_required('employees.manage')
def bulk():
    action = request.form.get('action')
    picked = Employee.query.filter(Employee.id.in_(_ids())).all()
    done, skipped = 0, []
    if action in ('activate', 'deactivate'):
        for e in picked:
            e.is_active = action == 'activate'
            done += 1
    elif action == 'move':
        did = request.form.get('department_id') or ''
        dept = get_scoped(Department, int(did)) if did.isdigit() else None
        if did and dept is None:
            abort(400)
        for e in picked:
            e.department_id = dept.id if dept else None
            done += 1
    elif action == 'delete':
        for e in picked:
            audit('employee.deleted', 'employee', e.id, name=e.full_name)
            db.session.delete(e)
            done += 1
    elif action == 'accounts':
        if not g.user.has_perm('users.manage'):
            abort(403)
        role = Role.query.filter_by(key=request.form.get('role_key') or 'teacher').first()
        stage_ids = [int(x) for x in request.form.getlist('stage_ids') if x.isdigit()]
        stage_ids = [st.id for st in Stage.query.filter(Stage.id.in_(stage_ids or [0])).all()]
        send = request.form.get('send_reset') == '1'
        created = []
        for e in picked:
            if e.user_id:
                skipped.append(_('{full_name} (له حساب)', full_name=e.full_name))
                continue
            if not e.email:
                skipped.append(_('{full_name} (بلا بريد)', full_name=e.full_name))
                continue
            existing = User.query.filter(func.lower(User.email) == e.email.lower()).first()
            if existing:
                e.user_id = existing.id          # link to the account that already uses this e-mail
                done += 1
                continue
            u = User(username=_make_username(e), full_name=e.full_name, email=e.email,
                     employee_number=e.employee_number, password_hash=hash_password(os.urandom(24).hex()),
                     is_active=True)
            db.session.add(u)
            db.session.flush()
            if role:
                db.session.add(UserRole(user_id=u.id, role_id=role.id))
            for sid in stage_ids:
                db.session.add(UserStage(user_id=u.id, stage_id=sid))
            e.user_id = u.id
            audit('user.created_from_employee', 'user', u.id, employee=e.id)
            created.append(u)
            done += 1
        db.session.commit()
        if send and created:
            from blueprints.auth import _issue_reset
            failed = sum(1 for u in created if not _issue_reset(u, g.tenant, by_admin=True))
            if failed:
                flash(_('تعذّر إرسال رابط كلمة المرور إلى {failed} — راجع سجل البريد', failed=failed), 'warning')
    else:
        abort(400)
    if action != 'accounts':
        audit(f'employee.bulk_{action}', count=done)
    db.session.commit()
    verb = {'activate': _('فُعِّل'), 'deactivate': _('أُوقف'), 'move': _('نُقل'), 'delete': _('حُذف'),
            'accounts': _('حسابات أُنشئت أو رُبطت')}[action]
    msg = f'{verb}: {done}' + ((_(' — تُرك: ') + _('، ').join(skipped[:10])) if skipped else '')
    flash(msg, 'success' if done else 'warning')
    return redirect(url_for('employees.index'))


@employees_bp.route('/search.json')
@permission_required('employees.manage', 'users.manage')
def search_json():
    """Employee picker (JSON)."""
    from flask import jsonify
    q = (request.args.get('q') or '').strip()
    query = Employee.query.filter(Employee.is_active.is_(True))
    if q:
        like = f'%{q.lower()}%'
        query = query.filter(func.lower(Employee.full_name).like(like) |
                             func.lower(func.coalesce(Employee.employee_number, '')).like(like) |
                             func.lower(func.coalesce(Employee.email, '')).like(like))
    return jsonify([{'id': e.id, 'name': e.full_name, 'number': e.employee_number or '', 'email': e.email or '',
                     'department': e.department.name if e.department else '', 'job': e.job_title or ''}
                    for e in query.order_by(Employee.full_name).limit(20)])
