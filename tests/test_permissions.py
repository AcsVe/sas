from extensions import db
from models import User, Role, AuditLog, EmailLog, Department
from authz import can_access_department
from tenancy import tenant_context
from tests.conftest import login, make_user, PASSWORD


def test_admin_pages_require_permission(app, two_tenants):
    t = two_tenants
    from models import Tenant
    make_user(db.session.get(Tenant, t['a']), 'hr1', ('principal',))
    for username, expect in [('emp', 403), ('mgr', 403), ('hr1', 403), ('admin', 200)]:
        c = app.test_client()
        login(c, username, org='a')
        assert c.get('/admin/users').status_code == expect, username
        assert c.get('/admin/roles').status_code == expect, username
    c = app.test_client()
    login(c, 'hr1', org='a')
    assert c.get('/admin/email-log').status_code == 200      # the principal sees e-mail errors
    assert c.get('/admin/audit').status_code == 403


def test_anonymous_redirected_to_login(app, two_tenants):
    r = app.test_client().get('/admin/users')
    assert r.status_code == 302 and '/login' in r.headers['Location']


def test_department_scoping(two_tenants):
    t = two_tenants
    with tenant_context(t['a']):
        mgr = db.session.get(User, t['mgr_a'])
        admin = db.session.get(User, t['admin_a'])
        emp = db.session.get(User, t['emp_a'])
        assert can_access_department(mgr, t['dept_a'], 'attendance.record', 'attendance.view_all')
        assert not can_access_department(mgr, t['dept_a2'], 'attendance.record', 'attendance.view_all')
        assert can_access_department(admin, t['dept_a2'], 'attendance.record', 'attendance.view_all')
        assert not can_access_department(emp, t['dept_a'], 'attendance.record', 'attendance.view_all')


def test_last_admin_cannot_lose_admin_rights(app, two_tenants):
    t = two_tenants
    c = app.test_client()
    login(c, 'admin', org='a')
    with tenant_context(t['a']):
        emp_role = Role.query.filter_by(key='teacher').one().id
    r = c.post(f"/admin/users/{t['admin_a']}", data={
        'username': 'admin', 'full_name': 'المدير', 'email': 'admin@a.test',
        'roles': [str(emp_role)], 'is_active': '1'})
    assert r.status_code == 400
    with tenant_context(t['a']):
        assert db.session.get(User, t['admin_a']).has_perm('users.manage')
    with tenant_context(t['a']):
        admin_role = Role.query.filter_by(key='admin').one()
        codes = [c_ for c_ in admin_role.permission_codes if c_ != 'users.manage']
    r = c.post(f'/admin/roles/{admin_role.id}', data={'name': 'مدير النظام', 'perms': codes})
    assert r.status_code == 400


def test_create_user_sends_setup_link_and_audits(app, two_tenants):
    t = two_tenants
    c = app.test_client()
    login(c, 'admin', org='a')
    with tenant_context(t['a']):
        role = Role.query.filter_by(key='supervisor').one().id
    r = c.post('/admin/users/new', data={
        'username': 'newmgr', 'full_name': 'مسؤول جديد', 'email': 'newmgr@a.test',
        'roles': [str(role)], 'departments': [str(t['dept_a2'])]})
    assert r.status_code == 302
    assert app.config['MAIL_OUTBOX'][-1]['to'] == ['newmgr@a.test']
    with tenant_context(t['a']):
        u = User.query.filter_by(username='newmgr').one()
        assert u.department_ids == {t['dept_a2']} and u.has_perm('attendance.approve')
        entry = AuditLog.query.filter_by(action='user.created').one()
        assert entry.user_id == t['admin_a'] and entry.ip_address


def test_html_is_escaped(app, two_tenants):
    t = two_tenants
    payload = '<script>alert(1)</script>'
    with tenant_context(t['a']):
        db.session.add(Department(code='XSS', name=payload, manager_name=payload))
        db.session.commit()
    c = app.test_client()
    login(c, 'admin', org='a')
    html = c.get('/admin/departments').get_data(as_text=True)
    assert payload not in html and '&lt;script&gt;alert(1)&lt;/script&gt;' in html


def test_email_failure_is_logged_and_visible(app, two_tenants):
    t = two_tenants
    app.config['MAIL_BACKEND'] = 'graph'          # Graph not configured in tests → must fail loudly
    c = app.test_client()
    login(c, 'admin', org='a')
    r = c.post(f"/admin/users/{t['emp_a']}/action", data={'action': 'send_reset'},
               follow_redirects=True)
    assert 'فشل الإرسال' in r.get_data(as_text=True)
    with tenant_context(t['a']):
        log = EmailLog.query.one()
        assert log.status == 'failed' and 'MS_' in log.error
    page = c.get('/admin/email-log?failed=1').get_data(as_text=True)
    assert 'فشل' in page and 'emp@a.test' in page


def test_department_type_uniqueness(app, two_tenants):
    c = app.test_client()
    login(c, 'admin', org='a')
    ok = c.post('/admin/departments/new', data={'code': 'HR', 'name': 'شؤون الموظفين',
                                                 'dept_type': 'HR', 'emails': 'hr@a.test; x@a.test',
                                                 'is_active': '1'})
    assert ok.status_code == 302
    dup = c.post('/admin/departments/new', data={'code': 'HR2', 'name': 'HR آخر',
                                                  'dept_type': 'HR', 'is_active': '1'})
    assert dup.status_code == 400
    bad = c.post('/admin/departments/new', data={'code': 'Q', 'name': 'ق', 'dept_type': 'normal',
                                                  'emails': 'not-an-email', 'is_active': '1'})
    assert bad.status_code == 400
    with tenant_context(two_tenants['a']):
        assert Department.query.filter_by(code='HR').one().emails == 'hr@a.test, x@a.test'
