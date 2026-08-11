"""A minimal CSRF guard.

The problem it solves: the browser attaches your session cookie to a request whether or
not you meant to send it. So a page on another site can contain a hidden form that posts
to /admin/users/3/approve, and if you are logged in as an admin and visit that page, your
browser sends the request with your cookie and the approval goes through.

The fix is to require a value the attacker's page cannot know: a random token stored in
the session and echoed back in a hidden form field. Their form can't read your session,
so it can't include the token, so the POST is rejected.

Flask-WTF does this and more, but it is a dependency and this is about twenty lines.
"""

import secrets

from flask import abort, jsonify, request, session

TOKEN_KEY = "_csrf_token"


def csrf_token():
    """The token for this session, creating one on first use.

    Registered as a Jinja global in create_app(), so templates call it directly:
        <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
    """
    if TOKEN_KEY not in session:
        session[TOKEN_KEY] = secrets.token_urlsafe(32)
    return session[TOKEN_KEY]


def verify_csrf():
    """Reject any POST whose form token doesn't match the session's. Registered as a
    before_request hook, so a new form cannot forget to opt in -- it is protected unless
    someone deliberately exempts it."""
    if request.method != "POST":
        return

    # /recognize posts JSON rather than a form, so it has no form fields to carry the
    # token. it sends the same value as a header instead (see camera.html).
    sent = request.form.get(TOKEN_KEY) or request.headers.get("X-CSRF-Token")
    expected = session.get(TOKEN_KEY)

    # compare_digest rather than == so the comparison takes the same time whether the
    # first character differs or only the last one
    if expected and sent and secrets.compare_digest(sent, expected):
        return  # ok -- returning None lets the request continue to its route

    # In practice the commonest cause of this on a logged-in page is not an attack but an
    # expired session: the session is gone, so the token stored in it is gone too. The
    # camera page polls /recognize forever and needs to tell that apart from a bad image,
    # so JSON callers get a machine-readable flag instead of an HTML error page.
    if request.is_json:
        return jsonify({
            "error": "Session expired or invalid security token. Please reload the page.",
            "csrf": True,
        }), 400

    abort(400, description="Invalid or missing CSRF token. Please reload and retry.")
