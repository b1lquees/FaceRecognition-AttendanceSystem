import pytest

from attendance.attendance_db import check_in
from attendance.auth_db import create_user, list_pending_users, register_pending_user

# the client / login / csrf fixtures come from conftest.py

LOGIN_PATH = "/login"


# --- authentication gates -------------------------------------------------------

# /recognize is the only route that writes to the database. it used to be reachable
# without a session at all, which meant anyone who could reach the server could mark
# attendance. this test is the regression guard for that.
@pytest.mark.parametrize(
    "path",
    [
        "/camera",
        "/attendance/today",
        "/attendance/all",
        "/attendance/export",
        "/admin/users",
    ],
)
def test_protected_pages_redirect_anonymous_users_to_login(client, path):
    response = client.get(path)

    assert response.status_code == 302
    assert LOGIN_PATH in response.headers["Location"]


# /recognize needs its own test because it is a POST, and the CSRF hook runs before the
# route's @login_required. So an anonymous POST is refused twice over, and which refusal
# you see depends on whether a token was sent. Both are asserted here rather than
# pretending only one of them exists.
def test_anonymous_post_to_recognize_is_refused_by_csrf(client):
    assert client.post("/recognize", json={}).status_code == 400


def test_anonymous_post_to_recognize_is_refused_by_login_even_with_a_token(client, csrf):
    # the client fixture seeds a valid token without logging in, so this gets past CSRF
    # and reaches @login_required -- which is the guard actually being tested
    response = client.post("/recognize", json={}, headers={"X-CSRF-Token": csrf})

    assert response.status_code == 302
    assert LOGIN_PATH in response.headers["Location"]


def test_viewer_cannot_export_csv(client, login):
    login(role="viewer")

    assert client.get("/attendance/export").status_code == 403


def test_viewer_cannot_reach_the_admin_page(client, login):
    login(role="viewer")

    assert client.get("/admin/users").status_code == 403


def test_admin_can_export_csv(client, login):
    login(role="admin")

    response = client.get("/attendance/export")

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert b"Name,Date,Time In,Time Out,Confidence" in response.data


def test_logout_clears_the_session(client, login):
    login()
    client.get("/logout")

    assert client.get("/attendance/today").status_code == 302


def test_login_rejects_a_wrong_password(client, csrf):
    create_user("viewer1", "the-real-password")

    response = client.post(
        LOGIN_PATH,
        data={"username": "viewer1", "password": "not-the-password", "_csrf_token": csrf},
    )

    assert response.status_code == 401
    assert b"Invalid username or password" in response.data


# a pending account gets a different message from a wrong password, because the password
# was in fact correct -- and crucially, still no session
def test_pending_account_cannot_log_in(client, login):
    response = login(approved=False)

    assert response.status_code == 403
    assert b"waiting for an administrator" in response.data
    assert client.get("/attendance/today").status_code == 302


# --- signup ---------------------------------------------------------------------

def test_signup_page_is_reachable_without_logging_in(client):
    assert client.get("/signup").status_code == 200


def test_signup_creates_a_pending_account(client, csrf):
    response = client.post(
        "/signup",
        data={
            "username": "newperson",
            "password": "password123",
            "confirm_password": "password123",
            "_csrf_token": csrf,
        },
    )

    assert response.status_code == 200
    assert b"waiting for an administrator" in response.data
    assert [u[1] for u in list_pending_users()] == ["newperson"]


@pytest.mark.parametrize(
    "data, expected_status, expected_text",
    [
        ({"username": "ab", "password": "password123", "confirm_password": "password123"},
         400, b"3-32 characters"),
        ({"username": "newperson", "password": "short", "confirm_password": "short"},
         400, b"at least 8 characters"),
        ({"username": "newperson", "password": "password123", "confirm_password": "different1"},
         400, b"do not match"),
    ],
)
def test_signup_rejects_bad_input(client, csrf, data, expected_status, expected_text):
    response = client.post("/signup", data={**data, "_csrf_token": csrf})

    assert response.status_code == expected_status
    assert expected_text in response.data
    assert list_pending_users() == []


def test_signup_refuses_a_taken_username(client, csrf):
    create_user("taken", "password123")

    response = client.post(
        "/signup",
        data={
            "username": "taken",
            "password": "password123",
            "confirm_password": "password123",
            "_csrf_token": csrf,
        },
    )

    assert response.status_code == 409
    assert b"already taken" in response.data


