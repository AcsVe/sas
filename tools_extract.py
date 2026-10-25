"""Lists every translatable Arabic text of SAS (templates and code) — the keys of i18n/en.json.

    python tools_extract.py            # the keys missing from i18n/en.json
    python tools_extract.py --all      # every key
"""
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AR = re.compile(r'[؀-ۿ]')
TPL = re.compile(r"""_\(\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")\s*[,)]""")
PY_DIRS = ['blueprints', 'services', 'utils', 'models']
PY_FILES = ['app.py', 'authz.py', 'security.py']
# files whose Arabic constants are shown through {{ _(…) }} in the templates (labels, lists of choices)
DISPLAY = {'authz.py', 'school.py', 'core.py', 'structure.py', 'holidays.py', 'students.py', 'ticker.py', 'main.py',
           'admin.py', 'employees.py'}
_REGEXY = re.compile(r'[\\^$\[\]|]')


def _unescape(s):
    return s.encode('utf-8').decode('unicode_escape').encode('latin-1').decode('utf-8') if '\\' in s else s


def template_keys():
    out = set()
    for f in (ROOT / 'templates').rglob('*.html'):
        if 'emails' in f.parts:
            continue
        for m in TPL.finditer(f.read_text(encoding='utf-8')):
            k = m.group(1) if m.group(1) is not None else m.group(2)
            if AR.search(k):
                out.add(_unescape(k))
    return out


def python_keys():
    out = set()
    files = [p for d in PY_DIRS for p in (ROOT / d).rglob('*.py')] + [ROOT / f for f in PY_FILES]
    for f in files:
        tree = ast.parse(f.read_text(encoding='utf-8'))
        skip = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ('FIELDS',)
                                                    for t in node.targets):
                skip.update(id(n) for n in ast.walk(node.value))
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body and \
                    isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                skip.add(id(node.body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == '_' and \
                    node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                out.add(node.args[0].value)
            elif f.name in DISPLAY and isinstance(node, ast.Constant) and isinstance(node.value, str) and \
                    AR.search(node.value) and id(node) not in skip and not _REGEXY.search(node.value) and \
                    len(node.value.strip()) > 1:
                out.add(node.value)
    return out


def all_keys():
    return template_keys() | python_keys()


if __name__ == '__main__':
    cat = json.loads((ROOT / 'i18n' / 'en.json').read_text(encoding='utf-8'))
    keys = sorted(all_keys())
    if '--all' not in sys.argv:
        keys = [k for k in keys if k not in cat]
    print(json.dumps(keys, ensure_ascii=False, indent=0))
    print(len(keys), file=sys.stderr)
