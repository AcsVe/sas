"""مراجعة المظهر في متصفح حقيقي (Chrome المثبّت عبر Playwright): كل صفحة على الحاسوب والهاتف.

واجهة عربية/إنجليزية × بيانات عربية/إنجليزية. يقيس: تباين النص مع خلفيته (4.5 على الأقل، و3 للنص الكبير، مع شفافية الآباء)،
والخط الأصغر من 13 بكسل، واتجاه النص (نص يبدأ بلغة داخل عنصر باتجاه اللغة الأخرى، وتاريخ ووقت داخل عنصر من اليمين لليسار)،
والنص العربي غير المترجم في الواجهة الإنجليزية ببيانات إنجليزية، والتمرير الأفقي، والعناصر الخارجة عن بطاقتها، وأخطاء JavaScript
وسياسة أمان المحتوى، ولغة النصوص ونصوص الإرشاد (placeholder وtitle وalt): نص يشبه الكود، أو كلمة عامية أو دخيلة،
أو نص إرشاد بغير لغة الواجهة، أو صورة بلا نص بديل. ويحفظ صوراً مصغّرة إن ضُبط SHOTS=1.

    python tests/ui_audit.py            (بيئة فيها playwright؛ SAS_PY = بايثون النظام لتشغيل الخادم والبيانات)
"""
import atexit
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.environ.get('E2E_OUT') or tempfile.mkdtemp(prefix='sas-ui-')
os.makedirs(OUT, exist_ok=True)
PY = os.environ.get('SAS_PY') or sys.executable
PORTS = {'ar': 5296, 'en': 5297}


