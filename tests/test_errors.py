import io

from tests.test_enrolment import storage  # noqa: F401

import pytest

from attendance.config import Config
from attendance.enrolment import MAX_PHOTO_BYTES, MAX_PHOTOS, MAX_REQUEST_BYTES


# --- the limits have to agree with each other -----------------------------------

# The bug this file exists for. The app-wide cap is sized for a webcam frame (8 MB) while
# the enrolment form invites ten photos of 5 MB. The form was promising 50 MB through an
# 8 MB door, and three ordinary phone photos were enough to hit it.
def test_the_enrolment_cap_covers_what_the_form_invites():
    assert MAX_REQUEST_BYTES >= MAX_PHOTOS * MAX_PHOTO_BYTES


# the app-wide cap stays tight on purpose: it protects /recognize, which is posted to on
# a loop and has no business receiving anything larger than a frame
def test_the_app_wide_cap_is_still_small():
    assert Config.MAX_CONTENT_LENGTH == 8 * 1024 * 1024
    assert Config.MAX_CONTENT_LENGTH < MAX_REQUEST_BYTES


def test_an_upload_larger_than_the_app_wide_cap_is_accepted_by_enrolment(
    client, login, csrf, storage, temp_db
):
    """A payload that the global limit would refuse must still reach enrolment.

    It is rejected once it gets there -- the photo is not a real image -- but a 400 about
    the image proves the request was read, which is the whole point. Before the per-route
    limit this returned 413 without the view ever running.
    """
    login(username="admin1", password="admin-password", role="admin")
    oversized = b"\xff\xd8\xff" + b"x" * (Config.MAX_CONTENT_LENGTH + 1024)

    response = client.post(
        "/admin/enrol",
        data={"name": "Alice", "_csrf_token": csrf,
              "photos": (io.BytesIO(oversized), "big.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert b"No usable photos" in response.data


def test_an_upload_beyond_even_the_enrolment_cap_is_refused(
    client, login, csrf, storage, temp_db
):
    login(username="admin1", password="admin-password", role="admin")
    far_too_big = b"x" * (MAX_REQUEST_BYTES + 1024)

    response = client.post(
        "/admin/enrol",
        data={"name": "Alice", "_csrf_token": csrf,
              "photos": (io.BytesIO(far_too_big), "huge.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 413


# --- the pages themselves --------------------------------------------------------

def test_a_missing_page_is_styled_not_bare(client):
    response = client.get("/no-such-page")

    assert response.status_code == 404
    # rendered through base.html rather than Werkzeug's default page
    assert b"Page not found" in response.data
    assert b"style.css" in response.data


def test_the_too_large_page_says_what_the_limits_are(client, login, csrf, storage, temp_db):
    login(username="admin1", password="admin-password", role="admin")

    response = client.post(
        "/admin/enrol",
        data={"name": "Alice", "_csrf_token": csrf,
              "photos": (io.BytesIO(b"x" * (MAX_REQUEST_BYTES + 1024)), "huge.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 413
    # a number the person can act on, rather than "Request Entity Too Large"
    assert str(MAX_PHOTOS).encode() in response.data
    assert b"MB each" in response.data


# an error page that offers no way onward is a dead end
def test_the_error_page_offers_a_way_back(client, login):
    login()

    body = client.get("/no-such-page").get_data(as_text=True)

    assert "/attendance/today" in body


def test_a_logged_out_visitor_is_pointed_at_sign_in(client):
    body = client.get("/no-such-page").get_data(as_text=True)

    assert "/login" in body


# --- machine callers get machine-readable errors ---------------------------------

# /recognize posts JSON on a loop and parses every reply. an HTML error page makes it
# fail at JSON.parse with something unrelated to what actually went wrong.
def test_a_json_caller_gets_json_not_html(client):
    # a GET, not a POST: the CSRF hook runs before routing raises the 404, so an
    # unauthenticated POST to a missing url is correctly refused as a bad token first
    response = client.get("/no-such-page", headers={"Accept": "application/json"})

    assert response.status_code == 404
    assert response.mimetype == "application/json"
    assert "error" in response.get_json()


def test_a_json_caller_gets_json_for_oversized_uploads(client, login, csrf):
    login(username="admin1", password="admin-password", role="admin")

    response = client.post(
        "/recognize",
        data=b"x" * (Config.MAX_CONTENT_LENGTH + 1024),
        content_type="application/json",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 413
    assert response.mimetype == "application/json"
