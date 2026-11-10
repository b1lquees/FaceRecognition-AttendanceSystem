"""Classes ("sessions"): starting one, finding the one running now, and its check-in code.

The code is a standard TOTP (time-based one-time password, RFC 6238) -- the same thing
Google Authenticator and Microsoft Authenticator show. Each class gets its own random
secret; the teacher scans it into their authenticator app once, and from then on the app
and this server compute the same 6-digit code from the secret and the current time.
Nothing about the code is stored: it is recomputed whenever it is checked.
"""

import time
from datetime import timedelta

import pyotp  # free library implementing the authenticator-app standard

from .clock import now, parse, stamp
from .db import connect

ISSUER = "Face Attendance"  # the name shown above the code in the authenticator app


def start_session(course, minutes, created_by=None):
    """Create a class running from now for `minutes`. Returns its id."""
    start = now()
    conn = connect()
    cursor = conn.execute(
        "INSERT INTO sessions (course, starts_at, ends_at, secret, created_by) "
        "VALUES (?, ?, ?, ?, ?)",
        # random_base32() makes a fresh random secret in the format authenticator apps expect
        (course, stamp(start), stamp(start + timedelta(minutes=minutes)),
         pyotp.random_base32(), created_by),
    )
    conn.commit()
    session_id = cursor.lastrowid  # the id SQLite just gave the new row
    conn.close()
    return session_id


COLUMNS = ("id", "course", "starts_at", "ends_at", "secret", "created_by")


def get_session(session_id):
    """One class as a dict, or None if there is no such id."""
    conn = connect()
    row = conn.execute(
        f"SELECT {', '.join(COLUMNS)} FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    conn.close()
    return dict(zip(COLUMNS, row)) if row else None


def current_session():
    """The class running right now, or None.

    Called on every camera frame, so it is one query. Compared as real moments (parse)
    rather than as text, because the stored times carry a UTC offset and two strings with
    different offsets do not sort in time order.
    """
    conn = connect()
    rows = conn.execute(
        f"SELECT {', '.join(COLUMNS)} FROM sessions ORDER BY id DESC LIMIT 50"
    ).fetchall()
    conn.close()
    moment = now()
    for row in rows:  # newest first, so overlapping classes resolve to the latest
        session = dict(zip(COLUMNS, row))
        if parse(session["starts_at"]) <= moment < parse(session["ends_at"]):
            return session
    return None


def end_session(session_id):
    """End a class now (an early finish); its records stay."""
    conn = connect()
    conn.execute("UPDATE sessions SET ends_at = ? WHERE id = ?", (stamp(), session_id))
    conn.commit()
    conn.close()


def verify_code(session, code):
    """Whether `code` is the class's current 6-digit code.

    Only the code for this exact 30-second step is accepted -- no grace period for the
    previous one -- so a code forwarded to someone outside the room is useless within 30 s.
    30 s rather than shorter because that is the only step Google and Microsoft
    Authenticator support; the teacher's app has to show the same code as the screen.
    """
    return pyotp.TOTP(session["secret"]).verify(str(code).strip(), valid_window=0)


def current_code(session):
    """The 6-digit code right now -- what the classroom screen shows."""
    return pyotp.TOTP(session["secret"]).now()


def seconds_left():
    """Seconds until the code changes. Codes change every 30 s, on the clock's half-minutes."""
    return 30 - int(time.time()) % 30


def authenticator_link(session):
    """The otpauth:// link that the QR code holds; scanning it adds the class to the app."""
    return pyotp.TOTP(session["secret"]).provisioning_uri(
        name=session["course"], issuer_name=ISSUER
    )
