import os
from datetime import datetime
from pathlib import Path
import re

from flask import Blueprint, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from openpyxl import load_workbook
from werkzeug.utils import secure_filename
from sqlalchemy import func

from extensions import db
from models import Admin, AuditLog, Certificate, ContactMessage, Course, Student
from security import admin_required, audit
from services.backup import create_backup
from services.blob_storage import blob_enabled, delete_file, upload_image, BlobStorageError

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

def protect(view):
    return login_required(admin_required(view))

def clean(value, max_length):
    return (str(value).strip() if value is not None else '')[:max_length]

def parse_course_fee(value):
    raw = clean(value, 30).replace(',', '')
    if not raw:
        return None
    try:
        amount = float(raw)
        if amount < 0 or amount > 99999999:
            raise ValueError
        return round(amount, 2)
    except (TypeError, ValueError):
        return None

def image_signature(file_storage):
    try:
        pos = file_storage.stream.tell()
        file_storage.stream.seek(0)
        header = file_storage.stream.read(12)
        file_storage.stream.seek(pos)
        return (
            header.startswith(b'\x89PNG\r\n\x1a\n')
            or header.startswith(b'\xff\xd8\xff')
        )
    except Exception:
        return False

def image_extension(filename):
    ext = Path(filename or '').suffix.lower()
    return '.jpg' if ext in {'.jpg', '.jpeg'} else '.png' if ext == '.png' else ''

def parse_passing_year(value):
    raw = clean(value, 10)
    if not raw:
        return ''
    if not raw.isdigit() or not 1900 <= int(raw) <= 2200:
        return None
    return raw

# ============================================================
# DASHBOARD
# ============================================================

@admin_bp.route('/')
@protect
def dashboard():
    return render_template(
        'admin/dashboard.html',
        student_count=Student.query.count(),
        course_count=Course.query.count(),
        certificate_count=Certificate.query.count(),
        message_count=ContactMessage.query.filter_by(status='unread').count(),
        recent_students=Student.query.order_by(Student.id.desc()).limit(5).all(),
        recent_certificates=Certificate.query.order_by(Certificate.id.desc()).limit(5).all(),
    )

# ============================================================
# STUDENTS MANAGEMENT
# ============================================================

@admin_bp.route('/students')
@protect
def students():
    q = clean(request.args.get('q'), 200)
    page = max(request.args.get('page', 1, type=int) or 1, 1)
    query = Student.query
    if q:
        query = query.filter(
            (Student.student_id.ilike(f'%{q}%')) |
            (Student.full_name.ilike(f'%{q}%')) |
            (Student.email.ilike(f'%{q}%'))
        )
    pagination = query.order_by(Student.id.desc()).paginate(page=page, per_page=50, error_out=False)
    return render_template('admin/students.html', students=pagination.items, pagination=pagination, q=q)

def apply_student_form(student):
    sid = clean(request.form.get('student_id'), 80)
    name = clean(request.form.get('full_name'), 200)
    email = clean(request.form.get('email'), 200)
    if not sid or not name or not email:
        return None, 'Student code, name and email are required.'

    student.student_id = sid
    student.full_name = name
    student.email = email
    # Keep the student form focused on the fields required for academic verification.
    student.passing_grade = clean(request.form.get('passing_grade'), 50)
    raw_year = (request.form.get('passing_year') or '').strip()
    if raw_year:
        year = parse_passing_year(raw_year)
        if year is None:
            return None, 'Passing year must be a valid 4-digit year.'
        student.passing_year = year
    else:
        student.passing_year = None

    course_id = request.form.get('course_id')
    if course_id:
        try:
            course = db.session.get(Course, int(course_id))
            if not course:
                raise ValueError
            student.course_id = course.id
        except (ValueError, TypeError):
            return None, 'Invalid course selected.'
    else:
        student.course_id = None

    admission_date = request.form.get('admission_date')
    if admission_date:
        try:
            student.admission_date = datetime.strptime(admission_date, '%Y-%m-%d').date()
        except ValueError:
            return None, 'Invalid admission date.'
    else:
        student.admission_date = None
    return student, None

