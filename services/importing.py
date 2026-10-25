"""Building the school from student lists: read every sheet, understand where each student belongs,
plan what has to be created, show it, then apply it in one transaction.

    plan = build_plan(read_sheets(text, file), options)   # nothing is written
    … the preview page shows plan …
    result = apply_plan(plan, stage_choice)                # one commit

A student's place comes from the columns stage / grade / section, or from one class code in any of them or
in the sheet's name: «4A», «4CPA», «Grade 6-C», «Five CP-A», «Nine IGCSE A», «الصف الرابع / أ»… The grade is
recognised by its number (digits, English or Arabic words) and matched to the existing grades by number,
the section by its name (Latin and Arabic letters match: A = أ). What does not exist is planned for creation;
a new grade goes to the stage whose name covers its number (e.g. «المرحلة الأساسية 4-6»), or to the stage
chosen on the preview.

Students are matched to the existing ones by student number, then by name (Arabic or English, letter
variants ignored); one student listed twice in the file is kept once. After this first build, students are
added and moved inside SAS; importing the same file again changes nothing.
"""
import re

from extensions import db
from models import Stage, Grade, Section, Student
from utils.i18n import _
from utils.tabular import norm, to_records

FIELDS = [
    ('student_number', ['رقم الطالب', 'الرقم المدرسي', 'الرقم الوطني', 'student_number', 'student number',
                        'student no', 'student id', 'number', 'id', 'code']),
    ('full_name', ['الاسم', 'اسم الطالب', 'الاسم الكامل', 'اسم الطالبة', 'name', 'student', 'full_name',
                   'student name', 'std. name', 'std name', 'std.name', 'الاسم بالعربية']),
    ('full_name_en', ['الاسم بالإنجليزية', 'الاسم بالانجليزي', 'name_en', 'english name', 'full_name_en',
                      'name (english)']),
    ('gender', ['الجنس', 'gender', 'sex']),
    ('stage', ['المرحلة', 'stage', 'group level', 'level']),
    ('grade', ['الصف', 'grade', 'class', 'الصف والشعبة', 'class name']),
    ('section', ['الشعبة', 'section', 'division']),
    ('section_en', ['الشعبة بالإنجليزية', 'section_en', 'section (english)']),
    ('homeroom', ['مربي الصف', 'مربية الصف', 'homeroom teacher', 'homeroom', 'class teacher']),
    ('guardian_name', ['ولي الأمر', 'اسم ولي الأمر', 'guardian', 'guardian_name', 'parent']),
    ('guardian_relation', ['صلة القرابة', 'القرابة', 'relation']),
    ('guardian_phone', ['هاتف ولي الأمر', 'الهاتف', 'الجوال', 'phone', 'mobile']),
    ('guardian_email', ['بريد ولي الأمر', 'البريد', 'البريد الإلكتروني', 'email', 'guardian_email']),
    ('notes', ['ملاحظات', 'notes']),
]

# ── numbers ─────────────────────────────────────────────────────────────────
ORD_AR = ['', 'الأول', 'الثاني', 'الثالث', 'الرابع', 'الخامس', 'السادس', 'السابع', 'الثامن', 'التاسع', 'العاشر',
          'الحادي عشر', 'الثاني عشر']