# --- admin approval -------------------------------------------------------------

def test_admin_can_approve_a_pending_account(client, login, csrf):
    register_pending_user("newperson", "password123")
    user_id = list_pending_users()[0][0]
    login(username="admin1", password="admin-password", role="admin")

    response = client.post(f"/admin/users/{user_id}/approve", data={"_csrf_token": csrf})

    assert response.status_code == 302
    assert list_pending_users() == []


def test_admin_can_reject_a_pending_account(client, login, csrf):
    register_pending_user("spammer", "password123")
    user_id = list_pending_users()[0][0]
    login(username="admin1", password="admin-password", role="admin")

    client.post(f"/admin/users/{user_id}/reject", data={"_csrf_token": csrf})

    assert list_pending_users() == []


def test_viewer_cannot_approve_anyone(client, login, csrf):
    register_pending_user("newperson", "password123")
    user_id = list_pending_users()[0][0]
    login(role="viewer")

    response = client.post(f"/admin/users/{user_id}/approve", data={"_csrf_token": csrf})

    assert response.status_code == 403
    assert len(list_pending_users()) == 1  # still pending


# --- CSRF -----------------------------------------------------------------------

# without a token, a page on another site could post to these endpoints using the
# browser's session cookie. the admin approve case is the one that matters most.
@pytest.mark.parametrize(
    "path", ["/login", "/signup", "/admin/users/1/approve", "/admin/users/1/reject"]
)
def test_post_without_a_csrf_token_is_rejected(client, login, path):
    login(username="admin1", password="admin-password", role="admin")

    assert client.post(path, data={"username": "x", "password": "y"}).status_code == 400


def test_post_with_the_wrong_csrf_token_is_rejected(client, login):
    login(username="admin1", password="admin-password", role="admin")

    response = client.post("/admin/users/1/approve", data={"_csrf_token": "not-the-token"})

    assert response.status_code == 400


def test_recognize_accepts_the_token_as_a_header(client, login, csrf):
    # /recognize posts JSON, which has no form fields, so the token travels as a header.
    # a 400 about the image (not about CSRF) proves the header was accepted.
    login()

    response = client.post("/recognize", json={}, headers={"X-CSRF-Token": csrf})

    assert response.status_code == 400
    assert "image" in response.get_json()["error"]


# --- page rendering -------------------------------------------------------------

# these render the real templates, which is the only way a wrong url_for() endpoint name
# gets caught: Jinja raises BuildError at render time, not at import time.
@pytest.mark.parametrize("path", ["/attendance/today", "/attendance/all", "/camera"])
def test_pages_render_for_a_logged_in_user(client, login, path):
    login()

    assert client.get(path).status_code == 200


def test_admin_page_renders(client, login):
    register_pending_user("newperson", "password123")
    login(username="admin1", password="admin-password", role="admin")

    response = client.get("/admin/users")

    assert response.status_code == 200
    assert b"newperson" in response.data


# the Accounts link is admin-only, so it must not appear in a viewer's nav
def test_admin_link_is_hidden_from_viewers(client, login):
    login(role="viewer")

    assert b"/admin/users" not in client.get("/attendance/today").data


def test_home_redirects_to_todays_register(client, login):
    login()

    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/attendance/today")


# a marked record should actually appear on the page, not just render an empty table
def test_a_marked_record_appears_on_the_today_page(client, login):
    login()
    check_in("Marked Person", 0.42)

    assert b"Marked Person" in client.get("/attendance/today").data


# --- /recognize input handling --------------------------------------------------

# every one of these used to raise an unhandled exception and return a 500 with a
# stack trace; they should all be clean 400s
@pytest.mark.parametrize(
    "payload",
    [
        {},                                  # no "image" key at all
        {"image": 12345},                    # not a string
        {"image": "data:image/jpeg;base64,not-valid-base64!!"},
        {"image": "data:image/jpeg;base64,aGVsbG8gd29ybGQ="},  # valid base64, not an image
    ],
)
def test_recognize_rejects_malformed_input(client, login, csrf, payload):
    login()

    response = client.post("/recognize", json=payload, headers={"X-CSRF-Token": csrf})

    assert response.status_code == 400
    assert "error" in response.get_json()
