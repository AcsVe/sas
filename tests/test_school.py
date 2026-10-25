"""SAS: structure, students, scope (no IDOR), daily sheet, approval, guardian e-mail, class leave, holidays,
reports and the pages themselves."""
import io
from datetime import date, timedelta

import pytest

from extensions import db
from models import (Tenant, Stage, Grade, Section, Student, AttendanceRecord, ClassLeave, ClassLeaveType,
                    Holiday, UserStage, Period, TimetableEntry, AcademicYear, EmailLog, Attachment)
from tenancy import tenant_context
from tests.conftest import login, make_user

TODAY = None


@pytest.fixture
def school(two_tenants):
    """Tenant A: two stages (each one grade, one section, two students), a supervisor of stage 1,
    a secretary of stage 1 and a teacher. Tenant B: one stage with a student."""
    global TODAY
    from utils.school import today
    t = dict(two_tenants)
    with tenant_context(t['a']):
        TODAY = today()
        ids = {}
        for n in (1, 2):
            st = Stage(name=f'المرحلة {n}', sort_order=n, school_days='0,1,2,3,4,5,6')
            db.session.add(st)
            db.session.flush()
            gr = Grade(stage_id=st.id, name=f'الصف {n}')
            db.session.add(gr)
            db.session.flush()
            sec = Section(grade_id=gr.id, name='أ')
            db.session.add(sec)
            db.session.flush()
            s1 = Student(section_id=sec.id, full_name=f'طالب {n}-1', student_number=f'{n}01',
                         guardian_email=f'parent{n}1@x.test', guardian_name='ولي')
            s2 = Student(section_id=sec.id, full_name=f'طالب {n}-2', student_number=f'{n}02')
            db.session.add_all([s1, s2])
            db.session.flush()
            ids.update({f'stage{n}': st.id, f'grade{n}': gr.id, f'sec{n}': sec.id,
                        f'st{n}1': s1.id, f'st{n}2': s2.id})
        db.session.commit()
    ta = db.session.get(Tenant, t['a'])
    ids['sup'] = make_user(ta, 'sup', ('supervisor',), email='sup@a.test')
    ids['sec_user'] = make_user(ta, 'secy', ('secretary',), email='secy@a.test')
    ids['teacher'] = make_user(ta, 'teach', ('teacher',), email='teach@a.test')
    with tenant_context(t['a']):
        db.session.add(UserStage(user_id=ids['sup'], stage_id=ids['stage1']))
        db.session.add(UserStage(user_id=ids['sec_user'], stage_id=ids['stage1']))
        db.session.commit()
    with tenant_context(t['b']):
        st = Stage(name='مرحلة ب')
        db.session.add(st)
        db.session.flush()
        gr = Grade(stage_id=st.id, name='صف ب')
        db.session.add(gr)
        db.session.flush()
        sec = Section(grade_id=gr.id, name='ب')
        db.session.add(sec)
        db.session.flush()
        sb = Student(section_id=sec.id, full_name='طالب المنظمة ب')
        db.session.add(sb)
        db.session.commit()
        ids['st_b'] = sb.id
    t.update(ids)
    return t


def _client(app, user):
    c = app.test_client()
    assert login(c, user, org='a').status_code == 302
    return c


def test_default_leave_types_seeded(school):
    with tenant_context(school['a']):
        assert ClassLeaveType.query.count() == 7


