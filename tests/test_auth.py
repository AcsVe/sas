import re
from datetime import timedelta

from extensions import db
from models import User, PasswordResetToken, AuditLog, EmailLog
from models.core import utcnow
from tenancy import tenant_context, tenant_bypass
from tests.conftest import login, make_tenant, make_user, csrf_from, PASSWORD


def test_password_is_hashed(two_tenants):
    with tenant_context(two_tenants['a']):
        u = db.session.get(User, two_tenants['admin_a'])
        assert PASSWORD not in u.password_hash and u.password_hash.startswith('scrypt:')


def test_login_success_and_wrong_password(app, two_tenants):
    c = app.test_client()
    r = login(c, 'admin', 'wrong-password-1', org='a')
    assert r.status_code == 401
    assert 'اسم المستخدم أو كلمة المرور غير صحيحة' in r.get_data(as_text=True)
    r2 = login(app.test_client(), 'nobody', 'whatever-123', org='a')
    assert r2.status_code == 401
    assert 'اسم المستخدم أو كلمة المرور غير صحيحة' in r2.get_data(as_text=True)
    r = login(c, 'admin', org='a')
    assert r.status_code == 302 and r.headers['Location'].endswith('/home')
    assert c.get('/home').status_code == 200


def test_login_by_email_and_wrong_org(app, two_tenants):
    assert login(app.test_client(), 'mgr@a.test', org='a').status_code == 302
    # tenant B's user cannot log into tenant A
    assert login(app.test_client(), 'secretuser', org='a').status_code == 401


def test_lockout_after_failures_and_admin_unlock(app, two_tenants):
    t = two_tenants
    c = app.test_client()
    for _ in range(5):
        assert login(c, 'emp', 'bad-password-9', org='a').status_code == 401
    r = login(c, 'emp', org='a')            # correct password, still locked
    assert r.status_code == 423
    with tenant_context(t['a']):
        assert AuditLog.query.filter_by(action='auth.locked').count() == 1
    admin = app.test_client()
    login(admin, 'admin', org='a')
    admin.post(f"/admin/users/{t['emp_a']}/action", data={'action': 'unlock'})
    assert login(c, 'emp', org='a').status_code == 302


def test_lock_expires(app, two_tenants):
    t = two_tenants
    with tenant_context(t['a']):
        u = db.session.get(User, t['emp_a'])
        u.locked_until = utcnow() - timedelta(minutes=1)
        db.session.commit()
    assert login(app.test_client(), 'emp', org='a').status_code == 302


def test_ip_throttle(app, two_tenants):
    app.config['LOGIN_IP_MAX_FAILURES'] = 3
    try:
        c = app.test_client()
        for i in range(3):
            login(c, f'ghost{i}', 'x-password-1', org='a')
        assert login(c, 'admin', org='a').status_code == 429
    finally:
        app.config['LOGIN_IP_MAX_FAILURES'] = 30


def test_deactivated_user_session_dies(app, two_tenants):
    t = two_tenants
    c = app.test_client()
    login(c, 'emp', org='a')
    assert c.get('/home').status_code == 200
    with tenant_context(t['a']):
        db.session.get(User, t['emp_a']).is_active = False
        db.session.commit()
    assert c.get('/home').status_code == 302


def test_password_change_logs_out_other_devices(app, two_tenants):
    c1, c2 = app.test_client(), app.test_client()
    login(c1, 'emp', org='a')
    login(c2, 'emp', org='a')
    r = c1.post('/change-password', data={'current': PASSWORD, 'password': 'New-Pass-2026x',
                                          'confirm': 'New-Pass-2026x'})
    assert r.status_code == 302
    assert c1.get('/home').status_code == 200
    assert c2.get('/home').status_code == 302


def test_weak_password_rejected(app, two_tenants):
    c = app.test_client()
    login(c, 'emp', org='a')
    r = c.post('/change-password', data={'current': PASSWORD, 'password': 'short1',
                                         'confirm': 'short1'})
    assert r.status_code == 200 and 'لا تقل عن' in r.get_data(as_text=True)


def test_must_change_password_redirect(app, ctx):
    t = make_tenant('solo')
    make_user(t, 'newbie', must_change=True)
    c = app.test_client()
    login(c, 'newbie')                          # single tenant: no org needed
    r = c.get('/home')
    assert r.status_code == 302 and '/change-password' in r.headers['Location']


def test_open_redirect_blocked(app, two_tenants):
    c = app.test_client()
    r = c.post('/login?next=//evil.example/x', data={'username': 'admin', 'password': PASSWORD,
                                                      'org': 'a'})
    assert r.headers['Location'].endswith('/home')
    r = c.post('/login?next=https://evil.example', data={'username': 'admin', 'password': PASSWORD,
                                                          'org': 'a'})


def _reset_link(app):
    mail = app.config['MAIL_OUTBOX'][-1]
    m = re.search(r'href="(https://sas\.test/reset/[^"]+)"', mail['html'])
    return m.group(1)


