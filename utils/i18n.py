"""Two languages (Arabic, the default, and English) for every page, message and e-mail.

The Arabic text in the code and the templates is the message key; ``i18n/en.json`` maps it to English:

    flash(_('أُضيف {n}', n=3))                     # Python
    {{ _('الطلبة') }}   {{ label(section) }}         # templates

``label(obj)`` picks the English name of a row (name_en / label_en / full_name_en) when the page is in
English and the row has one. A key missing from the catalogue shows in Arabic; ``tests/test_i18n.py`` fails
when a key used in the code has no English.
"""
import json
import os

from flask import g, has_request_context, request, session

LANGS = ('ar', 'en')
_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'i18n', 'en.json')
_CATALOG = None


def catalog():
    global _CATALOG
    if _CATALOG is None:
        try:
            with open(_PATH, encoding='utf-8') as fh:
                _CATALOG = json.load(fh)
        except (OSError, ValueError):
            _CATALOG = {}
    return _CATALOG


def lang():
    if not has_request_context():
        return 'ar'
    cur = g.get('lang')
    if cur:
        return cur
    cur = session.get('lang') or request.cookies.get('lang') or 'ar'
    return cur if cur in LANGS else 'ar'


def is_en():
    return lang() == 'en'


def _(text, **kw):
    """The text in the page language; {name} placeholders are filled from kw (after translating)."""
    if text is None:
        return ''
    text = str(text)
    if is_en():
        text = catalog().get(text, text)
    return text.format(**kw) if kw else text


def pick(ar, en):
    """For a value that has its own English (a setting, a tenant name)."""
    return (en or ar) if is_en() else (ar or en or '')


def label(obj, field=None):
    """The display name of a row in the page language."""
    if obj is None:
        return ''
    names = [field] if field else ['name', 'label', 'full_name', 'title']
    for n in names:
        if hasattr(obj, n) and not callable(getattr(obj, n)):
            ar = getattr(obj, n)
            if is_en():
                en = getattr(obj, f'{n}_en', None)
                return en or _(ar)
            return ar or ''
    return str(obj)


class force_lang:
    """with force_lang('ar'): …  — e.g. the guardians' e-mail is always in Arabic, whatever the sender's page."""
    def __init__(self, code):
        self.code, self.prev = code, None

    def __enter__(self):
        if has_request_context():
            self.prev = g.get('lang')
            g.lang = self.code
        return self

    def __exit__(self, *exc):
        if has_request_context():
            g.lang = self.prev
        return False


def direction():
    return 'ltr' if is_en() else 'rtl'


def init_app(app):
    app.jinja_env.globals.update(_=_, label=label, pick=pick)

    @app.before_request
    def _set_lang():
        g.lang = None
        g.lang = lang()

    @app.context_processor
    def _inject():
        return {'lang': lang(), 'dir': direction(), 'is_en': is_en()}