@admin_bp.route('/students/add', methods=['GET', 'POST'])
@protect
def add_student():
    courses = Course.query.filter_by(status='active').order_by(Course.category, Course.course_name).all()
    if request.method == 'POST':
        student = Student()
        student, error = apply_student_form(student)
        if error:
            flash(error, 'error')
            return redirect(url_for('admin.add_student'))
        if Student.query.filter_by(student_id=student.student_id).first():
            flash('Student code already exists.', 'error')
            return redirect(url_for('admin.add_student'))
        db.session.add(student)
        db.session.commit()
        audit('CREATE_STUDENT', student.student_id)
        flash('Student added successfully.', 'success')
        return redirect(url_for('admin.students'))
    return render_template('admin/student_form.html', student=None, courses=courses)

@admin_bp.route('/students/<int:student_id>/edit', methods=['GET', 'POST'])
@protect
def edit_student(student_id):
    student = db.session.get(Student, student_id)
    if not student:
        flash('Student record not found.', 'error')
        return redirect(url_for('admin.students'))
    courses = Course.query.filter_by(status='active').order_by(Course.category, Course.course_name).all()
    if request.method == 'POST':
        old_sid = student.student_id
        student, error = apply_student_form(student)
        if error:
            flash(error, 'error')
            return redirect(url_for('admin.edit_student', student_id=student.id))
        existing = Student.query.filter_by(student_id=student.student_id).first()
        if existing and existing.id != student.id:
            flash('Student code already in use by another student.', 'error')
            return redirect(url_for('admin.edit_student', student_id=student.id))
        db.session.commit()
        audit('EDIT_STUDENT', f'{student.student_id} - {student.full_name}')
        flash('Student record updated successfully.', 'success')
        return redirect(url_for('admin.students'))
    return render_template('admin/student_form.html', student=student, courses=courses)

@admin_bp.route('/students/<int:student_id>/delete', methods=['POST'])
@protect
def delete_student(student_id):
    student = db.session.get(Student, student_id)
    if not student:
        flash('Student not found.', 'error')
        return redirect(url_for('admin.students'))
    sid, name = student.student_id, student.full_name
    if blob_enabled():
        for cert in student.certificates:
            try:
                if _is_remote_file_reference(cert.file_name):
                    delete_file(cert.file_name)
            except BlobStorageError as exc:
                current_app.logger.warning(
                    "Could not delete certificate blob %s: %s",
                    cert.file_name,
                    exc,
                )
    else:
        upload_dir = Path(current_app.config['UPLOAD_FOLDER']).resolve()
        for cert in student.certificates:
            if not _is_remote_file_reference(cert.file_name):
                (upload_dir / Path(str(cert.file_name)).name).unlink(missing_ok=True)
    db.session.delete(student)
    db.session.commit()
    audit('DELETE_STUDENT', f'{sid} - {name}')
    flash(f'Student {name} ({sid}) deleted successfully.', 'success')
    return redirect(url_for('admin.students'))

# ============================================================
# BULK STUDENT IMPORT
# ============================================================

