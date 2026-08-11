"""The landing page."""

from flask import Blueprint, redirect, url_for

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def home():
    # "/" used to return the string "hello world". sending people to today's register
    # instead makes the root url useful: it is what someone actually wants to see, and
    # anyone not logged in is bounced to the login page by that route's own guard.
    return redirect(url_for("records.attendance_today"))
