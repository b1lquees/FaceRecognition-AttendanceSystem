"""Logging, and in particular the audit trail.

Until now this application had access control with no record of it being used. Accounts
get approved, rejected and linked to faces; people get enrolled; spoofs get refused --
and none of it left a trace. "Who let this person in?" had no answer, which for an
attendance system is the question most likely to be asked.

Two separate things live here. configure_logging() decides where log output goes at all,
since Flask's default handler only prints in debug mode and a production server would
otherwise be silent. audit() records the specific actions worth being able to reconstruct
afterwards.

What is deliberately never logged: passwords, session tokens, CSRF tokens, and face
encodings. A log that leaks the thing it is protecting is worse than no log, and logs get
copied to places the database never goes.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler

from flask import current_app, has_request_context, request, session

FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

# 1 MB each, five kept. Enough to cover a while without a stray loop filling the disk,
# which is a real failure mode for a per-frame endpoint.
MAX_LOG_BYTES = 1024 * 1024
LOG_BACKUPS = 5


def configure_logging(app):
    """Send log output somewhere it will actually be seen.

    Flask only attaches a handler when debugging, so without this a production server
    logs nothing at all -- including the 500 handler's tracebacks.
    """
    level = getattr(logging, app.config["LOG_LEVEL"].upper(), logging.INFO)
    formatter = logging.Formatter(FORMAT)

    # stderr, because a process manager, container runtime or systemd will collect it.
    # Writing only to a file assumes something is there to read the file.
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)

    app.logger.handlers.clear()  # or Flask's own handler duplicates every line
    app.logger.addHandler(stream)
    app.logger.setLevel(level)

    log_file = app.config.get("LOG_FILE")
    if log_file:
        rotating = RotatingFileHandler(
            log_file, maxBytes=MAX_LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
        )
        rotating.setFormatter(formatter)
        app.logger.addHandler(rotating)

    # werkzeug's request log is separate and noisy at INFO; it repeats what the audit
    # trail already says, one line per frame posted to /recognize
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


def who():
    """The signed-in username, or a marker for nobody.

    Falls back rather than raising: audit() is called from error paths too, and a logging
    call that throws would turn a handled problem into an unhandled one.
    """
    if not has_request_context():
        return "-"
    return session.get("username") or "anonymous"


def where():
    if not has_request_context():
        return "-"
    return request.remote_addr or "-"


def audit(action, **details):
    """Record a security-relevant action.

    One line per action, in a shape that can be grepped: the action name first, then the
    actor, then whatever identifies the thing acted on.

        audit("account.approved", target="alice")
        -> audit action=account.approved by=admin1 ip=127.0.0.1 target=alice

    Callers pass identifiers, never secrets. There is no filtering here to enforce that,
    because a filter would imply it is safe to pass one.
    """
    extra = " ".join(f"{key}={value}" for key, value in details.items())
    current_app.logger.info(
        "audit action=%s by=%s ip=%s %s", action, who(), where(), extra
    )
