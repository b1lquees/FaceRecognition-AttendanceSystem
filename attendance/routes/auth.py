"""Signing up, logging in and logging out."""

import sqlite3

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ..audit import audit
from ..auth_db import (
    change_password,
    register_pending_user,
    validate_credentials,
    verify_user,
)
from ..decorators import login_required
from ..ratelimit import rate_limit

# a Blueprint is a group of routes that gets attached to an app later, rather than being
# registered on a global `app` object at import time. the name "auth" becomes a prefix on
# the endpoint names, so this login view is referred to as "auth.login" in url_for().
auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])  # GET shows the form, POST processes it
# 10 attempts per 5 minutes is generous for someone mistyping a password and useless for
# guessing one: a four-digit PIN would take a day and a real password essentially forever
@rate_limit(limit=10, per_seconds=300, template="login.html")
def login():
    if request.method == "POST":
        username = request.form.get("username", "")  # form data lives in request.form
        password = request.form.get("password", "")

        result = verify_user(username, password)

        if result.status == "ok":
            session["username"] = username
            session["role"] = result.role
            audit("login.success", account=username, role=result.role)
            return redirect(url_for("main.home"))

        if result.status == "pending":
            audit("login.pending", account=username)
            # the password was correct, so saying "invalid credentials" here would send
            # someone off resetting a password that works fine. no session is started.
            return render_template(
                "login.html",
                error="Your account is waiting for an administrator to approve it.",
                pending=True,
            ), 403

        # the attempted username is recorded, never the password. repeated lines with
        # the same ip are what a brute-force attempt looks like from here.
        audit("login.failed", account=username)
        return render_template("login.html", error="Invalid username or password."), 401

    # a plain GET just displays the empty form
    return render_template("login.html")


@auth_bp.route("/signup", methods=["GET", "POST"])
# tighter than login: a person signs up once, so anything beyond a few attempts an hour
# is either a mistake or someone trying to bury the approval page in junk accounts
@rate_limit(limit=5, per_seconds=3600, template="signup.html")
def signup():
    """Public registration. Creates an account that cannot log in until approved."""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if password != confirm:
            return render_template(
                "signup.html", error="The two passwords do not match.", username=username
            ), 400

        # validated server-side: the HTML pattern/minlength attributes are a convenience
        # for the person filling the form in, not a control -- anyone can post directly
        error = validate_credentials(username, password)
        if error:
            return render_template("signup.html", error=error, username=username), 400

        try:
            register_pending_user(username, password)
        except sqlite3.IntegrityError:
            # users.username is UNIQUE, so a duplicate raises rather than overwriting
            return render_template(
                "signup.html",
                error="That username is already taken.",
                username=username,
            ), 409

        audit("signup.requested", account=username)
        # deliberately no session here: the whole point is that an admin decides
        return render_template("signup_submitted.html", username=username)

    return render_template("signup.html")


@auth_bp.route("/account/password", methods=["GET", "POST"])
@login_required
# the same limit as login, and for the same reason: this form takes the current password,
# so without a limit it is another place to guess it -- one that a signed-in attacker on a
# borrowed session could use to confirm they had the right person before doing anything else
@rate_limit(limit=10, per_seconds=300, template="change_password.html")
def change_own_password():
    """Change your own password, proving you know the current one."""
    if request.method == "POST":
        username = session["username"]
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        # the current password is verified through the normal login path, so a pending or
        # disabled account cannot use this either
        if verify_user(username, current).status != "ok":
            audit("password.change_refused", account=username)
            return render_template(
                "change_password.html", error="Your current password is not correct."
            ), 403

        if new != confirm:
            return render_template(
                "change_password.html", error="The two new passwords do not match."
            ), 400

        # reuses the signup rules, so the strength requirement cannot drift between the
        # two places a password is chosen
        error = validate_credentials(username, new)
        if error:
            return render_template("change_password.html", error=error), 400

        if new == current:
            return render_template(
                "change_password.html",
                error="The new password is the same as the current one.",
            ), 400

        change_password(username, new)
        audit("password.changed", account=username)

        # the session survives on purpose: the person changing it is the person using it,
        # and logging them out here would be a punishment for good behaviour
        flash("Your password has been changed.", "success")
        return redirect(url_for("main.home"))

    return render_template("change_password.html")


@auth_bp.route("/logout")
def logout():
    audit("logout")
    session.clear()  # removes all stored session data, so the user is no longer authenticated
    return redirect(url_for("auth.login"))
