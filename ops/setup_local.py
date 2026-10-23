"""إعداد ملف config.env لنظام غياب الطلبة على هذا الجهاز (مرة واحدة). يقرأ إعدادات الأنظمة الأخرى على الجهاز
إن وجدها (مجلد Google Drive، رمز Cloudflare، إعدادات Microsoft Graph من نظام الحجوزات)."""
import getpass
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BASE, t  # noqa: E402

ENV = os.path.join(BASE, 'config.env')


def read_env(path):
    out = {}
    if os.path.isfile(path):
        for raw in open(path, encoding='utf-8-sig', errors='ignore'):
            line = raw.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def other_systems():
    """[(folder, settings)] of the other systems on this PC — to reuse the Drive folder, the Cloudflare token and the
    Microsoft Graph mail settings (the bookings system «venues» has them)."""
    cands = [os.environ.get('IAJ_DIR', '')]
    parent = os.path.dirname(BASE)
    try:
        cands += [os.path.join(parent, d) for d in os.listdir(parent)]
    except OSError:
        pass
    for drive in 'CDEFG':
        cands += [f'{drive}:\\{n}' for n in ('venues', 'RASTKT', 'IAJ', 'IAJ-main', 'iaj', 'Att', 'attendance', 'Bell')]
    out, seen = [], set()
    for d in cands:
        if not d or os.path.normcase(d) in seen or os.path.normcase(d) == os.path.normcase(BASE):
            continue
        seen.add(os.path.normcase(d))
        for name in ('.env', 'config.env'):
            f = os.path.join(d, name)
            if os.path.isfile(f):
                out.append((d, read_env(f)))
    return out


def pick(others, key):
    for d, e in others:
        if e.get(key):
            return e[key], d
    return '', ''


def ask(label, default='', secret=False):
    shown = (' [' + t('مخفي', 'hidden') + ']') if secret and default else (f' [{default}]' if default else '')
    v = (getpass.getpass if secret else input)(f'  {label}{shown}: ').strip()
    return v or default


def main():
    cur = read_env(ENV)
    others = other_systems()
    cf_token, cf_from = pick(others, 'CF_API_TOKEN')
    ms_tenant, ms_from = pick(others, 'MS_TENANT_ID')
    bk_other, _ = pick(others, 'BACKUP_DIR')
    bk_default = cur.get('BACKUP_DIR') or ''
    if not bk_default and bk_other:
        parent = os.path.dirname(bk_other.rstrip('\\/'))
        bk_default = os.path.join(parent, 'sas')
    print(t('\n  إعدادات نظام غياب الطلبة (اضغط Enter لقبول القيمة بين القوسين أو للتخطي)\n',
            '\n  SAS settings (press Enter to accept the value in brackets, or to skip)\n'))
    if cf_from:
        print(t(f'  رمز Cloudflare مأخوذ من: {cf_from}', f'  Cloudflare token taken from: {cf_from}'))
    if ms_from:
        print(t(f'  إعدادات البريد (Microsoft Graph) مأخوذة من: {ms_from}', f'  Mail settings (Microsoft Graph) taken from: {ms_from}'))
    vals = dict(cur)
    vals.setdefault('SECRET_KEY', secrets.token_urlsafe(48))
    vals['BASE_URL'] = ask(t('عنوان الموقع', 'Site address'), cur.get('BASE_URL', 'https://8.iajaward.org'))
    vals['PORT'] = cur.get('PORT', '8008')
    vals['TRUST_PROXY'] = '1'
    vals['TIMEZONE'] = cur.get('TIMEZONE', 'Asia/Amman')
    vals.setdefault('DISABLE_REMINDERS', '0')
    vals['BACKUP_DIR'] = ask(t('مجلد النسخ الاحتياطية (داخل Google Drive)', 'Backup folder (inside Google Drive)'), bk_default)
    vals['CF_API_TOKEN'] = cur.get('CF_API_TOKEN') or cf_token or ask(t('رمز Cloudflare API', 'Cloudflare API token'), '', True)
    # Microsoft Graph (the same app registration as the bookings system)
    for key, label_ar, label_en, secret in (('MS_TENANT_ID', 'Microsoft Tenant ID', 'Microsoft Tenant ID', False),
                                            ('MS_CLIENT_ID', 'Microsoft Client ID', 'Microsoft Client ID', False),
                                            ('MS_CLIENT_SECRET', 'Microsoft Client Secret', 'Microsoft Client Secret', True),
                                            ('MS_SENDER_EMAIL', 'بريد المُرسِل (مثل noreply@...)', 'Sender mailbox (e.g. noreply@...)', False)):
        known = cur.get(key) or pick(others, key)[0]
        vals[key] = known or ask(t(label_ar, label_en), '', secret)
    order = ['SECRET_KEY', 'BASE_URL', 'PORT', 'TRUST_PROXY', 'TIMEZONE', 'BACKUP_DIR', 'CF_API_TOKEN',
             'MS_TENANT_ID', 'MS_CLIENT_ID', 'MS_CLIENT_SECRET', 'MS_SENDER_EMAIL', 'DISABLE_REMINDERS']
    keys = order + [k for k in vals if k not in order]
    with open(ENV, 'w', encoding='utf-8') as fh:
        fh.write('# إعدادات نظام غياب الطلبة على هذا الجهاز — لا ترفع هذا الملف ولا تشاركه\n')
        for k in keys:
            if vals.get(k):
                fh.write(f'{k}={vals[k]}\n')
    print(t(f'\n  ✓ حُفظت الإعدادات في {ENV}', f'\n  ✓ Settings saved to {ENV}'))
    if not all(vals.get(k) for k in ('MS_TENANT_ID', 'MS_CLIENT_ID', 'MS_CLIENT_SECRET', 'MS_SENDER_EMAIL')):
        print(t('  ⚠ إعدادات البريد ناقصة — لن تُرسل الإشعارات حتى تُكمل في config.env (ثم أعد التشغيل)',
                '  ⚠ Mail settings incomplete - no emails until they are completed in config.env (then restart)'))


if __name__ == '__main__':
    main()
