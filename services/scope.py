"""Who sees which students. Principal / admin (attendance.view_all): everyone. Supervisors and secretaries:
the stages linked to their account. Teachers: the sections linked to their account (or taught by them in the
timetable). Every portal action goes through can_act(), so a user can never touch another stage's students
by sending other ids (the hole in the old /portal/<code> pages)."""
from models import Grade, Section, Student, UserSection, UserStage, TimetableEntry


def stage_ids(user):
    return {r.stage_id for r in UserStage.query.filter_by(user_id=user.id).all()}


def section_ids(user):
    own = {r.section_id for r in UserSection.query.filter_by(user_id=user.id).all()}
    own |= {r.section_id for r in TimetableEntry.query.filter_by(teacher_user_id=user.id).all()}
    return own


def sees_all(user):
    return user.has_perm('attendance.view_all')


def visible_section_ids(user):
    """None = all sections."""
    if sees_all(user):
        return None
    ids = set(section_ids(user))
    st = stage_ids(user)
    if st:
        ids |= {s.id for s in Section.query.join(Grade).filter(Grade.stage_id.in_(st)).all()}
    return ids


def students_query(user, include_inactive=False):
    q = Student.query
    if not include_inactive:
        q = q.filter(Student.is_active.is_(True))
    ids = visible_section_ids(user)
    if ids is not None:
        q = q.filter(Student.section_id.in_(list(ids) or [0]))
    return q


def sections_query(user):
    q = Section.query.filter(Section.is_active.is_(True))
    ids = visible_section_ids(user)
    if ids is not None:
        q = q.filter(Section.id.in_(list(ids) or [0]))
    return q


def can_act(user, student):
    if student is None:
        return False
    ids = visible_section_ids(user)
    return ids is None or student.section_id in ids