def test_structure_crud_bulk_and_protected_delete(app, school):
    c = _client(app, 'admin')
    r = c.post('/structure/sections/new', data={'grade_id': school['grade1'], 'name': 'ب\nج\nد', 'sort_order': '1',
                                                'is_active': '1'})
    assert r.status_code == 302
    with tenant_context(school['a']):
        names = sorted(s.name for s in Section.query.filter_by(grade_id=school['grade1']).all())
        assert names == ['أ', 'ب', 'ج', 'د']
        empty = Section.query.filter_by(name='د').one().id
    # a section with students is not deleted; an empty one is
    c.post('/structure/sections/bulk', data={'action': 'delete', 'ids': [school['sec1'], empty]})
    with tenant_context(school['a']):
        assert db.session.get(Section, school['sec1']) is not None
        assert db.session.get(Section, empty) is None
    c.post('/structure/sections/bulk', data={'action': 'deactivate', 'ids': [school['sec1']]})
    with tenant_context(school['a']):
        assert not db.session.get(Section, school['sec1']).is_active
    # stage with grades is protected
    c.post('/structure/stages/bulk', data={'action': 'delete', 'ids': [school['stage1']]})
    with tenant_context(school['a']):
        assert db.session.get(Stage, school['stage1']) is not None
    # one current year only
    c.post('/structure/years/new', data={'name': '2025-2026', 'is_current': '1', 'is_active': '1'})
    c.post('/structure/years/new', data={'name': '2026-2027', 'is_current': '1', 'is_active': '1'})
    with tenant_context(school['a']):
        assert [y.name for y in AcademicYear.query.filter_by(is_current=True)] == ['2026-2027']
    # validation
    r = c.post('/structure/periods/new', data={'stage_id': school['stage1'], 'weekday': '6', 'label': 'الأولى',
                                               'kind': 'period', 'start_time': '08:45', 'end_time': '08:00'})
    with tenant_context(school['a']):
        assert Period.query.count() == 0
    for key in ('years', 'semesters', 'stages', 'grades', 'sections', 'leave_types', 'periods'):
        assert c.get(f'/structure/{key}/').status_code == 200, key


def test_structure_needs_permission(app, school):
    c = _client(app, 'sup')
    assert c.get('/structure/stages/').status_code == 403
    assert c.post('/structure/stages/bulk', data={'action': 'delete', 'ids': [school['stage2']]}).status_code == 403


def test_students_import_and_protected_delete(app, school):
    c = _client(app, 'admin')
    text = 'رقم الطالب\tالاسم\tالصف\tالشعبة\tبريد ولي الأمر\n900\tسعيد أحمد\tالصف 2\tأ\tp@x.test\n901\tبلا شعبة\t\t\t'
    r = c.post('/students/import', data={'text': text})
    import re
    token = re.search(r'/students/import/([0-9a-f]{32})/apply', r.data.decode()).group(1)
    with tenant_context(school['a']):
        assert Student.query.filter_by(student_number='900').first() is None        # preview writes nothing
    c.post(f'/students/import/{token}/apply')
    with tenant_context(school['a']):
        s = Student.query.filter_by(student_number='900').one()
        assert s.section_id == school['sec2'] and s.guardian_email == 'p@x.test'
        assert Student.query.filter_by(student_number='901').first() is None      # no section → refused
        db.session.add(AttendanceRecord(student_id=school['st11'], record_date=TODAY, kind='absent'))
        db.session.commit()
        new_id = s.id
    c.post('/students/bulk', data={'action': 'delete', 'ids': [school['st11'], new_id]})
    with tenant_context(school['a']):
        assert db.session.get(Student, school['st11']) is not None
        assert db.session.get(Student, new_id) is None
    r = c.get('/students/?export=xlsx')
    assert r.status_code == 200 and r.data[:2] == b'PK'


def test_supervisor_scope_no_idor(app, school):
    c = _client(app, 'sup')
    page = c.get('/students/').get_data(as_text=True)
    assert 'طالب 1-1' in page and 'طالب 2-1' not in page
    assert c.get(f"/students/{school['st21']}").status_code == 404
    assert c.get(f"/students/{school['st_b']}").status_code == 404
    # daily sheet on another stage's section
    assert c.post('/attendance/daily', data={'section': school['sec2'], 'student_ids': [school['st21']]}
                  ).status_code == 404
    # a record of stage 2 cannot be approved, deleted or notified by sending its id
    with tenant_context(school['a']):
        r = AttendanceRecord(student_id=school['st21'], record_date=TODAY, kind='absent', status='pending')
        db.session.add(r)
        db.session.commit()
        rid = r.id
    c.post('/attendance/records/bulk', data={'action': 'approve', 'ids': [rid]})
    c.post('/attendance/records/bulk', data={'action': 'delete', 'ids': [rid]})
    c.post('/attendance/notify/send', data={'ids': [rid]})
    with tenant_context(school['a']):
        r = db.session.get(AttendanceRecord, rid)
        assert r is not None and r.status == 'pending' and r.notified_at is None
    assert c.get(f'/attendance/record/{rid}').status_code == 404
    assert 'طالب 2-1' not in c.get('/attendance/records').get_data(as_text=True)
    # moving a student into another stage's section is refused
    c.post('/students/bulk', data={'action': 'move', 'section_id': school['sec2'], 'ids': [school['st11']]})
    with tenant_context(school['a']):
        assert db.session.get(Student, school['st11']).section_id == school['sec1']


