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


def read_table(text=None, file_storage=None):
    """→ list of rows (list of strings). Accepts pasted text (tab / comma / ; / | separated), .csv, .xlsx."""
    if file_storage is not None and file_storage.filename:
        name = file_storage.filename.lower()
        data = file_storage.read()
        if name.endswith(('.xlsx', '.xlsm')):
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            ws = wb.worksheets[0]
            rows = [[_clean(c) for c in r] for r in ws.iter_rows(values_only=True)]
            wb.close()
            return [r for r in rows if any(r)]
        if not name.endswith(('.csv', '.txt', '.tsv')):
            raise ValueError('صيغة الملف غير مدعومة — استخدم Excel ‏(.xlsx) أو CSV')
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


def to_records(rows, fields):
    """fields: [(key, [header aliases…])] in the default column order. → (records, used_header: bool)."""
    if not rows:
        return [], False
    alias = {}
    for key, names in fields:
        for n in [key] + list(names):
            alias[norm(n)] = key
    first = [norm(c) for c in rows[0]]
    hits = sum(1 for c in first if c in alias)
    if hits >= 1 and hits >= len([c for c in first if c]) / 2:
        mapping = [alias.get(c) for c in first]
        body = rows[1:]
    else:
        mapping = [k for k, _ in fields]
        body = rows
    out = []
    for r in body:
        rec = {}
        for i, key in enumerate(mapping):
            if key and i < len(r) and r[i] and not rec.get(key):
                rec[key] = r[i]
        if rec:
            out.append(rec)
    return out, mapping is not None and body is not rows


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
