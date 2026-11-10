import time

from attendance.attendance_db import register_student
from attendance.auth_db import link_user_to_student, list_approved_users
from attendance.db import connect
from attendance.routes.recognition import CLASS_SCAN_KEY, SCAN_TO_FACE_SECONDS
from attendance.sessions_db import current_code, get_session, start_session

# client, login, csrf, frame, recognises and app come from conftest.py


def sign_in_as(login, username, student_name):
    """Log in as a student whose account is linked to their enrolled face."""
    login(username=username, password="a-good-password", role="viewer")
    user_id = next(row[0] for row in list_approved_users() if row[1] == username)
    link_user_to_student(user_id, register_student(student_name))


def scan(client, csrf, text):
    return client.post("/checkin/code", json={"code": text}, headers={"X-CSRF-Token": csrf})


def post_frame(client, csrf, frame):
    return client.post("/recognize", json={"image": frame}, headers={"X-CSRF-Token": csrf})


def qr_text(session_id):
    """Exactly what the classroom QR holds right now."""
    return f"FA:{session_id}:{current_code(get_session(session_id))}"


def class_of_last_record():
    conn = connect()
    row = conn.execute("SELECT session_id FROM attendance ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return row[0] if row else None


# --- phone (personal) mode during a class ----------------------------------------

def test_a_face_without_a_scan_is_refused(client, login, csrf, frame, recognises, app):
    app.config["KIOSK_MODE"] = False
    sign_in_as(login, "alice", "Alice")
    recognises("Alice")
    start_session("AI 301", minutes=60)

    assert post_frame(client, csrf, frame).get_json()["status"] == "code_required"
    assert class_of_last_record() is None  # nothing was recorded


def test_scan_then_face_checks_in_to_the_class(client, login, csrf, frame, recognises, app):
    app.config["KIOSK_MODE"] = False
    sign_in_as(login, "alice", "Alice")
    recognises("Alice")
    class_id = start_session("AI 301", minutes=60)

    assert scan(client, csrf, qr_text(class_id)).get_json()["status"] == "ok"
    assert post_frame(client, csrf, frame).get_json()["status"] == "checked_in"
    assert class_of_last_record() == class_id


def test_typing_the_six_digits_works_too(client, login, csrf, frame, recognises, app):
    app.config["KIOSK_MODE"] = False
    sign_in_as(login, "alice", "Alice")
    class_id = start_session("AI 301", minutes=60)

    code = current_code(get_session(class_id))
    assert scan(client, csrf, code).get_json()["status"] == "ok"


def test_a_wrong_code_is_refused(client, login, csrf, app):
    app.config["KIOSK_MODE"] = False
    sign_in_as(login, "alice", "Alice")
    class_id = start_session("AI 301", minutes=60)
    wrong = "111111" if current_code(get_session(class_id)) != "111111" else "222222"

    response = scan(client, csrf, wrong)

    assert response.status_code == 400
    assert response.get_json()["status"] == "bad_code"


def test_a_qr_from_another_class_is_refused(client, login, csrf, app):
    app.config["KIOSK_MODE"] = False
    sign_in_as(login, "alice", "Alice")
    old_class = start_session("Maths", minutes=60)
    old_qr = qr_text(old_class)
    start_session("Physics", minutes=60)  # the newest class is the running one

    assert scan(client, csrf, old_qr).get_json()["status"] == "wrong_class"


def test_scanning_with_no_class_running(client, login, csrf, app):
    app.config["KIOSK_MODE"] = False
    sign_in_as(login, "alice", "Alice")

    assert scan(client, csrf, "123456").get_json()["status"] == "no_class"


def test_a_scan_older_than_the_limit_has_expired(client, login, csrf, frame, recognises, app):
    app.config["KIOSK_MODE"] = False
    sign_in_as(login, "alice", "Alice")
    recognises("Alice")
    class_id = start_session("AI 301", minutes=60)
    # pretend the scan happened just over the limit ago
    with client.session_transaction() as cookie:
        cookie[CLASS_SCAN_KEY] = {"session_id": class_id,
                                  "at": time.time() - SCAN_TO_FACE_SECONDS - 1}

    assert post_frame(client, csrf, frame).get_json()["status"] == "code_expired"


def test_one_scan_is_one_check_in(client, login, csrf, frame, recognises, app):
    app.config["KIOSK_MODE"] = False
    sign_in_as(login, "alice", "Alice")
    recognises("Alice")
    class_id = start_session("AI 301", minutes=60)
    scan(client, csrf, qr_text(class_id))
    post_frame(client, csrf, frame)

    # the scan was used up: the next frame needs a fresh one
    assert post_frame(client, csrf, frame).get_json()["status"] == "code_required"


# --- kiosk mode during a class ----------------------------------------------------

def test_the_door_camera_needs_no_code_and_records_the_class(
    client, login, csrf, frame, recognises, app
):
    app.config["KIOSK_MODE"] = True
    login(username="operator", password="a-good-password", role="viewer")
    recognises("Alice")
    class_id = start_session("AI 301", minutes=60)

    assert post_frame(client, csrf, frame).get_json()["status"] == "checked_in"
    assert class_of_last_record() == class_id
