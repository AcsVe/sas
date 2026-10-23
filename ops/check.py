"""sas_check.bat — one click: checks the whole sas system on this PC, fixes what it can, and says in plain words
what (if anything) is still wrong.

    1. the website on this PC (http://localhost:8008)  → starts it if it is not running
    2. the public address (https://8.iajaward.org)      → is Cloudflare reaching this PC?
If the website cannot start, it runs it once in front of you and prints the error to send.
"""
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BASE, load_env  # noqa: E402

IS_WIN = os.name == 'nt'


def get(url, timeout=5):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'sas-check'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(200).decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, ''
    except Exception as e:   # noqa: BLE001
        return None, str(e)


def local_ok(port):
    st, body = get(f'http://127.0.0.1:{port}/healthz', 3)
    return st == 200 and 'ok' in body


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, errors='replace')


def line(ok, text):
    print(('  [OK] ' if ok else '  [X]  ') + text)


def tail(path, n=20):
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return f.readlines()[-n:]
    except OSError:
        return []


def main():
    load_env()
    port = os.environ.get('PORT') or '8008'
    site = (os.environ.get('BASE_URL') or 'https://8.iajaward.org').rstrip('/')
    print('\n  SAS - system check\n')
    problems = 0

    # 1) website on this PC
    if local_ok(port):
        line(True, f'Website is running on this PC (port {port})')
    else:
        print(f'  ...  Website is not answering on port {port} - starting it')
        if IS_WIN:
            q = run(['schtasks', '/query', '/tn', 'SAS Website'])
            if q.returncode != 0:
                print('  [!]  The start-up task is missing - creating it needs 3_autostart.bat (as administrator)')
            run(['schtasks', '/change', '/tn', 'SAS Website', '/enable'])
            run(['schtasks', '/end', '/tn', 'SAS Website'])
            time.sleep(2)
            run(['schtasks', '/run', '/tn', 'SAS Website'])
        for _ in range(25):
            time.sleep(1)
            if local_ok(port):
                break
        if local_ok(port):
            line(True, 'Website started')
        else:
            problems += 1
            line(False, 'The website does not start. Running it once here to see why (15 seconds)...')
            p = subprocess.Popen([sys.executable, os.path.join(BASE, 'serve.py')], cwd=BASE, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, errors='replace')
            try:
                out, _ = p.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
                out, _ = p.communicate()
            print('\n  ---- copy from here and send it ----')
            for ln in (out or '').splitlines()[-25:]:
                print('  ' + ln)
            print('  ---- logs\\server.log ----')
            for ln in tail(os.path.join(BASE, 'logs', 'server.log')):
                print('  ' + ln.rstrip())
            print('  ---- to here ----\n')
            if local_ok(port):
                problems -= 1
                line(True, 'It answered when started here - the start-up task is the problem: run 3_autostart.bat as administrator')

    # 2) public address
    st, body = get(site + '/healthz', 10)
    if st == 200:
        line(True, f'{site} reaches this PC')
    else:
        problems += 1
        if st == 502:
            line(False, f'{site} gives 502: Cloudflare reaches the tunnel but not the website (see 1)')
        elif st in (530, 1033) or (st is None and 'resolve' in body.lower()):
            line(False, f'{site}: the Cloudflare tunnel on this PC is not connected (cloudflared service) - run site_where.bat')
        else:
            line(False, f'{site}: {st or body}')

    print('\n  ' + ('Everything is working.' if problems <= 0 else
                     'Something is still wrong - send the lines above (from "copy from here").') + '\n')


if __name__ == '__main__':
    main()