ORD_EN = ['', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve']
_ORD_EN2 = ['', 'first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth', 'tenth',
            'eleventh', 'twelfth']
_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789')
_KG = re.compile(r'^(kg\s*[12]|روضة|تمهيدي|بستان)', re.I)


def grade_name(n):
    return (_('الصف {v}', v=ORD_AR[n]), f'Grade {n}') if 0 < n < len(ORD_AR) else (str(n), str(n))


def _strip_ar(w):
    return w.translate(str.maketrans({'أ': 'ا', 'إ': 'ا', 'آ': 'ا', 'ة': 'ه', 'ى': 'ي'}))


def _number_words():
    """(regex alternation → number), longest first so «الحادي عشر» wins over «الحادي»."""
    pairs = []
    for i in range(1, 13):
        pairs += [(ORD_EN[i], i), (_ORD_EN2[i], i), (_strip_ar(ORD_AR[i]), i), (_strip_ar(ORD_AR[i])[2:], i)]
    pairs.sort(key=lambda p: -len(p[0]))
    return pairs


_WORDS = _number_words()
_LEAD = re.compile(r'^\s*(?:grade|class|year|الصف|صف|الصفوف)\s*', re.I)


def parse_grade_number(text):
    """'Grade 5', 'Five CP-A', '4CPA', 'الصف الرابع', 'الثاني عشر الأكاديمي' → (number, rest) or (None, text)."""
    t = (text or '').translate(_DIGITS).strip()
    t = _LEAD.sub('', t)
    m = re.match(r'^(\d{1,2})(?!\d)\s*(.*)$', t)
    if m and 0 < int(m.group(1)) <= 12:
        return int(m.group(1)), m.group(2)
    low = _strip_ar(t.lower())
    for word, n in _WORDS:
        if low.startswith(word) and (len(low) == len(word) or not low[len(word)].isalpha()):
            return n, t[len(word):]
    return None, t


_TRACK = re.compile(r'^([A-Z]{2,6})\s*-?\s*([A-H])$')
_NOISE = re.compile(r'(?:^|\s)(new|جديد|جديدة)\s*$', re.I)


def clean_section(rest):
    """'A' / '-C' / 'CPA' / 'CP-A' / 'A2 - Scientific' / '/ أ' → 'A', 'C', 'CP A', 'CP A', 'A2 Scientific', 'أ'."""
    r = _NOISE.sub('', (rest or '').strip()).strip()
    r = r.strip(' -_/\\|.:،,')
    r = re.sub(r'\s*[-–—/]\s*', ' - ', r)
    r = re.sub(r'\s+', ' ', r).strip(' -')
    m = _TRACK.match(r.replace(' - ', '-'))
    if m:
        return f'{m.group(1)} {m.group(2)}'
    if re.fullmatch(r'[a-hA-H]', r):
        return r.upper()
    return r.replace(' - ', ' ')


def parse_class(text):
    """→ (grade number or None, section text)."""
    n, rest = parse_grade_number(text)
    return n, clean_section(rest)


# ── section-name matching ─────────────────────────────────────────────────
_LAT_AR = dict(zip('ABCDEFGH', 'أبجدهوزح'))


def section_keys(name):
    """Every spelling a section name may have in a file: 'A' ↔ 'أ', 'CP A' ↔ 'CPA' ↔ 'cp-a'."""
    keys = {norm(name)}
    up = (name or '').strip().upper()
    if up in _LAT_AR:
        keys.add(norm(_LAT_AR[up]))
    for lat, ar in _LAT_AR.items():
        if norm(name) == norm(ar):
            keys.add(norm(lat))
    return {k for k in keys if k}


def _number_of(*names):
    for nm in names:
        n, _rest = parse_grade_number(nm or '')
        if n:
            return n
    return None


def stage_range(stage):
    """(first, last) grade numbers written in a stage name: 'المرحلة الأساسية 4-6' → (4, 6)."""
    txt = f'{stage.name} {stage.name_en or ""}'.translate(_DIGITS)
    m = re.search(r'(\d{1,2})\s*[-–—+/]\s*(\d{1,2})', txt)
    if m:
        return int(m.group(1)), int(m.group(2))
    if re.search(r'ثانو|secondary|high', txt, re.I):
        return 7, 12
    return None


# ── reading ───────────────────────────────────────────────────────────────
def read_records(sheets):
    """[(sheet, rows)] → records, each with its sheet and line. A sheet without a grade/section column
    gives its own name as the class code."""
    out = []
    for sheet, rows in sheets:
        recs, _hdr = to_records(rows, FIELDS, keep_row=True)
        for r in recs:
            r['_sheet'] = sheet
            out.append(r)
    return out


def _name_lang(name):
    return 'ar' if re.search(r'[؀-ۿ]', name or '') else 'en'


def _place_of(rec):
    """→ (stage text, grade number, grade text, section text) or an error text."""
    grade_txt = (rec.get('grade') or '').strip()
    sec_txt = (rec.get('section') or '').strip()
    n = None
    if grade_txt:
        n, rest = parse_class(grade_txt)
        if not sec_txt and rest:
            sec_txt = rest
    if n is None and sec_txt:
        n2, rest2 = parse_class(sec_txt)
        if n2:
            n, sec_txt = n2, rest2
    if (n is None or not sec_txt) and rec.get('_sheet'):
        n3, rest3 = parse_class(rec['_sheet'])
        if n is None:
            n = n3
        if not sec_txt and n3 == n:
            sec_txt = rest3
    sec_txt = clean_section(sec_txt) if sec_txt else ''
    return (rec.get('stage') or '').strip(), n, grade_txt, sec_txt


# ── planning ──────────────────────────────────────────────────────────────
def sheet_overview(sheets):
    """Per sheet: its students, and how many of them an earlier sheet already lists (a copy sheet, such as
    a notebooks list made from the class list, shows up here)."""
    out, seen = [], {}
    for sheet, rows in sheets:
        names = {norm(r.get('full_name') or r.get('full_name_en') or '') for r in read_records([(sheet, rows)])}
        names.discard('')
        overlap = {}
        for n in names:
            if n in seen:
                overlap[seen[n]] = overlap.get(seen[n], 0) + 1
        for n in names:
            seen.setdefault(n, sheet)
        best = max(overlap.items(), key=lambda kv: kv[1]) if overlap else (None, 0)
        out.append({'name': sheet, 'students': len(names), 'copy_of': best[0] if best[1] else None,
                    'overlap': best[1]})
    return out


def build_plan(sheets, update=False, default_section_id=None, allowed_sections=None, skip_sheets=()):
    """Reads and matches everything; writes nothing. → a JSON-safe dict (kept between preview and apply)."""
    overview = sheet_overview(sheets)
    skip = set(skip_sheets or ())
    records = read_records([(n, rows) for n, rows in sheets if n not in skip])
    stages = Stage.query.order_by(Stage.sort_order, Stage.id).all()
    grades = Grade.query.all()
    sections = Section.query.all()
    by_num = {}
    for gr in grades:
        n = _number_of(gr.name, gr.name_en)
        if n:
            by_num.setdefault(n, gr)
    sec_index = {}
    for s in sections:
        for k in section_keys(s.name) | section_keys(s.name_en or ''):
            sec_index.setdefault((s.grade_id, k), s)
    stage_by_name = {}
    for st in stages:
        for nm in (st.name, st.name_en):
            if nm:
                stage_by_name[norm(nm)] = st

    def stage_for(n, stage_txt):
        if stage_txt and norm(stage_txt) in stage_by_name:
            return stage_by_name[norm(stage_txt)].id
        for st in stages:
            rg = stage_range(st)
            if rg and rg[0] <= n <= rg[1]:
                return st.id
        return None

    existing = Student.query.all()
    by_number = {s.student_number: s for s in existing if s.student_number}
    by_name = {}
    for s in existing:
        for nm in (s.full_name, s.full_name_en):
            if nm:
                by_name.setdefault(norm(nm), s)

    new_stages, new_grades, new_sections, rows, problems, warnings = {}, {}, {}, [], [], []
    seen = {}
    homerooms = {}
    for rec in records:
        where = f'{rec["_sheet"]} · {rec["_row"]}' if rec.get('_sheet') else str(rec['_row'])
        name = re.sub(r'\s+', ' ', (rec.get('full_name') or '')).strip()
        name_en = re.sub(r'\s+', ' ', (rec.get('full_name_en') or '')).strip()
        if not name and name_en:
            name = name_en
        if not name:
            continue                                    # blank line, or a totals row
        if not name_en and _name_lang(name) == 'en':
            name_en = name
        stage_txt, n, grade_txt, sec_txt = _place_of(rec)
        target = None                                   # ('sec', id) | ('new', key)
        if n is None and not sec_txt and default_section_id:
            target = ('sec', default_section_id)
        elif n is None:
            problems.append({'where': where, 'name': name,
                             'why': _('لم يُعرف الصف من «{t}»', t=grade_txt or sec_txt or rec.get('_sheet') or '—')})
            continue
        elif not sec_txt:
            problems.append({'where': where, 'name': name, 'why': _('لم تُعرف الشعبة')})
            continue
        else:
            gr = by_num.get(n)
            sec = None
            if gr is not None:
                for k in section_keys(sec_txt):
                    sec = sec_index.get((gr.id, k))
                    if sec:
                        break
            if sec is not None:
                target = ('sec', sec.id)
            else:
                gkey = f'g{n}'
                if gr is None and gkey not in new_grades:
                    ar, en = grade_name(n)
                    sid = stage_for(n, stage_txt)
                    skey = None
                    if sid is None and stage_txt:
                        skey = norm(stage_txt)
                        new_stages.setdefault(skey, {'key': skey, 'name': stage_txt,
                                                     'name_en': stage_txt if _name_lang(stage_txt) == 'en' else None})
                    new_grades[gkey] = {'key': gkey, 'number': n, 'name': ar, 'name_en': en,
                                        'stage_id': sid, 'stage_key': skey, 'stage_text': stage_txt}
                skey = f'{gkey}:{norm(sec_txt)}'
                if skey not in new_sections:
                    sec_en = (rec.get('section_en') or '').strip() or \
                        (sec_txt if _name_lang(sec_txt) == 'en' else None)
                    new_sections[skey] = {'key': skey, 'grade_key': gkey, 'grade_id': gr.id if gr else None,
                                          'grade_number': n, 'name': sec_txt, 'name_en': sec_en}
                target = ('new', skey)
        if rec.get('homeroom') and rec['homeroom'].strip().lower() not in ('(empty)', 'empty', '-', '—'):
            homerooms[f'{target[0]}:{target[1]}'] = rec['homeroom'].strip()
        if target[0] == 'sec' and allowed_sections is not None and target[1] not in allowed_sections:
            problems.append({'where': where, 'name': name, 'why': _('الشعبة خارج نطاقك')})
            continue
        place = f'{target[0]}:{target[1]}'
        first = seen.get(norm(name))
        if first is not None and first['place'] == place:
            first['repeats'] += 1                       # the same student listed twice (e.g. a copy sheet)
            continue
        if first is not None:
            warnings.append({'where': where, 'name': name,
                             'why': _('الاسم نفسه في شعبة أخرى في الملف ({w}) — أُضيف الاثنان، تحقّق منهما',
                                      w=first['where'])})
        st = by_number.get((rec.get('student_number') or '').strip()) if rec.get('student_number') else None
        if st is None:
            st = by_name.get(norm(name)) or (by_name.get(norm(name_en)) if name_en else None)
        status = 'new'
        if st is not None:
            same = target[0] == 'sec' and st.section_id == target[1]
            status = 'same' if same else ('move' if update else 'other')
        row = {'where': where, 'name': name, 'name_en': name_en or None,
               'number': (rec.get('student_number') or '').strip() or None, 'place': place,
               'status': status, 'student_id': st.id if st else None, 'repeats': 0,
               'current': st.section.full_name if st else None,
               'extra': {k: (rec.get(k) or '').strip() for k in ('gender', 'guardian_name', 'guardian_relation',
                                                              'guardian_phone', 'guardian_email', 'notes')
                         if rec.get(k)}}
        seen.setdefault(norm(name), row)
        rows.append(row)
    places = {}
    for s in sections:
        places[f'sec:{s.id}'] = {'label': f'{s.grade.stage.name} — {s.full_name}', 'grade': s.grade.name,
                                 'number': _number_of(s.grade.name, s.grade.name_en) or 99,
                                 'section': s.name, 'is_new': False}
    for k, ns in new_sections.items():
        gname = new_grades[ns['grade_key']]['name'] if ns['grade_key'] in new_grades else \
            next((gr.name for gr in grades if gr.id == ns['grade_id']), '')
        places[f'new:{k}'] = {'label': f'{gname} / {ns["name"]}', 'grade': gname, 'number': ns['grade_number'],
                              'section': ns['name'], 'is_new': True}
    used = {r['place'] for r in rows}
    summary = []
    for p in sorted(used, key=lambda p: (places[p]['number'], norm(places[p]['section']))):
        rs = [r for r in rows if r['place'] == p]
        summary.append({'place': p, **places[p], 'total': len(rs),
                        'new': sum(1 for r in rs if r['status'] == 'new'),
                        'homeroom': homerooms.get(p, '')})
    counts = {k: sum(1 for r in rows if r['status'] == k) for k in ('new', 'same', 'move', 'other')}
    counts['repeats'] = sum(r['repeats'] for r in rows)
    counts['problems'] = len(problems)
    return {'rows': rows, 'problems': problems, 'warnings': warnings, 'new_stages': list(new_stages.values()),
            'new_grades': list(new_grades.values()),
            'new_sections': list(new_sections.values()), 'summary': summary, 'counts': counts,
            'homerooms': homerooms, 'update': bool(update), 'sheets': overview, 'skipped': sorted(skip),
            'default_section_id': default_section_id}


def stages_needed(plan):
    """New grades that still need a stage chosen on the preview."""
    return [g for g in plan['new_grades'] if not g.get('stage_id') and not g.get('stage_key')]


def apply_plan(plan, stage_choice=None, new_stage_name=None):
    """Creates the planned grades and sections, adds / moves the students. stage_choice: {grade key: stage
    id} for the grades the plan could not place. → counts. The caller commits."""
    from models import Student as S
    stage_choice = stage_choice or {}
    valid_stages = {s.id for s in Stage.query.all()}
    created_stage = None
    grade_ids = {}
    made = {'stages': 0, 'grades': 0, 'sections': 0}
    stage_ids = {}
    for i, ns in enumerate(plan.get('new_stages') or []):
        st = Stage(name=ns['name'][:100], name_en=(ns.get('name_en') or None), sort_order=len(valid_stages) + i)
        db.session.add(st)
        db.session.flush()
        stage_ids[ns['key']] = st.id
        valid_stages.add(st.id)
        made['stages'] += 1
    for g in sorted(plan['new_grades'], key=lambda g: g['number']):
        sid = g.get('stage_id') or stage_ids.get(g.get('stage_key')) or stage_choice.get(g['key'])
        if sid == 'new' or (sid is None and new_stage_name):
            if created_stage is None:
                created_stage = Stage(name=(new_stage_name or _('المرحلة الجديدة'))[:100],
                                      sort_order=len(valid_stages))
                db.session.add(created_stage)
                db.session.flush()
                made['stages'] += 1
            sid = created_stage.id
        if sid not in valid_stages and (created_stage is None or sid != created_stage.id):
            raise ValueError(_('اختر المرحلة لـ {g}', g=g['name']))
        gr = Grade(stage_id=sid, name=g['name'], name_en=g['name_en'], sort_order=g['number'])
        db.session.add(gr)
        db.session.flush()
        grade_ids[g['key']] = gr.id
        made['grades'] += 1
    section_ids = {}
    order = {}
    for ns in plan['new_sections']:
        gid = ns['grade_id'] or grade_ids[ns['grade_key']]
        order[gid] = order.get(gid, Section.query.filter_by(grade_id=gid).count())
        sec = Section(grade_id=gid, name=ns['name'][:50], name_en=(ns.get('name_en') or None),
                      sort_order=order[gid])
        order[gid] += 1
        db.session.add(sec)
        db.session.flush()
        section_ids[f'new:{ns["key"]}'] = sec.id
        made['sections'] += 1

    def sec_id(place):
        return section_ids[place] if place.startswith('new:') else int(place.split(':', 1)[1])

    for place, teacher in (plan.get('homerooms') or {}).items():
        sec = db.session.get(Section, sec_id(place))
        if sec is not None and not sec.homeroom_teacher:
            sec.homeroom_teacher = teacher[:200]
    added = moved = 0
    for r in plan['rows']:
        if r['status'] == 'new':
            st = S(section_id=sec_id(r['place']), full_name=r['name'][:200],
                   full_name_en=(r.get('name_en') or None), student_number=r.get('number'), is_active=True)
            ex = r.get('extra') or {}
            from blueprints.students import _gender
            st.gender = _gender(ex.get('gender')) if ex.get('gender') else None
            for k in ('guardian_name', 'guardian_relation', 'guardian_phone', 'guardian_email', 'notes'):
                if ex.get(k):
                    setattr(st, k, ex[k][:300 if k == 'guardian_email' else 200])
            if st.student_number and S.query.filter_by(student_number=st.student_number).first():
                st.student_number = None                 # a number already used: keep the student, drop it
            db.session.add(st)
            added += 1
        elif r['status'] == 'move' and r.get('student_id'):
            st = db.session.get(S, r['student_id'])
            if st is not None:
                st.section_id = sec_id(r['place'])
                if r.get('name_en') and not st.full_name_en:
                    st.full_name_en = r['name_en']
                moved += 1
    db.session.flush()
    return {**made, 'added': added, 'moved': moved}