@admin_bp.route('/students/import', methods=['GET', 'POST'])
@protect
def import_students():
    if request.method == 'POST':
        uploaded = request.files.get('file')
        if not uploaded or not uploaded.filename or not uploaded.filename.lower().endswith('.xlsx'):
            flash('Please upload an .xlsx file.', 'error')
            return redirect(url_for('admin.import_students'))
        try:
            workbook = load_workbook(uploaded, read_only=True, data_only=True)
            rows = list(workbook.active.iter_rows(values_only=True))
            workbook.close()
        except Exception:
            flash('The uploaded spreadsheet could not be read.', 'error')
            return redirect(url_for('admin.import_students'))
        if not rows:
            flash('Spreadsheet is empty.', 'error')
            return redirect(url_for('admin.import_students'))

        def normalize(h):
            return re.sub(r'[^a-z0-9]+', '_', clean(h, 80).lower()).strip('_')

        headers = [normalize(x).replace('_if', '') for x in rows[0]]
        aliases = {
            'student_id': 'student_code',
            'full_name': 'name',
            'course_name': 'course',
            'passing_grade': 'passing_grade',
            'passing_year': 'passing_year',
        }
        headers = [aliases.get(h, h) for h in headers]
        required = {'student_code', 'name', 'email', 'course'}
        if not required.issubset(headers):
            flash('Required columns: Student code, Name, Email, Course. Optional: Passing grade, Passing year.', 'error')
            return redirect(url_for('admin.import_students'))

        # Normalize legacy spreadsheet course names to the institute catalogue convention.
        def normalize_course_name(value):
            raw = re.sub(r'\s+', ' ', clean(value, 200)).strip()
            upper = raw.upper()
            m = re.match(r'^\s*([A-Z0-9]+)\s*\(\s*(.*?)\s*\)\s*$', upper)
            code = m.group(1) if m else ''
            title = m.group(2) if m else upper
            title = re.sub(r'\bPOST\s+GRADUATE\b', 'POSTGRADUATE', title)
            title = re.sub(r'\bGRADUTE\b', 'GRADUATE', title)
            title = re.sub(r'\bGRADUATE\b', 'POSTGRADUATE', title)
            title = re.sub(r'\bMEHCHANICAL\b', 'MECHANICAL', title)
            title = re.sub(r'\bMEHCANICAL\b', 'MECHANICAL', title)
            title = re.sub(r'\bARTITECTURE\b', 'ARCHITECTURE', title)
            title = re.sub(r'\bDIPOMA\b', 'DIPLOMA', title)
            title = re.sub(r'\bGRADUTE\b', 'GRADUATE', title)
            title = re.sub(r'\s+', ' ', title).strip()
            # Specific legacy abbreviations / website-aligned names.
            known = {
                'GBA': 'POSTGRADUATE IN BUSINESS ADMINISTRATION',
                'GDBA': 'POSTGRADUATE DIPLOMA IN BUSINESS ADMINISTRATION',
                'PDBA': 'PROFESSIONAL DIPLOMA IN BUSINESS ADMINISTRATION',
                'MBA': 'MASTER IN BUSINESS ADMINISTRATION',
                'MPBA': 'MASTER PROGRAMME IN BUSINESS ADMINISTRATION',
                'EMBA': 'EXECUTIVE MASTER IN BUSINESS ADMINISTRATION',
                'PDM': 'PROFESSIONAL DOCTRATE IN MANAGEMENT',
                'DBA': 'DIPLOMA IN BUSINESS ADMINISTRATION',
                'DCA': 'DIPLOMA IN COMPUTER APPLICATION',
                'GCA': 'POSTGRADUATE IN COMPUTER APPLICATION',
                'PGDCA': 'POSTGRADUATE DIPLOMA IN COMPUTER APPLICATION',
                'MCA': 'MASTER IN COMPUTER APPLICATION',
                'GHM': 'POSTGRADUATE IN HOTEL MANAGEMENT',
                'DME': 'DIPLOMA IN MECHANICAL ENGINEERING',
                'GDME': 'POSTGRADUATE DIPLOMA IN MECHANICAL ENGINEERING',
                'GME': 'POSTGRADUATE IN MECHANICAL ENGINEERING',
                'DCE': 'DIPLOMA IN CIVIL ENGINEERING',
                'DAE': 'DIPLOMA IN ARCHITECTURE ENGINEERING',
                'DEE': 'DIPLOMA IN ELECTRICAL ENGINEERING',
                'GEE': 'POSTGRADUATE IN ELECTRICAL ENGINEERING',
                'GAM': 'POSTGRADUATE IN AUTOMOBILE ENGINEERING',
                'CDCN': 'CERTIFIED DIPLOMA IN COMPUTER NETWORKING',
                'DECE': 'DIPLOMA IN ELECTRONICS & COMMUNICATION ENGINEERING',
                'GDECE': 'POSTGRADUATE DIPLOMA IN ELECTRONICS & COMMUNICATION ENGINEERING',
                'GECE': 'POSTGRADUATE IN ELECTRONICS & COMMUNICATION ENGINEERING',
            }
            if code in known:
                title = known[code]
            # If no known code, use the cleaned text. Keep an input abbreviation if present.
            if code:
                return f'{title} ({code})'
            return title

        def course_category(course_name):
            u = course_name.upper()
            engineering_words = ('ENGINEERING', 'MECHANICAL', 'CIVIL', 'ELECTRICAL', 'ELECTRONICS', 'AUTOMOBILE', 'CHEMICAL', 'ARCHITECTURE', 'COMPUTER NETWORKING')
            return 'engineering' if any(w in u for w in engineering_words) else 'management'

        def engineering_section_for(course_name):
            u = course_name.upper()
            if 'MECHANICAL' in u: return 'mechanical'
            if 'CHEMICAL' in u: return 'chemical'
            if 'CIVIL' in u: return 'civil'
            if 'ELECTRICAL' in u and 'ELECTRONICS' not in u: return 'electrical'
            if 'ELECTRONICS' in u or 'ELECTRONICS &' in u: return 'electronics'
            if 'AUTOMOBILE' in u: return 'automobile'
            if 'COMPUTER' in u: return 'computer'
            return 'other'

        def generate_course_code(course_name):
            m = re.search(r'\(([A-Z0-9]+)\)$', course_name.upper())
            base = m.group(1) if m else re.sub(r'[^A-Z0-9]+', '', course_name.upper())[:30] or 'COURSE'
            code = base[:50]
            n = 2
            while Course.query.filter(func.lower(Course.course_code) == code.lower()).first():
                code = f'{base[:46]}-{n}'
                n += 1
            return code

        count = skipped = created_courses = 0
        for row in rows[1:]:
            data = dict(zip(headers, row))
            sid = clean(data.get('student_code'), 80)
            name = clean(data.get('name'), 200)
            email = clean(data.get('email'), 200)
            course_value = clean(data.get('course'), 200)
            grade = clean(data.get('passing_grade'), 50)
            year = parse_passing_year(data.get('passing_year'))
            # Email and year remain required for the existing SIMTS import contract.
            if not sid or not name or not email or not course_value or year is None:
                skipped += 1
                continue
            if Student.query.filter_by(student_id=sid).first():
                skipped += 1
                continue

            normalized_course = normalize_course_name(course_value)
            # Match by normalized full name, or by abbreviation in parentheses.
            course = Course.query.filter(func.lower(Course.course_name) == normalized_course.lower()).first()
            if not course:
                m = re.search(r'\(([A-Z0-9]+)\)$', normalized_course)
                if m:
                    code = m.group(1)
                    course = Course.query.filter(func.lower(Course.course_code) == code.lower()).first()
            if not course:
                category = course_category(normalized_course)
                section = engineering_section_for(normalized_course) if category == 'engineering' else None
                course = Course(course_code=generate_course_code(normalized_course), course_name=normalized_course,
                                category=category, engineering_section=section, status='active')
                db.session.add(course)
                db.session.flush()
                created_courses += 1

            db.session.add(Student(
                student_id=sid, full_name=name, email=email,
                course_id=course.id, passing_grade=grade, passing_year=year,
                admission_status='active', completion_status='ongoing'
            ))
            count += 1

        db.session.commit()
        audit('IMPORT_STUDENTS', f'{count} imported / {skipped} skipped / {created_courses} courses created')
        flash(f'Imported {count} students; skipped {skipped}; created {created_courses} missing courses.', 'success')
        return redirect(url_for('admin.students'))
    return render_template('admin/import_students.html')

