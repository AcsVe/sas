"""Tenant isolation, enforced in one place.

Every model that inherits ``TenantScoped`` gets:

* reads  — every ORM SELECT / relationship load / bulk UPDATE / bulk DELETE
  is rewritten with ``tenant_id = <current tenant>`` (``with_loader_criteria``);
* writes — on flush, new rows get the current tenant automatically, and any
  attempt to insert, move or delete a row of another tenant raises.

If code touches a tenant-scoped model while no tenant is active, it raises
``TenantContextError`` instead of silently returning everything. The only
way around the filter is the explicit ``tenant_bypass()`` context manager,
used by the super-admin area and by system jobs, so a forgotten filter is a
crash in tests, never a data leak.
"""
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import event, Integer, ForeignKey
from sqlalchemy.orm import Session, with_loader_criteria, declared_attr, mapped_column

_current_tenant: ContextVar = ContextVar('current_tenant', default=None)
_bypass: ContextVar = ContextVar('tenant_bypass', default=False)


class TenantContextError(RuntimeError):
    pass


class TenantScoped:
    """Mixin: adds a mandatory, indexed tenant_id column."""

    @declared_attr
    def tenant_id(cls):
        return mapped_column(Integer, ForeignKey('tenants.id', ondelete='CASCADE'),
                             nullable=False, index=True)


def current_tenant_id():
    return _current_tenant.get()


def set_current_tenant(tenant_id):
    """Set for the rest of the current context (one request / one thread)."""
    return _current_tenant.set(tenant_id)


@contextmanager
def tenant_context(tenant_id):
    token = _current_tenant.set(tenant_id)
    try:
        yield
    finally:
        _current_tenant.reset(token)


@contextmanager
def tenant_bypass():
    token = _bypass.set(True)
    try:
        yield
    finally:
        _bypass.reset(token)


def bypass_active():
    return _bypass.get()


def _statement_touches_tenant_models(orm_execute_state):
    for mapper in orm_execute_state.all_mappers:
        if issubclass(mapper.class_, TenantScoped):
            return True
    return False


@event.listens_for(Session, 'do_orm_execute')
def _add_tenant_criteria(orm_execute_state):
    if not (orm_execute_state.is_select or orm_execute_state.is_update
            or orm_execute_state.is_delete):
        return
    if _bypass.get():
        return
    if orm_execute_state.is_column_load:
        # Refreshing attributes of an object we already hold — it was
        # loaded under the tenant filter in the first place.
        return
    tid = _current_tenant.get()
    if tid is None:
        if _statement_touches_tenant_models(orm_execute_state):
            raise TenantContextError('محاولة الوصول لبيانات منظمة بدون تحديد المنظمة')
        return
    orm_execute_state.statement = orm_execute_state.statement.options(
        with_loader_criteria(
            TenantScoped,
            lambda cls: cls.tenant_id == tid,
            include_aliases=True,
        )
    )


@event.listens_for(Session, 'before_flush')
def _guard_writes(session, flush_context, instances):
    if _bypass.get():
        return
    tid = _current_tenant.get()
    for obj in session.new:
        if not isinstance(obj, TenantScoped):
            continue
        if tid is None:
            raise TenantContextError('إنشاء سجل بدون منظمة حالية')
        if obj.tenant_id is None:
            obj.tenant_id = tid
        elif obj.tenant_id != tid:
            raise TenantContextError('إنشاء سجل لمنظمة أخرى')
    for obj in list(session.dirty) + list(session.deleted):
        if not isinstance(obj, TenantScoped):
            continue
        if tid is None:
            raise TenantContextError('تعديل سجل بدون منظمة حالية')
        from sqlalchemy import inspect
        hist = inspect(obj).attrs.tenant_id.history
        original = hist.deleted[0] if hist.deleted else obj.tenant_id
        if original != tid or obj.tenant_id != tid:
            raise TenantContextError('تعديل أو حذف سجل منظمة أخرى')


def get_scoped(model, ident):
    """``db.session.get`` that never returns another organization's row. A plain ``session.get`` answers from
    the identity map without a query, so the tenant filter above would not run for an object that is
    already loaded in this session."""
    from extensions import db
    obj = db.session.get(model, ident)
    if obj is not None and isinstance(obj, TenantScoped) and not _bypass.get() \
            and obj.tenant_id != _current_tenant.get():
        return None
    return obj
