"""Reading pasted lists, CSV and Excel files into rows of named fields (employees, students, departments).
A header row is recognised by its column names (Arabic or English); without one the columns are taken in
the documented order."""
import csv
import io
import re

_AR = str.maketrans({'أ': 'ا', 'إ': 'ا', 'آ': 'ا', 'ة': 'ه', 'ى': 'ي', 'ـ': ''})


def norm(s):
    s = re.sub(r'[ً-ٰٟ]', '', str(s or '')).translate(_AR)
    return re.sub(r'[\s_\-:]+', '', s).lower()


def _clean(v):
    if v is None:
        return ''
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def read_sheets(text=None, file_storage=None):
    """→ [(sheet name, rows)]: every sheet of an Excel workbook (a school file often has one sheet per
    section), or one unnamed sheet for CSV / pasted text."""
    if file_storage is not None and file_storage.filename and \
            file_storage.filename.lower().endswith(('.xlsx', '.xlsm')):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(file_storage.read()), read_only=True, data_only=True)
        out = []
        for ws in wb.worksheets:
            rows = [[_clean(c) for c in r] for r in ws.iter_rows(values_only=True)]
            rows = [r for r in rows if any(r)]
            if rows:
                out.append((ws.title.strip(), rows))
        wb.close()
        return out
    rows = read_table(text, file_storage)
    return [('', rows)] if rows else []


def read_table(text=None, file_storage=None):
    """→ list of rows (list of strings). Accepts pasted text (tab / comma / ; / | separated), .csv, .xlsx
    (first sheet)."""
    if file_storage is not None and file_storage.filename:
        name = file_storage.filename.lower()
        if name.endswith(('.xlsx', '.xlsm')):
            sheets = read_sheets(None, file_storage)
            return sheets[0][1] if sheets else []
        data = file_storage.read()
        if not name.endswith(('.csv', '.txt', '.tsv')):
            from utils.i18n import _
            raise ValueError(_('صيغة الملف غير مدعومة — استخدم Excel ‏(.xlsx) أو CSV'))
        for enc in ('utf-8-sig', 'cp1256', 'latin-1'):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    text = (text or '').strip()
    if not text:
        return []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    sample = '\n'.join(lines[:5])
    delim = '\t' if '\t' in sample else ('|' if '|' in sample else (';' if sample.count(';') > sample.count(',') else ','))
    return [[_clean(c) for c in r] for r in csv.reader(lines, delimiter=delim) if any(c.strip() for c in r)]


def find_header(rows, fields, scan=10):
    """The index of the header row among the first rows (a title or «last update» line may come first),
    and its column → field mapping; (None, None) without a header."""
    alias = {}
    for key, names in fields:
        for n in [key] + list(names):
            alias[norm(n)] = key
    best = (None, None, 0)
    for i, row in enumerate(rows[:scan]):
        cells = [norm(c) for c in row]
        hits = sum(1 for c in cells if c in alias)
        filled = len([c for c in cells if c])
        if (hits >= 2 or (hits >= 1 and hits >= filled / 2)) and hits > best[2]:
            best = (i, [alias.get(c) for c in cells], hits)
    return best[0], best[1]


def to_records(rows, fields, keep_row=False):
    """fields: [(key, [header aliases…])] in the default column order. → (records, used_header: bool).
    keep_row: each record also gets «_row», its line number in the sheet."""
    if not rows:
        return [], False
    at, mapping = find_header(rows, fields)
    if at is not None:
        body = rows[at + 1:]
        start = at + 2
    else:
        mapping = [k for k, _x in fields]
        body = rows
        start = 1
    out = []
    for n, r in enumerate(body, start):
        rec = {}
        for i, key in enumerate(mapping):
            if key and i < len(r) and r[i] and not rec.get(key):
                rec[key] = r[i]
        if rec:
            if keep_row:
                rec['_row'] = n
            out.append(rec)
    return out, at is not None


def parse_date(v):
    """'2026-10-22', '22/10/2026', Excel datetimes → date or None."""
    from datetime import date, datetime
    if not v:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip().split(' ')[0].split('T')[0]
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None
