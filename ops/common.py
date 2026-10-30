"""أدوات مشتركة للخادم المحلي (الكمبيوتر) والهاتف: قراءة config.env ومسارات البيانات والنسخ — نظام غياب الطلبة."""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Windows command windows cannot show Arabic (boxes), so the PC uses English; the phone (Termux) stays Arabic.
# Force either with IAJ_LANG=ar / IAJ_LANG=en.
_LANG = os.environ.get('IAJ_LANG') or os.environ.get('LANG_UI') or ('en' if os.name == 'nt' else 'ar')
EN = _LANG == 'en'
_ASCII = {'✓': '[OK]', '✅': '[OK]', '✖': '[X]', '❌': '[X]', '⚠': '[!]', '←': '<-', '→': '->', '★': '*', '•': '-',
          '🟢': '[OK]', '🔴': '[X]', '✋': '[!]', '…': '...', '—': '-', '–': '-', '«': '"', '»': '"'}


def _plain(text):
    for k, v in _ASCII.items():
        text = text.replace(k, v)
    return text


def t(ar, en):
    if not EN:
        return ar
    return _plain(en) if os.name == 'nt' else en


def load_env():
    """يقرأ config.env (لا يستبدل متغيراً موجوداً مسبقاً). الافتراضي: قاعدة SQLite في مجلد data داخل المشروع
    (خارج الكود — لا يلمسها update.bat)."""
    path = os.path.join(BASE, 'config.env')
    if os.path.isfile(path):
        with open(path, encoding='utf-8-sig', errors='ignore') as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    os.environ.setdefault('DATA_DIR', os.path.join(BASE, 'data'))
    os.makedirs(os.environ['DATA_DIR'], exist_ok=True)
    os.environ.setdefault('SAS_DB_PATH', os.path.join(os.environ['DATA_DIR'], 'sas.db'))
    # التطبيق يقرأ DATABASE_URL — على الكمبيوتر والهاتف نوجّهه دائماً للقاعدة المحلية SQLite
    if not os.environ.get('DATABASE_URL', '').startswith('sqlite'):
        os.environ['DATABASE_URL'] = 'sqlite:///' + os.environ['SAS_DB_PATH'].replace('\\', '/')
    os.environ.setdefault('UPLOAD_DIR', os.path.join(os.environ['DATA_DIR'], 'uploads'))


def data_dir():
    return os.environ.get('DATA_DIR') or os.path.join(BASE, 'data')


def db_path():
    return os.environ.get('SAS_DB_PATH') or os.path.join(data_dir(), 'sas.db')


def uploads_dir():
    """المرفقات (الشعارات، مرفقات السجلات) — تُنسخ مع النسخة الاحتياطية"""
    return os.environ.get('UPLOAD_DIR') or os.path.join(data_dir(), 'uploads')


DRIVE_FOLDER = 'sas'      # this system's folder inside IAJ-backups (the standby phone reads it there)


def _drive_backups():
    """IAJ-backups in Google Drive for desktop on this computer: the award site's BACKUP_DIR (C:\\IAJ\\.env),
    else the usual Drive folders. None when Drive is not on this computer."""
    if os.name != 'nt':
        return None
    try:
        with open(r'C:\IAJ\.env', encoding='utf-8-sig', errors='ignore') as fh:
            for line in fh:
                if line.strip().startswith('BACKUP_DIR='):
                    d = line.split('=', 1)[1].strip().strip('"')
                    if d and os.path.isdir(d):
                        return d
    except OSError:
        pass
    for letter in 'GHIJKLMNOPDEF':
        for sub in ('My Drive', 'Mon Drive', 'محرّك الأقراص', 'محرك الأقراص الخاص بي'):
            d = f'{letter}:\\{sub}\\IAJ-backups'
            if os.path.isdir(d):
                return d
    return None


def backup_root():
    """BACKUP_DIR when it points to a folder that exists; otherwise this system's folder in the Google Drive
    backup folder (IAJ-backups/<system>) so the standby phone always finds the backups; otherwise ./backups."""
    d = os.environ.get('BACKUP_DIR', '').strip().strip('"')
    drive = _drive_backups()
    usable = d and (os.path.isdir(d) or os.path.isdir(os.path.dirname(d.rstrip('\\/'))))
    if usable and (drive is None or 'IAJ-backups' in d):
        return d
    if drive:          # BACKUP_DIR empty, missing, or a local folder the phone cannot reach
        return os.path.join(drive, DRIVE_FOLDER)
    return os.path.join(BASE, 'backups')


def stamp():
    """وقت عمّان (للترتيب ولأسماء الملفات)"""
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo('Asia/Amman')).strftime('%Y-%m-%d_%H%M%S')
    except Exception:
        return datetime.now().strftime('%Y-%m-%d_%H%M%S')


def die(msg, code=1):
    print('\n✖ ' + msg)
    sys.exit(code)