# Engineering sections used by the public catalogue and admin course form.
ENGINEERING_SECTIONS = [
    ('civil', 'Civil Engineering'),
    ('mechanical', 'Mechanical Engineering'),
    ('chemical', 'Chemical Engineering'),
    ('electrical', 'Electrical Engineering'),
    ('electronics', 'Electronics Engineering'),
    ('automobile', 'Automobile Engineering'),
    ('computer', 'Computer Engineering'),
    ('other', 'Other Engineering'),
]
ENGINEERING_SECTION_KEYS = {key for key, _ in ENGINEERING_SECTIONS}

# ============================================================
# COURSES MANAGEMENT
# ============================================================

@admin_bp.route('/courses')
@protect
def courses():
    courses = Course.query.order_by(Course.category, Course.engineering_section, Course.course_name).all()
    counts = dict(db.session.query(Student.course_id, func.count(Student.id))
                  .filter(Student.course_id.isnot(None)).group_by(Student.course_id).all())
    return render_template('admin/courses.html', courses=courses, student_counts=counts, engineering_sections=ENGINEERING_SECTIONS)

@admin_bp.route('/courses/add', methods=['GET', 'POST'])
@protect
def add_course():
    preset_section = clean(request.args.get('engineering_section'), 40).lower()
    if preset_section not in ENGINEERING_SECTION_KEYS:
        preset_section = ''
    if request.method == 'POST':
        code, name = clean(request.form.get('course_code'), 50), clean(request.form.get('course_name'), 200)
        category = clean(request.form.get('category'), 30).lower()
        if category not in {'management', 'engineering'}: category = 'management'
        engineering_section = clean(request.form.get('engineering_section'), 40).lower() or None
        if category == 'engineering' and engineering_section not in ENGINEERING_SECTION_KEYS:
            flash('Please select an Engineering section.', 'error')
            return redirect(url_for('admin.add_course'))
        if category == 'management':
            engineering_section = None
        if not code or not name:
            flash('Course code and name are required.', 'error')
            return redirect(url_for('admin.add_course'))
        if Course.query.filter_by(course_code=code).first():
            flash('Course code already exists.', 'error')
            return redirect(url_for('admin.add_course'))
        course = Course(course_code=code, course_name=name, category=category, engineering_section=engineering_section,
                        duration=clean(request.form.get('duration'), 100),
                        eligibility=clean(request.form.get('eligibility'), 500),
                        fees=parse_course_fee(request.form.get('fees')),
                        status=clean(request.form.get('status'), 30) or 'active',
                        description=(request.form.get('description') or '').strip()[:10000])
        db.session.add(course); db.session.commit()
        audit('CREATE_COURSE', code); flash('Course added.', 'success')
        return redirect(url_for('admin.courses'))
    return render_template('admin/course_form.html', course=None, preset_category=('engineering' if preset_section else 'management'), preset_engineering_section=preset_section)

