import re
from werkzeug.security import generate_password_hash, check_password_hash
from flask import current_app
from utils.i18n import _

# Used to spend the same hashing time when the username does not exist,
# so response timing does not reveal which usernames are valid.
_DUMMY_HASH = generate_password_hash('timing-equalizer-not-a-password')


def hash_password(pw):
    return generate_password_hash(pw)  # scrypt by default in Werkzeug 3


def verify_password(pw_hash, pw):
    return check_password_hash(pw_hash or _DUMMY_HASH, pw or '')


def password_problems(pw, username=None):
    """Return a list of Arabic messages; empty means acceptable."""
    problems = []
    min_len = current_app.config.get('PASSWORD_MIN_LENGTH', 10)
    if len(pw or '') < min_len:
        problems.append(_('كلمة المرور يجب ألا تقل عن {min_len} أحرف', min_len=min_len))
    if not re.search(_(r'[A-Za-z؀-ۿ]'), pw or ''):
        problems.append(_('يجب أن تحتوي كلمة المرور على حرف واحد على الأقل'))
    if not re.search(r'\d', pw or ''):
        problems.append(_('يجب أن تحتوي كلمة المرور على رقم واحد على الأقل'))
    if username and pw and username.lower() in pw.lower():
        problems.append(_('لا يجوز أن تحتوي كلمة المرور على اسم المستخدم'))
    return problems
