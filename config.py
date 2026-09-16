import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / '.env')


def env_bool(name, default=False):
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {
        '1',
        'true',
        'yes',
        'on'
    }


class Config:

    # ---------------------------------------------------------
    # SECURITY
    # ---------------------------------------------------------
    SECRET_KEY = os.getenv(
        'SECRET_KEY'
    ) or 'CHANGE_THIS_IN_ENV'

    # ---------------------------------------------------------
    # DATABASE
    # ---------------------------------------------------------
    SQLALCHEMY_DATABASE_URI = os.getenv(
        'DATABASE_URL'
    ) or (
        'sqlite:///' +
        str(BASE_DIR / 'instance' / 'simts.db')
    )

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
    }

    # ---------------------------------------------------------
    # FILE STORAGE
    # ---------------------------------------------------------
    #
    # Local:
    #   uploads/
    #   backups/
    #
    # Vercel:
    #   app.py changes these to /tmp/...
    #
    UPLOAD_FOLDER = (
        os.getenv('UPLOAD_FOLDER')
        or str(BASE_DIR / 'uploads')
    )

    BACKUP_FOLDER = (
        os.getenv('BACKUP_FOLDER')
        or str(BASE_DIR / 'backups')
    )

    # ---------------------------------------------------------
    # UPLOAD / FORM LIMITS
    # ---------------------------------------------------------
    MAX_CONTENT_LENGTH = int(
        os.getenv('MAX_CONTENT_LENGTH')
        or ('4194304' if os.getenv('VERCEL') else '10485760')
    )

    MAX_FORM_MEMORY_SIZE = int(
        os.getenv('MAX_FORM_MEMORY_SIZE')
        or '500000'
    )

    MAX_FORM_PARTS = int(
        os.getenv('MAX_FORM_PARTS')
        or '100'
    )

    # ---------------------------------------------------------
    # SESSION SECURITY
    # ---------------------------------------------------------
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    SESSION_COOKIE_SECURE = env_bool(
        'SESSION_COOKIE_SECURE',
        False
    )

    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = 'Lax'

    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE

    # ---------------------------------------------------------
    # CSRF
    # ---------------------------------------------------------
    WTF_CSRF_TIME_LIMIT = int(
        os.getenv('WTF_CSRF_TIME_LIMIT')
        or '3600'
    )

    # ---------------------------------------------------------
    # RATE LIMITING
    # ---------------------------------------------------------
    RATELIMIT_STORAGE_URI = (
        os.getenv('RATELIMIT_STORAGE_URI')
        or 'memory://'
    )

    # ---------------------------------------------------------
    # APPLICATION SETTINGS
    # ---------------------------------------------------------
    DEBUG = env_bool(
        'FLASK_DEBUG',
        False
    )

    TESTING = env_bool(
        'FLASK_TESTING',
        False
    )

    TRUST_PROXY = env_bool(
        'TRUST_PROXY',
        False
    )
