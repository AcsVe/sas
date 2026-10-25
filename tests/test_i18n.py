"""Two languages: every text of the pages and messages has its English, and a page in English shows no
Arabic of its own (only the school's data: names of students, stages…)."""
import json
import re
from datetime import timedelta
from pathlib import Path

from extensions import db
from models import AttendanceRecord, Holiday, Stage, Grade, Section, Student, ClassLeaveType, Role
from tenancy import tenant_context
from tests.test_school import school, _client  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parent.parent
AR = re.compile(r'[؀-ۿ]+')


def test_every_key_has_english():
    import sys
    sys.path.insert(0, str(ROOT))
    from tools_extract import all_keys
    cat = json.loads((ROOT / 'i18n' / 'en.json').read_text(encoding='utf-8'))
    missing = sorted(k for k in all_keys() if k not in cat)
    assert not missing, missing[:20]
    for k, v in cat.items():
        assert set(re.findall(r'\{\w+\}', k)) == set(re.findall(r'\{\w+\}', v)), k


def _visible(html):
    html = re.sub(r'<(script|style)\b.*?</\1>', ' ', html, flags=re.S)
    html = re.sub(r'<option[^>]*>[^<]*</option>', ' ', html)       # choices are data (sections, names)
    html = re.sub(r'<[^>]+>', ' ', html)
    return html


def test_pages_in_english(app, school):
    """With the English switch on, no Arabic is left on the pages except the data itself."""
    with tenant_context(school['a']):
        for st in Stage.query.all():
            st.name_en = f'Stage {st.id}'
        for gr in Grade.query.all():
            gr.name_en = f'Grade {gr.id}'
        for sec in Section.query.all():
            sec.name_en = 'A'
        r = AttendanceRecord(student_id=school['st11'], record_date=__import__('datetime').date.today(),
                             kind='absent')
        db.session.add(r)
        h = Holiday(title='Holiday', start_date=r.record_date, end_date=r.record_date + timedelta(days=1))
        db.session.add(h)
        db.session.commit()
        rid, hid = r.id, h.id
        data = {w for s in Student.query.all() for w in AR.findall(s.full_name + ' ' + (s.guardian_name or ''))}
        data |= {w for x in Section.query.all() for w in AR.findall(x.name)}
        data |= {w for x in Stage.query.all() for w in AR.findall(x.name)}
        data |= {w for x in Grade.query.all() for w in AR.findall(x.name)}
        data |= {w for t in ClassLeaveType.query.all() for w in AR.findall(t.name)}
        from models import User, Tenant, Department, Employee
        data |= {w for t in User.query.all() for w in AR.findall(t.full_name) + [t.full_name[:1]]}
        data |= {w for t in Department.query.all() for w in AR.findall(t.name)}
        data |= {w for t in Employee.query.all() for w in AR.findall(t.full_name + ' ' + (t.job_title or ''))}
        data |= set(AR.findall(db.session.get(Tenant, school['a']).name_ar))
    data |= {'العربية'}                                   # the language switch names Arabic in Arabic
    cat = json.loads((ROOT / 'i18n' / 'en.json').read_text(encoding='utf-8'))
    data |= {w for v in cat.values() for w in AR.findall(v)}   # Arabic examples kept in the English help
    c = _client(app, 'admin')
    assert c.get('/lang/en').status_code == 302
    left = {}
    for url in ['/home', '/attendance/daily', f"/attendance/daily?section={school['sec1']}", '/attendance/records',
                f'/attendance/record/{rid}', '/attendance/notify', '/leaves/', '/holidays/', f'/holidays/{hid}',
                '/students/', f"/students/{school['st11']}", f"/students/{school['st11']}/edit", '/students/import',
                f"/timetable/section/{school['sec1']}", '/timetable/mine', '/reports/', '/reports/compare',
                '/employees/', '/admin/users', '/admin/users/new', '/admin/roles', '/admin/departments',
                '/admin/settings', '/admin/ticker', '/admin/email-log', '/admin/audit',
                '/structure/years/', '/structure/semesters/', '/structure/stages/', '/structure/grades/',
                '/structure/sections/', '/structure/leave_types/', '/structure/periods/', '/structure/periods/import']:
        r = c.get(url)
        assert r.status_code == 200, url
        html = r.get_data(as_text=True)
        assert '<html lang="en" dir="ltr">' in html, url
        words = set(AR.findall(_visible(html))) - data
        if words:
            left[url] = sorted(words)
    assert not left, json.dumps(left, ensure_ascii=False, indent=0)
    # and back to Arabic
    c.get('/lang/ar')
    assert '<html lang="ar" dir="rtl">' in c.get('/home').get_data(as_text=True)


def test_language_switch_only_redirects_to_this_site(app, school):
    c = _client(app, 'admin')
    r = c.get('/lang/en', headers={'Referer': 'https://evil.example/x'})
    assert r.status_code == 302 and 'evil' not in r.headers['Location']
    assert 'lang=en' in r.headers.get('Set-Cookie', '')