@admin_bp.route('/courses/<int:course_id>/edit', methods=['GET', 'POST'])
@protect
def edit_course(course_id):
    course = db.session.get(Course, course_id)
    if not course:
        flash('Course not found.', 'error'); return redirect(url_for('admin.courses'))
    if request.method == 'POST':
        code, name = clean(request.form.get('course_code'), 50), clean(request.form.get('course_name'), 200)
        category = clean(request.form.get('category'), 30).lower()
        if category not in {'management', 'engineering'}: category = 'management'
        engineering_section = clean(request.form.get('engineering_section'), 40).lower() or None
        if category == 'engineering' and engineering_section not in ENGINEERING_SECTION_KEYS:
            flash('Please select an Engineering section.', 'error')
            return redirect(url_for('admin.edit_course', course_id=course.id))
        if category == 'management':
            engineering_section = None
        if not code or not name:
            flash('Course code and name are required.', 'error'); return redirect(url_for('admin.edit_course', course_id=course.id))
        existing = Course.query.filter_by(course_code=code).first()
        if existing and existing.id != course.id:
            flash('Course code already in use by another course.', 'error'); return redirect(url_for('admin.edit_course', course_id=course.id))
        course.course_code, course.course_name, course.category, course.engineering_section = code, name, category, engineering_section
        course.duration = clean(request.form.get('duration'), 100)
        course.eligibility = clean(request.form.get('eligibility'), 500)
        course.fees = parse_course_fee(request.form.get('fees'))
        course.status = clean(request.form.get('status'), 30) or 'active'
        course.description = (request.form.get('description') or '').strip()[:10000]
        db.session.commit(); audit('EDIT_COURSE', code); flash('Course updated successfully.', 'success')
        return redirect(url_for('admin.courses'))
    return render_template('admin/course_form.html', course=course)

