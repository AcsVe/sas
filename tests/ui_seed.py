"""بيانات تجربة لمراجعة المظهر واللغة (tests/ui_audit.py): مدرسة بمرحلتين وصفوف وشعب وطلبة، وحصص وجدول، وسجلات غياب
وتأخر واستئذان، وعطلة، وموظفون ومستخدمون.

    python tests/ui_seed.py <ملف القاعدة> [ar|en]
اللغة الثانية لغة البيانات (الأسماء والمراحل والملاحظات)، لفحص الواجهة الإنجليزية ببيانات إنجليزية. يطبع معرّفات JSON.
"""
import json
import os
import sys
from datetime import timedelta

DB = sys.argv[1]
EN = len(sys.argv) > 2 and sys.argv[2] == 'en'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ['TEST_DATABASE_URL'] = 'sqlite:///' + DB.replace('\\', '/')
os.environ['DATABASE_URL'] = os.environ['TEST_DATABASE_URL']
os.environ.setdefault('SECRET_KEY', 'ui-audit-secret')
os.environ['DISABLE_REMINDERS'] = '1'

try:
    import pytest  # noqa: F401  — tests.conftest يستورده؛ قد لا يكون مثبّتاً على كمبيوتر المدرسة
except ImportError:
    import types
    sys.modules['pytest'] = types.SimpleNamespace(
        fixture=lambda *a, **k: a[0] if a and callable(a[0]) else (lambda f: f))

from app import create_app  # noqa: E402
from extensions import db  # noqa: E402
from models import (AcademicYear, AttendanceRecord, ClassLeave, ClassLeaveType, Employee, Holiday, Period,  # noqa: E402
                    Section, Semester, Stage, Grade, Student, SuperAdmin, TimetableEntry, User, UserSection, UserStage)
from security import hash_password  # noqa: E402
from tenancy import tenant_context  # noqa: E402
from tests.conftest import PASSWORD, make_dept, make_tenant, make_user  # noqa: E402