def start(data):
    """قاعدة وخادم مستقلان لكل لغة بيانات."""
    folder = os.path.join(OUT, f'data-{data}')
    os.makedirs(folder, exist_ok=True)
    db_path = os.path.join(folder, 'ui.db')
    for ext in ('', '-wal', '-shm'):
        if os.path.exists(db_path + ext):
            os.remove(db_path + ext)
    base = f'http://127.0.0.1:{PORTS[data]}'
    env = dict(os.environ, PYTHONUTF8='1', SECRET_KEY='ui-audit-secret', BASE_URL=base, PORT=str(PORTS[data]),
               DATABASE_URL='sqlite:///' + db_path.replace('\\', '/'), DATA_DIR=folder, UPLOAD_DIR=os.path.join(folder, 'uploads'),
               DISABLE_SCHEDULER='1', DISABLE_REMINDERS='1', TRUST_PROXY='0', CONFIG_ENV=os.path.join(folder, 'none.env'))
    for k in ('MS_TENANT_ID', 'MS_CLIENT_ID', 'MS_CLIENT_SECRET', 'MS_SENDER_EMAIL'):
        env.pop(k, None)
    out = subprocess.run([PY, os.path.join(ROOT, 'tests', 'ui_seed.py'), db_path, data], env=env, cwd=ROOT,
                         capture_output=True, text=True, encoding='utf-8', check=True).stdout
    proc = subprocess.Popen([PY, os.path.join(ROOT, 'serve.py')], env=env, cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        try:
            urllib.request.urlopen(base + '/login', timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    atexit.register(proc.terminate)   # لا يبقى خادم قديم يعمل إن توقّف الفحص في منتصفه
    return json.loads(out.strip().splitlines()[-1]), proc


started = {d: start(d) for d in PORTS}
# نصوص عربية مقصودة في الواجهة الإنجليزية (اسم اللغة في مبدّل اللغة)
UNTRANSLATED_OK = ['العربية', 'عربي']

PAGES = ['/home', '/attendance/daily', '/attendance/records', '/attendance/record/new?student={student}', '/attendance/record/{record}',
         '/attendance/notify', '/students/', '/students/{student}', '/students/{student}/edit', '/students/import',
         '/leaves/', '/leaves/{leave}', '/holidays/', '/holidays/{holiday}', '/timetable/mine',
         '/timetable/section/{section}', '/reports/', '/reports/compare', '/structure/', '/structure/years/',
         '/structure/semesters/', '/structure/stages/', '/structure/grades/', '/structure/sections/',
         '/structure/leave_types/', '/structure/periods/', '/structure/periods/import', '/employees/',
         '/employees/{employee}', '/admin/departments', '/admin/departments/{dept}', '/admin/users',
         '/admin/users/new', '/admin/users/{user}', '/admin/roles', '/admin/roles/new', '/admin/settings',
         '/admin/ticker', '/admin/email-log', '/admin/audit', '/change-password']
PUBLIC = ['/login', '/forgot', '/super/login', '/no-such-page']

AUDIT_JS = r"""(args) => {
  const [checkArabic, okList] = args;
  const lum = (c) => { const m = c.match(/[\d.]+/g); if (!m) return null; const [r, g, b] = m.slice(0, 3).map((v) => { v = v / 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return { l: 0.2126 * r + 0.7152 * g + 0.0722 * b, a: m.length > 3 ? Number(m[3]) : 1 }; };
  const bgOf = (el) => { while (el) { const s = getComputedStyle(el); const L = lum(s.backgroundColor);
      if (s.backgroundImage !== 'none') return null; if (L && L.a > 0.9) return L.l; el = el.parentElement; } return 1; };
  const AR = /[؀-ۿ]/, LA = /[A-Za-z]/, NEUTRAL = /[0-9٠-٩.:*?؟()\-–—%/]/;
  const firstStrong = (t) => { for (const ch of t) { if (AR.test(ch)) return 'rtl'; if (LA.test(ch)) return 'ltr'; } return null; };
  const isolated = (n) => n.nodeType === 1 && (n.tagName === 'BDI' || n.hasAttribute('dir') || getComputedStyle(n).unicodeBidi === 'plaintext');
  // النص بين علامتي العزل (U+2068…U+2069) يأخذ اتجاهه بنفسه
  const textOf = (n) => n.nodeType === 3 ? n.textContent.replace(/⁨[^⁩]*⁩/g, ' ') : (isolated(n) ? ' ' : [...n.childNodes].map(textOf).join(''));
  const out = { contrast: [], small: [], spill: [], bidi: [], untranslated: [] }; const seen = new Set();
  const add = (k, list, msg) => { if (!seen.has(k)) { seen.add(k); out[list].push(msg.slice(0, 150)); } };
  document.querySelectorAll('body *').forEach((el) => {
    if (!el.offsetParent && getComputedStyle(el).position !== 'fixed') return;
    if (![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim().length > 1)) return;
    const s = getComputedStyle(el); if (s.visibility === 'hidden') return;
    // الشفافية الفعلية = حاصل ضرب شفافية العنصر وكل آبائه
    let op = 1; for (let n = el; n; n = n.parentElement) op *= Number(getComputedStyle(n).opacity);
    if (op < 0.15) return;
    if (el.closest('canvas,svg,option,select,script,style')) return;
    const fs = parseFloat(s.fontSize), fw = Number(s.fontWeight) || 400, txt = el.textContent.replace(/\s+/g, ' ').trim().slice(0, 40);
    let fg = lum(s.color); const bg = bgOf(el);
    if (fg && bg !== null && op < 1) fg = { l: fg.l * op + bg * (1 - op), a: fg.a };   // تقريب: مزج الإضاءة مع الخلفية
    if (fg && bg !== null && !el.closest(':disabled,[disabled]')) {
      const ratio = (Math.max(fg.l, bg) + 0.05) / (Math.min(fg.l, bg) + 0.05), large = fs >= 24 || (fs >= 18.6 && fw >= 700);
      if (ratio < (large ? 3 : 4.5)) add('c' + txt, 'contrast', `${ratio.toFixed(2)} «${txt}» ${el.tagName}.${el.className}`);
    }
    if (fs < 13) add('s' + txt, 'small', `${fs}px «${txt}» ${el.tagName}.${el.className}`);
    const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(' ').replace(/\s+/g, ' ').trim();
    // تاريخ ووقت (2026-11-06 18:05) داخل عنصر من اليمين لليسار: يظهر الوقت قبل التاريخ
    if (s.direction === 'rtl' && /^\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2}/.test(own) && s.unicodeBidi !== 'plaintext')
      add('t' + own, 'bidi', `rtl «${own.slice(0, 40)}» ${el.tagName}.${el.className}`);
    const full = s.unicodeBidi === 'plaintext' ? '' : [...el.childNodes].map(textOf).join('').replace(/\s+/g, ' ').trim();
    const want = firstStrong(full);
    if (want && want !== s.direction && NEUTRAL.test(full) && (AR.test(full) && LA.test(full) || want === 'rtl' ? true : /^[^A-Za-z]|[^A-Za-z0-9]$/.test(full))
        && !el.closest('code,pre,kbd,input,textarea')) add('d' + full, 'bidi', `${s.direction} «${full.slice(0, 60)}» ${el.tagName}.${el.className}`);
    // data-content: محتوى يكتبه المستخدم
    if (checkArabic && !el.closest('[data-content]') && AR.test(own) && !okList.includes(own))
      add('u' + own, 'untranslated', `«${own.slice(0, 70)}» ${el.tagName}.${el.className}`);
  });
  document.querySelectorAll('.card, .kpi, .stat, .auth-card').forEach((card) => {
    const cr = card.getBoundingClientRect(); if (!cr.width) return;
    card.querySelectorAll('*').forEach((el) => {
      if (el.closest('.table-wrap,.dt-wrap,.tbl-wrap,[style*="overflow"],details:not([open]),[hidden],.menu,dialog')) return;
      const r = el.getBoundingClientRect(); if (!r.width || !r.height) return;
      if (r.left < cr.left - 2 || r.right > cr.right + 2)
        add('p' + el.tagName + el.className, 'spill', `${el.tagName}.${el.className} «${(el.textContent || '').trim().slice(0, 30)}» in ${card.className}`);
    });
  });
  out.overflow = document.documentElement.scrollWidth - innerWidth;
  return out;
}"""

# فحص لغة الواجهة: النصوص الظاهرة ونصوص الإرشاد (placeholder وtitle وalt وaria-label) — يُنسخ إلى tests/ui_audit.py في كل نظام.
# يرصد: نصاً يشبه الكود (أسماء متغيرات وملفات وقيم برمجية)، وكلمات عامية أو دخيلة في الواجهة العربية، ونص إرشاد بالإنجليزية
# في الواجهة العربية أو بالعربية في الإنجليزية، وصورة بلا نص بديل.
LANG_JS = r"""(args) => {
  const [ui, okWords, checkArabic, scopeSel] = args;   // checkArabic: الواجهة الإنجليزية ببيانات إنجليزية
  const out = []; const seen = new Set();
  const add = (k, m) => { if (!seen.has(k)) { seen.add(k); out.push(m.slice(0, 150)); } };
  const AR = /[؀-ۿ]/;
  const ok = new Set(['iOS', 'iPhone', 'iPad', 'macOS', 'iCloud', 'eSchool', 'YouTube', 'WhatsApp', 'PayPal', 'OneDrive',
                      'SharePoint', 'PowerPoint', 'GitHub', 'MenaPAY', 'MenaITech', 'ZKTeco', 'BellPlayer', 'JavaScript'].concat(okWords || []));
  // يُحذف البريد والروابط والنطاقات قبل الفحص: ليست كوداً
  const clean = (t) => t.replace(/\S+@\S+|https?:\/\/\S*|\b[\w.-]+\.(org|com|net|edu|jo|test|gov)\b\S*/gi, ' ');
  const CODE = [
    [/\b[a-z][a-z0-9]*_[a-z0-9_]+\b/, 'snake_case'],
    [/\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b/, 'variable name'],
    [/\b[a-z]+[A-Z][A-Za-z]+\b/, 'camelCase'],
    [/\{\{|\}\}|\{%|%\}|\$\{|<\/?[a-z]+>/, 'template code'],
    [/^(undefined|null|NaN)$|\b(undefined|NaN|Traceback|TypeError|KeyError|ValueError)\b|\[object /, 'program value'],
    [/\b[\w-]+\.(py|bat|env|db|sqlite|json|js|css|html|ps1|sql|log|ini|cfg)\b/i, 'file name'],
    [/\b(HTTP|Error|errno|status)\s*\d{3}\b|\bSQLITE_|\bOperationalError\b/i, 'error code'],
  ];
  // None/True/False كلمات إنجليزية سليمة في الواجهة الإنجليزية؛ وفي العربية تعني قيمة برمجية تسرّبت
  if (ui === 'ar') CODE.push([/^(None|True|False)$/, 'program value']);
  const codeLike = (t) => { t = clean(t); for (const [re, name] of CODE) { const m = t.match(re); if (m && !ok.has(m[0])) return `${name} «${m[0]}»`; } return null; };
  // عامي أو دخيل (القاعدة: «إذا/إن» لا «لو»، «فقط» لا «بس»، «الخادم» لا «السيرفر»، «الهاتف» لا «الموبايل»...)
  const COLLOQ = /(?:^|[\s،؛.؟!«(\/])(لو|بس|اللي|إللي|فاضي|فاضية|هيك|شو|مش|كمان|هلق|هلأ|منيح|ليش|وين|هاي|هاد|هادا|السيرفر|سيرفر|الموبايل|موبايل|تيمز|الباسورد|باسورد|يوزر|اليوزر|اللوجو|لوجو|الداتا|داتا|لينك|اللينك|فايل|الفايل|ايميل|إيميل|الإيميل|الايميل|اوكي|أوكي|نزّل|نزّلها|حمّلها|سيستم|السيستم|أبديت|ابديت)(?=$|[\s،؛.؟!»):\/])/;
  const LATIN_WORD = /\b[A-Za-z]{3,}\b/g;
  const check = (el, where, t) => {
    if (!t || !t.trim()) return;
    t = t.replace(/\s+/g, ' ').trim();
    const tag = `${el.tagName}.${(el.className && el.className.baseVal === undefined ? el.className : '')}`.slice(0, 40);
    const c = codeLike(t); if (c) add('c' + where + t, `code-like ${c} in ${where} «${t.slice(0, 60)}» ${tag}`);
    if (ui === 'ar') {
      const m = t.match(COLLOQ); if (m) add('q' + t, `colloquial «${m[1]}» in ${where} «${t.slice(0, 60)}» ${tag}`);
      if (where !== 'text' && !AR.test(t)) {   // نص إرشاد كله بالإنجليزية (الرموز والأسماء بجانب العربية مقبولة)
        const latin = (clean(t).match(LATIN_WORD) || []).filter((w) => !ok.has(w) && !/^x+$/i.test(w) && !/^(PDF|Excel|Word|CSV|QR|Microsoft|Google|Outlook|Teams|Android|Windows|Chrome|Edge|Wi|Fi|USB|SMS|API|URL|IP|PIN|OTP|ID|Cloudflare|Dahua|Render|Drive|Gmail|Zoom|Forms|Office|Apple|Huawei|Samsung)$/i.test(w));
        if (latin.length) add('l' + where + t, `English «${latin.slice(0, 3).join(' ')}» in ${where} of the Arabic interface «${t.slice(0, 60)}» ${tag}`);
      }
    } else if (checkArabic && where !== 'text' && AR.test(t) && !el.closest('[data-content]') && !(okWords || []).includes(t)) {
      add('a' + where + t, `Arabic in ${where} of the English interface «${t.slice(0, 60)}» ${tag}`);
    }
  };
  const scope = (scopeSel && document.querySelector(scopeSel)) || document.body;
  scope.querySelectorAll('*').forEach((el) => {
    if (el.closest('script,style,code,pre,kbd,noscript,template,textarea,[data-content],[data-raw]')) return;   // textarea: نص يكتبه المستخدم أو رمز تضمين للنسخ
    const s = getComputedStyle(el); if (s.display === 'none' && !el.closest('select')) return;
    [...el.childNodes].forEach((n) => { if (n.nodeType === 3) check(el, 'text', n.textContent); });
    for (const a of ['placeholder', 'title', 'alt', 'aria-label']) if (el.hasAttribute(a)) check(el, a, el.getAttribute(a));
    if (el.matches('input[type=submit],input[type=button]')) check(el, 'button', el.value);
    if (el.tagName === 'IMG' && !el.hasAttribute('alt')) add('i' + el.src, `image without alt text ${el.src.split('/').pop().slice(0, 50)}`);
  });
  check(document.documentElement, 'title', document.title);
  return out;
}"""


report = []
try:
    with sync_playwright() as p:
        kw = {'executable_path': os.environ['PW_EXE']} if os.environ.get('PW_EXE') else {'channel': os.environ.get('E2E_CHANNEL', 'chrome')}
        browser = p.chromium.launch(**kw)
        for lang, data in (('ar', 'ar'), ('en', 'ar'), ('en', 'en')):
          BASE = f'http://127.0.0.1:{PORTS[data]}'
          check_ar = lang == 'en' and data == 'en'
          for vp_kind, vp in (('desktop', {'viewport': {'width': 1366, 'height': 900}}), ('phone', dict(p.devices['iPhone 13']))):
            vp_name = f'{vp_kind}-{lang}{data}'
            ctx = browser.new_context(**vp)
            page = ctx.new_page()
            errs = []
            page.on('pageerror', lambda e: errs.append(str(e)))
            page.on('response', lambda r: errs.append(f'HTTP {r.status} {r.request.method} {r.url}') if r.status >= 400 and '/no-such-page' not in r.url else None)

            def visit(u):
                errs.clear()
                page.goto(BASE + u, wait_until='networkidle')
                page.wait_for_timeout(250)
                d = page.evaluate('document.documentElement.dir')
                if d != ('ltr' if lang == 'en' else 'rtl'):
                    errs.append(f'page direction is {d}')
                res = page.evaluate(AUDIT_JS, [check_ar, UNTRANSLATED_OK])
                res['lang'] = page.evaluate(LANG_JS, [lang, UNTRANSLATED_OK, check_ar])
                report.append((vp_name, u, res, list(errs)))
                if os.environ.get('SHOTS'):
                    name = f"{vp_name}-{u.strip('/').replace('/', '_').replace('?', '_')[:40] or 'root'}.jpg"
                    try:
                        page.screenshot(path=os.path.join(OUT, name), type='jpeg', quality=40, full_page=True)
                    except Exception:
                        page.screenshot(path=os.path.join(OUT, name), type='jpeg', quality=40)
            ids = started[data][0]
            pages = [x.format(**ids) for x in PAGES]
            public = [x.format(**ids) for x in PUBLIC]
            page.goto(BASE + f'/lang/{lang}?next=/login')
            for u in public:
                visit(u)
            page.goto(BASE + '/login')
            if page.locator('[name=org]').count():
                page.fill('[name=org]', 'raed')
            page.fill('[name=username]', 'admin')
            page.fill('[name=password]', 'Strong-Pass-2026')
            page.click('button[type=submit]')
            page.wait_for_load_state('networkidle')
            for u in pages:
                visit(u)
            ctx.close()
        browser.close()
finally:
    for _, proc in started.values():
        proc.terminate()

bad = 0
for vp_name, u, res, errs in report:
    issues = ([f"horizontal overflow {res['overflow']}px"] if res['overflow'] > 2 else []) + ['contrast ' + x for x in res['contrast']] \
        + ['small ' + x for x in res['small']] + ['spill ' + x for x in res['spill']] + ['direction ' + x for x in res['bidi']] \
        + ['untranslated ' + x for x in res['untranslated']] + ['language ' + x for x in res['lang']] + ['JS ' + e for e in errs]
    if issues:
        bad += len(issues)
        print(f'[{vp_name}] {u}')
        for i in issues[:15]:
            print('   ', i)
print(f'\n{len(report)} page views, {bad} issue(s). Output: {OUT}')
sys.exit(1 if bad else 0)