@admin_bp.route('/courses/<int:course_id>/delete', methods=['POST'])
@protect
def delete_course(course_id):
    course = db.session.get(Course, course_id)
    if not course:
        flash('Course not found.', 'error'); return redirect(url_for('admin.courses'))
    code = course.course_code
    Student.query.filter_by(course_id=course.id).update({'course_id': None})
    db.session.delete(course); db.session.commit(); audit('DELETE_COURSE', code)
    flash(f'Course {code} deleted successfully.', 'success'); return redirect(url_for('admin.courses'))

# ============================================================
# CERTIFICATE IMAGE MANAGEMENT
# ============================================================

@admin_bp.route('/certificates', methods=['GET', 'POST'])
@protect
def certificates():
    if request.method == 'POST':
        sid = clean(request.form.get('student_id'), 80)
        number = clean(request.form.get('certificate_number'), 100)
        image = request.files.get('certificate_file')
        student = Student.query.filter_by(student_id=sid).first()
        ext = image_extension(image.filename if image else '')
        if not student or not number or not image or not image.filename or ext not in {'.png', '.jpg'} or not image_signature(image):
            flash('Valid Student ID, certificate number and PNG/JPG certificate image are required.', 'error')
            return redirect(url_for('admin.certificates'))
        if Certificate.query.filter_by(certificate_number=number).first():
            flash('Certificate number already exists.', 'error'); return redirect(url_for('admin.certificates'))

        filename = secure_filename(f'cert_{number}') + ext
        target = None; blob_reference = None
        try:
            if blob_enabled():
                blob_reference = upload_image(image, 'certificates', filename)
                file_reference = blob_reference
            else:
                upload_dir = Path(current_app.config['UPLOAD_FOLDER']).resolve()
                upload_dir.mkdir(parents=True, exist_ok=True)
                target = upload_dir / filename
                image.save(target); file_reference = filename
        except BlobStorageError as exc:
            flash(str(exc), 'error'); return redirect(url_for('admin.certificates'))

        issue_date_val = request.form.get('issue_date')
        try: issue_date = datetime.strptime(issue_date_val, '%Y-%m-%d').date() if issue_date_val else datetime.utcnow().date()
        except ValueError: issue_date = datetime.utcnow().date()
        try:
            cert = Certificate(certificate_number=number, student_id=student.id, issue_date=issue_date,
                               status='valid', file_name=file_reference)
            db.session.add(cert); student.certificate_number = number; student.certificate_issue_date = issue_date
            db.session.commit()
        except Exception:
            db.session.rollback()
            if blob_reference:
                try: delete_file(blob_reference)
                except BlobStorageError: pass
            elif target: target.unlink(missing_ok=True)
            flash('Certificate could not be saved.', 'error'); return redirect(url_for('admin.certificates'))
        audit('UPLOAD_CERTIFICATE_IMAGE', number); flash('Certificate image uploaded.', 'success')
        return redirect(url_for('admin.certificates'))
    return render_template('admin/certificates.html', certificates=Certificate.query.order_by(Certificate.id.desc()).all())

