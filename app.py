# Updated `app.py`
import os
import sys

from flask import Flask, request
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from extensions import csrf, db, limiter, login_manager
from models import Admin
from routes.admin import admin_bp
from routes.auth import auth_bp
from routes.public import public_bp


def sync_database_schema(app):
    """Create tables and add non-destructive columns needed by the current build."""
    with app.app_context():
        try:
            db.create_all()
            with db.engine.connect() as conn:
                dialect = db.engine.dialect.name
                if dialect == 'postgresql':
                    conn.execute(db.text("""
                        DO $$
                        BEGIN
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='student' AND column_name='gender') THEN
                                ALTER TABLE student ADD COLUMN gender VARCHAR(30);
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='student' AND column_name='academic_session') THEN
                                ALTER TABLE student ADD COLUMN academic_session VARCHAR(50);
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='student' AND column_name='admission_date') THEN
                                ALTER TABLE student ADD COLUMN admission_date DATE;
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='student' AND column_name='completion_status') THEN
                                ALTER TABLE student ADD COLUMN completion_status VARCHAR(50) DEFAULT 'ongoing';
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='student' AND column_name='passing_grade') THEN
                                ALTER TABLE student ADD COLUMN passing_grade VARCHAR(50);
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='student' AND column_name='passing_year') THEN
                                ALTER TABLE student ADD COLUMN passing_year VARCHAR(10);
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='course' AND column_name='category') THEN
                                ALTER TABLE course ADD COLUMN category VARCHAR(30) DEFAULT 'management';
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='course' AND column_name='fees') THEN
                                ALTER TABLE course ADD COLUMN fees NUMERIC(10,2);
                            END IF;
                            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='course' AND column_name='engineering_section') THEN
                                ALTER TABLE course ADD COLUMN engineering_section VARCHAR(40);
                            END IF;
                            IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='student' AND column_name='subject_code') THEN
                                ALTER TABLE student DROP COLUMN subject_code;
                            END IF;
                            IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='course' AND column_name='subject_code') THEN
                                ALTER TABLE course DROP COLUMN subject_code;
                            END IF;
                            IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='certificate' AND column_name='subject_code') THEN
                                ALTER TABLE certificate DROP COLUMN subject_code;
                            END IF;
                        END $$;
                    """))
                    conn.execute(db.text("UPDATE course SET category='management' WHERE category IS NULL OR category=''"))
                    conn.commit()
                elif dialect == 'sqlite':
                    student_cols = {row[1] for row in conn.execute(db.text("PRAGMA table_info(student)")).fetchall()}
                    additions = {
                        'gender': 'ALTER TABLE student ADD COLUMN gender VARCHAR(30)',
                        'academic_session': 'ALTER TABLE student ADD COLUMN academic_session VARCHAR(50)',
                        'admission_date': 'ALTER TABLE student ADD COLUMN admission_date DATE',
                        'completion_status': "ALTER TABLE student ADD COLUMN completion_status VARCHAR(50) DEFAULT 'ongoing'",
                        'passing_grade': 'ALTER TABLE student ADD COLUMN passing_grade VARCHAR(50)',
                        'passing_year': 'ALTER TABLE student ADD COLUMN passing_year VARCHAR(10)',
                    }
                    for col, sql in additions.items():
                        if col not in student_cols:
                            conn.execute(db.text(sql))
                    course_cols = {row[1] for row in conn.execute(db.text("PRAGMA table_info(course)")).fetchall()}
                    if 'category' not in course_cols:
                        conn.execute(db.text("ALTER TABLE course ADD COLUMN category VARCHAR(30) DEFAULT 'management'"))
                    if 'fees' not in course_cols:
                        conn.execute(db.text("ALTER TABLE course ADD COLUMN fees NUMERIC(10,2)"))
                    if 'engineering_section' not in course_cols:
                        conn.execute(db.text("ALTER TABLE course ADD COLUMN engineering_section VARCHAR(40)"))
                    for table in ('student', 'course', 'certificate'):
                        cols = {row[1] for row in conn.execute(db.text(f"PRAGMA table_info({table})")).fetchall()}
                        if 'subject_code' in cols:
                            conn.execute(db.text(f"ALTER TABLE {table} DROP COLUMN subject_code"))
                    conn.execute(db.text("UPDATE course SET category='management' WHERE category IS NULL OR category=''"))
                    conn.commit()
        except Exception as e:
            db_url = app.config.get('SQLALCHEMY_DATABASE_URI', '')
            if 'postgresql' in db_url:
                print(f"\n[!] Notice during schema sync: {e}")
                print("    Make sure your PostgreSQL database is available.\n")

