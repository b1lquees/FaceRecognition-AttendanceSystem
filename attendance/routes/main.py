"""The landing page, and the endpoint a container orchestrator asks whether this is alive."""

import os
import sqlite3

from flask import Blueprint, current_app, jsonify, redirect, url_for

from ..db import connect, get_db_path

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def home():
    # "/" used to return the string "hello world". sending people to today's register
    # instead makes the root url useful: it is what someone actually wants to see, and
    # anyone not logged in is bounced to the login page by that route's own guard.
    return redirect(url_for("records.attendance_today"))


def database_is_writable():
    """Whether a check-in could actually be recorded, asked without recording one.

    A named function rather than two inline os.access calls so that a test can replace
    this one seam; patching os.access itself would switch it off for everything running
    in the process, which is a large blast radius for one branch.

    The directory is checked as well as the file: WAL creates attendance.db-wal beside
    the database, so a writable file in a directory that is not writable still cannot
    take a write.
    """
    database = get_db_path()
    return os.access(database, os.W_OK) and os.access(os.path.dirname(database) or ".", os.W_OK)


@main_bp.route("/healthz")
def healthz():
    """Whether this process can actually serve, not merely whether it is running.

    Deliberately unauthenticated, because the thing asking is Docker rather than a
    person: a check that needed a session could not be made by the daemon that has to
    make it. That is safe only because the answer is a fixed word -- no version, no
    counts, no exception text. Nothing here tells an unauthenticated caller anything they
    could not learn by loading the login page.

    What it adds over gunicorn's own supervision is narrow and worth being exact about.
    `--timeout 60` in the Dockerfile already kills and replaces a worker wedged mid
    request, so a hung worker is handled and was handled before this existed. What the
    arbiter cannot see is a worker that is perfectly responsive and has nothing to answer
    *with*: /data unmounted, the volume gone, or -- the likeliest of the three by a wide
    margin -- a stack brought up without anyone running init_db.py, where every page
    fails the moment it touches a table. From the outside that container is up, healthy
    and serving 500s.

    So the query looks for the schema rather than running SELECT 1. `SELECT 1` proves
    sqlite3 can open a file, which it can do by *creating* an empty one, and an empty
    database is precisely the failure this is here to name.
    """
    try:
        with connect() as connection:
            tables = connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='attendance'"
            ).fetchone()[0]
    except sqlite3.Error as error:
        # logged rather than returned: the operator reading `docker compose logs` is
        # entitled to the reason, an anonymous caller is not.
        current_app.logger.error("health check failed to query the database: %s", error)
        return jsonify({"status": "unavailable"}), 503

    if not tables:
        current_app.logger.error(
            "health check found no attendance table -- has scripts/init_db.py been run?"
        )
        return jsonify({"status": "unavailable"}), 503

    # Readable is not the same as usable, and this application exists to write. The case
    # is not hypothetical: restoring a backup with `docker compose cp` writes the file in
    # as root, the container runs as uid 10001, and what follows is a container that
    # serves every page perfectly and fails the first check-in of the day with "attempt
    # to write a readonly database". Every query above succeeds in that state.
    #
    # Tested by asking rather than by writing, because a probe that inserted a row would
    # be modifying the attendance record every thirty seconds to prove it could. The
    # directory counts too: WAL needs to create attendance.db-wal beside the database, so
    # a writable file in a read-only directory still cannot take a check-in.
    if not database_is_writable():
        current_app.logger.error(
            "health check found the database read-only -- check ownership of /data "
            "(the container runs as uid 10001; `docker cp` restores files as root)"
        )
        return jsonify({"status": "unavailable"}), 503

    return jsonify({"status": "ok"}), 200