@admin_bp.route('/certificates/<int:certificate_id>/edit', methods=['GET', 'POST'])
@protect
def edit_certificate(certificate_id):
    certificate = db.session.get(Certificate, certificate_id)
    if not certificate:
        flash('Certificate not found.', 'error'); return redirect(url_for('admin.certificates'))
    if request.method == 'POST':
        number = clean(request.form.get('certificate_number'), 100)
        status = clean(request.form.get('status'), 30) or 'valid'
        image = request.files.get('certificate_file')
        if not number:
            flash('Certificate number is required.', 'error'); return redirect(url_for('admin.edit_certificate', certificate_id=certificate.id))
        existing = Certificate.query.filter_by(certificate_number=number).first()
        if existing and existing.id != certificate.id:
            flash('Certificate number already in use.', 'error'); return redirect(url_for('admin.edit_certificate', certificate_id=certificate.id))
        issue_date_str = request.form.get('issue_date')
        try: certificate.issue_date = datetime.strptime(issue_date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError): pass
        certificate.certificate_number, certificate.status = number, status
        if image and image.filename:
            ext = image_extension(image.filename)
            if ext not in {'.png', '.jpg'} or not image_signature(image):
                flash('Only valid PNG/JPG certificate images are allowed.', 'error')
                return redirect(url_for('admin.edit_certificate', certificate_id=certificate.id))
            filename = secure_filename(f'cert_{number}_{datetime.utcnow().strftime("%Y%m%d%H%M%S")}') + ext
            old_reference = certificate.file_name
            target = None; new_reference = None
            try:
                if blob_enabled():
                    new_reference = upload_image(image, 'certificates', filename)
                    certificate.file_name = new_reference
                else:
                    upload_dir = Path(current_app.config['UPLOAD_FOLDER']).resolve()
                    upload_dir.mkdir(parents=True, exist_ok=True)
                    target = upload_dir / filename
                    image.save(target); certificate.file_name = filename
                if blob_enabled() and old_reference != certificate.file_name:
                    try: delete_file(old_reference)
                    except BlobStorageError: pass
                elif not blob_enabled() and old_reference != certificate.file_name and not _is_remote_file_reference(old_reference):
                    (Path(current_app.config['UPLOAD_FOLDER']).resolve() / Path(str(old_reference)).name).unlink(missing_ok=True)
            except BlobStorageError as exc:
                flash(str(exc), 'error'); return redirect(url_for('admin.edit_certificate', certificate_id=certificate.id))
        student = db.session.get(Student, certificate.student_id)
        if student:
            student.certificate_number = number; student.certificate_issue_date = certificate.issue_date
        db.session.commit(); audit('EDIT_CERTIFICATE', number); flash('Certificate updated.', 'success')
        return redirect(url_for('admin.certificates'))
    return render_template('admin/certificate_form.html', certificate=certificate)

def _is_remote_file_reference(reference):
    value = str(reference or '').strip().lower()
    return value.startswith('http://') or value.startswith('https://')


@admin_bp.route('/certificates/<int:certificate_id>/delete', methods=['POST'])
@protect
def delete_certificate(certificate_id):
    certificate = db.session.get(Certificate, certificate_id)
    if not certificate:
        flash('Certificate not found.', 'error'); return redirect(url_for('admin.certificates'))
    number = certificate.certificate_number
    try:
        # Remote Vercel Blob references are deleted through blob_storage.
        # Local files are handled separately below.
        if blob_enabled() and _is_remote_file_reference(certificate.file_name):
            delete_file(certificate.file_name)
    except BlobStorageError as exc:
        current_app.logger.warning(
            "Could not delete certificate blob %s: %s",
            certificate.file_name,
            exc,
        )

    if not blob_enabled() and not _is_remote_file_reference(certificate.file_name):
        local_name = Path(str(certificate.file_name)).name
        (Path(current_app.config['UPLOAD_FOLDER']).resolve() / local_name).unlink(missing_ok=True)
    student = db.session.get(Student, certificate.student_id)
    if student and student.certificate_number == number:
        student.certificate_number = None; student.certificate_issue_date = None
    db.session.delete(certificate); db.session.commit(); audit('DELETE_CERTIFICATE', number)
    flash(f'Certificate {number} deleted successfully.', 'success')
    return redirect(url_for('admin.certificates'))

