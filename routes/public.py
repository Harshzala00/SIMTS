from flask import (
    Blueprint,
    abort,
    current_app,
    make_response,
    render_template,
    request,
    send_file,
    send_from_directory,
)
from sqlalchemy import or_
from werkzeug.exceptions import BadRequest

from extensions import db, limiter
from models import Certificate, ContactMessage, Course, Student
from services.blob_storage import BlobStorageError, blob_enabled, download_to_temp


public_bp = Blueprint("public", __name__)


def no_store(response):
    response = make_response(response)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def find_student(query):
    query = (query or "").strip()
    if not query or len(query) > 200:
        return None

    student = Student.query.filter(
        or_(Student.student_id == query, Student.email == query)
    ).first()

    if student:
        return student

    return Student.query.filter(
        or_(Student.student_id.ilike(query), Student.email.ilike(query))
    ).first()


@public_bp.route("/")
def home():
    courses = (
        Course.query.filter_by(status="active")
        .order_by(Course.id.desc())
        .limit(6)
        .all()
    )
    return render_template("index.html", courses=courses)


@public_bp.route("/courses")
def courses():
    selected_category = (request.args.get("category") or "").strip().lower()
    if selected_category not in {"management", "engineering"}:
        selected_category = None

    query = Course.query.filter_by(status="active")
    if selected_category:
        query = query.filter_by(category=selected_category)

    courses = query.order_by(Course.course_name).all()
    return render_template(
        "courses.html",
        courses=courses,
        selected_category=selected_category,
    )


@public_bp.route("/admission-verification", methods=["GET", "POST"])
@limiter.limit("30 per minute", methods=["POST"])
def admission_verification():
    student = None
    certificate = None
    searched = False

    if request.method == "POST":
        searched = True
        query = request.form.get("query", "").strip()

        if len(query) > 200:
            raise BadRequest("Verification input is too long.")

        student = find_student(query)

        if student:
            certificate = (
                Certificate.query
                .filter_by(student_id=student.id, status="valid")
                .order_by(Certificate.id.desc())
                .first()
            )

    return no_store(
        render_template(
            "admission_verification.html",
            student=student,
            certificate=certificate,
            searched=searched,
        )
    )


@public_bp.route("/certificate/<int:certificate_id>/view")
@limiter.limit("60 per minute")
def view_certificate(certificate_id):
    """Serve a valid private certificate image inline."""
    certificate = db.session.get(Certificate, certificate_id)

    if not certificate or certificate.status != "valid":
        abort(404)

    reference = str(certificate.file_name or "").strip()
    mimetype = _image_mimetype(reference)

    if mimetype not in {"image/png", "image/jpeg"}:
        current_app.logger.warning(
            "Certificate %s has unsupported file reference: %r",
            certificate_id,
            reference,
        )
        abort(404)

    if reference.startswith(("http://", "https://")):
        if not blob_enabled():
            current_app.logger.error(
                "Certificate %s is a remote Blob URL but BLOB_READ_WRITE_TOKEN is missing.",
                certificate_id,
            )
            abort(404)

        try:
            temp_path = download_to_temp(reference)
        except BlobStorageError as exc:
            current_app.logger.error(
                "Certificate %s could not be read from private Blob: %s",
                certificate_id,
                exc,
            )
            abort(404)

        response = send_file(
            temp_path,
            mimetype=mimetype,
            as_attachment=False,
            download_name=None,
            max_age=0,
        )
        response.call_on_close(
            lambda path=temp_path: path.unlink(missing_ok=True)
        )
    else:
        # Local development fallback.
        response = send_from_directory(
            current_app.config["UPLOAD_FOLDER"],
            reference,
            as_attachment=False,
            mimetype=mimetype,
            max_age=0,
        )

    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Disposition"] = "inline"

    return response


def _image_mimetype(filename):
    name = str(filename or "").lower()

    # Blob URLs do not normally contain a query string, but handle it safely.
    name = name.split("?", 1)[0].split("#", 1)[0]

    if name.endswith(".png"):
        return "image/png"

    if name.endswith(".jpg") or name.endswith(".jpeg"):
        return "image/jpeg"

    return "application/octet-stream"


@public_bp.route("/about")
def about():
    return render_template("about.html")


@public_bp.route("/contact", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def contact():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        subject = request.form.get("subject", "").strip()
        message = request.form.get("message", "").strip()

        if not name or not email or not message:
            return render_template(
                "contact.html",
                error="Please complete all required fields.",
            )

        if (
            len(name) > 200
            or len(email) > 200
            or len(subject) > 300
            or len(message) > 5000
        ):
            return render_template(
                "contact.html",
                error="One or more fields are too long.",
            )

        db.session.add(
            ContactMessage(
                name=name,
                email=email,
                subject=subject,
                message=message,
            )
        )
        db.session.commit()

        return render_template(
            "contact.html",
            success="Your message has been sent successfully.",
        )

    return render_template("contact.html")
