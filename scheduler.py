"""Background jobs (APScheduler), same pattern as venues: one scheduler per
process, skipped entirely when DISABLE_REMINDERS=1, and idle at night.
Jobs iterate over active tenants and run each one inside tenant_context,
so the isolation layer applies to scheduled work too. Jobs are registered
in jobs.py (none yet: guardian e-mail is manual)."""
from datetime import datetime
from zoneinfo import ZoneInfo

JOBS = []   # (function(app, tenant), minutes)


def register_job(minutes):
    def deco(fn):
        JOBS.append((fn, minutes))
        return fn
    return deco


def run_for_all_tenants(app, fn):
    from models import Tenant
    from tenancy import tenant_context
    from extensions import db
    with app.app_context():
        tenant_ids = [t.id for t in Tenant.query.filter_by(is_active=True).all()]
        for tid in tenant_ids:
            with tenant_context(tid):
                try:
                    fn(app, db.session.get(Tenant, tid))
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    app.logger.error('[scheduler] %s failed for tenant %s: %s', fn.__name__, tid, e)


def start_scheduler(app):
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        app.logger.warning('[scheduler] APScheduler not installed')
        return None
    tz = ZoneInfo(app.config.get('TIMEZONE', 'Asia/Amman'))

    def active_hours():
        h = datetime.now(tz).hour
        return not (h >= 23 or h < 6)

    sched = BackgroundScheduler(daemon=True)
    for fn, minutes in JOBS:
        sched.add_job(lambda f=fn: active_hours() and run_for_all_tenants(app, f),
                      'interval', minutes=minutes, id=fn.__name__, replace_existing=True)
    sched.start()
    return sched