# ============================================================
# MESSAGES, SECURITY LOGS, PASSWORDS, BACKUPS
# ============================================================

@admin_bp.route('/messages')
@protect
def messages():
    messages = ContactMessage.query.order_by(ContactMessage.id.desc()).all()
    unread = [m for m in messages if m.status == 'unread']
    for message in unread:
        message.status = 'read'
    if unread:
        db.session.commit()
    return render_template('admin/messages.html', messages=messages)


@admin_bp.route('/audit-logs')
@protect
def audit_logs():
    return render_template('admin/audit_logs.html', logs=AuditLog.query.order_by(AuditLog.id.desc()).limit(200).all())


@admin_bp.route('/change-password', methods=['GET', 'POST'])
@protect
def change_password():
    if request.method == 'POST':
        current = request.form.get('current_password', '')
        new = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')
        if not current_user.check_password(current):
            flash('Current password is incorrect.', 'error')
        elif len(new) < 12:
            flash('New password must be at least 12 characters.', 'error')
        elif new != confirm:
            flash('Passwords do not match.', 'error')
        else:
            current_user.set_password(new)
            db.session.commit()
            audit('CHANGE_PASSWORD', current_user.username)
            flash('Password changed. Please sign in again.', 'success')
            from flask_login import logout_user
            logout_user()
            return redirect(url_for('auth.login'))
    return render_template('admin/change_password.html')


@admin_bp.route('/backup', methods=['GET', 'POST'])
@protect
def backup():
    if request.method == 'POST':
        try:
            archive = create_backup(current_app)
            flash('Backup created successfully: ' + os.path.basename(archive), 'success')
            audit('CREATE_BACKUP', os.path.basename(archive))
        except Exception as e:
            flash(f'Backup could not be created: {str(e)}', 'error')

    folder = Path(current_app.config['BACKUP_FOLDER'])
    backups = []
    if folder.exists():
        for p in sorted(folder.glob('*.zip'), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_file():
                size_kb = p.stat().st_size / 1024
                size_str = f"{size_kb / 1024:.2f} MB" if size_kb > 1024 else f"{size_kb:.1f} KB"
                mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                backups.append({
                    'name': p.name,
                    'size': size_str,
                    'mtime': mtime
                })

    return render_template('admin/backup.html', backups=backups)


@admin_bp.route('/backup/download/<path:filename>')
@protect
def download_backup(filename):
    folder = Path(current_app.config['BACKUP_FOLDER']).resolve()
    path = (folder / filename).resolve()
    if folder not in path.parents or not path.is_file() or path.suffix.lower() != '.zip':
        return 'Backup not found', 404
    audit('DOWNLOAD_BACKUP', filename)
    return send_file(path, as_attachment=True, download_name=path.name)


@admin_bp.route('/backup/delete/<path:filename>', methods=['POST'])
@protect
def delete_backup(filename):
    folder = Path(current_app.config['BACKUP_FOLDER']).resolve()
    path = (folder / filename).resolve()
    if folder in path.parents and path.is_file() and path.suffix.lower() == '.zip':
        path.unlink(missing_ok=True)
        audit('DELETE_BACKUP', filename)
        flash(f'Backup archive {filename} deleted.', 'success')
    else:
        flash('Backup file not found.', 'error')
    return redirect(url_for('admin.backup'))