def test_daily_sheet_status_and_duplicates(app, school):
    sup = _client(app, 'sup')
    sup.post('/attendance/daily', data={'section': school['sec1'], 'date': TODAY.isoformat(), 'kind': 'absent',
                                        'student_ids': [school['st11'], school['st12']]})
    sup.post('/attendance/daily', data={'section': school['sec1'], 'date': TODAY.isoformat(), 'kind': 'absent',
                                        'student_ids': [school['st11']]})
    secy = _client(app, 'secy')
    secy.post('/attendance/daily', data={'section': school['sec1'], 'date': TODAY.isoformat(), 'kind': 'late',
                                         'all_day': '0', 'time_from': '07:50',
                                         'student_ids': [school['st11']]})
    with tenant_context(school['a']):
        recs = AttendanceRecord.query.order_by(AttendanceRecord.id).all()
        assert [(r.kind, r.status) for r in recs] == [('absent', 'approved'), ('absent', 'approved'),
                                                      ('late', 'pending')]
        assert recs[2].time_from == '07:50' and not recs[2].all_day
        late = recs[2].id
    # the secretary cannot approve
    assert secy.post('/attendance/records/bulk', data={'action': 'approve', 'ids': [late]}).status_code == 403
    sup.post('/attendance/records/bulk', data={'action': 'approve', 'ids': [late]})
    with tenant_context(school['a']):
        assert db.session.get(AttendanceRecord, late).status == 'approved'
    page = sup.get(f"/attendance/daily?section={school['sec1']}&date={TODAY.isoformat()}").get_data(as_text=True)
    assert 'طالب 1-1' in page and 'تأخر' in page


def test_record_with_attachment(app, school):
    c = _client(app, 'sup')
    r = c.post('/attendance/record/new', data={
        'student': school['st11'], 'record_date': TODAY.isoformat(), 'kind': 'excused', 'all_day': '1',
        'notes': 'تقرير طبي', 'attachment': (io.BytesIO(b'%PDF-1.4 test'), 'report.pdf')},
        content_type='multipart/form-data')
    assert r.status_code == 302
    with tenant_context(school['a']):
        rec = AttendanceRecord.query.one()
        att_id = rec.attachment_id
        assert db.session.get(Attachment, att_id).original_name == 'report.pdf'
    assert c.get(f'/attendance/attachment/{att_id}').data.startswith(b'%PDF')
    other = app.test_client()
    login(other, 'teach', org='a')
    assert other.get(f'/attendance/attachment/{att_id}').status_code == 404
    r = c.post('/attendance/record/new', data={
        'student': school['st12'], 'record_date': TODAY.isoformat(), 'kind': 'absent', 'all_day': '1',
        'attachment': (io.BytesIO(b'MZ'), 'virus.exe')}, content_type='multipart/form-data')
    assert r.status_code == 400


