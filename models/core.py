"""Core tables: tenants, accounts, roles/permissions, departments, logs."""
from datetime import datetime, timezone

from extensions import db
from tenancy import TenantScoped


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Tenant(db.Model):
    """An organization. Not tenant-scoped itself; only the super-admin area
    lists or edits tenants (under tenant_bypass)."""
    __tablename__ = 'tenants'
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(50), unique=True, nullable=False)
    name_ar = db.Column(db.String(200), nullable=False)
    name_en = db.Column(db.String(200))
    domain = db.Column(db.String(200), unique=True)  # host or subdomain, optional
    accent_color = db.Column(db.String(9), default='#3D5A80', nullable=False)
    logo_primary = db.Column(db.String(300))    # file name inside UPLOAD_DIR/tenants/<id>/
    logo_secondary = db.Column(db.String(300))
    mail_sender = db.Column(db.String(200))     # overrides MS_SENDER_EMAIL when set
    email_footer = db.Column(db.String(300))
    form_footer = db.Column(db.String(100))        # footer of printed reports
    ocr_cloud_enabled = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class SuperAdmin(db.Model):
    """Platform operators who create organizations. Separate from tenant
    users on purpose: a tenant admin can never become one by editing a row
    they can see."""
    __tablename__ = 'super_admins'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(200))
    password_hash = db.Column(db.String(300), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    failed_logins = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime)
    session_version = db.Column(db.Integer, default=1, nullable=False)
    last_login = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class User(TenantScoped, db.Model):
    __tablename__ = 'users'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'username', name='uq_users_tenant_username'),
        db.UniqueConstraint('tenant_id', 'email', name='uq_users_tenant_email'),
    )
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(200))
    full_name = db.Column(db.String(200), nullable=False)
    employee_number = db.Column(db.String(50))
    password_hash = db.Column(db.String(300), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)
    failed_logins = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime)
    session_version = db.Column(db.Integer, default=1, nullable=False)
    last_login = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    roles = db.relationship('Role', secondary='user_roles', lazy='selectin',
                            primaryjoin='User.id == UserRole.user_id',
                            secondaryjoin='Role.id == UserRole.role_id', viewonly=True)
    departments = db.relationship('Department', secondary='user_departments', lazy='selectin',
                                  primaryjoin='User.id == UserDepartment.user_id',
                                  secondaryjoin='Department.id == UserDepartment.department_id',
                                  viewonly=True)

    @property
    def permission_codes(self):
        codes = set()
        for r in self.roles:
            if r.is_active:
                codes.update(rp.permission_code for rp in r.permissions)
        return codes

    def has_perm(self, code):
        return code in self.permission_codes

    @property
    def department_ids(self):
        return {d.id for d in self.departments}


DEPARTMENT_TYPES = (('normal', 'قسم عادي'), ('HR', 'شؤون الموظفين'), ('Finance', 'المحاسبة'))


class Department(TenantScoped, db.Model):
    """Mirrors the ``الاقسام`` sheet: id, code, name, icon, manager, email, type.
    ``code`` is kept only as an identifier for import/compatibility — it is no
    longer a login credential."""
    __tablename__ = 'departments'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'code', name='uq_departments_tenant_code'),
    )
    id = db.Column(db.Integer, primary_key=True)
    legacy_id = db.Column(db.String(100))       # the sheet's id column, for import
    code = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    icon = db.Column(db.String(20), default='🏢')
    manager_name = db.Column(db.String(200))
    emails = db.Column(db.String(500))           # comma-separated, cleaned
    dept_type = db.Column(db.String(20), default='normal', nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    @property
    def email_list(self):
        from utils.helpers import split_emails
        return split_emails(self.emails)


class Role(TenantScoped, db.Model):
    __tablename__ = 'roles'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'key', name='uq_roles_tenant_key'),
    )
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(300))
    is_system = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    permissions = db.relationship('RolePermission', cascade='all, delete-orphan',
                                  lazy='selectin', back_populates='role')

    @property
    def permission_codes(self):
        return {p.permission_code for p in self.permissions}


class RolePermission(TenantScoped, db.Model):
    __tablename__ = 'role_permissions'
    __table_args__ = (
        db.UniqueConstraint('role_id', 'permission_code', name='uq_role_perm'),
    )
    id = db.Column(db.Integer, primary_key=True)
    role_id = db.Column(db.Integer, db.ForeignKey('roles.id', ondelete='CASCADE'), nullable=False)
    permission_code = db.Column(db.String(60), nullable=False)
    role = db.relationship('Role', back_populates='permissions')


class UserRole(TenantScoped, db.Model):
    __tablename__ = 'user_roles'
    __table_args__ = (db.UniqueConstraint('user_id', 'role_id', name='uq_user_role'),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey('roles.id', ondelete='CASCADE'), nullable=False)


class UserDepartment(TenantScoped, db.Model):
    __tablename__ = 'user_departments'
    __table_args__ = (db.UniqueConstraint('user_id', 'department_id', name='uq_user_dept'),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id', ondelete='CASCADE'),
                              nullable=False)


class PasswordResetToken(TenantScoped, db.Model):
    """Only a SHA-256 of the token is stored; the token itself exists only
    in the emailed link. Single use, short expiry."""
    __tablename__ = 'password_reset_tokens'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    token_hash = db.Column(db.String(64), unique=True, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class LoginAttempt(db.Model):
    """Per-IP throttling and forensic trail. Not tenant-scoped: failed
    attempts may not resolve to any tenant at all."""
    __tablename__ = 'login_attempts'
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id', ondelete='CASCADE'), index=True)
    username = db.Column(db.String(200))
    ip_address = db.Column(db.String(64), index=True)
    success = db.Column(db.Boolean, default=False, nullable=False)
    reason = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)


class AuditLog(TenantScoped, db.Model):
    __tablename__ = 'audit_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    actor = db.Column(db.String(200))            # name snapshot (survives user deletion)
    action = db.Column(db.String(80), nullable=False, index=True)
    entity_type = db.Column(db.String(50))
    entity_id = db.Column(db.String(64))
    details = db.Column(db.JSON)
    ip_address = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)


class EmailLog(TenantScoped, db.Model):
    """Every send attempt is recorded — failures are visible in the admin
    panel instead of disappearing in an empty catch block."""
    __tablename__ = 'email_logs'
    id = db.Column(db.Integer, primary_key=True)
    recipients = db.Column(db.Text, nullable=False)
    subject = db.Column(db.String(500), nullable=False)
    kind = db.Column(db.String(50))
    status = db.Column(db.String(20), nullable=False)   # sent | failed | skipped
    error = db.Column(db.Text)
    entity_type = db.Column(db.String(50))
    entity_id = db.Column(db.String(64))
    acknowledged = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
