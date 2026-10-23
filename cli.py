"""Management commands. Passwords are always typed into a hidden prompt on
the server, never passed as command-line arguments or pasted elsewhere."""
import getpass

import click

from extensions import db
from tenancy import tenant_bypass


def _prompt_password(username):
    from security import password_problems
    while True:
        pw = getpass.getpass('كلمة المرور (لن تظهر أثناء الكتابة): ')
        pw2 = getpass.getpass('أعد كتابتها: ')
        problems = password_problems(pw, username)
        if pw != pw2:
            problems.append('غير متطابقتين')
        if not problems:
            return pw
        for p in problems:
            click.echo('  - ' + p)


def register_cli(app):
    @app.cli.command('create-superadmin')
    @click.argument('username')
    @click.option('--email', default=None)
    def create_superadmin(username, email):
        """Create a platform super admin (asks for the password)."""
        from models import SuperAdmin
        from security import hash_password
        with tenant_bypass():
            if SuperAdmin.query.filter_by(username=username).first():
                raise click.ClickException('موجود مسبقًا')
            pw = _prompt_password(username)
            db.session.add(SuperAdmin(username=username, email=email,
                                      password_hash=hash_password(pw)))
            db.session.commit()
        click.echo('تم إنشاء المدير العام')

    @app.cli.command('seed-roles')
    @click.argument('slug')
    def seed_roles(slug):
        """Add any missing default roles to an organization."""
        from models import Tenant
        from tenancy import tenant_context
        from authz import seed_default_roles
        t = Tenant.query.filter_by(slug=slug).first()
        if not t:
            raise click.ClickException('منظمة غير موجودة')
        with tenant_context(t.id):
            seed_default_roles(t.id)
            db.session.commit()
        click.echo('تم')
