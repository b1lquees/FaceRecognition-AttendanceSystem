
from attendance.auth_db import change_password, create_user, verify_user

PATH = "/account/password"


def post_change(client, csrf, current, new, confirm=None):
    return client.post(PATH, data={
        "current_password": current,
        "new_password": new,
        "confirm_password": new if confirm is None else confirm,
        "_csrf_token": csrf,
    })


# --- changing your own ------------------------------------------------------------

def test_changing_your_password(client, login, csrf):
    login(username="alice", password="the-old-password")

    response = post_change(client, csrf, "the-old-password", "a-brand-new-password")

    assert response.status_code == 302
    assert verify_user("alice", "a-brand-new-password").status == "ok"
    assert verify_user("alice", "the-old-password").status == "invalid"


# a session left open on a shared machine should not be enough to lock its owner out of
# their own account
def test_the_current_password_is_required(client, login, csrf):
    login(username="alice", password="the-old-password")

    response = post_change(client, csrf, "not-the-old-password", "a-brand-new-password")

    assert response.status_code == 403
    assert verify_user("alice", "the-old-password").status == "ok"  # unchanged


def test_the_two_new_passwords_must_match(client, login, csrf):
    login(username="alice", password="the-old-password")

    response = post_change(
        client, csrf, "the-old-password", "a-brand-new-password", confirm="something-else"
    )

    assert response.status_code == 400
    assert b"do not match" in response.data
    assert verify_user("alice", "the-old-password").status == "ok"


# the same rules the signup form uses, so the requirement cannot drift between the two
# places a password gets chosen
def test_a_weak_new_password_is_refused(client, login, csrf):
    login(username="alice", password="the-old-password")

    response = post_change(client, csrf, "the-old-password", "short")

    assert response.status_code == 400
    assert b"at least 8 characters" in response.data
    assert verify_user("alice", "the-old-password").status == "ok"


def test_reusing_the_same_password_is_refused(client, login, csrf):
    login(username="alice", password="the-old-password")

    response = post_change(client, csrf, "the-old-password", "the-old-password")

    assert response.status_code == 400
    assert b"same as the current one" in response.data


# the person changing it is the person using it; logging them out would be a punishment
# for doing the right thing
def test_the_session_survives_the_change(client, login, csrf):
    login(username="alice", password="the-old-password")
    post_change(client, csrf, "the-old-password", "a-brand-new-password")

    assert client.get("/attendance/today").status_code == 200


def test_you_must_be_signed_in(client):
    response = client.get(PATH)

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_the_form_is_csrf_protected(client, login):
    login(username="alice", password="the-old-password")

    response = client.post(PATH, data={
        "current_password": "the-old-password",
        "new_password": "a-brand-new-password",
        "confirm_password": "a-brand-new-password",
    })

    assert response.status_code == 400
    assert verify_user("alice", "the-old-password").status == "ok"


# this form takes the current password, so without a limit it is another place to guess it
def test_repeated_wrong_attempts_are_rate_limited(client, login, csrf):
    login(username="alice", password="the-old-password")

    for _ in range(10):
        post_change(client, csrf, "wrong", "a-brand-new-password")

    assert post_change(client, csrf, "wrong", "a-brand-new-password").status_code == 429


def test_the_password_is_never_logged(client, login, csrf, caplog):
    import logging
    caplog.set_level(logging.INFO)
    login(username="alice", password="the-old-password")

    post_change(client, csrf, "the-old-password", "a-brand-new-password")

    everything = " ".join(r.getMessage() for r in caplog.records)
    assert "the-old-password" not in everything
    assert "a-brand-new-password" not in everything


def test_the_change_is_audited(client, login, csrf, caplog):
    import logging
    caplog.set_level(logging.INFO)
    login(username="alice", password="the-old-password")

    post_change(client, csrf, "the-old-password", "a-brand-new-password")

    assert any("password.changed" in r.getMessage() for r in caplog.records)


# --- the database function ---------------------------------------------------------

# it deliberately does not check the old password: the two callers have different rights,
# and putting the check here would either block the admin path or make it look like the
# check had happened when it had not
def test_change_password_does_not_ask_for_the_old_one(temp_db):
    create_user("alice", "the-old-password")

    assert change_password("alice", "a-brand-new-password") is True
    assert verify_user("alice", "a-brand-new-password").status == "ok"


def test_changing_an_unknown_account_reports_nothing_changed(temp_db):
    assert change_password("nobody", "a-brand-new-password") is False


def test_the_new_password_is_hashed_not_stored(temp_db):
    from attendance.db import connect

    create_user("alice", "the-old-password")
    change_password("alice", "a-brand-new-password")

    conn = connect()
    stored = conn.execute(
        "SELECT password_hash FROM users WHERE username = ?", ("alice",)
    ).fetchone()[0]
    conn.close()

    assert "a-brand-new-password" not in stored


# a pending account must not be able to use this to get around approval
def test_a_pending_account_cannot_change_its_password(client, login, csrf):
    login(username="alice", password="the-old-password", approved=False)

    # never got a session, so the route is unreachable
    response = post_change(client, csrf, "the-old-password", "a-brand-new-password")

    assert response.status_code == 302
    assert verify_user("alice", "the-old-password").status == "pending"
