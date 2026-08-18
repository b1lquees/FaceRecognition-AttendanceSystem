"""The attendance system, built as a Flask application factory.

app.py used to create the application at import time: the Flask object, the secret key,
and the face encodings were all set up the moment anything imported the module. That had
three consequences worth naming, because they are the reason this file exists:

  - there was exactly one configuration, decided by the environment at import time
  - the tests had to set environment variables *before* importing app, which is fragile
    and easy to get wrong
  - importing the app for any reason read files from disk as a side effect

create_app() defers all of that until someone actually asks for an application, so the
test suite can build a throwaway one with its own config and its own database.
"""

from flask import Flask, request

from .config import get_config


def create_app(config=None):
    """Build a configured Flask application.

    config: a class from attendance.config. Defaults to whatever get_config() picks
    from the environment, which is ProductionConfig when FLASK_ENV=production.
    """
    config = config or get_config()

    # Flask(__name__) with __name__ == "attendance" means Flask looks for templates and
    # static files inside this package, which is where they now live
    app = Flask(__name__)
    app.config.from_object(config)

    # secret_key() is a method rather than a class attribute so that reading the
    # environment (and, in development, writing the cached key file) happens here when
    # an app is built, not when attendance.config is first imported
    app.config["SECRET_KEY"] = config.secret_key()

    # before anything else that might want to log, and before the first request:
    # Flask only attaches its own handler in debug mode, so a production server
    # would otherwise be silent -- including the 500 handler's tracebacks.
    from .audit import configure_logging

    configure_logging(app)

    # every POST is CSRF-checked by default. doing it as a before_request hook rather
    # than a decorator means a newly added form is protected automatically instead of
    # being protected only if its author remembered to opt in.
    # Raise the upload limit for enrolment BEFORE the CSRF hook runs, and it has to be
    # in that order. verify_csrf reads request.form, and reading the form is what pulls
    # the body in and triggers the size check -- so a limit set inside the view arrives
    # too late and a large upload 413s before any view executes. before_request handlers
    # run in registration order, which is what makes this work.
    from .enrolment import MAX_REQUEST_BYTES
    from .security import csrf_token, verify_csrf

    @app.before_request
    def allow_larger_enrolment_uploads():
        if request.endpoint == "admin.enrol_person":
            request.max_content_length = MAX_REQUEST_BYTES

    app.before_request(verify_csrf)
    app.jinja_env.globals["csrf_token"] = csrf_token  # so templates can call csrf_token()

    # display helpers, so the templates can write {{ t_in | short_time }} instead of
    # doing string slicing and arithmetic inline
    from functools import partial

    from .formatting import duration, match_bands, match_quality, match_strength, short_time
    from .recognition import TOLERANCE

    app.jinja_env.filters["short_time"] = short_time
    app.jinja_env.globals["duration"] = duration

    # The match column is drawn relative to the recognition cutoff, so the pages have to
    # know what it currently is. Bound here rather than read in the template: a template
    # that imports a constant to do arithmetic with is how the old hardcoded 0.6 ended up
    # in two files and outlived the value it was copied from.
    strong, fair = match_bands(TOLERANCE)
    app.jinja_env.globals.update(
        tolerance=TOLERANCE,
        match_strong=strong,
        match_fair=fair,
        match_quality=partial(match_quality, tolerance=TOLERANCE),
        match_strength=partial(match_strength, tolerance=TOLERANCE),
    )

    # without these an error falls out of the site's design into a bare Werkzeug page
    from .errors import register_error_handlers

    register_error_handlers(app)

    # imported inside the function rather than at module level: the route modules import
    # from this package, so importing them at the top would be a circular import
    from .routes import admin_bp, auth_bp, main_bp, recognition_bp, records_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(records_bp)
    app.register_blueprint(recognition_bp)
    app.register_blueprint(admin_bp)

    return app
