import time

import pytest

from attendance.auth_db import create_user, verify_user
from attendance.config import Config, ProductionConfig, TestingConfig
from attendance.db import DEFAULT_DB


# --- rate limiting --------------------------------------------------------------

def post_login(client, csrf, password="wrong-password"):
    return client.post(
        "/login",
        data={"username": "viewer1", "password": password, "_csrf_token": csrf},
    )


def test_repeated_failed_logins_are_eventually_refused(client, csrf):
    create_user("viewer1", "the-real-password")

    # the limit is 10 per 5 minutes, so the first ten are allowed through to the normal
    # "invalid password" answer
    for _ in range(10):
        assert post_login(client, csrf).status_code == 401

    blocked = post_login(client, csrf)
    assert blocked.status_code == 429
    assert b"Too many attempts" in blocked.data


def test_the_limit_response_says_how_long_to_wait(client, csrf):
    create_user("viewer1", "the-real-password")
    for _ in range(11):
        response = post_login(client, csrf)

    assert response.headers["Retry-After"].isdigit()
    assert 0 < int(response.headers["Retry-After"]) <= 301


# being rate limited must not become a way to lock a legitimate user out permanently, so
# the correct password is still refused while blocked -- but only while blocked
def test_a_blocked_client_is_refused_even_with_the_right_password(client, csrf):
    create_user("viewer1", "the-real-password")
    for _ in range(11):
        post_login(client, csrf)

    response = post_login(client, csrf, password="the-real-password")

    assert response.status_code == 429


# fetching the form is harmless, and limiting it would hide the page that explains the
# limit from the person who hit it
def test_get_requests_to_login_are_never_limited(client, csrf):
    create_user("viewer1", "the-real-password")
    for _ in range(11):
        post_login(client, csrf)

    assert client.get("/login").status_code == 200


def test_signup_is_limited_separately_from_login(client, csrf):
    create_user("viewer1", "the-real-password")
    for _ in range(11):
        post_login(client, csrf)  # exhaust the login bucket

    # signup has its own bucket, so it is unaffected
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


def test_signup_has_its_own_lower_limit(client, csrf):
    for i in range(5):
        client.post(
            "/signup",
            data={
                "username": f"person{i}",
                "password": "password123",
                "confirm_password": "password123",
                "_csrf_token": csrf,
            },
        )

    response = client.post(
        "/signup",
        data={
            "username": "onetoomany",
            "password": "password123",
            "confirm_password": "password123",
            "_csrf_token": csrf,
        },
    )

    assert response.status_code == 429


# the counters hang off the app, not a module global. if that regresses, one test's
# failed logins would start counting against the next test's and the suite would fail
# in confusing, order-dependent ways.
def test_each_application_gets_its_own_counters(client, csrf, app):
    create_user("viewer1", "the-real-password")
    for _ in range(11):
        post_login(client, csrf)

    fresh_client = app.test_client()
    with fresh_client.session_transaction() as session:
        session["_csrf_token"] = csrf

    # same app here, so still blocked -- this is the control for the assertion below
    assert post_login(fresh_client, csrf).status_code == 429
    assert "rate_limits" in app.extensions


# --- user enumeration by timing -------------------------------------------------

# a missing username used to return immediately while a real one paid for a full scrypt
# comparison. that difference is measurable over a network and tells an attacker which
# usernames exist. both paths now do the same work.
def test_unknown_and_known_usernames_take_a_similar_time(temp_db):
    create_user("realuser", "the-real-password")

    def measure(username):
        samples = []
        for _ in range(3):
            start = time.perf_counter()
            verify_user(username, "some-wrong-password")
            samples.append(time.perf_counter() - start)
        return min(samples)  # min is the least noisy estimate of the true cost

    known = measure("realuser")
    unknown = measure("no-such-user")

    # generous bound: this is asserting the dummy hash is being computed at all, not
    # measuring a precise ratio, because CI timing is noisy
    assert unknown > known / 3, (
        f"unknown-user path was much faster ({unknown:.4f}s vs {known:.4f}s), "
        "so the dummy hash comparison is probably missing"
    )


# --- session cookie hardening ---------------------------------------------------

def test_session_cookie_is_not_readable_by_javascript():
    assert Config.SESSION_COOKIE_HTTPONLY is True


def test_session_cookie_is_not_sent_on_cross_site_posts():
    assert Config.SESSION_COOKIE_SAMESITE == "Lax"


# without this, one plain-http request leaks the session cookie in cleartext, and the
# cookie is the whole login
def test_production_requires_https_for_the_session_cookie():
    assert ProductionConfig.SESSION_COOKIE_SECURE is True


# development and tests run over plain http, so the flag has to be off there or nothing
# would stay logged in
@pytest.mark.parametrize("config", [Config, TestingConfig])
def test_secure_cookie_is_off_outside_production(config):
    assert config.SESSION_COOKIE_SECURE is False


def test_the_app_actually_applies_the_cookie_settings(app):
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


# --- database path --------------------------------------------------------------

# sqlite creates any database it cannot find. when the default was the bare filename
# "attendance.db", running anything from another directory silently opened a new empty
# one, which looks exactly like every record having been lost.
def test_the_default_database_path_is_absolute():
    from pathlib import Path

    assert Path(DEFAULT_DB).is_absolute()
    assert Path(DEFAULT_DB).name == "attendance.db"