def test_password_reset_flow_single_use(app, two_tenants):
    c = app.test_client()
    r = c.post('/forgot', data={'username': 'emp', 'org': 'a'},
               headers={'Host': 'evil.example'})
    assert r.status_code == 302
    link = _reset_link(app)                     # built from BASE_URL, not the Host header
    assert 'evil.example' not in link
    path = link.replace('https://sas.test', '')
    assert c.get(path).status_code == 200
    r = c.post(path, data={'password': 'Reset-Pass-2026', 'confirm': 'Reset-Pass-2026'})
    assert r.status_code == 302
    assert login(app.test_client(), 'emp', 'Reset-Pass-2026', org='a').status_code == 302
    assert c.get(path).status_code == 400       # second use refused
    with tenant_context(two_tenants['a']):
        assert EmailLog.query.filter_by(kind='password_reset', status='sent').count() == 1
        tok = PasswordResetToken.query.one()
        assert tok.used_at is not None and len(tok.token_hash) == 64
        assert path.rsplit('/', 1)[1] != tok.token_hash   # only the hash is stored


def test_reset_token_expires(app, two_tenants):
    c = app.test_client()
    c.post('/forgot', data={'username': 'emp', 'org': 'a'})
    path = _reset_link(app).replace('https://sas.test', '')
    with tenant_bypass():
        PasswordResetToken.query.update({'expires_at': utcnow() - timedelta(seconds=1)})
        db.session.commit()
    assert c.get(path).status_code == 400


def test_forgot_does_not_reveal_accounts(app, two_tenants):
    c = app.test_client()
    r1 = c.post('/forgot', data={'username': 'emp', 'org': 'a'}, follow_redirects=True)
    r2 = c.post('/forgot', data={'username': 'no-such-user', 'org': 'a'}, follow_redirects=True)
    assert r1.status_code == r2.status_code == 200
    msg = 'إن كان الحساب موجودًا'
    assert msg in r1.get_data(as_text=True) and msg in r2.get_data(as_text=True)
    assert len(app.config['MAIL_OUTBOX']) == 1


def test_csrf_enforced(app, two_tenants):
    app.config['WTF_CSRF_ENABLED'] = True
    c = app.test_client()
    r = c.post('/login', data={'username': 'admin', 'password': PASSWORD, 'org': 'a'})
    assert r.status_code == 302 and '/home' not in r.headers['Location']    # refused, back with a message
    assert c.get('/home').status_code == 302                                   # not signed in
    token = csrf_from(c.get('/login').get_data(as_text=True))
    r = c.post('/login', data={'username': 'admin', 'password': PASSWORD, 'org': 'a',
                               'csrf_token': token})
    assert r.status_code == 302
    # logout is POST-only; it is not blocked by an old token, but a post from another site is refused
    assert c.get('/logout').status_code == 405
    assert c.post('/logout', headers={'Origin': 'https://evil.example'}).status_code == 400


def test_session_cookie_flags(app, two_tenants):
    c = app.test_client()
    r = login(c, 'admin', org='a')
    cookie = r.headers.get('Set-Cookie', '')
    assert 'HttpOnly' in cookie and 'SameSite=Lax' in cookie


def test_security_headers(app, two_tenants):
    r = app.test_client().get('/login')
    assert r.headers['X-Frame-Options'] == 'DENY'
    assert "script-src 'self'" in r.headers['Content-Security-Policy']
    assert r.headers['Referrer-Policy'] == 'same-origin'


def test_logout_and_old_forms_never_show_bad_request(app, two_tenants):
    """A page left open while the session changed (another tab signed in again) posts a stale CSRF token:
    signing out still works, and any other old form returns to its page with a message."""
    from tests.conftest import login
    app.config['WTF_CSRF_ENABLED'] = True
    try:
        c = app.test_client()
        with app.app_context():
            from models import Tenant
            from extensions import db
            from tenancy import tenant_bypass
            with tenant_bypass():
                t = db.session.get(Tenant, two_tenants['a'])
            make_user(t, 'stale', ('admin',), email='stale@a.test')
        app.config['WTF_CSRF_ENABLED'] = False
        assert login(c, 'stale', org='a').status_code == 302
        app.config['WTF_CSRF_ENABLED'] = True
        r = c.post('/logout', data={'csrf_token': 'old'}, headers={'Referer': 'http://localhost/home'})
        assert r.status_code == 302 and '/login' in r.headers['Location']
        assert c.get('/home').status_code == 302                       # really signed out
        r = c.post('/logout', headers={'Origin': 'https://evil.example'})
        assert r.status_code == 400                                     # not from another site
        r = c.post('/structure/stages/new', data={'csrf_token': 'old', 'name': 'x'},
                   headers={'Referer': 'http://localhost/structure/stages/'})
        assert r.status_code == 302 and r.headers['Location'].endswith('/structure/stages/')
    finally:
        app.config['WTF_CSRF_ENABLED'] = False
