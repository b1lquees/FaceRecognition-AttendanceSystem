"""The health endpoint, which exists for a reader that cannot log in and cannot read prose.

The failure it is aimed at is a container that is up, responsive, and useless: the stack
was brought up but init_db.py never run, so every page 500s on the first query while
`docker ps` reports everything fine. Gunicorn cannot see that -- the workers are healthy,
they simply have nothing to answer with -- so these tests care most about the empty
database case, which is the one a passing `SELECT 1` would have hidden.
"""

import sqlite3

import pytest

from attendance import create_app
from attendance.config import TestingConfig


def test_healthz_reports_ok_on_a_real_database(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_healthz_needs_no_session(client):
    """Docker is the caller, and it cannot log in.

    The client fixture is not logged in, so a 200 here is the whole assertion: any auth
    guard on this route would redirect to /login instead, and the container would be
    marked unhealthy forever.
    """
    response = client.get("/healthz")

    assert response.status_code == 200, "the daemon making this call has no session to offer"


def test_an_empty_database_is_unhealthy(tmp_path, monkeypatch):
    """The case this endpoint exists for: running, serving, and never initialised.

    No create_schema() call, so the file has no tables -- exactly the state of a volume
    that came up without init_db.py. sqlite3 opens it happily, which is why `SELECT 1`
    would return 200 and call this container healthy.
    """
    monkeypatch.setenv("ATTENDANCE_DB", str(tmp_path / "never-initialised.db"))
    client = create_app(TestingConfig).test_client()

    response = client.get("/healthz")

    assert response.status_code == 503
    assert response.get_json() == {"status": "unavailable"}


def test_an_unreachable_database_is_unhealthy(monkeypatch, client):
    """A volume that vanished underneath a running worker."""
    def refuse():
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr("attendance.routes.main.connect", refuse)

    response = client.get("/healthz")

    assert response.status_code == 503


def test_a_read_only_database_is_unhealthy(monkeypatch, client):
    """Readable but not writable, which is what a restored backup looks like.

    `docker compose cp` writes the file in as root while the container runs as uid 10001,
    so every query the checks above make succeeds and the first check-in of the day fails.
    Simulated by refusing at the module's own seam rather than by chmod, because a
    read-only bit does not mean the same thing on Windows as it does in the container,
    and the point here is the branch rather than the filesystem.
    """
    monkeypatch.setattr("attendance.routes.main.database_is_writable", lambda: False)

    response = client.get("/healthz")

    assert response.status_code == 503, "a database it cannot write to is not a healthy one"
    assert response.get_json() == {"status": "unavailable"}


@pytest.mark.parametrize("scenario", ["healthy", "empty"])
def test_healthz_says_nothing_a_stranger_should_not_hear(scenario, tmp_path, monkeypatch, client):
    """Unauthenticated, so the body has to stay a fixed word in both directions.

    The reason for the parametrize is that the failing branch is the tempting place to be
    helpful -- returning the sqlite error, the path it tried, or the missing table name --
    and every one of those hands an anonymous caller a piece of the filesystem layout.
    """
    if scenario == "empty":
        monkeypatch.setenv("ATTENDANCE_DB", str(tmp_path / "never-initialised.db"))
        client = create_app(TestingConfig).test_client()

    response = client.get("/healthz")
    body = response.get_data(as_text=True)

    assert set(response.get_json()) == {"status"}
    for leak in ("Traceback", "sqlite", ".db", "/data", "attendance"):
        assert leak not in body, f"the health body leaks {leak!r} to an anonymous caller"