L = (lambda ar, en: en if EN else ar)
app = create_app({'MAIL_BACKEND': 'memory', 'WTF_CSRF_ENABLED': False, 'SESSION_COOKIE_SECURE': False})
with app.app_context():
    from flask_migrate import upgrade
    upgrade(directory=os.path.join(ROOT, 'migrations'))
    from utils.school import today
    t = make_tenant('raed', L('مدرسة تجريبية', 'Trial School'))
    if EN:
        t.name_en = 'Trial School'
    db.session.commit()
    office = make_dept(t, 'ADM', L('الإدارة', 'Administration'), emails='office@raed.test')
    admin = make_user(t, 'admin', ('admin',), email='admin@raed.test')
    sup = make_user(t, 'sup', ('supervisor',), email='sup@raed.test')
    teacher = make_user(t, 'teach', ('teacher',), email='teach@raed.test')
    db.session.add(SuperAdmin(username='root', password_hash=hash_password(PASSWORD)))
    db.session.commit()
    with tenant_context(t.id):
        if EN:
            for u in User.query.all():
                u.full_name = f'User {u.username}'
        d0 = today()
        y = AcademicYear(name='2026-2027', start_date=d0 - timedelta(days=60), end_date=d0 + timedelta(days=240),
                         is_current=True)
        db.session.add(y)
        db.session.flush()
        db.session.add(Semester(year_id=y.id, name=L('الفصل الأول', 'First semester'), name_en='First semester',
                                start_date=y.start_date, end_date=d0 + timedelta(days=60), sort_order=1))
        ids = {}
        names = [[L('ليان أحمد', 'Layan Ahmad'), L('يزن خالد', 'Yazan Khaled'), L('سارة محمود', 'Sara Mahmoud')],
                 [L('عمر سليم', 'Omar Saleem'), L('رهف علي', 'Rahaf Ali'), L('كريم يوسف', 'Kareem Yousef')]]
        students = []
        for n, (stage_name, grade_name) in enumerate([(L('المرحلة الأساسية', 'Primary stage'), L('الصف الخامس', 'Grade 5')),
                                                      (L('المرحلة الثانوية', 'Secondary stage'), L('الصف العاشر', 'Grade 10'))], 1):
            st = Stage(name=stage_name, name_en=L(None, stage_name), sort_order=n, school_days='0,1,2,3,4,5,6')
            db.session.add(st)
            db.session.flush()
            gr = Grade(stage_id=st.id, name=grade_name, sort_order=n)
            db.session.add(gr)
            db.session.flush()
            sec = Section(grade_id=gr.id, name=L('أ', 'A'), homeroom_teacher=L('مستخدم teach', 'User teach'))
            db.session.add(sec)
            db.session.flush()
            for i, nm in enumerate(names[n - 1]):
                s = Student(section_id=sec.id, full_name=nm, student_number=f'{n}0{i + 1}', gender='mf'[i % 2],
                            guardian_name=L('ولي الأمر', 'Guardian'), guardian_phone='0790000000',
                            guardian_email=None if i == 2 else f'parent{n}{i}@raed.test')
                db.session.add(s)
                students.append(s)
            for k in range(1, 4):
                p = Period(stage_id=st.id, weekday=d0.weekday(), sort_order=k, kind='period',
                           label=L(f'الحصة {k}', f'Period {k}'), start_time=f'0{7 + k}:00', end_time=f'0{7 + k}:45')
                db.session.add(p)
                db.session.flush()
                db.session.add(TimetableEntry(section_id=sec.id, period_id=p.id, subject=L('الرياضيات', 'Mathematics'),
                                              teacher_name=L('مستخدم teach', 'User teach'), teacher_user_id=teacher))
            ids[f'stage{n}'], ids[f'section{n}'] = st.id, sec.id
        db.session.add(UserStage(user_id=sup, stage_id=ids['stage1']))
        db.session.add(UserSection(user_id=teacher, section_id=ids['section1']))
        db.session.flush()
        kinds = [('absent', 'approved'), ('late', 'pending'), ('excused', 'approved'), ('early_leave', 'rejected')]
        for i, (kind, status) in enumerate(kinds):
            r = AttendanceRecord(student_id=students[i].id, record_date=d0 - timedelta(days=i), kind=kind, status=status,
                                 all_day=kind in ('absent', 'excused'), time_from=None if kind in ('absent', 'excused') else '08:10',
                                 notes=L('ملاحظة من المعلم', 'A note from the teacher'), created_by=teacher)
            db.session.add(r)
        db.session.flush()
        rec_id = AttendanceRecord.query.first().id
        lt = ClassLeaveType.query.first()
        if lt is None:
            lt = ClassLeaveType(name=L('العيادة', 'Clinic'), name_en='Clinic')
            db.session.add(lt)
            db.session.flush()
        db.session.add(ClassLeave(student_id=students[1].id, leave_type_id=lt.id, leave_date=d0, leave_time='09:15',
                                  return_time='09:40', status='approved', notes=L('عاد إلى الصف', 'Back in class')))
        db.session.add(Holiday(title=L('عطلة رسمية', 'Public holiday'), start_date=d0 + timedelta(days=10),
                               end_date=d0 + timedelta(days=11), status='approved'))
        db.session.add(Employee(full_name=L('هبة علي', 'Hiba Ali'), employee_number='501', email='hiba@raed.test',
                                job_title=L('مرشدة تربوية', 'School counsellor'), department_id=office))
        db.session.commit()
        sid = students[0].id
        leave_id = ClassLeave.query.first().id
        hid = Holiday.query.first().id
        emp_id = Employee.query.first().id
    print(json.dumps({'tenant': t.id, 'student': sid, 'section': ids['section1'], 'record': rec_id, 'leave': leave_id,
                      'holiday': hid, 'employee': emp_id, 'dept': office, 'user': admin}))
