"""Tests run on SQLite (the production database on the school PC) by default, or on PostgreSQL when
TEST_DATABASE_URL points to one. The schema is built by running the Alembic migrations, so the migrations
themselves are tested too. Every table is emptied between tests.
"""
import os
import re
import tempfile

import pytest
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix='sas-test-')
os.environ.setdefault('TEST_DATABASE_URL', 'sqlite:///' + os.path.join(_TMP, 'test.db').replace('\\', '/'))
os.environ['DATABASE_URL'] = os.environ['TEST_DATABASE_URL']
os.environ['DISABLE_REMINDERS'] = '1'
os.environ['BASE_URL'] = 'https://sas.test'
os.environ['TRUST_PROXY'] = '0'

from app import create_app  # noqa: E402
from extensions import db  # noqa: E402
from tenancy import tenant_context, tenant_bypass  # noqa: E402

PASSWORD = 'Strong-Pass-2026'


@pytest.fixture(scope='session')
def app():
    app = create_app({'TESTING': True, 'MAIL_BACKEND': 'memory', 'WTF_CSRF_ENABLED': False,
                      'SECRET_KEY': 'test-secret', 'BASE_URL': 'https://sas.test',
                      'SESSION_COOKIE_SECURE': False})
    with app.app_context():
        if db.engine.dialect.name == 'postgresql':
            db.session.execute(text('DROP SCHEMA public CASCADE; CREATE SCHEMA public;'))
            db.session.commit()
        from flask_migrate import upgrade
        upgrade(directory=os.path.join(os.path.dirname(os.path.dirname(__file__)), 'migrations'))
    yield app


@pytest.fixture(autouse=True)
def clean_db(app):
    with app.app_context():
        tables = [t.name for t in db.metadata.sorted_tables]
        if db.engine.dialect.name == 'postgresql':
            db.session.execute(text('TRUNCATE ' + ', '.join(f'"{t}"' for t in tables)
                                    + ' RESTART IDENTITY CASCADE'))
        else:
            for t in reversed(tables):
                db.session.execute(text(f'DELETE FROM "{t}"'))
            try:
                db.session.execute(text('DELETE FROM sqlite_sequence'))
            except Exception:   # noqa: BLE001 — no AUTOINCREMENT table yet
                pass
        db.session.commit()
    app.config['MAIL_OUTBOX'] = []
    app.config['WTF_CSRF_ENABLED'] = False
    app.config['MAIL_BACKEND'] = 'memory'
    yield
    with app.app_context():
        db.session.remove()


@pytest.fixture
def ctx(app):
    with app.app_context():
        yield


def make_tenant(slug, name=None, domain=None):
    from models import Tenant
    from authz import seed_default_roles
    t = Tenant(slug=slug, name_ar=name or f'منظمة {slug}', domain=domain)
    db.session.add(t)
    db.session.flush()
    with tenant_context(t.id):
        seed_default_roles(t.id)
    db.session.commit()
    return t


def make_dept(tenant, code, name, dtype='normal', emails=None):
    from models import Department
    with tenant_context(tenant.id):
        d = Department(code=code, name=name, dept_type=dtype, emails=emails)
        db.session.add(d)
        db.session.commit()
        return d.id


def make_user(tenant, username, roles=('teacher',), depts=(), email=None, password=PASSWORD,
              must_change=False):
    from models import User, Role, UserRole, UserDepartment
    from security import hash_password
    with tenant_context(tenant.id):
        u = User(username=username, full_name=f'مستخدم {username}', email=email,
                 password_hash=hash_password(password), must_change_password=must_change)
        db.session.add(u)
        db.session.flush()
        for key in roles:
            r = Role.query.filter_by(key=key).one()
            db.session.add(UserRole(user_id=u.id, role_id=r.id))
        for did in depts:
            db.session.add(UserDepartment(user_id=u.id, department_id=did))
        db.session.commit()
        return u.id


@pytest.fixture
def two_tenants(ctx):
    """Tenant A and B, each with an admin; tenant A also has a supervisor and a teacher. Returns a dict of ids."""
    a = make_tenant('a', 'مدرسة أ')
    b = make_tenant('b', 'مدرسة ب')
    da = make_dept(a, 'IT', 'تقنية المعلومات أ')
    da2 = make_dept(a, 'LIB', 'المكتبة أ')
    db_ = make_dept(b, 'IT', 'تقنية المعلومات ب')
    return {
        'a': a.id, 'b': b.id, 'dept_a': da, 'dept_a2': da2, 'dept_b': db_,
        'admin_a': make_user(a, 'admin', ('admin',), email='admin@a.test'),
        'admin_b': make_user(b, 'admin', ('admin',), email='admin@b.test'),
        'mgr_a': make_user(a, 'mgr', ('supervisor',), (da,), email='mgr@a.test'),
        'emp_a': make_user(a, 'emp', ('teacher',), email='emp@a.test'),
        'secret_b': make_user(b, 'secretuser', ('teacher',), email='secret@b.test'),
    }


def login(client, username, password=PASSWORD, org=None, **kw):
    data = {'username': username, 'password': password}
    if org:
        data['org'] = org
    return client.post('/login', data=data, **kw)


def csrf_from(html):
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return m.group(1) if m else None
