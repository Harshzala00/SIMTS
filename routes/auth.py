from datetime import datetime
import re

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user

from extensions import db, limiter
from models import Admin
from security import audit

auth_bp = Blueprint('auth', __name__, url_prefix='/admin')


def valid_username(value):
    return bool(re.fullmatch(r'[A-Za-z0-9_.-]{4,50}', value))


@auth_bp.route('/setup', methods=['GET', 'POST'])
@limiter.limit('3 per hour', methods=['POST'])
def setup_admin():
    if Admin.query.count() > 0:
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        if not valid_username(username):
            flash('Username must be 4–50 characters and contain only letters, numbers, dot, dash or underscore.', 'error')
        elif len(password) < 12:
            flash('Password must be at least 12 characters.', 'error')
        elif password != confirm:
            flash('Passwords do not match.', 'error')
        elif Admin.query.filter_by(username=username).first():
            flash('That username already exists.', 'error')
        else:
            admin = Admin(username=username, is_active=True)
            admin.set_password(password)
            db.session.add(admin)
            db.session.commit()
            audit('ADMIN_CREATED', username)
            flash('Admin account created. Please sign in.', 'success')
            return redirect(url_for('auth.login'))

    return render_template('admin/setup.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit('5 per minute', methods=['POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('admin.dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        admin = Admin.query.filter_by(username=username, is_active=True).first()

        if admin and admin.check_password(password):
            admin.last_login = datetime.utcnow()
            db.session.commit()
            login_user(admin, remember=False, fresh=True)
            audit('LOGIN_SUCCESS', username)
            return redirect(url_for('admin.dashboard'))

        audit('LOGIN_FAILED', username)
        flash('Invalid username or password.', 'error')

    return render_template('admin/login.html')


@auth_bp.route('/logout', methods=['POST'])
def logout():
    username = current_user.username if current_user.is_authenticated else ''
    if current_user.is_authenticated:
        audit('LOGOUT', username)
    logout_user()
    return redirect(url_for('auth.login'))
