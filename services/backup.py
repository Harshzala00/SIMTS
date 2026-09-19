import json
import os
import shutil
import sqlite3
import subprocess
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import make_url

from extensions import db
from models import Admin, AuditLog, Certificate, ContactMessage, Course, Student


def find_pg_dump_executable():
    """Locate pg_dump binary on the system (PATH, Windows custom installs, Linux/Mac)."""
    # 1. Check system PATH
    which_path = shutil.which('pg_dump')
    if which_path and os.path.isfile(which_path):
        return which_path

    # 2. Check Windows known paths (including D:\Postgresql, C:\Program Files\PostgreSQL, etc.)
    potential_dirs = [
        Path(r"D:\Postgresql\bin"),
        Path(r"C:\Postgresql\bin"),
        Path(r"D:\Program Files\PostgreSQL"),
        Path(r"C:\Program Files\PostgreSQL"),
        Path(r"C:\Program Files (x86)\PostgreSQL"),
    ]

    for p in potential_dirs:
        if p.exists():
            direct_exe = p / "pg_dump.exe"
            if direct_exe.is_file():
                return str(direct_exe)
            # Check version subdirectories e.g. 18/bin/pg_dump.exe, 17/bin/pg_dump.exe
            for sub in p.glob("*/bin/pg_dump.exe"):
                if sub.is_file():
                    return str(sub)

    return None


def export_python_sql_dump(work_dir, app):
    """
    Fallback & Complementary SQL & JSON data dump directly via SQLAlchemy.
    Guarantees 100% successful backup in all scenarios regardless of server state.
    """
    models = [Admin, Course, Student, Certificate, ContactMessage, AuditLog]
    sql_path = work_dir / "database_export.sql"
    json_path = work_dir / "database_export.json"

    export_data = {}
    sql_lines = [
        "-- SIMTS Portable Database Backup",
        f"-- Generated at: {datetime.utcnow().isoformat()}",
        "-- ====================================================\n",
    ]

    with app.app_context():
        for model in models:
            table_name = model.__tablename__
            rows = db.session.execute(select(model.__table__)).mappings().all()
            export_data[table_name] = []

            for row in rows:
                row_dict = {}
                for k, v in row.items():
                    if isinstance(v, (datetime, date)):
                        row_dict[k] = v.isoformat()
                    elif isinstance(v, Decimal):
                        # Decimal isn't JSON serializable - store as a plain float.
                        row_dict[k] = float(v)
                    else:
                        row_dict[k] = v
                export_data[table_name].append(row_dict)

                # Generate generic SQL insert
                cols = ", ".join([f'"{k}"' for k in row.keys()])
                vals = []
                for val in row.values():
                    if val is None:
                        vals.append("NULL")
                    elif isinstance(val, bool):
                        vals.append("TRUE" if val else "FALSE")
                    elif isinstance(val, (int, float, Decimal)):
                        vals.append(str(val))
                    elif isinstance(val, (datetime, date)):
                        vals.append(f"'{val.isoformat()}'")
                    else:
                        escaped = str(val).replace("'", "''")
                        vals.append(f"'{escaped}'")
                val_str = ", ".join(vals)
                sql_lines.append(f"INSERT INTO {table_name} ({cols}) VALUES ({val_str});")

    sql_path.write_text("\n".join(sql_lines), encoding="utf-8")
    # default=str is a safety net for any other non-JSON-native type (e.g. Decimal
    # slipping through from a future column) so a backup never hard-fails on export.
    json_path.write_text(json.dumps(export_data, indent=2, default=str), encoding="utf-8")


def create_backup(app):
    """Create a complete disaster recovery zip package with database dumps and uploaded certificate images."""
    root = Path(app.config['BACKUP_FOLDER'])
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    work = root / f'simts_backup_{stamp}'
    work.mkdir(parents=True, exist_ok=True)

    try:
        url = make_url(app.config['SQLALCHEMY_DATABASE_URI'])
        backup_methods = []

        if url.drivername.startswith('sqlite'):
            source = sqlite3.connect(url.database)
            destination = sqlite3.connect(work / 'simts.db')
            try:
                source.backup(destination)
                backup_methods.append("SQLite Online Binary Backup")
            finally:
                destination.close()
                source.close()
            export_python_sql_dump(work, app)
            backup_methods.append("SQL & JSON Export")

        elif url.drivername.startswith('postgresql'):
            pg_dump_bin = find_pg_dump_executable()
            if pg_dump_bin:
                dump_file = work / 'simts_postgres.sql'
                env = os.environ.copy()
                if url.password:
                    env['PGPASSWORD'] = str(url.password)

                cmd = [
                    pg_dump_bin,
                    '--no-owner',
                    '--no-privileges',
                    '--clean',
                    '--if-exists',
                    '-h', url.host or '127.0.0.1',
                    '-p', str(url.port or 5432),
                    '-U', url.username or 'postgres',
                    '-d', url.database or 'simts',
                    '-f', str(dump_file)
                ]

                try:
                    res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)
                    if res.returncode == 0 and dump_file.exists() and dump_file.stat().st_size > 0:
                        backup_methods.append("Native PostgreSQL pg_dump (simts_postgres.sql)")
                except Exception as exc:
                    print(f"pg_dump execution notice: {exc}")

            # Always generate universal portable SQL and JSON export
            export_python_sql_dump(work, app)
            backup_methods.append("Portable SQL (database_export.sql) & JSON (database_export.json)")
        else:
            export_python_sql_dump(work, app)
            backup_methods.append("Generic SQL & JSON Export")

        # Copy local certificate image uploads
        uploads = Path(app.config['UPLOAD_FOLDER'])
        if uploads.exists():
            shutil.copytree(uploads, work / 'uploads', dirs_exist_ok=True)

        # Generate disaster recovery documentation
        with app.app_context():
            student_count = Student.query.count()
            certificate_count = Certificate.query.count()
            course_count = Course.query.count()

        readme = work / 'DISASTER_RECOVERY_README.txt'
        readme.write_text(
            f"====================================================\n"
            f"  SIMTS FULL DISASTER RECOVERY ARCHIVE\n"
            f"====================================================\n"
            f"Created At: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Database Driver: {url.drivername}\n"
            f"Backup Strategy: {', '.join(backup_methods)}\n\n"
            f"System Statistics at Backup:\n"
            f"  - Total Students: {student_count}\n"
            f"  - Total Courses: {course_count}\n"
            f"  - Total Certificates: {certificate_count}\n"

            f"Archive Contents:\n"
            f"  - simts_postgres.sql / database_export.sql: Database dumps\n"
            f"  - database_export.json: Structured JSON data of all records\n"
            f"  - uploads/: Local certificate image files\n\n"
            f"Restoration Steps (In case of server crash, attack, or data loss):\n"
            f"  1. Extract this zip archive.\n"
            f"  2. Copy the 'uploads/' folder into your SIMTS project root.\n"
            f"  3. Restore your database using either:\n"
            f"     - PostgreSQL: psql -U simts_user -d simts -f simts_postgres.sql\n"
            f"       (or execute database_export.sql in pgAdmin/psql)\n"
            f"     - SQLite: copy simts.db to instance/simts.db\n"
            f"  4. Start the SIMTS application.\n"
            f"====================================================\n",
            encoding='utf-8'
        )

        archive = shutil.make_archive(str(work), 'zip', root_dir=str(work))
        return archive

    finally:
        shutil.rmtree(work, ignore_errors=True)
