"""School structure and attendance (replaces the SAS* tables of ARS).

Hierarchy: academic year → semester;  stage → grade (الصف) → section (الشعبة) → student.
Scope of a user: UserStage (supervisors / secretaries see their stages) and UserSection (teachers see their sections).
"""
from extensions import db
from tenancy import TenantScoped
from .core import utcnow

RECORD_KINDS = [('absent', 'غياب'), ('late', 'تأخر'), ('excused', 'غياب بعذر'), ('early_leave', 'مغادرة مبكرة'),
                ('other', 'أخرى')]
STATUSES = [('pending', 'بانتظار الاعتماد'), ('approved', 'معتمد'), ('rejected', 'مرفوض')]
WEEKDAYS = [(6, 'الأحد'), (0, 'الاثنين'), (1, 'الثلاثاء'), (2, 'الأربعاء'), (3, 'الخميس'), (4, 'الجمعة'), (5, 'السبت')]
DEFAULT_LEAVE_TYPES = [('المكتبة', True), ('لم يحضر الحصة', False), ('مغادرة اليوم', False), ('الإرشاد', True),
                       ('العيادة', True), ('المقصف', True), ('أخرى', True)]


class AcademicYear(TenantScoped, db.Model):
    __tablename__ = 'academic_years'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)          # 2026-2027
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    is_current = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    semesters = db.relationship('Semester', back_populates='year', order_by='Semester.sort_order',
                                cascade='all, delete-orphan', lazy='selectin')


