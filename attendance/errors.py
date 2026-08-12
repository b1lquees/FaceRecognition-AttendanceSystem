"""What the user sees when something goes wrong.

Without these, an error drops out of the application's design into a bare Werkzeug page
that says "Request Entity Too Large" and nothing else -- no nav, no explanation, no way
back. That is how the upload size limit was discovered: the page said 5 MB per photo, the
server disagreed, and the only feedback was a white page with three words on it.
"""

from flask import render_template, request

from .enrolment import MAX_PHOTO_BYTES, MAX_PHOTOS


def wants_json():
    """Whether this caller is a script rather than a browser.

    /recognize posts JSON on a loop and parses every reply. Handing it an HTML error page
    makes it fail at JSON.parse with something unrelated to what actually went wrong.
    """
    return request.is_json or request.accept_mimetypes.best == "application/json"


def register_error_handlers(app):
    @app.errorhandler(404)
    def not_found(error):
        if wants_json():
            return {"error": "not found"}, 404
        return render_template(
            "error.html",
            code=404,
            title="Page not found",
            message=(
                "That address does not exist. It may have been mistyped, or the link "
                "that brought you here may be out of date."
            ),
        ), 404

    @app.errorhandler(413)
    def too_large(error):
        megabytes = MAX_PHOTO_BYTES // (1024 * 1024)
        if wants_json():
            return {"error": "the uploaded data was too large"}, 413
        return render_template(
            "error.html",
            code=413,
            title="Upload too large",
            message=(
                f"That upload exceeded the size limit. Enrolment accepts up to "
                f"{MAX_PHOTOS} photos of {megabytes} MB each. Try fewer photos at a "
                "time, or smaller ones."
            ),
        ), 413

    # Flask only reaches this when debug is off; with the debugger on it re-raises so you
    # get the traceback, which is what you want while developing.
    @app.errorhandler(500)
    def server_error(error):
        # logged with the traceback so there is a record, since the page deliberately
        # does not show one -- a stack trace tells an attacker about your internals
        app.logger.exception("unhandled error serving %s", request.path)
        if wants_json():
            return {"error": "something went wrong"}, 500
        return render_template(
            "error.html",
            code=500,
            title="Something went wrong",
            message=(
                "The server hit an unexpected problem. It has been logged. Try again, "
                "and tell an administrator if it keeps happening."
            ),
        ), 500
