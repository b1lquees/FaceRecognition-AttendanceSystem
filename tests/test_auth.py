import sqlite3

import pytest

from attendance.auth_db import (
    approve_user,
    create_user,
    list_approved_users,
    list_pending_users,
    register_pending_user,
    reject_user,
    validate_credentials,
    verify_user,
)
from attendance.db import connect


# --- password handling ----------------------------------------------------------

def test_verify_user_returns_role_for_correct_password(temp_db):
    create_user("alice", "correct-horse", role="admin")

    result = verify_user("alice", "correct-horse")

    assert result.status == "ok"
    assert result.role == "admin"


def test_verify_user_rejects_wrong_password(temp_db):
    create_user("alice", "correct-horse")

    assert verify_user("alice", "wrong-password").status == "invalid"


def test_verify_user_rejects_unknown_username(temp_db):
    assert verify_user("nobody", "whatever").status == "invalid"


def test_create_user_defaults_to_viewer(temp_db):
    create_user("bob", "hunter2")

    assert verify_user("bob", "hunter2").role == "viewer"


# the whole point of hashing is that the database never sees the real password --
# this test fails loudly if someone ever "simplifies" create_user by storing it directly
def test_password_is_never_stored_in_plaintext(temp_db):
    create_user("carol", "s3cret-password")

    conn = connect()
    stored_hash = conn.execute(
        "SELECT password_hash FROM users WHERE username = ?", ("carol",)
    ).fetchone()[0]
    conn.close()

    assert "s3cret-password" not in stored_hash


# --- approval -------------------------------------------------------------------

# the distinction that matters: a pending account has the *right* password, so telling
# the person "invalid credentials" would send them off resetting something that works
def test_pending_account_is_reported_separately_from_a_bad_password(temp_db):
    register_pending_user("dave", "good-password")

    assert verify_user("dave", "good-password").status == "pending"
    assert verify_user("dave", "wrong-password").status == "invalid"


def test_signup_can_never_create_an_admin(temp_db):
    register_pending_user("mallory", "password123")
    approve_user(list_pending_users()[0][0])

    assert verify_user("mallory", "password123").role == "viewer"


def test_approving_lets_the_account_log_in(temp_db):
    register_pending_user("dave", "good-password")
    user_id, _ = list_pending_users()[0]

    assert approve_user(user_id) is True
    assert verify_user("dave", "good-password").status == "ok"
    assert list_pending_users() == []


def test_approving_twice_reports_nothing_changed(temp_db):
    register_pending_user("dave", "good-password")
    user_id, _ = list_pending_users()[0]
    approve_user(user_id)

    assert approve_user(user_id) is False


def test_rejecting_removes_the_pending_account(temp_db):
    register_pending_user("spam", "password123")
    user_id, _ = list_pending_users()[0]

    assert reject_user(user_id) is True
    assert verify_user("spam", "password123").status == "invalid"


# reject_user has `AND is_approved = 0` in its WHERE clause precisely so that a stray id
# cannot delete a working account. this is the test for that clause.
def test_rejecting_cannot_delete_an_approved_account(temp_db):
    create_user("realuser", "password123", role="admin")
    user_id = list_approved_users()[0][0]

    assert reject_user(user_id) is False
    assert verify_user("realuser", "password123").status == "ok"


def test_duplicate_username_is_refused(temp_db):
    create_user("alice", "password123")

    with pytest.raises(sqlite3.IntegrityError):
        register_pending_user("alice", "another-password")


# --- input validation -----------------------------------------------------------

@pytest.mark.parametrize(
    "username, password",
    [
        ("ab", "password123"),            # username too short
        ("a" * 33, "password123"),        # username too long
        ("has spaces", "password123"),    # disallowed character
        ("has/slash", "password123"),     # disallowed character
        ("", "password123"),              # empty
        ("alice", "short"),               # password under 8 characters
        ("alice", ""),                    # empty password
    ],
)
def test_invalid_credentials_are_rejected(username, password):
    assert validate_credentials(username, password) is not None


@pytest.mark.parametrize(
    "username",
    ["abc", "alice", "a" * 32, "first.last", "with-dash", "with_underscore", "user123"],
)
def test_valid_usernames_are_accepted(username):
    assert validate_credentials(username, "password123") is None
