import pytest

from attendance.attendance_db import get_todays_attendance, register_student
from attendance.auth_db import (
    create_user,
    get_linked_student_id,
    link_user_to_student,
    list_approved_users,
)

# the frame and recognises fixtures live in conftest.py, shared with test_checkout.py


def post_frame(client, csrf, frame):
    return client.post("/recognize", json={"image": frame}, headers={"X-CSRF-Token": csrf})


def user_id_for(username):
    return next(row[0] for row in list_approved_users() if row[1] == username)


def sign_in_as(client, login, username, student_name=None):
    """Log in, and optionally link the account to an enrolled person."""
    login(username=username, password="a-good-password", role="viewer")
    if student_name:
        link_user_to_student(user_id_for(username), register_student(student_name))


# --- kiosk mode -----------------------------------------------------------------

# the shared-door-camera model: the signed-in account is the operator running the
# station, and has nothing to do with who gets recorded
def test_kiosk_mode_marks_whoever_is_recognised(client, login, csrf, frame, recognises, app):
    app.config["KIOSK_MODE"] = True
    sign_in_as(client, login, "operator")
    recognises("Alice")

    data = post_frame(client, csrf, frame).get_json()

    assert data["status"] == "checked_in"
    assert data["results"][0]["name"] == "Alice"
    assert len(get_todays_attendance()) == 1


def test_kiosk_mode_does_not_care_that_the_account_is_unlinked(
    client, login, csrf, frame, recognises, app
):
    app.config["KIOSK_MODE"] = True
    sign_in_as(client, login, "operator")  # deliberately not linked to anyone
    recognises("Alice")

    assert post_frame(client, csrf, frame).get_json()["status"] == "checked_in"


# --- personal mode --------------------------------------------------------------

def test_personal_mode_marks_the_signed_in_person(
    client, login, csrf, frame, recognises, app
):
    app.config["KIOSK_MODE"] = False
    sign_in_as(client, login, "alice", student_name="Alice")
    recognises("Alice")

    data = post_frame(client, csrf, frame).get_json()

    assert data["status"] == "checked_in"
    assert data["results"][0]["name"] == "Alice"


# this is the test that makes "only the registered person can check in" true rather than
# aspirational. before the binding existed, this marked Alice present.
def test_personal_mode_refuses_somebody_elses_face(
    client, login, csrf, frame, recognises, app
):
    app.config["KIOSK_MODE"] = False
    register_student("Alice")
    sign_in_as(client, login, "bob", student_name="Bob")
    recognises("Alice")

    data = post_frame(client, csrf, frame).get_json()

    assert data["status"] == "mismatch"
    assert data["results"][0]["marked"] is False
    assert data["results"][0]["name"] is None  # withheld, deliberately
    assert get_todays_attendance() == []  # and nothing was recorded for anyone


# telling Bob that Alice was in front of the camera would leak her presence to anyone
# able to point a webcam at her
def test_a_refused_check_in_does_not_reveal_who_was_seen(
    client, login, csrf, frame, recognises, app
):
    app.config["KIOSK_MODE"] = False
    register_student("Alice")
    sign_in_as(client, login, "bob", student_name="Bob")
    recognises("Alice")

    body = post_frame(client, csrf, frame).get_data(as_text=True)

    assert "Alice" not in body


def test_personal_mode_refuses_an_unlinked_account(
    client, login, csrf, frame, recognises, app
):
    app.config["KIOSK_MODE"] = False
    register_student("Alice")
    sign_in_as(client, login, "alice")  # approved, but never linked to a person
    recognises("Alice")

    data = post_frame(client, csrf, frame).get_json()

    assert data["status"] == "not_linked"
    assert get_todays_attendance() == []


# the same request, the same people, opposite outcomes -- the setting is doing the work
def test_the_setting_is_what_changes_the_outcome(
    client, login, csrf, frame, recognises, app
):
    register_student("Alice")
    sign_in_as(client, login, "bob", student_name="Bob")
    recognises("Alice")

    app.config["KIOSK_MODE"] = False
    assert post_frame(client, csrf, frame).get_json()["status"] == "mismatch"

    app.config["KIOSK_MODE"] = True
    assert post_frame(client, csrf, frame).get_json()["status"] == "checked_in"


# --- unaffected outcomes --------------------------------------------------------

@pytest.mark.parametrize("kiosk", [True, False])
def test_an_unrecognised_face_is_reported_the_same_in_both_modes(
    client, login, csrf, frame, recognises, app, kiosk
):
    app.config["KIOSK_MODE"] = kiosk
    sign_in_as(client, login, "alice", student_name="Alice")
    recognises("Unknown", distance=0.9)

    data = post_frame(client, csrf, frame).get_json()

    assert data["status"] == "unknown"
    assert data["results"][0]["marked"] is False


def test_no_face_in_frame(client, login, csrf, frame, monkeypatch, app):
    import attendance.routes.recognition as route

    monkeypatch.setattr(route.face_recognition, "face_locations", lambda image: [])
    monkeypatch.setattr(route.face_recognition, "face_encodings", lambda image, locations: [])
    sign_in_as(client, login, "alice", student_name="Alice")

    assert post_frame(client, csrf, frame).get_json()["status"] == "no_face"


# --- linking ---------------------------------------------------------------------

def test_linking_and_reading_it_back(temp_db):
    create_user("alice", "a-good-password")
    student_id = register_student("Alice")

    assert link_user_to_student(user_id_for("alice"), student_id) is True
    assert get_linked_student_id("alice") == student_id


def test_an_account_starts_unlinked(temp_db):
    create_user("alice", "a-good-password")

    assert get_linked_student_id("alice") is None


# an admin who links the wrong person needs a way back that is not editing the database
def test_unlinking(temp_db):
    create_user("alice", "a-good-password")
    link_user_to_student(user_id_for("alice"), register_student("Alice"))

    assert link_user_to_student(user_id_for("alice"), None) is True
    assert get_linked_student_id("alice") is None


def test_linking_an_unknown_account_changes_nothing(temp_db):
    assert link_user_to_student(9999, 1) is False


def test_get_linked_student_id_for_an_unknown_account(temp_db):
    assert get_linked_student_id("nobody") is None


# --- the admin screen -------------------------------------------------------------

def test_admin_can_link_an_account_from_the_page(client, login, csrf):
    create_user("alice", "a-good-password")
    student_id = register_student("Alice")
    alice_id = user_id_for("alice")
    login(username="admin1", password="admin-password", role="admin")

    response = client.post(
        f"/admin/users/{alice_id}/link",
        data={"student_id": str(student_id), "_csrf_token": csrf},
    )

    assert response.status_code == 302
    assert get_linked_student_id("alice") == student_id


def test_a_viewer_cannot_link_accounts(client, login, csrf):
    create_user("alice", "a-good-password")
    student_id = register_student("Alice")
    alice_id = user_id_for("alice")
    login(role="viewer")

    response = client.post(
        f"/admin/users/{alice_id}/link",
        data={"student_id": str(student_id), "_csrf_token": csrf},
    )

    assert response.status_code == 403
    assert get_linked_student_id("alice") is None


def test_the_admin_page_shows_the_linking_control(client, login):
    create_user("alice", "a-good-password")
    register_student("Alice")
    login(username="admin1", password="admin-password", role="admin")

    body = client.get("/admin/users").get_data(as_text=True)

    assert "not linked" in body
    assert "Alice" in body
