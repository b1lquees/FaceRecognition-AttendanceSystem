"""Signing up, logging in and logging out."""

import sqlite3

from flask import Blueprint, redirect, render_template, request, session, url_for

from ..auth_db import register_pending_user, validate_credentials, verify_user
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
            return redirect(url_for("main.home"))

        if result.status == "pending":
            # the password was correct, so saying "invalid credentials" here would send
            # someone off resetting a password that works fine. no session is started.
            return render_template(
                "login.html",
                error="Your account is waiting for an administrator to approve it.",
                pending=True,
            ), 403

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

        # deliberately no session here: the whole point is that an admin decides
        return render_template("signup_submitted.html", username=username)

    return render_template("signup.html")


@auth_bp.route("/logout")
def logout():
    session.clear()  # removes all stored session data, so the user is no longer authenticated
    return redirect(url_for("auth.login"))
