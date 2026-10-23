"""تشغيل نظام غياب الطلبة على كمبيوتر المدرسة أو الهاتف بخادم Waitress (Windows وTermux).

    python serve.py

• العنوان المحلي: http://localhost:8008  (PORT في config.env) — والعام عبر Cloudflare: https://8.iajaward.org
• البيانات: مجلد data (القاعدة sas.db والمرفقات uploads) — DATA_DIR في config.env لتغييره
• السجل: logs/server.log
"""
import logging
import logging.handlers
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'ops'))
from common import load_env  # noqa: E402

load_env()
os.makedirs(os.path.join(BASE, 'logs'), exist_ok=True)
handler = logging.handlers.RotatingFileHandler(os.path.join(BASE, 'logs', 'server.log'),
                                               maxBytes=5 * 1024 * 1024, backupCount=5, encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()])
log = logging.getLogger('sas')


def main():
    from app import create_app
    from waitress import serve
    if os.environ.get('DISABLE_SCHEDULER') == '1':
        os.environ['DISABLE_REMINDERS'] = '1'
    app = create_app()
    port = int(os.environ.get('PORT') or 8008)
    log.info('SAS running on http://localhost:%s  (site: %s, db: %s)', port,
             os.environ.get('BASE_URL') or '-', os.environ.get('SAS_DB_PATH'))
    # Cloudflare Tunnel connects from this same PC; ProxyFix (TRUST_PROXY=1) reads its https/host headers.
    serve(app, host=os.environ.get('HOST', '127.0.0.1'), port=port, threads=int(os.environ.get('THREADS') or 8),
          max_request_body_size=64 * 1024 ** 2, channel_timeout=300, ident='SAS')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        log.exception('Server failed to start')
        raise