def test_guardian_email_manual_with_duplicate_guard(app, school):
    with tenant_context(school['a']):
        for sid in (school['st11'], school['st12']):
            db.session.add(AttendanceRecord(student_id=sid, record_date=TODAY, kind='absent', status='approved'))
        db.session.commit()
        ids = [r.id for r in AttendanceRecord.query.all()]
    c = _client(app, 'sup')
    preview = c.get('/attendance/notify').get_data(as_text=True)
    assert 'parent11@x.test' in preview and 'طالب 1-2' in preview          # one to send, one without e-mail
    assert app.config['MAIL_OUTBOX'] == []                                 # nothing sent by a preview
    c.post('/attendance/notify/send', data={'ids': ids})
    assert len(app.config['MAIL_OUTBOX']) == 1
    mail = app.config['MAIL_OUTBOX'][0]
    assert mail['to'] == ['parent11@x.test'] and 'طالب 1-1' in mail['subject']
    c.post('/attendance/notify/send', data={'ids': ids})                   # already notified → skipped
    assert len(app.config['MAIL_OUTBOX']) == 1
    c.post('/attendance/notify/send', data={'ids': ids, 'resend': '1'})
    assert len(app.config['MAIL_OUTBOX']) == 2
    with tenant_context(school['a']):
        r = AttendanceRecord.query.filter_by(student_id=school['st11']).one()
        assert r.notified_count == 2 and r.notified_at is not None
        assert EmailLog.query.filter_by(kind='guardian_absence', status='sent').count() == 2
    teacher = _client(app, 'teach')
    assert teacher.get('/attendance/notify').status_code == 403


def test_class_leave_out_and_return(app, school):
    with tenant_context(school['a']):
        lib = ClassLeaveType.query.filter_by(name='المكتبة').one().id
        db.session.add(TimetableEntry(section_id=school['sec1'], period_id=_period(school), subject='رياضيات',
                                      teacher_user_id=school['teacher']))
        db.session.commit()
    c = _client(app, 'teach')                    # sees sec1 through the timetable only
    assert c.get(f"/leaves/?section={school['sec2']}").get_data(as_text=True).count('طالب 2-1') == 0
    c.post('/leaves/new', data={'section': school['sec1'], 'leave_type_id': lib, 'leave_time': '09:00',
                                'student_ids': [school['st11']]})
    c.post('/leaves/new', data={'section': school['sec2'], 'leave_type_id': lib, 'student_ids': [school['st21']]})
    with tenant_context(school['a']):
        leaves = ClassLeave.query.all()
        assert len(leaves) == 1 and leaves[0].is_out and leaves[0].status == 'pending'
        lid = leaves[0].id
    assert 'خارج الصف' in c.get('/leaves/?out=1').get_data(as_text=True)
    c.post('/leaves/bulk', data={'action': 'return', 'ids': [lid]})
    with tenant_context(school['a']):
        assert not db.session.get(ClassLeave, lid).is_out
    assert c.get('/timetable/mine').status_code == 200
    assert c.get(f"/students/{school['st11']}").status_code == 200
    assert c.get(f"/students/{school['st21']}").status_code == 404


def _period(school):
    p = Period(stage_id=school['stage1'], weekday=6, label='الأولى', start_time='08:00', end_time='08:45')
    db.session.add(p)
    db.session.flush()
    return p.id


def test_holidays_scope_and_daily_notice(app, school):
    sup = _client(app, 'sup')
    sup.post('/holidays/new', data={'title': 'عطلة المدرسة', 'start_date': TODAY.isoformat(), 'scope_type': 'school'})
    sup.post('/holidays/new', data={'title': 'عطلة المرحلة 2', 'start_date': TODAY.isoformat(),
                                    'scope_type': 'stages', 'scope_ids_stages': [school['stage2']]})
    sup.post('/holidays/new', data={'title': 'عطلة المرحلة 1', 'start_date': TODAY.isoformat(),
                                    'scope_type': 'stages', 'scope_ids_stages': [school['stage1']]})
    with tenant_context(school['a']):
        hs = Holiday.query.all()
        assert [h.title for h in hs] == ['عطلة المرحلة 1'] and hs[0].status == 'pending'
        hid = hs[0].id
    assert sup.post('/holidays/bulk', data={'action': 'approve', 'ids': [hid]}).status_code == 403
    admin = _client(app, 'admin')
    admin.post('/holidays/bulk', data={'action': 'approve', 'ids': [hid]})
    page = sup.get(f"/attendance/daily?section={school['sec1']}&date={TODAY.isoformat()}").get_data(as_text=True)
    assert 'عطلة المرحلة 1' in page
    with tenant_context(school['a']):
        h = db.session.get(Holiday, hid)
        assert h.covers(TODAY, db.session.get(Section, school['sec1']))
        assert not h.covers(TODAY, db.session.get(Section, school['sec2']))


