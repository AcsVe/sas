from extensions import db
from models import SuperAdmin, Tenant, User, Role
from security import hash_password
from tenancy import tenant_bypass, tenant_context
from tests.conftest import login, PASSWORD


def _make_super():
    db.session.add(SuperAdmin(username='root', password_hash=hash_password(PASSWORD)))
    db.session.commit()


def test_superadmin_creates_tenant(app, ctx):
    _make_super()
    c = app.test_client()
    assert c.post('/super/login', data={'username': 'root', 'password': PASSWORD}).status_code == 302
    r = c.post('/super/tenants/new', data={
        'slug': 'raed', 'name_ar': 'مدرسة الرائد العربي', 'name_en': 'Al-Raed',
        'accent_color': '#0C67EC', 'admin_username': 'hradmin', 'admin_full_name': 'مدير',
        'admin_email': 'hr@raed.test', 'admin_password': 'Temp-Pass-2026'})
    assert r.status_code == 302
    with tenant_bypass():
        t = Tenant.query.filter_by(slug='raed').one()
    with tenant_context(t.id):
        assert Role.query.count() == 5
        u = User.query.filter_by(username='hradmin').one()
        assert u.must_change_password and u.has_perm('users.manage')
    # the new admin logs in (single tenant → no org field) and must change password
    tc = app.test_client()
    assert login(tc, 'hradmin', 'Temp-Pass-2026').status_code == 302
    assert '/change-password' in tc.get('/home').headers['Location']


def test_superadmin_pages_need_superadmin(app, two_tenants):
    c = app.test_client()
    login(c, 'admin', org='a')                      # a tenant admin
    r = c.get('/super/')
    assert r.status_code == 302 and '/super/login' in r.headers['Location']


def test_superadmin_session_has_no_tenant_access(app, ctx):
    _make_super()
    c = app.test_client()
    c.post('/super/login', data={'username': 'root', 'password': PASSWORD})
    r = c.get('/admin/users')
    assert r.status_code == 302 and '/login' in r.headers['Location']


def test_superadmin_lockout(app, ctx):
    _make_super()
    c = app.test_client()
    for _ in range(5):
        c.post('/super/login', data={'username': 'root', 'password': 'nope-nope-1'})
    assert c.post('/super/login', data={'username': 'root', 'password': PASSWORD}).status_code == 401
