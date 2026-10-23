import logging
import os

from flask import Flask, g, session, send_from_directory, make_response, render_template, abort
from werkzeug.middleware.proxy_fix import ProxyFix

from config import build_config, load_config_env, program_dir
from extensions import db, csrf, migrate
from tenancy import set_current_tenant, tenant_bypass, TenantContextError


def create_app(overrides=None):
    load_config_env()
    app = Flask(__name__)
    app.config.update(build_config(overrides))
    os.makedirs(app.config['UPLOAD_DIR'], exist_ok=True)
    logging.basicConfig(level=logging.INFO)

    if app.config['TRUST_PROXY']:
        # Behind Caddy on the same machine: trust exactly one proxy hop.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    try:
        from flask_compress import Compress
        Compress(app)
    except ImportError:
        pass

    db.init_app(app)
    if app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite'):
        from sqlalchemy import event

        with app.app_context():
            @event.listens_for(db.engine, 'connect')
            def _sqlite_pragmas(conn, _rec):
                cur = conn.cursor()
                cur.execute('PRAGMA foreign_keys=ON')        # ON DELETE CASCADE / SET NULL like PostgreSQL
                cur.execute('PRAGMA journal_mode=WAL')       # readers never block the writer
                cur.execute('PRAGMA busy_timeout=10000')
                cur.close()
    csrf.init_app(app)
    migrate.init_app(app, db, directory=str(program_dir() / 'migrations'))

    import models  # noqa: F401  (register tables)

    from blueprints.auth import auth_bp
    from blueprints.main import main_bp
    from blueprints.admin import admin_bp
    from blueprints.superadmin import super_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(super_bp, url_prefix='/super')
    from blueprints.employees import employees_bp
    app.register_blueprint(employees_bp, url_prefix='/employees')
    from blueprints.structure import structure_bp
    from blueprints.students import students_bp
    from blueprints.attendance import attendance_bp
    from blueprints.leaves import leaves_bp
    from blueprints.holidays import holidays_bp
    from blueprints.timetable import timetable_bp
    from blueprints.reports import reports_bp
    app.register_blueprint(structure_bp, url_prefix='/structure')
    app.register_blueprint(students_bp, url_prefix='/students')
    app.register_blueprint(attendance_bp, url_prefix='/attendance')
    app.register_blueprint(leaves_bp, url_prefix='/leaves')
    app.register_blueprint(holidays_bp, url_prefix='/holidays')
    app.register_blueprint(timetable_bp, url_prefix='/timetable')
    app.register_blueprint(reports_bp, url_prefix='/reports')
    import jobs  # noqa: F401  (housekeeping jobs only — guardian e-mail is manual)

    from cli import register_cli
    register_cli(app)

    _register_request_hooks(app)
    _register_pwa_routes(app)
    _register_error_handlers(app)

    if app.config['AUTO_MIGRATE'] and not app.config.get('TESTING'):
        with app.app_context():
            from flask_migrate import upgrade
            upgrade(directory=str(program_dir() / 'migrations'))

    if not app.config['DISABLE_REMINDERS'] and not app.config.get('TESTING'):
        from scheduler import start_scheduler
        start_scheduler(app)

    return app


def _register_request_hooks(app):
    from models import Tenant, User, SuperAdmin
    from authz import has_perm

    @app.before_request
    def load_identity():
        set_current_tenant(None)
        g.user = g.tenant = g.superadmin = None

        sid = session.get('super_id')
        if sid:
            with tenant_bypass():
                sa = db.session.get(SuperAdmin, sid)
            if sa and sa.is_active and sa.session_version == session.get('sv'):
                g.superadmin = sa
            else:
                session.clear()
            return

        uid, tid = session.get('uid'), session.get('tid')
        if uid and tid:
            tenant = db.session.get(Tenant, tid)
            if tenant and tenant.is_active:
                set_current_tenant(tenant.id)
                user = db.session.get(User, uid)
                if user and user.is_active and user.session_version == session.get('sv'):
                    g.tenant, g.user = tenant, user
                    return
            set_current_tenant(None)
            session.clear()

        from blueprints.auth import resolve_tenant_from_request
        g.tenant = resolve_tenant_from_request()

    @app.teardown_request
    def clear_tenant(exc=None):
        # Waitress reuses threads: never let one request's tenant leak
        # into the next one handled by the same thread.
        set_current_tenant(None)

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
        resp.headers.setdefault('X-Frame-Options', 'DENY')
        resp.headers.setdefault('Referrer-Policy', 'same-origin')
        resp.headers.setdefault(
            'Content-Security-Policy',
            "default-src 'self'; img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
            "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
            "script-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'self'")
        if g.get('user') or g.get('superadmin'):
            resp.headers.setdefault('Cache-Control', 'no-store')
        return resp

    @app.context_processor
    def inject():
        tenant = g.get('tenant')
        return {
            'current_user': g.get('user'),
            'current_tenant': tenant,
            'superadmin': g.get('superadmin'),
            'has_perm': has_perm,
            'accent': (tenant.accent_color if tenant else app.config['ACCENT_COLOR']),
        }


def _register_pwa_routes(app):
    @app.route('/healthz')
    def healthz():
        return 'ok', 200, {'Content-Type': 'text/plain', 'Cache-Control': 'no-store'}

    @app.route('/sw.js')
    def service_worker():
        resp = make_response(send_from_directory(app.static_folder, 'sw.js'))
        resp.headers['Content-Type'] = 'application/javascript'
        resp.headers['Service-Worker-Allowed'] = '/'
        resp.headers['Cache-Control'] = 'no-cache'
        return resp

    @app.route('/manifest.webmanifest')
    def manifest():
        tenant = g.get('tenant')
        name = tenant.name_ar if tenant else 'غياب الطلبة والاستئذان'
        data = {
            'name': f'غياب الطلبة والاستئذان — {name}', 'short_name': 'غياب الطلبة',
            'start_url': '/', 'scope': '/', 'display': 'standalone',
            'background_color': '#eef3f5', 'theme_color': '#1B3358',
            'lang': 'ar', 'dir': 'rtl',
            'icons': [
                {'src': '/static/icon-192.png', 'sizes': '192x192', 'type': 'image/png'},
                {'src': '/static/icon-512.png', 'sizes': '512x512', 'type': 'image/png'},
            ],
        }
        resp = app.json.response(data)
        resp.headers['Content-Type'] = 'application/manifest+json'
        return resp

    @app.route('/tenant-logo/<int:tenant_id>/<which>')
    def tenant_logo(tenant_id, which):
        from models import Tenant
        if which not in ('primary', 'secondary'):
            abort(404)
        with tenant_bypass():
            t = db.session.get(Tenant, tenant_id)
        fname = t and (t.logo_primary if which == 'primary' else t.logo_secondary)
        if not fname:
            return send_from_directory(app.static_folder, 'logo.png')
        folder = os.path.join(app.config['UPLOAD_DIR'], 'tenants', str(tenant_id))
        return send_from_directory(folder, fname)


def _register_error_handlers(app):
    @app.errorhandler(403)
    def forbidden(e):
        return render_template('errors/error.html', code=403,
                               message='لا تملك صلاحية لهذه الصفحة'), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template('errors/error.html', code=404, message='الصفحة غير موجودة'), 404

    @app.errorhandler(TenantContextError)
    def tenant_error(e):
        app.logger.error('Tenant isolation violation: %s', e)
        return render_template('errors/error.html', code=500, message='خطأ داخلي'), 500


if __name__ == '__main__':
    create_app().run(debug=False)
