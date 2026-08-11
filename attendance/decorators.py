"""Route guards, written once and reused instead of repeating the check in every view."""

from functools import wraps

from flask import redirect, session, url_for

# @wraps copies the wrapped function's __name__ and __doc__ onto the inner function.
# Without it every decorated route would appear to Flask as a function literally named
# "decorated_function", and registering more than one would collide -- Flask uses the
# function name as the endpoint name, which is what url_for() looks up.

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):  # *args/**kwargs so it works with any route signature
        if "username" not in session:
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("auth.login"))
        if session.get("role") != "admin":
            return "Access denied: Admins only", 403
        return f(*args, **kwargs)
    return decorated_function