def test_reports_rate_zero_safe_and_exports(app, school):
    from blueprints.reports import rate
    assert rate(3, 0) == 0.0 and rate(1, 4) == 25.0
    with tenant_context(school['a']):
        db.session.add(AttendanceRecord(student_id=school['st11'], record_date=TODAY, kind='absent',
                                        status='approved'))
        db.session.add(AttendanceRecord(student_id=school['st12'], record_date=TODAY, kind='absent',
                                        status='rejected'))
        db.session.commit()
    c = _client(app, 'admin')
    d = TODAY.isoformat()
    page = c.get(f'/reports/?date_from={d}&date_to={d}').get_data(as_text=True)
    assert '25.0' in page                         # 1 of 4 students, 1 day; the rejected record is not counted
    assert c.get(f'/reports/?date_from={d}&date_to={d}&export=xlsx').data[:2] == b'PK'
    assert c.get('/reports/compare').status_code == 200
    assert c.get(f'/reports/?date_from={d}&date_to={d}&print=1').status_code == 200
    sup = _client(app, 'sup')
    page = sup.get(f'/reports/?date_from={d}&date_to={d}').get_data(as_text=True)
    assert 'المرحلة 2' not in page.split('حسب الشعبة')[1]


def test_dashboard_numbers_link_to_filtered_lists(app, school):
    with tenant_context(school['a']):
        db.session.add(AttendanceRecord(student_id=school['st11'], record_date=TODAY, kind='absent',
                                        status='pending'))
        db.session.commit()
    home = _client(app, 'sup').get('/home').get_data(as_text=True)
    assert 'غياب اليوم' in home and 'kind=absent' in home and 'status=pending' in home
    assert 'لم يُشعر أولياء أمورهم' in home
    t_home = _client(app, 'teach').get('/home').get_data(as_text=True)
    assert 'خارج الصف الآن' in t_home and 'غياب اليوم' not in t_home


def test_user_form_scope_checkboxes(app, school):
    c = _client(app, 'admin')
    with tenant_context(school['a']):
        from models import Role
        role = Role.query.filter_by(key='supervisor').one().id
    r = c.post('/admin/users/new', data={'username': 'sup2', 'full_name': 'مشرف ثان', 'email': 'sup2@a.test',
                                         'roles': [str(role)], 'scope_form': '1',
                                         'stages': [str(school['stage2'])], 'sections': [str(school['sec1'])]})
    assert r.status_code == 302
    with tenant_context(school['a']):
        from models import User, UserSection
        u = User.query.filter_by(username='sup2').one()
        assert {x.stage_id for x in UserStage.query.filter_by(user_id=u.id)} == {school['stage2']}
        assert {x.section_id for x in UserSection.query.filter_by(user_id=u.id)} == {school['sec1']}
        from services.scope import visible_section_ids
        assert visible_section_ids(u) == {school['sec1'], school['sec2']}


def test_every_page_renders(app, school):
    with tenant_context(school['a']):
        r = AttendanceRecord(student_id=school['st11'], record_date=TODAY, kind='absent')
        db.session.add(r)
        h = Holiday(title='ع', start_date=TODAY, end_date=TODAY + timedelta(days=1))
        db.session.add(h)
        db.session.commit()
        rid, hid = r.id, h.id
    c = _client(app, 'admin')
    for url in ['/home', '/attendance/daily', f"/attendance/daily?section={school['sec1']}", '/attendance/records',
                f'/attendance/record/{rid}', f"/attendance/record/new?student={school['st12']}",
                '/attendance/notify', '/leaves/', f"/leaves/?section={school['sec1']}", '/holidays/',
                f'/holidays/{hid}', '/students/', f"/students/{school['st11']}",
                f"/students/{school['st11']}/edit", f"/timetable/section/{school['sec1']}", '/timetable/mine',
                '/reports/', '/reports/compare', '/employees/', '/admin/users/new', '/admin/settings',
                f"/structure/stages/{school['stage1']}", '/students/template.csv',
                '/attendance/records?export=xlsx', '/leaves/?export=xlsx']:
        assert c.get(url).status_code == 200, url
