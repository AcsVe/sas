"""The news ticker at the top of every page of an organization (as on appace.com/sas): messages in Arabic
and English, colours, opacity, font, size, speed, direction, logo and its pulse, the separator, and the
fade at the edges. Kept per organization in Tenant.ticker_json; every value is checked on save, and the
messages are plain text (escaped when shown — no HTML can be injected through the ticker).
"""
import json
import re
from utils.i18n import _

DEFAULTS = {
    'enabled': False,
    'feeds_ar': [],
    'feeds_en': [],
    'fg': '#1B3358',
    'bg': '#ffffff',
    'opacity': 92,          # background opacity, %
    'font': 'inherit',
    'size': 15,             # px
    'weight': 700,
    'speed': 60,            # px per second
    'direction': 'auto',    # auto: Arabic moves to the right, English to the left | left | right
    'logo': 'school',       # school | none | url
    'logo_url': '',
    'logo_size': 26,
    'logo_pulse': True,
    'logo_pulse_speed': 6,  # s
    'separator': 'logo',    # logo | dot | diamond | bar | image
    'sep_img_url': '',
    'mask_fade': 8,         # % of the width faded at each edge
    'pause_on_hover': True,
    'on_login': True,       # also on the sign-in pages
}
FONTS = [('inherit', 'خط الموقع (Tajawal)'), ("'Cairo',sans-serif", 'Cairo'), ("'Segoe UI',Tahoma,Arial,sans-serif", 'Segoe UI'),
         ("Georgia,serif", 'Georgia'), ("Consolas,monospace", 'Consolas')]
SEPARATORS = [('logo', 'شعار المدرسة'), ('dot', '•'), ('diamond', '◆'), ('bar', '|'), ('image', 'صورة (رابط)')]
_COLOR = re.compile(r'^#[0-9a-fA-F]{6}$')
_URL = re.compile(r'^(https://[^\s"\'<>]{3,500}|/[^\s"\'<>]{1,300})$')


def _int(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def load(tenant):
    cfg = dict(DEFAULTS)
    if tenant is not None and tenant.ticker_json:
        try:
            data = json.loads(tenant.ticker_json)
            if isinstance(data, dict):
                cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
        except ValueError:
            pass
    return cfg


def _lines(text):
    out = []
    for ln in (text or '').splitlines():
        ln = re.sub(r'\s+', ' ', ln).strip()
        if ln:
            out.append(ln[:300])
    return out[:30]


def from_form(form):
    """Validated settings from the admin form. → (cfg, errors)."""
    errors = []
    cfg = dict(DEFAULTS)
    cfg['enabled'] = form.get('enabled') == '1'
    cfg['feeds_ar'] = _lines(form.get('feeds_ar'))
    cfg['feeds_en'] = _lines(form.get('feeds_en'))
    for k in ('fg', 'bg'):
        v = (form.get(k) or '').strip()
        if _COLOR.match(v):
            cfg[k] = v.lower()
        elif v:
            errors.append(_('لون غير صالح (الصيغة ‎#1B3358‎)'))
    cfg['opacity'] = _int(form.get('opacity'), 0, 100, DEFAULTS['opacity'])
    fonts = {f for f, _l in FONTS}
    cfg['font'] = form.get('font') if form.get('font') in fonts else DEFAULTS['font']
    cfg['size'] = _int(form.get('size'), 10, 32, DEFAULTS['size'])
    cfg['weight'] = 700 if form.get('weight') == '700' else 500
    cfg['speed'] = _int(form.get('speed'), 10, 300, DEFAULTS['speed'])
    cfg['direction'] = form.get('direction') if form.get('direction') in ('auto', 'left', 'right') else 'auto'
    cfg['logo'] = form.get('logo') if form.get('logo') in ('school', 'none', 'url') else 'school'
    cfg['logo_size'] = _int(form.get('logo_size'), 12, 60, DEFAULTS['logo_size'])
    cfg['logo_pulse'] = form.get('logo_pulse') == '1'
    cfg['logo_pulse_speed'] = _int(form.get('logo_pulse_speed'), 2, 30, DEFAULTS['logo_pulse_speed'])
    cfg['separator'] = form.get('separator') if form.get('separator') in {s for s, _l in SEPARATORS} else 'logo'
    cfg['mask_fade'] = _int(form.get('mask_fade'), 0, 30, DEFAULTS['mask_fade'])
    cfg['pause_on_hover'] = form.get('pause_on_hover') == '1'
    cfg['on_login'] = form.get('on_login') == '1'
    for k in ('logo_url', 'sep_img_url'):
        v = (form.get(k) or '').strip()
        if v and not _URL.match(v):
            errors.append(_('رابط الصورة يجب أن يبدأ بـ https://'))
            v = ''
        cfg[k] = v
    if cfg['logo'] == 'url' and not cfg['logo_url']:
        cfg['logo'] = 'school'
    if cfg['separator'] == 'image' and not cfg['sep_img_url']:
        cfg['separator'] = 'logo'
    if cfg['enabled'] and not (cfg['feeds_ar'] or cfg['feeds_en']):
        errors.append(_('أضف رسالة واحدة على الأقل لتفعيل الشريط'))
    return cfg, errors


def save(tenant, cfg):
    tenant.ticker_json = json.dumps(cfg, ensure_ascii=False)


def view(tenant, lang, page='app'):
    """What the page shows, or None: the messages of the page language (the other language when it has
    none) and the style values."""
    cfg = load(tenant)
    if not cfg['enabled'] or (page == 'login' and not cfg['on_login']):
        return None
    return render_values(cfg, lang)


def render_values(cfg, lang):
    """The values a page needs to draw the ticker from settings (also the admin page's preview)."""
    feeds = (cfg['feeds_en'] or cfg['feeds_ar']) if lang == 'en' else (cfg['feeds_ar'] or cfg['feeds_en'])
    if not feeds:
        return None
    r, g_, b = (int(cfg['bg'][i:i + 2], 16) for i in (1, 3, 5))
    direction = cfg['direction'] if cfg['direction'] != 'auto' else ('left' if lang == 'en' else 'right')
    return {**cfg, 'feeds': feeds, 'bg_rgba': f'rgba({r},{g_},{b},{cfg["opacity"] / 100:.2f})',
            'move': direction}
