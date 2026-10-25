"""Configuration loading.

Settings come from environment variables, optionally filled from a
``config.env`` file that sits next to the program (next to the .exe when
packaged with PyInstaller, next to this file otherwise). Real environment
variables always win over the file.
"""
import os
import sys
from datetime import timedelta
from pathlib import Path


class ConfigError(RuntimeError):
    pass


def program_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def load_config_env():
    path = os.environ.get('CONFIG_ENV') or str(program_dir() / 'config.env')
    if os.path.isfile(path):
        from dotenv import load_dotenv
        load_dotenv(path, override=False, encoding='utf-8')
        return path
    return None


def _flag(name, default='0'):
    return os.environ.get(name, default).strip().lower() in ('1', 'true', 'yes', 'on')


def build_config(overrides=None):
    """Return a dict of Flask config values. Raises ConfigError when a
    production-critical value is missing, so the server never starts in a
    half-configured, insecure state."""
    overrides = overrides or {}
    testing = overrides.get('TESTING', False)
    dev = _flag('DEV_MODE') or testing
    base = program_dir()

    secret = os.environ.get('SECRET_KEY', '')
    if not secret:
        if not dev:
            raise ConfigError('SECRET_KEY غير مضبوط في config.env')
        secret = 'dev-only-not-secret'

    base_url = os.environ.get('BASE_URL', '').strip().rstrip('/')
    if not base_url and not dev:
        raise ConfigError('BASE_URL إلزامي (مثال: https://8.iajaward.org)')

    db_url = os.environ.get('DATABASE_URL', '').strip()
    if not db_url:
        if not dev:
            raise ConfigError('DATABASE_URL غير مضبوط')
        db_url = f"sqlite:///{base / 'sas_dev.db'}"
    if db_url.startswith('postgres://'):
        db_url = 'postgresql+psycopg://' + db_url[len('postgres://'):]
    elif db_url.startswith('postgresql://'):
        db_url = 'postgresql+psycopg://' + db_url[len('postgresql://'):]

    upload_dir = os.environ.get('UPLOAD_DIR', '').strip() or str(base / 'uploads')

    cfg = {
        'SECRET_KEY': secret,
        'DEV_MODE': dev,
        'BASE_URL': base_url,
        'SQLALCHEMY_DATABASE_URI': db_url,
        'SQLALCHEMY_TRACK_MODIFICATIONS': False,
        'SQLALCHEMY_ENGINE_OPTIONS': {'pool_pre_ping': True, 'pool_recycle': 280},
        'UPLOAD_DIR': upload_dir,
        'MAX_CONTENT_LENGTH': int(os.environ.get('MAX_UPLOAD_MB', '25')) * 1024 * 1024,
        'ACCENT_COLOR': os.environ.get('ACCENT_COLOR', '#0C67EC'),
        'TIMEZONE': os.environ.get('TIMEZONE', 'Asia/Amman'),
        # Microsoft Graph mail — same settings as the venues system
        'MS_TENANT_ID': os.environ.get('MS_TENANT_ID', ''),
        'MS_CLIENT_ID': os.environ.get('MS_CLIENT_ID', ''),
        'MS_CLIENT_SECRET': os.environ.get('MS_CLIENT_SECRET', ''),
        'MS_SENDER_EMAIL': os.environ.get('MS_SENDER_EMAIL', ''),
        'DISABLE_REMINDERS': _flag('DISABLE_REMINDERS'),
        'TRUST_PROXY': _flag('TRUST_PROXY', '1' if not dev else '0'),
        # Sessions
        'PERMANENT_SESSION_LIFETIME': timedelta(hours=int(os.environ.get('SESSION_HOURS', '12'))),
        'SESSION_COOKIE_HTTPONLY': True,
        'SESSION_COOKIE_SAMESITE': 'Lax',
        'SESSION_COOKIE_SECURE': base_url.startswith('https://'),
        'SESSION_COOKIE_NAME': 'sas_session',
        'WTF_CSRF_TIME_LIMIT': None,
        # Login protection
        'LOGIN_MAX_FAILURES': int(os.environ.get('LOGIN_MAX_FAILURES', '5')),
        'LOGIN_LOCK_MINUTES': int(os.environ.get('LOGIN_LOCK_MINUTES', '15')),
        'LOGIN_IP_MAX_FAILURES': int(os.environ.get('LOGIN_IP_MAX_FAILURES', '30')),
        'PASSWORD_MIN_LENGTH': int(os.environ.get('PASSWORD_MIN_LENGTH', '10')),
        'RESET_TOKEN_HOURS': int(os.environ.get('RESET_TOKEN_HOURS', '1')),
        'SEND_FILE_MAX_AGE_DEFAULT': 60 * 60 * 24 * 7,
        'AUTO_MIGRATE': _flag('AUTO_MIGRATE', '1'),
    }
    cfg.update(overrides)
    return cfg
