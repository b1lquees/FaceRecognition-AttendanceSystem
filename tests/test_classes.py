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
