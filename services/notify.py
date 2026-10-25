"""Guardian e-mail about absences — manual only (one record, the ticked records, or all of today's).

plan(records) splits the chosen records into: to send (grouped per student), without a guardian e-mail,
and already notified (skipped unless resend). send(plan) sends one message per student via MS Graph
(utils.mail, an EmailLog row per attempt) and stamps notified_at / notified_count on each record sent."""
from flask import g

from extensions import db
from models.core import utcnow
from utils.helpers import split_emails
from utils.mail import send_email
from utils.i18n import _, force_lang


def plan(records, resend=False):
    groups, no_email, already = {}, [], []
    for r in records:
        if r.status == 'rejected':
            continue
        if r.notified_at and not resend:
            already.append(r)
            continue
        if not split_emails(r.student.guardian_email):
            no_email.append(r)
            continue
        groups.setdefault(r.student_id, []).append(r)
    return {'groups': [sorted(v, key=lambda x: (x.record_date, x.kind)) for v in groups.values()],
            'no_email': no_email, 'already': already}


def _facts(r):
    when = _('طوال اليوم') if r.all_day else ' – '.join(t for t in (r.time_from, r.time_to) if t)
    return (r.kind_label, f'{r.record_date.isoformat()} ({when})' if when else r.record_date.isoformat())


def send(p):
    """→ (sent_students, failed: [(student name, error)]). The guardians' e-mail is always in Arabic."""
    with force_lang('ar'):
        return _send(p)


def _send(p):
    tenant = g.tenant
    sent, failed = 0, []
    for recs in p['groups']:
        st = recs[0].student
        dates = sorted({r.record_date for r in recs})
        subject = _('إشعار غياب: {full_name} — {v}', full_name=st.full_name, v=dates[0].isoformat()) + \
            (_(' إلى {v}', v=dates[-1].isoformat()) if len(dates) > 1 else '')
        res = send_email(
            st.guardian_email, subject, 'emails/notice.html', tenant, kind='guardian_absence',
            entity=('student', st.id),
            heading=_('إشعار من المدرسة بغياب الطالب أو تأخره'),
            greeting=st.guardian_name or _('ولي أمر الطالب'),
            paragraphs=[_('نحيطكم علمًا بما سُجّل للطالب {full_name} في {name} / الشعبة {name2}:', full_name=st.full_name, name=st.section.grade.name, name2=st.section.name)],
            items=[_facts(r) for r in recs], items_color='#1e3a8a',
            note=_('نرجو التواصل مع إدارة المدرسة إن كان لديكم عذر أو استفسار.'), note_label=_('ملاحظة'),
            after=[_('هذه رسالة آلية من نظام غياب الطلبة؛ يُرجى عدم الرد عليها.')])
        if res:
            now = utcnow()
            for r in recs:
                r.notified_at = now
                r.notified_count = (r.notified_count or 0) + 1
            db.session.commit()
            sent += 1
        else:
            failed.append((st.full_name, res.error))
    return sent, failed
