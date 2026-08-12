"""Admin-only account management."""

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from ..attendance_db import list_students
from ..audit import audit
from ..auth_db import (
    approve_user,
    count_users,
    link_user_to_student,
    list_approved_users,
    list_pending_users,
    reject_user,
)
from ..decorators import admin_required
from ..enrolment import MAX_PHOTOS, EnrolmentError, enrol, remove_person
from ..pagination import paginate
from ..recognition import get_known_encodings

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/users")
@admin_required
def users():
    # two lists on one page, so two independent page numbers. Sharing one would move
    # both at once, which is never what an admin working through a queue wants.
    pending_page = paginate(
        request.args.get("pending", type=int), count_users(approved=False)
    )
    approved_page = paginate(
        request.args.get("approved", type=int), count_users(approved=True)
    )

    return render_template(
        "admin_users.html",
        pending=list_pending_users(pending_page.size, pending_page.offset),
        approved=list_approved_users(approved_page.size, approved_page.offset),
        pending_page=pending_page,
        approved_page=approved_page,
        students=list_students(),
        kiosk_mode=current_app.config["KIOSK_MODE"],
    )


# approving is a state change, so it is a POST rather than a link. a GET would mean any
# <img src="/admin/users/3/approve"> on any page could trigger it, and browsers (and link
# prefetchers) follow those automatically.
@admin_bp.route("/users/<int:user_id>/approve", methods=["POST"])
@admin_required
def approve(user_id):
    if approve_user(user_id):
        audit("account.approved", target_id=user_id)
        flash("Account approved.", "success")
    else:
        # either the id doesn't exist or it was already approved -- both mean "nothing
        # happened", and neither is worth distinguishing for the person clicking
        flash("That account could not be approved.", "error")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/reject", methods=["POST"])
@admin_required
def reject(user_id):
    if reject_user(user_id):
        audit("account.rejected", target_id=user_id)
        flash("Account rejected and removed.", "success")
    else:
        flash("That account could not be rejected.", "error")
    return redirect(url_for("admin.users"))


@admin_bp.route("/enrol", methods=["GET", "POST"])
@admin_required
def enrol_person():
    """Add someone to the system by uploading photos of them.

    Admin-only, and it has to be: enrolling a face is deciding who the system will let
    check in. If people could enrol themselves, they could enrol themselves under
    somebody else's name.
    """
    if request.method == "POST":
        # the upload size limit for this route is raised in create_app, not here: it has
        # to happen before the CSRF hook reads the form, which is earlier than any view

        # getlist, not get -- a single file input with `multiple` sends one field name
        # repeated, and .get() would silently take only the first
        uploads = [f for f in request.files.getlist("photos") if f and f.filename]
        photos = [(f.filename, f.read()) for f in uploads]

        try:
            name, added, problems = enrol(request.form.get("name", ""), photos)
        except EnrolmentError as error:
            return render_template(
                "admin_enrol.html",
                error=str(error),
                name=request.form.get("name", ""),
                enrolled=sorted(get_known_encodings()),
                max_photos=MAX_PHOTOS,
            ), 400

        audit("person.enrolled", person=name, photos=added, rejected=len(problems))
        flash(f"Enrolled {name}: {added} photo{'' if added == 1 else 's'} added.", "success")
        for problem in problems:
            # partial success is still success, but the admin needs to know which photos
            # were dropped and why, or they will assume all of them worked
            flash(problem, "warn")
        return redirect(url_for("admin.enrol_person"))

    return render_template(
        "admin_enrol.html",
        enrolled=sorted(get_known_encodings()),
        max_photos=MAX_PHOTOS,
    )


@admin_bp.route("/enrol/remove", methods=["GET", "POST"])
@admin_required
def remove_enrolled_person():
    """Stop recognising someone, after confirming.

    Two steps on purpose. Deleting somebody's photos is not undoable from the interface,
    and a single button next to their name is one misclick away from doing it. The GET
    shows what will happen; only the POST does it.
    """
    name = (request.values.get("name") or "").strip()

    if request.method == "POST":
        try:
            photos = remove_person(name)
        except EnrolmentError as error:
            flash(str(error), "error")
            return redirect(url_for("admin.enrol_person"))

        audit("person.removed", person=name, photos=photos)
        flash(
            f"Removed {name}: {photos} photo{'' if photos == 1 else 's'} deleted. "
            "Their attendance history has been kept.",
            "success",
        )
        return redirect(url_for("admin.enrol_person"))

    known = get_known_encodings()
    if name not in known:
        flash(f"{name!r} is not enrolled.", "error")
        return redirect(url_for("admin.enrol_person"))

    return render_template(
        "admin_remove.html", name=name, photo_count=len(known[name])
    )


@admin_bp.route("/users/<int:user_id>/link", methods=["POST"])
@admin_required
def link(user_id):
    """Say which enrolled person an account belongs to.

    Admin-only by design. If people could set this themselves, anyone could claim to be
    anyone, which is precisely the impersonation personal mode exists to stop.
    """
    raw = request.form.get("student_id", "")
    # an empty selection means "unlink", which has to stay possible -- an admin who links
    # the wrong person needs a way back that is not editing the database by hand
    student_id = int(raw) if raw.isdigit() else None

    if link_user_to_student(user_id, student_id):
        audit("account.linked", target_id=user_id, student_id=student_id or "none")
        flash("Linked account updated." if student_id else "Account unlinked.", "success")
    else:
        flash("That account could not be updated.", "error")
    return redirect(url_for("admin.users"))