def create_app(config_class=Config):
    app = Flask(__name__)

    # ---------------------------------------------------------
    # Load configuration
    # ---------------------------------------------------------
    if config_class:
        app.config.from_object(config_class)

    # ---------------------------------------------------------
    # SECRET KEY CHECK
    # ---------------------------------------------------------
    if (
        not app.config.get('SECRET_KEY')
        or app.config['SECRET_KEY'] == 'CHANGE_THIS_IN_ENV'
    ):
        if not app.config.get('TESTING'):
            raise RuntimeError(
                'Set a strong SECRET_KEY in your environment '
                'before running SIMTS.'
            )

    # ---------------------------------------------------------
    # VERCEL FILESYSTEM HANDLING
    # ---------------------------------------------------------
    #
    # Vercel's deployed filesystem is read-only.
    #
    # Locally:
    #   instance/
    #   uploads/
    #   backups/
    #
    # can be created normally.
    #
    # On Vercel:
    #   Do NOT create these directories inside /var/task.
    #
    # ---------------------------------------------------------

    is_vercel = bool(os.getenv('VERCEL'))

    if not is_vercel:

        # Local development only
        os.makedirs(
            app.instance_path,
            exist_ok=True
        )

        if app.config.get('UPLOAD_FOLDER'):
            os.makedirs(
                app.config['UPLOAD_FOLDER'],
                exist_ok=True
            )

        if app.config.get('BACKUP_FOLDER'):
            os.makedirs(
                app.config['BACKUP_FOLDER'],
                exist_ok=True
            )

    # ---------------------------------------------------------
    # VERCEL TEMPORARY STORAGE
    # ---------------------------------------------------------
    #
    # If your existing routes need a writable directory during
    # a Vercel function execution, use /tmp instead of /var/task.
    #
    # IMPORTANT:
    # /tmp is temporary and NOT persistent.
    #
    # Permanent certificate/document storage should eventually
    # use cloud object storage.
    #
    # ---------------------------------------------------------

    if is_vercel:

        # IMPORTANT:
        # Use "or" so an empty Vercel environment variable
        # does NOT produce an empty path.
        app.config['UPLOAD_FOLDER'] = (
            os.getenv('UPLOAD_FOLDER')
            or '/tmp/simts_uploads'
        )

        app.config['BACKUP_FOLDER'] = (
            os.getenv('BACKUP_FOLDER')
            or '/tmp/simts_backups'
        )

        # /tmp is writable on Vercel
        os.makedirs(
            app.config['UPLOAD_FOLDER'],
            exist_ok=True
        )

        os.makedirs(
            app.config['BACKUP_FOLDER'],
            exist_ok=True
        )

    # ---------------------------------------------------------
    # TRUST PROXY
    # ---------------------------------------------------------
    if app.config.get('TRUST_PROXY'):
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=1,
            x_proto=1,
            x_host=1
        )

    # ---------------------------------------------------------
    # INITIALIZE EXTENSIONS
    # ---------------------------------------------------------
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    # ---------------------------------------------------------
    # REGISTER BLUEPRINTS
    # ---------------------------------------------------------
    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)

    # ---------------------------------------------------------
    # FLASK-LOGIN USER LOADER
    # ---------------------------------------------------------
    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(
                Admin,
                int(user_id)
            )
        except (ValueError, TypeError):
            return None

    # ---------------------------------------------------------
    # SECURITY HEADERS
    # ---------------------------------------------------------
    @app.after_request
    def security_headers(response):

        # Cache static CSS/JS/images so repeat navigation does not refetch them.
        if request.path.startswith('/static/'):
            response.headers['Cache-Control'] = (
                'public, max-age=86400, stale-while-revalidate=604800'
            )

        response.headers['X-Content-Type-Options'] = 'nosniff'

        response.headers['X-Frame-Options'] = 'SAMEORIGIN'

        response.headers[
            'Referrer-Policy'
        ] = 'strict-origin-when-cross-origin'

        response.headers[
            'Permissions-Policy'
        ] = (
            'camera=(), '
            'microphone=(), '
            'geolocation=()'
        )

        response.headers[
            'Content-Security-Policy'
        ] = (
            "default-src 'self'; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self' data:; "
            "script-src 'self'; "
            "frame-ancestors 'self'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "object-src 'none'"
        )

        # HSTS only when HTTPS is being used
        if request.is_secure:
            response.headers[
                'Strict-Transport-Security'
            ] = (
                'max-age=31536000; '
                'includeSubDomains'
            )

        return response

    # ---------------------------------------------------------
    # DATABASE INITIALIZATION / SCHEMA SYNC
    # ---------------------------------------------------------
    if not app.config.get('TESTING'):
        sync_database_schema(app)

    return app


# -------------------------------------------------------------
# CREATE FLASK APPLICATION
# -------------------------------------------------------------
app = create_app()


# -------------------------------------------------------------
# LOCAL DEVELOPMENT SERVER
# -------------------------------------------------------------
if __name__ == '__main__':

    port = int(
        os.getenv(
            'PORT',
            '5000'
        )
    )

    debug = app.config.get(
        'DEBUG',
        False
    )

    print("\n" + "=" * 60)
    print("  SIMTS Academic & Verification Portal")
    print("=" * 60)

    print(
        f"  * Public Portal : "
        f"http://127.0.0.1:{port}"
    )

    print(
        "  * Admin Setup   : "
        f"http://127.0.0.1:{port}/admin/setup"
    )

    print(
        "  * Admin Login   : "
        f"http://127.0.0.1:{port}/admin/login"
    )

    database_url = app.config.get(
        'SQLALCHEMY_DATABASE_URI',
        ''
    )

    # Don't print the password
    if '@' in database_url:
        database_display = database_url.split('@')[-1]
    else:
        database_display = database_url

    print(
        f"  * Database      : "
        f"{database_display}"
    )

    print("=" * 60)

    print(
        "  Server is active. "
        f"Open http://127.0.0.1:{port} "
        "in your browser."
    )

    print(
        "  (Press CTRL+C in this terminal "
        "to stop the server)\n"
    )

    sys.stdout.flush()

    app.run(
        host='127.0.0.1',
        port=port,
        debug=debug
    )

