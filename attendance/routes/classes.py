"""Classes: the teacher starts one, and the projector shows its rotating check-in QR code."""

import qrcode  # draws QR codes
import qrcode.image.svg  # ...as SVG: crisp at projector size, and needs no image library
from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from markupsafe import Markup  # marks our own generated SVG as safe to put in the page

from ..audit import audit
from ..decorators import admin_required
from ..sessions_db import (
    authenticator_link,
    current_code,
    current_session,
    end_session,
    get_session,
    seconds_left,
    start_session,
)

classes_bp = Blueprint("classes", __name__, url_prefix="/classes")

MAX_MINUTES = 240  # four hours: longer than any class, short enough to catch a typo like 6000


def qr_svg(text):
    """The QR code for `text`, as SVG markup ready to drop into a page."""
    image = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage)
    return Markup(image.to_string(encoding="unicode"))


def student_qr_text(class_session):
    """What the classroom QR holds: which class, and its code right now.

    "FA" marks it as ours, so the check-in page can ignore any other QR code it sees.
    """
    return f"FA:{class_session['id']}:{current_code(class_session)}"


def get_or_404(session_id):
    """The class with this id, or a 404 page if there is none."""
    class_session = get_session(session_id)
    if class_session is None:
        abort(404)
    return class_session


@classes_bp.route("/")
@admin_required
def index():
    # the start form, plus a shortcut to the class already running (if any)
    return render_template("classes.html", running=current_session())


@classes_bp.route("/start", methods=["POST"])
@admin_required
def start():
    course = request.form.get("course", "").strip()
    minutes = request.form.get("minutes", type=int)  # None if missing or not a number

    if not course or not minutes or not 1 <= minutes <= MAX_MINUTES:
        flash(f"Enter a course name and a length between 1 and {MAX_MINUTES} minutes.", "error")
        return redirect(url_for("classes.index"))

    session_id = start_session(course[:64], minutes, created_by=session["username"])
    audit("class.started", target_id=session_id, course=course[:64])
    return redirect(url_for("classes.show", session_id=session_id))


@classes_bp.route("/<int:session_id>")
@admin_required
def show(session_id):
    # the projector page. Everything is rendered here once, so it works even before the
    # page's script runs; the script then keeps the code fresh from code() below.
    class_session = get_or_404(session_id)
    return render_template(
        "class_show.html",
        cls=class_session,
        code=current_code(class_session),
        seconds=seconds_left(),
        student_qr=qr_svg(student_qr_text(class_session)),
        teacher_qr=qr_svg(authenticator_link(class_session)),
    )


@classes_bp.route("/<int:session_id>/code")
@admin_required
def code(session_id):
    # polled by the projector page every few seconds, so the QR changes with the code
    class_session = get_or_404(session_id)
    response = jsonify(
        code=current_code(class_session),
        seconds=seconds_left(),
        qr=str(qr_svg(student_qr_text(class_session))),
    )
    response.headers["Cache-Control"] = "no-store"  # a cached code would be a stale code
    return response


@classes_bp.route("/<int:session_id>/end", methods=["POST"])
@admin_required
def end(session_id):
    get_or_404(session_id)
    end_session(session_id)
    audit("class.ended", target_id=session_id)
    flash("Class ended. Its attendance records are kept.", "success")
    return redirect(url_for("classes.index"))
