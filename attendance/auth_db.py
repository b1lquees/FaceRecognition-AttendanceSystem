"""Accounts: creating them, approving them, and checking a password against one.

Passwords are never stored. generate_password_hash() turns one into a salted hash, and
check_password_hash() compares a fresh attempt against that hash without ever being able
to recover the original.
"""

import re
from collections import namedtuple

from werkzeug.security import check_password_hash, generate_password_hash

from .db import connect

# what verify_user() hands back. status is one of:
#   "ok"      - correct password and the account is approved
#   "pending" - correct password but an admin has not approved the account yet
#   "invalid" - no such user, or the wrong password
# the caller needs to tell "pending" apart from "invalid" so it can show a useful
# message instead of "invalid credentials" to someone whose password was actually right.
AuthResult = namedtuple("AuthResult", ["status", "role"])

INVALID = AuthResult("invalid", None)

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PASSWORD_LENGTH = 8

# A real hash of a value nobody can log in with, used when the username does not exist.
# Without it, a missing user returns immediately while a real one costs a full scrypt
# comparison -- tens of milliseconds, comfortably measurable over a network. Timing the
# response would tell an attacker which usernames are real, which is the first half of
# guessing a password. Checking against this instead makes both paths cost the same.
# Computed once at import rather than per request, because hashing is deliberately slow.
DUMMY_PASSWORD_HASH = generate_password_hash("not-a-real-password")


def validate_credentials(username, password):
    """Return an error message, or None if the username and password are acceptable.

    Checked on the server rather than trusting the HTML `required`/`pattern` attributes,
    which are a convenience for the user and nothing more -- anyone can post to the
    endpoint directly.
    """
    if not USERNAME_PATTERN.match(username or ""):
        return (
            "Username must be 3-32 characters, using only letters, numbers, "
            "dots, dashes or underscores."
        )
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    return None


def create_user(username, password, role="viewer", is_approved=True):
    """Create an account.

    Approved by default because this is the command-line path: someone who already has
    shell access to the server does not need to ask themselves for permission.
    Raises sqlite3.IntegrityError if the username is taken (users.username is UNIQUE).
    """
    conn = connect()
    conn.execute(
        "INSERT INTO users (username, password_hash, role, is_approved) VALUES (?, ?, ?, ?)",
        (username, generate_password_hash(password), role, 1 if is_approved else 0),
    )
    conn.commit()
    conn.close()


def register_pending_user(username, password):
    """Create an account from the public signup form.

    Always a viewer, and always unapproved. role is deliberately not a parameter: if the
    signup form could choose it, anyone who could reach the page could make themselves
    an admin, which would defeat the entire point of having roles.
    """
    create_user(username, password, role="viewer", is_approved=False)


def verify_user(username, password):
    """Check a login attempt. Returns an AuthResult."""
    conn = connect()
    row = conn.execute(
        "SELECT password_hash, role, is_approved FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    conn.close()

    if row is None:
        # deliberately do the work anyway, so this path takes as long as a real one.
        # the result is discarded -- it can never be True.
        check_password_hash(DUMMY_PASSWORD_HASH, password)
        return INVALID

    password_hash, role, is_approved = row

    if not check_password_hash(password_hash, password):
        return INVALID

    if not is_approved:
        # the password was right, so tell the caller that specifically -- but note the
        # role is returned without granting anything; the caller must not start a session
        return AuthResult("pending", role)

    return AuthResult("ok", role)


def list_pending_users():
    """Every account waiting for approval, oldest id first."""
    conn = connect()
    rows = conn.execute(
        "SELECT id, username FROM users WHERE is_approved = 0 ORDER BY id"
    ).fetchall()
    conn.close()
    return rows


def list_approved_users():
    """Every account that can currently log in, with the person it is linked to.

    LEFT JOIN rather than JOIN: an account with no linked person must still appear in
    the list -- an inner join would silently hide exactly the accounts an admin needs
    to see in order to link them.
    """
    conn = connect()
    rows = conn.execute("""
        SELECT users.id, users.username, users.role, users.student_id, students.name
        FROM users
        LEFT JOIN students ON users.student_id = students.id
        WHERE users.is_approved = 1
        ORDER BY users.username
    """).fetchall()
    conn.close()
    return rows


def link_user_to_student(user_id, student_id):
    """Bind an account to an enrolled person, or pass None to unlink.

    Deliberately admin-only and not part of signup: letting people choose their own
    identity at registration would mean anyone could claim to be anyone, which is the
    exact impersonation this is meant to prevent.
    """
    conn = connect()
    cursor = conn.execute(
        "UPDATE users SET student_id = ? WHERE id = ? AND is_approved = 1",
        (student_id, user_id),
    )
    conn.commit()
    changed = cursor.rowcount == 1
    conn.close()
    return changed


def get_linked_student_id(username):
    """Which enrolled person this account is, or None if it has not been linked."""
    conn = connect()
    row = conn.execute(
        "SELECT student_id FROM users WHERE username = ?", (username,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def approve_user(user_id):
    """Let a pending account log in. Returns True if a row actually changed."""
    conn = connect()
    cursor = conn.execute(
        "UPDATE users SET is_approved = 1 WHERE id = ? AND is_approved = 0", (user_id,)
    )
    conn.commit()
    changed = cursor.rowcount == 1
    conn.close()
    return changed


def reject_user(user_id):
    """Delete a pending account.

    The `AND is_approved = 0` is the important part: it means this can only ever remove
    an account that has not been approved, so a mistyped id cannot delete a real user.
    """
    conn = connect()
    cursor = conn.execute(
        "DELETE FROM users WHERE id = ? AND is_approved = 0", (user_id,)
    )
    conn.commit()
    deleted = cursor.rowcount == 1
    conn.close()
    return deleted
