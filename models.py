from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db


class Admin(UserMixin, db.Model):
    __tablename__ = 'admin'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_login = db.Column(db.DateTime)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Course(db.Model):
    __tablename__ = 'course'

    id = db.Column(db.Integer, primary_key=True)
    course_code = db.Column(db.String(50), unique=True, nullable=True, index=True)
    course_name = db.Column(db.String(200), nullable=False)
    duration = db.Column(db.String(100))
    eligibility = db.Column(db.String(500))
    category = db.Column(db.String(30), default='management', nullable=False, index=True)
    engineering_section = db.Column(db.String(40), nullable=True, index=True)
    fees = db.Column(db.Numeric(10, 2), nullable=True)
    description = db.Column(db.Text)
    status = db.Column(db.String(30), default='active', nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    students = db.relationship('Student', backref='course', lazy=True)


class Student(db.Model):
    __tablename__ = 'student'

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(80), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), nullable=False, index=True)
    phone = db.Column(db.String(50))
    gender = db.Column(db.String(30))
    academic_session = db.Column(db.String(50))
    admission_date = db.Column(db.Date)
    admission_status = db.Column(db.String(50), default='active', nullable=False)
    completion_status = db.Column(db.String(50), default='ongoing', nullable=False)
    passing_grade = db.Column(db.String(50))
    passing_year = db.Column(db.String(10))
    certificate_number = db.Column(db.String(100), unique=True, index=True)
    certificate_issue_date = db.Column(db.Date)
    course_id = db.Column(db.Integer, db.ForeignKey('course.id'))

    certificates = db.relationship('Certificate', backref='student', lazy=True, cascade='all, delete-orphan')


class Certificate(db.Model):
    __tablename__ = 'certificate'

    id = db.Column(db.Integer, primary_key=True)
    certificate_number = db.Column(db.String(100), unique=True, nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    issue_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(30), default='valid', nullable=False)
    file_name = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ContactMessage(db.Model):
    __tablename__ = 'contact_message'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), nullable=False, index=True)
    subject = db.Column(db.String(300))
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(30), default='unread', nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class AuditLog(db.Model):
    __tablename__ = 'audit_log'

    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey('admin.id'))
    action = db.Column(db.String(200), nullable=False)
    target = db.Column(db.String(200))
    ip_address = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
