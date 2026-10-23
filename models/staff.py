"""Staff roster (like venues / RASTKT / Clearance; teachers and supervisors get their accounts from here): an employee may have a login (user_id) but does not need one."""
from extensions import db
from tenancy import TenantScoped
from .core import utcnow


class Employee(TenantScoped, db.Model):
    __tablename__ = 'employees'
    __table_args__ = (db.UniqueConstraint('tenant_id', 'employee_number', name='uq_employees_tenant_number'),)
    id = db.Column(db.Integer, primary_key=True)
    employee_number = db.Column(db.String(50))
    full_name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200))
    phone = db.Column(db.String(50))
    job_title = db.Column(db.String(150))
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id', ondelete='SET NULL'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    hire_date = db.Column(db.Date)
    notes = db.Column(db.String(500))
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    department = db.relationship('Department', lazy='joined')
    user = db.relationship('User', lazy='joined')
