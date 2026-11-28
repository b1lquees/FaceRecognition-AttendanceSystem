from attendance.sessions_db import current_session, get_session, verify_code

# client, login and csrf come from conftest.py


def start(client, csrf, course="AI 301", minutes="60"):
    return client.post(
        "/classes/start", data={"course": course, "minutes": minutes, "_csrf_token": csrf}
    )


def test_only_admins_can_open_the_classes_page(client, login):
    login()  # a viewer
    assert client.get("/classes/").status_code == 403


def test_starting_a_class_opens_its_check_in_screen(client, login, csrf):
    login(username="teacher", role="admin")

    response = start(client, csrf)

    assert response.status_code == 302
    session = current_session()
    assert session["course"] == "AI 301"
    assert session["created_by"] == "teacher"
    assert response.headers["Location"].endswith(f"/classes/{session['id']}")

    page = client.get(f"/classes/{session['id']}").get_data(as_text=True)
    assert "AI 301" in page
    assert "<svg" in page  # the QR codes were drawn


def test_a_bad_length_is_refused(client, login, csrf):
    login(username="teacher", role="admin")

    start(client, csrf, minutes="0")
    start(client, csrf, minutes="9999")
    start(client, csrf, course="")

    assert current_session() is None


def test_the_screen_gets_a_code_the_server_accepts(client, login, csrf):
    login(username="teacher", role="admin")
    start(client, csrf)
    session = current_session()

    response = client.get(f"/classes/{session['id']}/code")
    data = response.get_json()

    assert verify_code(get_session(session["id"]), data["code"])
    assert f"FA:{session['id']}:" not in data["qr"]  # the QR is drawn, not the raw text
    assert data["qr"].startswith("<svg")
    assert response.headers["Cache-Control"] == "no-store"


def test_ending_a_class(client, login, csrf):
    login(username="teacher", role="admin")
    start(client, csrf)
    session_id = current_session()["id"]

    client.post(f"/classes/{session_id}/end", data={"_csrf_token": csrf})

    assert current_session() is None


def test_an_unknown_class_is_a_404(client, login):
    login(username="teacher", role="admin")
    assert client.get("/classes/999").status_code == 404


def test_the_register_shows_which_class_a_record_belongs_to(client, login, csrf):
    from attendance.attendance_db import check_in

    login(username="teacher", role="admin")
    start(client, csrf, course="AI 301")
    check_in("Alice", 0.3, current_session()["id"])
    check_in("Bob", 0.3)  # the door camera, no class

    today = client.get("/attendance/today").get_data(as_text=True)
    assert "AI 301" in today

    export = client.get("/attendance/export").get_data(as_text=True)
    assert export.splitlines()[0] == "Name,Date,Time In,Time Out,Confidence,Class"
    alice = next(line for line in export.splitlines() if line.startswith("Alice,"))
    assert alice.endswith(",AI 301")

    # and the archive can be searched by class name
    searched = client.get("/attendance/all?q=AI+301").get_data(as_text=True)
    assert "Alice" in searched
    assert "Bob" not in searched


def test_an_ended_class_shows_no_code(client, login, csrf):
    login(username="teacher", role="admin")
    start(client, csrf)
    session_id = current_session()["id"]
    client.post(f"/classes/{session_id}/end", data={"_csrf_token": csrf})

    page = client.get(f"/classes/{session_id}").get_data(as_text=True)
    assert "This class has ended" in page
    assert "student-qr" not in page  # no QR left to scan

    # the screen's refresh is told the class is gone, so it reloads into the message
    response = client.get(f"/classes/{session_id}/code")
    assert response.status_code == 410
    assert "code" not in response.get_json()


def test_the_class_secret_is_never_on_the_projector_page(client, login, csrf):
    # the authenticator QR holds the secret that makes every code: a photo of it would let
    # anyone check in from anywhere for the whole class
    login(username="teacher", role="admin")
    start(client, csrf)
    session = current_session()

    page = client.get(f"/classes/{session['id']}").get_data(as_text=True)

    assert session["secret"] not in page
    assert "otpauth" not in page
    assert f"/classes/{session['id']}/authenticator" in page  # only a link to it


def test_the_authenticator_page_is_for_the_teacher_only(client, login, csrf):
    login(username="teacher", role="admin")
    start(client, csrf)
    session_id = current_session()["id"]

    response = client.get(f"/classes/{session_id}/authenticator")
    assert response.status_code == 200
    assert "<svg" in response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store"

    client.post(f"/classes/{session_id}/end", data={"_csrf_token": csrf})
    assert client.get(f"/classes/{session_id}/authenticator").status_code == 404

    client.get("/logout")
    with client.session_transaction() as cookie:
        cookie["_csrf_token"] = csrf  # signing out cleared it, and signing in needs it
    login(username="student", role="viewer")
    assert client.get(f"/classes/{session_id}/authenticator").status_code == 403
