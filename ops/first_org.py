"""بعد الإعداد: المدير العام للنظام + أول منظمة ومديرها (مرة واحدة؛ يتخطى ما هو موجود).

    python ops/first_org.py
المنظمات الأخرى تُنشأ لاحقاً من صفحة المدير العام: /super/login
"""
import getpass
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BASE, load_env, t  # noqa: E402


def ask(label, default=''):
    v = input(f'  {label}' + (f' [{default}]' if default else '') + ': ').strip()
    return v or default


def ask_password(username):
    from security import password_problems
    while True:
        pw = getpass.getpass(t('  كلمة المرور (10 أحرف على الأقل، فيها حرف ورقم): ', '  Password (10+ characters, a letter and a digit): '))
        problems = password_problems(pw, username)
        if pw != getpass.getpass(t('  أعد كتابتها: ', '  Type it again: ')):
            problems.append(t('غير متطابقتين', 'they do not match'))
        if not problems:
            return pw
        for p in problems:
            print('   - ' + p)


def main():
    load_env()
    sys.path.insert(0, BASE)
    os.environ.setdefault('DISABLE_REMINDERS', '1')
    from app import create_app
    from extensions import db
    from tenancy import tenant_bypass, tenant_context
    app = create_app({'DISABLE_REMINDERS': True})
    with app.app_context():
        from models import SuperAdmin, Tenant, User, Role, UserRole
        from security import hash_password
        from authz import seed_default_roles
        with tenant_bypass():
            if not SuperAdmin.query.first():
                print(t('\n  -- المدير العام للنظام (ينشئ المنظمات) --', '\n  -- Platform super admin (creates organizations) --'))
                u = ask(t('اسم المستخدم', 'User name'), 'root')
                db.session.add(SuperAdmin(username=u, password_hash=hash_password(ask_password(u))))
                db.session.commit()
                print(t('  ✓ أُنشئ المدير العام — صفحة الدخول: /super/login', '  ✓ Super admin created - sign in at /super/login'))
            if Tenant.query.first():
                print(t('  ✓ توجد منظمة مسبقاً — لا شيء آخر', '  ✓ An organization exists already - nothing else to do'))
                return
        print(t('\n  -- أول منظمة --', '\n  -- First organization --'))
        name = ask(t('اسم المنظمة', 'Organization name'), 'الجمعية الثقافية العربية / مدرسة الرائد العربي')
        slug = ask(t('الاسم المختصر (حروف إنجليزية)', 'Short name (latin letters)'), 'ras')
        host = (os.environ.get('BASE_URL') or '').split('//')[-1].split('/')[0]
        print(t('\n  -- مدير المنظمة --', '\n  -- Organization admin --'))
        while True:
            au = ask(t('اسم المستخدم', 'User name'), 'admin')
            if re.fullmatch(r'[A-Za-z0-9._@-]{3,80}', au):
                break
            print(t('   - حروف إنجليزية وأرقام فقط', '   - latin letters and digits only'))
        an = ask(t('الاسم الكامل', 'Full name'), 'مدير النظام')
        ae = ask(t('البريد', 'Email'))
        pw = ask_password(au)
        with tenant_bypass():
            ten = Tenant(slug=slug, name_ar=name, domain=host or None, is_active=True)
            db.session.add(ten)
            db.session.flush()
        with tenant_context(ten.id):
            seed_default_roles(ten.id)
            admin = User(username=au, full_name=an, email=ae or None, password_hash=hash_password(pw))
            db.session.add(admin)
            db.session.flush()
            db.session.add(UserRole(user_id=admin.id, role_id=Role.query.filter_by(key='admin').one().id))
            db.session.commit()
        print(t(f'  ✓ أُنشئت المنظمة ومديرها — الدخول: {os.environ.get("BASE_URL", "")}/login',
                f'  ✓ Organization and admin created - sign in: {os.environ.get("BASE_URL", "")}/login'))


if __name__ == '__main__':
    main()
