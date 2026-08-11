import base64

import cv2
import numpy as np
import pytest

from attendance import create_app
from attendance.auth_db import create_user
from attendance.config import TestingConfig
from attendance.db import create_schema

# CSRF protection is left switched on for the tests rather than disabled by a config
# flag, so the real guard is what the tests run against. Seeding a known token into the
# session is what lets them post forms without scraping the token out of the HTML first.
CSRF_TOKEN = "test-csrf-token"


# a fixture is setup code pytest runs before a test that asks for it by name.
# living in conftest.py means every test file can use it without importing anything.
# this one points the app at a brand new database inside pytest's own temp folder:
# tmp_path    - a fresh empty directory pytest creates per test (and cleans up after)
# monkeypatch - sets the env var and automatically puts it back when the test ends
@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_attendance.db"
    monkeypatch.setenv("ATTENDANCE_DB", str(db_path))
    create_schema()  # build the students/attendance/users tables in that throwaway file
    return db_path


# before create_app() existed, the application was built at import time, so the tests had
# to set FLASK_SECRET_KEY as a module-level side effect before importing app.py and hope
# the import order held. now each test just builds its own app with TestingConfig.
@pytest.fixture
def app(temp_db):
    return create_app(TestingConfig)


@pytest.fixture
def client(app):
    # requests made through this client hit the throwaway database, never attendance.db,
    # because the app fixture depends on temp_db
    client = app.test_client()
    # session_transaction opens the session the way a view would, so the token is stored
    # exactly as csrf_token() would have stored it
    with client.session_transaction() as session:
        session["_csrf_token"] = CSRF_TOKEN
    return client


@pytest.fixture
def csrf():
    """The token to include in any POST made by a test."""
    return CSRF_TOKEN


@pytest.fixture
def frame():
    """A real, decodable JPEG.

    It has to be genuine: /recognize runs the bytes through cv2.imdecode and returns 400
    on anything that is not an image, so a made-up string would be rejected long before
    reaching the logic most of these tests are about.
    """
    image = np.zeros((48, 64, 3), dtype=np.uint8)
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    return "data:image/jpeg;base64," + base64.b64encode(buffer).decode()


@pytest.fixture
def recognises(monkeypatch):
    """Force the pipeline to 'see' a named person.

    Detection and encoding are stubbed because a generated black rectangle contains no
    face, and what these tests are about is what happens *after* someone is identified --
    the check-in rules, not the neural network. Recognition itself is covered by
    test_attendance.py::test_identify_face.
    """
    def _recognises(name, distance=0.3):
        import attendance.routes.recognition as route

        monkeypatch.setattr(
            route.face_recognition, "face_locations", lambda image: [(0, 10, 10, 0)]
        )
        monkeypatch.setattr(
            route.face_recognition, "face_encodings", lambda image, locations: [np.zeros(128)]
        )
        monkeypatch.setattr(route, "identify_face", lambda encoding, known: (name, distance))

    return _recognises


@pytest.fixture
def login(client):
    """Create an account and log in as it. Returns the login response.

    Defaults to an approved viewer, since that is what most tests want; pass
    approved=False to exercise the pending-account path.
    """
    def _login(username="viewer1", password="viewer-password", role="viewer", approved=True):
        create_user(username, password, role=role, is_approved=approved)
        return client.post(
            "/login",
            data={"username": username, "password": password, "_csrf_token": CSRF_TOKEN},
        )
    return _login
