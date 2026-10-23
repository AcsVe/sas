"""School days: a day counts for a section when it is one of its stage's school days and no approved holiday
covers it (school-wide, its stage, its grade or the section itself)."""
from datetime import timedelta

from models import Holiday


def approved_holidays(start, end):
    return Holiday.query.filter(Holiday.status == 'approved', Holiday.start_date <= end,
                                Holiday.end_date >= start).all()


def holiday_for(day, section, holidays=None):
    for h in (holidays if holidays is not None else approved_holidays(day, day)):
        if h.covers(day, section):
            return h
    return None


def is_school_day(day, section, holidays=None):
    if day.weekday() not in section.grade.stage.day_list:
        return False
    return holiday_for(day, section, holidays) is None


def school_days(start, end, section, holidays=None):
    """Number of school days of a section between two dates (inclusive)."""
    if end < start:
        return 0
    hs = holidays if holidays is not None else approved_holidays(start, end)
    n, d = 0, start
    while d <= end:
        if is_school_day(d, section, hs):
            n += 1
        d += timedelta(days=1)
    return n
