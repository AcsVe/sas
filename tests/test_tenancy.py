import pytest
from sqlalchemy import select, inspect
from sqlalchemy.orm import aliased

from extensions import db
from models import User, Role, Department, UserRole, AuditLog
from tenancy import (TenantContextError, TenantScoped, tenant_context, tenant_bypass,
                     current_tenant_id)
from tests.conftest import login


def test_query_without_tenant_raises(two_tenants):
    with pytest.raises(TenantContextError):
        User.query.all()
    with pytest.raises(TenantContextError):
        db.session.execute(select(Department)).all()


def test_reads_are_filtered(two_tenants):
    t = two_tenants
    with tenant_context(t['a']):
        names = {u.username for u in User.query.all()}
        assert 'secretuser' not in names and 'admin' in names
        assert db.session.get(User, t['secret_b']) is None
        assert db.session.get(Department, t['dept_b']) is None
        rows = db.session.execute(select(User.username)).scalars().all()
        assert 'secretuser' not in rows
        # aliased self-join / join through link tables stay filtered
        ua = aliased(User)
        joined = db.session.execute(select(ua.username).join(UserRole, UserRole.user_id == ua.id)).scalars().all()
        assert 'secretuser' not in joined
        # count/aggregate
        assert User.query.filter(User.username == 'admin').count() == 1


def test_identity_map_does_not_leak_between_tenants(two_tenants):
    t = two_tenants
    with tenant_bypass():
        db.session.get(User, t['secret_b'])          # now in identity map
    with tenant_context(t['a']):
        db.session.expire_all()
        assert User.query.filter_by(id=t['secret_b']).first() is None


def test_relationship_loads_are_filtered(two_tenants):
    t = two_tenants
    with tenant_context(t['a']):
        admin = db.session.get(User, t['admin_a'])
        assert all(r.tenant_id == t['a'] for r in admin.roles)
        for role in Role.query.all():
            assert all(p.tenant_id == t['a'] for p in role.permissions)


def test_insert_gets_current_tenant_and_rejects_foreign(two_tenants):
    t = two_tenants
    with tenant_context(t['a']):
        d = Department(code='NEW', name='جديد')
        db.session.add(d)
        db.session.commit()
        assert d.tenant_id == t['a']
        db.session.add(Department(code='X', name='دخيل', tenant_id=t['b']))
        with pytest.raises(TenantContextError):
            db.session.flush()
        db.session.rollback()


def test_insert_without_tenant_raises(two_tenants):
    db.session.add(Department(code='Z', name='بلا منظمة'))
    with pytest.raises(TenantContextError):
        db.session.flush()
    db.session.rollback()


def test_cannot_modify_or_delete_foreign_rows(two_tenants):
    t = two_tenants
    with tenant_bypass():
        foreign = db.session.get(User, t['secret_b'])
    with tenant_context(t['a']):
        foreign.full_name = 'مخترق'
        with pytest.raises(TenantContextError):
            db.session.flush()
        db.session.rollback()
    with tenant_bypass():
        foreign = db.session.get(User, t['secret_b'])
    with tenant_context(t['a']):
        db.session.delete(foreign)
        with pytest.raises(TenantContextError):
            db.session.flush()
        db.session.rollback()
    with tenant_context(t['a']):
        mine = db.session.get(User, t['emp_a'])
        mine.tenant_id = t['b']                       # moving a row to another tenant
        with pytest.raises(TenantContextError):
            db.session.flush()
        db.session.rollback()


def test_bulk_update_and_delete_stay_in_tenant(two_tenants):
    t = two_tenants
    with tenant_context(t['a']):
        n = User.query.update({'full_name': 'معدّل'}, synchronize_session=False)
        db.session.commit()
        assert n == 3   # admin, mgr, emp of tenant A only
        Department.query.delete(synchronize_session=False)
        db.session.commit()
    with tenant_context(t['b']):
        assert {u.full_name for u in User.query.all()} != {'معدّل'}
        assert Department.query.count() == 1


def test_every_data_table_is_tenant_scoped():
    """Guard for future phases: any new table must carry tenant_id unless it
    is explicitly whitelisted here with a reason."""
    allowed_without = {'tenants', 'super_admins',
                       'login_attempts'}  # may not resolve to a tenant at all
    for mapper in db.Model.registry.mappers:
        cls = mapper.class_
        table = cls.__table__.name
        if table in allowed_without:
            continue
        assert issubclass(cls, TenantScoped), f'{table} is not tenant-scoped'
        assert not cls.__table__.c.tenant_id.nullable


def test_tenant_context_reset_after_request(app, two_tenants):
    client = app.test_client()
    login(client, 'admin', org='a')
    client.get('/home')
    assert current_tenant_id() is None


def test_http_cross_tenant_access_is_404(app, two_tenants):
    t = two_tenants
    c = app.test_client()
    assert login(c, 'admin', org='a').status_code == 302
    r = c.get('/admin/users')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'secretuser' not in html and 'secret@b.test' not in html
    assert c.get(f"/admin/users/{t['secret_b']}").status_code == 404
    assert c.post(f"/admin/users/{t['secret_b']}", data={'username': 'x'}).status_code == 404
    assert c.post(f"/admin/users/{t['secret_b']}/action", data={'action': 'unlock'}).status_code == 404
    assert c.get(f"/admin/departments/{t['dept_b']}").status_code == 404
    assert 'تقنية المعلومات ب' not in c.get('/admin/departments').get_data(as_text=True)


def test_http_cannot_link_foreign_role_or_department(app, two_tenants):
    t = two_tenants
    with tenant_context(t['b']):
        foreign_role = Role.query.filter_by(key='admin').one().id
    c = app.test_client()
    login(c, 'admin', org='a')
    r = c.post(f"/admin/users/{t['emp_a']}", data={
        'username': 'emp', 'full_name': 'x', 'roles': [str(foreign_role)],
        'departments': [str(t['dept_b'])], 'is_active': '1'})
    assert r.status_code == 400
    with tenant_context(t['a']):
        assert UserRole.query.filter_by(user_id=t['emp_a'], role_id=foreign_role).count() == 0


def test_same_username_in_two_tenants_logs_into_its_own(app, two_tenants):
    c = app.test_client()
    login(c, 'admin', org='b')
    html = c.get('/admin/users').get_data(as_text=True)
    assert 'secretuser' in html and 'mgr' not in html


def test_audit_log_is_per_tenant(app, two_tenants):
    t = two_tenants
    c = app.test_client()
    login(c, 'admin', org='a')
    login(app.test_client(), 'admin', org='b')
    with tenant_context(t['a']):
        assert all(a.tenant_id == t['a'] for a in AuditLog.query.all())
        assert AuditLog.query.filter_by(action='auth.login').count() == 1
