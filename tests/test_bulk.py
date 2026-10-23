"""Bulk select / delete / activate on the lists, the clickable dashboard and the users card view."""
from extensions import db
from models import User, Role, Department, Tenant
from tenancy import tenant_context
from tests.conftest import login, make_user, make_dept


def _admin(app):
    c = app.test_client()
    login(c, 'admin', org='a')
    return c


def test_users_bulk_protects_self_and_tenant(app, two_tenants):
    t = two_tenants
    c = _admin(app)
    r = c.post('/admin/users/bulk', data={'action': 'deactivate',
                                          'ids': [t['admin_a'], t['emp_a'], t['secret_b']]})
    assert r.status_code == 302
    with tenant_context(t['a']):
        assert db.session.get(User, t['admin_a']).is_active          # never yourself
        assert not db.session.get(User, t['emp_a']).is_active
    with tenant_context(t['b']):
        assert db.session.get(User, t['secret_b']).is_active          # other tenant untouched
    c.post('/admin/users/bulk', data={'action': 'activate', 'ids': [t['emp_a']]})
    c.post('/admin/users/bulk', data={'action': 'delete', 'ids': [t['emp_a'], t['admin_a']]})
    with tenant_context(t['a']):
        assert db.session.get(User, t['emp_a']) is None
        assert db.session.get(User, t['admin_a']) is not None


def test_users_bulk_keeps_last_admin(app, two_tenants):
    t = two_tenants
    a = db.session.get(Tenant, t['a'])
    second = make_user(a, 'admin2', ('admin',))
    third = make_user(a, 'admin3', ('admin',))
    c = _admin(app)
    # admin is signed in, so admin2/admin3 may go — admin itself stays as the last administrator
    c.post('/admin/users/bulk', data={'action': 'deactivate', 'ids': [second, third]})
    with tenant_context(t['a']):
        assert not db.session.get(User, second).is_active and not db.session.get(User, third).is_active
        assert db.session.get(User, t['admin_a']).is_active


def test_departments_bulk(app, two_tenants):
    t = two_tenants
    a = db.session.get(Tenant, t['a'])
    hr1 = make_dept(a, 'HR', 'شؤون الموظفين', 'HR')
    hr2 = make_dept(a, 'HR2', 'شؤون 2', 'HR')
    c = _admin(app)
    c.post('/admin/departments/bulk', data={'action': 'deactivate', 'ids': [hr2]})
    # a second active HR department is refused on bulk activate
    r = c.post('/admin/departments/bulk', data={'action': 'activate', 'ids': [hr2]}, follow_redirects=True)
    assert 'يوجد قسم فعّال من النوع نفسه' in r.get_data(as_text=True)
    c.post('/admin/departments/bulk', data={'action': 'delete', 'ids': [t['dept_a2'], hr2, t['dept_b']]})
    with tenant_context(t['a']):
        assert db.session.get(Department, t['dept_a2']) is None
        assert db.session.get(Department, hr2) is None
        assert db.session.get(Department, hr1) is not None
    with tenant_context(t['b']):
        assert db.session.get(Department, t['dept_b']) is not None


def test_roles_bulk_keeps_system_and_used(app, two_tenants):
    t = two_tenants
    c = _admin(app)
    c.post('/admin/roles/new', data={'name': 'دور مؤقت', 'perms': ['reports.view']})
    with tenant_context(t['a']):
        custom = Role.query.filter_by(name='دور مؤقت').one().id
        sysrole = Role.query.filter_by(key='teacher').one().id
    c.post('/admin/roles/bulk', data={'action': 'delete', 'ids': [custom, sysrole]})
    with tenant_context(t['a']):
        assert db.session.get(Role, custom) is None
        assert db.session.get(Role, sysrole) is not None


def test_dashboard_and_cards_render(app, two_tenants):
    c = _admin(app)
    home = c.get('/home').get_data(as_text=True)
    assert 'class="kpi' in home and 'الحسابات الفعّالة' in home and 'f=' in home
    page = c.get('/admin/users').get_data(as_text=True)
    assert 'data-cards' in page and 'data-table' in page and 'الغياب:' in page
    assert 'secretuser' not in page                                    # other tenant's user
    assert c.get('/admin/departments').status_code == 200
    assert c.get('/admin/roles').status_code == 200
    assert c.get('/admin/email-log').status_code == 200
    assert c.get('/admin/audit').status_code == 200


def test_employee_dashboard_has_no_admin_numbers(app, two_tenants):
    c = app.test_client()
    login(c, 'emp', org='a')
    page = c.get('/home').get_data(as_text=True)
    assert 'الحسابات الفعّالة' not in page
