"""Sign-in accounts on this computer: list them and set a new password for one (when a password is forgotten).

    python ops/reset_password.py          (reset_password.bat)

Shows the platform super admins (/super/login) and the administrators of every organization (/login), then
asks which account to reset and the new password (hidden). The account is also unlocked and re-enabled, and
its open sessions are signed out. No password is ever shown or stored in plain text.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BASE, load_env, t  # noqa: E402
from first_org import ask_password  # noqa: E402


def main():
    load_env()
    sys.path.insert(0, BASE)
    os.environ.setdefault('DISABLE_REMINDERS', '1')
    from app import create_app
    from extensions import db
    from tenancy import tenant_bypass
    app = create_app({'DISABLE_REMINDERS': True})
    with app.app_context():
        from models import SuperAdmin, Tenant, User, Role, UserRole
        from security import hash_password
        rows = []
        with tenant_bypass():
            for s in SuperAdmin.query.order_by(SuperAdmin.username):
                rows.append(('super', s, t('المدير العام', 'Super admin') + '  (/super/login)'))
            admin_role_ids = {r.id: r.tenant_id for r in Role.query.filter_by(key='admin')}
            tenants = {x.id: x for x in Tenant.query}
            admin_ids = {ur.user_id for ur in UserRole.query.filter(UserRole.role_id.in_(list(admin_role_ids)))}
            for u in User.query.filter(User.id.in_(list(admin_ids))).order_by(User.tenant_id, User.username):
                ten = tenants.get(u.tenant_id)
                rows.append(('user', u, t('مدير المنظمة', 'Organization admin') + f' «{ten.slug if ten else u.tenant_id}»  (/login)'))
        if not rows:
            print(t('  لا توجد حسابات بعد — شغّل 1_setup.bat', '  No accounts yet - run 1_setup.bat'))
            return
        print(t('\n  حسابات الدخول:', '\n  Sign-in accounts:'))
        for i, (_, acc, what) in enumerate(rows, 1):
            state = '' if acc.is_active else t('  [معطّل]', '  [disabled]')
            print(f'   {i}  {acc.username:<20} {what}{state}')
        pick = input(t('\n  رقم الحساب لتعيين كلمة مرور جديدة (Enter للخروج): ',
                       '\n  Account number to set a new password (Enter to exit): ')).strip()
        if not pick.isdigit() or not 1 <= int(pick) <= len(rows):
            return
        kind, acc, _ = rows[int(pick) - 1]
        name = acc.username
        pw = ask_password(name)
        with tenant_bypass():
            acc.password_hash = hash_password(pw)
            acc.is_active = True
            acc.failed_logins = 0
            acc.locked_until = None
            acc.session_version = (acc.session_version or 1) + 1
            if kind == 'user':
                acc.must_change_password = False
            db.session.commit()
        print(t(f'  ✓ تم تعيين كلمة المرور الجديدة للحساب {name} (وفُكّ القفل)',
                f'  ✓ New password set for {name} (and unlocked)'))


if __name__ == '__main__':
    main()