class Semester(TenantScoped, db.Model):
    __tablename__ = 'semesters'
    id = db.Column(db.Integer, primary_key=True)
    year_id = db.Column(db.Integer, db.ForeignKey('academic_years.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    year = db.relationship('AcademicYear', back_populates='semesters')


class Stage(TenantScoped, db.Model):
    __tablename__ = 'stages'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    name_en = db.Column(db.String(100))
    school_days = db.Column(db.String(20), default='6,0,1,2,3', nullable=False)   # Python weekdays; Sun–Thu
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    grades = db.relationship('Grade', back_populates='stage', order_by='Grade.sort_order', lazy='selectin')

    @property
    def day_list(self):
        return [int(d) for d in (self.school_days or '').split(',') if d.strip().lstrip('-').isdigit()]


class Grade(TenantScoped, db.Model):
    __tablename__ = 'grades'
    id = db.Column(db.Integer, primary_key=True)
    stage_id = db.Column(db.Integer, db.ForeignKey('stages.id'), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    name_en = db.Column(db.String(100))
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    stage = db.relationship('Stage', back_populates='grades', lazy='joined')
    sections = db.relationship('Section', back_populates='grade', order_by='Section.sort_order', lazy='selectin')


class Section(TenantScoped, db.Model):
    __tablename__ = 'sections'
    id = db.Column(db.Integer, primary_key=True)
    grade_id = db.Column(db.Integer, db.ForeignKey('grades.id'), nullable=False, index=True)
    name = db.Column(db.String(50), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    grade = db.relationship('Grade', back_populates='sections', lazy='joined')

    @property
    def stage_id(self):
        return self.grade.stage_id if self.grade else None

    @property
    def full_name(self):
        return f'{self.grade.name} / {self.name}' if self.grade else self.name


class Student(TenantScoped, db.Model):
    __tablename__ = 'students'
    __table_args__ = (db.UniqueConstraint('tenant_id', 'student_number', name='uq_students_tenant_number'),)
    id = db.Column(db.Integer, primary_key=True)
    section_id = db.Column(db.Integer, db.ForeignKey('sections.id'), nullable=False, index=True)
    student_number = db.Column(db.String(30))
    full_name = db.Column(db.String(200), nullable=False)
    full_name_en = db.Column(db.String(200))
    gender = db.Column(db.String(1))                         # m / f
    guardian_name = db.Column(db.String(200))
    guardian_relation = db.Column(db.String(50))
    guardian_phone = db.Column(db.String(50))
    guardian_email = db.Column(db.String(300))                # several allowed, comma separated
    notes = db.Column(db.String(500))
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    section = db.relationship('Section', lazy='joined')


class UserStage(TenantScoped, db.Model):
    __tablename__ = 'user_stages'
    __table_args__ = (db.UniqueConstraint('user_id', 'stage_id', name='uq_user_stage'),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    stage_id = db.Column(db.Integer, db.ForeignKey('stages.id', ondelete='CASCADE'), nullable=False)


class UserSection(TenantScoped, db.Model):
    __tablename__ = 'user_sections'
    __table_args__ = (db.UniqueConstraint('user_id', 'section_id', name='uq_user_section'),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    section_id = db.Column(db.Integer, db.ForeignKey('sections.id', ondelete='CASCADE'), nullable=False)


class Attachment(TenantScoped, db.Model):
    """A file in UPLOAD_DIR/tenants/<id>/attachments/ (not in the database)."""
    __tablename__ = 'attachments'
    id = db.Column(db.Integer, primary_key=True)
    original_name = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(255), nullable=False)
    mime = db.Column(db.String(100))
    size = db.Column(db.Integer, default=0, nullable=False)
    uploaded_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class AttendanceRecord(TenantScoped, db.Model):
    """One absence / lateness / permission of one student on one day (SASRecord)."""
    __tablename__ = 'attendance_records'
    __table_args__ = (db.UniqueConstraint('student_id', 'record_date', 'kind', name='uq_record_student_date_kind'),)
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'), nullable=False, index=True)
    record_date = db.Column(db.Date, nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False, index=True)
    all_day = db.Column(db.Boolean, default=True, nullable=False)
    time_from = db.Column(db.String(5))
    time_to = db.Column(db.String(5))
    status = db.Column(db.String(20), default='pending', nullable=False, index=True)
    notes = db.Column(db.String(1000))
    attachment_id = db.Column(db.Integer, db.ForeignKey('attachments.id', ondelete='SET NULL'))
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    approved_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    approved_at = db.Column(db.DateTime)
    notified_at = db.Column(db.DateTime)
    notified_count = db.Column(db.Integer, default=0, nullable=False)
    student = db.relationship('Student', lazy='joined')
    attachment = db.relationship('Attachment', lazy='joined')
    creator = db.relationship('User', foreign_keys=[created_by], lazy='joined')
    approver = db.relationship('User', foreign_keys=[approved_by], lazy='joined')

    @property
    def kind_label(self):
        return dict(RECORD_KINDS).get(self.kind, self.kind)

    @property
    def status_label(self):
        return dict(STATUSES).get(self.status, self.status)


class Holiday(TenantScoped, db.Model):
    """A day off (school, or some stages / grades / sections). Proposed, then approved."""
    __tablename__ = 'holidays'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    scope_type = db.Column(db.String(20), default='school', nullable=False)   # school / stages / grades / sections
    scope_ids = db.Column(db.String(1000), default='', nullable=False)         # comma separated
    status = db.Column(db.String(20), default='pending', nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    approved_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    @property
    def ids(self):
        return {int(x) for x in (self.scope_ids or '').split(',') if x.strip().isdigit()}

    def covers(self, day, section):
        if self.status != 'approved' or not (self.start_date <= day <= self.end_date):
            return False
        if self.scope_type == 'school':
            return True
        ref = {'stages': section.grade.stage_id, 'grades': section.grade_id, 'sections': section.id}[self.scope_type]
        return ref in self.ids

    @property
    def status_label(self):
        return dict(STATUSES).get(self.status, self.status)


class ClassLeaveType(TenantScoped, db.Model):
    __tablename__ = 'class_leave_types'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    needs_return = db.Column(db.Boolean, default=True, nullable=False)   # the student comes back to class
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)


class ClassLeave(TenantScoped, db.Model):
    """A student leaving the class (library, clinic, counselling…) and coming back (SASClassLeave)."""
    __tablename__ = 'class_leaves'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'), nullable=False, index=True)
    leave_type_id = db.Column(db.Integer, db.ForeignKey('class_leave_types.id'), nullable=False)
    leave_date = db.Column(db.Date, nullable=False, index=True)
    leave_time = db.Column(db.String(5), nullable=False)
    return_time = db.Column(db.String(5))
    status = db.Column(db.String(20), default='pending', nullable=False, index=True)
    notes = db.Column(db.String(500))
    attachment_id = db.Column(db.Integer, db.ForeignKey('attachments.id', ondelete='SET NULL'))
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    approved_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    student = db.relationship('Student', lazy='joined')
    leave_type = db.relationship('ClassLeaveType', lazy='joined')
    creator = db.relationship('User', foreign_keys=[created_by], lazy='joined')

    @property
    def status_label(self):
        return dict(STATUSES).get(self.status, self.status)

    @property
    def is_out(self):
        return self.leave_type is not None and self.leave_type.needs_return and not self.return_time \
            and self.status != 'rejected'


class Period(TenantScoped, db.Model):
    """The bell periods of a stage for one weekday (SASPeriod)."""
    __tablename__ = 'periods'
    id = db.Column(db.Integer, primary_key=True)
    stage_id = db.Column(db.Integer, db.ForeignKey('stages.id', ondelete='CASCADE'), nullable=False, index=True)
    weekday = db.Column(db.Integer, nullable=False)              # Python weekday (Mon=0 … Sun=6)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    kind = db.Column(db.String(10), default='period', nullable=False)   # period / break
    label = db.Column(db.String(100), nullable=False)
    start_time = db.Column(db.String(5), nullable=False)
    end_time = db.Column(db.String(5), nullable=False)


class TimetableEntry(TenantScoped, db.Model):
    """What a section has in one period (SASTimetable); the teacher account sees that section."""
    __tablename__ = 'timetable_entries'
    __table_args__ = (db.UniqueConstraint('section_id', 'period_id', name='uq_timetable_section_period'),)
    id = db.Column(db.Integer, primary_key=True)
    section_id = db.Column(db.Integer, db.ForeignKey('sections.id', ondelete='CASCADE'), nullable=False, index=True)
    period_id = db.Column(db.Integer, db.ForeignKey('periods.id', ondelete='CASCADE'), nullable=False)
    subject = db.Column(db.String(200))
    teacher_name = db.Column(db.String(200))
    teacher_user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
