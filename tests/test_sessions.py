import time

import pyotp

from attendance.attendance_db import check_in, check_out
from attendance.sessions_db import (
    authenticator_link,
    current_session,
    end_session,
    get_session,
    start_session,
    verify_code,
)

# temp_db comes from conftest.py: every test gets its own throwaway database


def test_a_started_class_is_the_current_one(temp_db):
    session_id = start_session("AI 301", minutes=60, created_by="teacher")

    assert current_session()["id"] == session_id
    assert current_session()["course"] == "AI 301"


def test_no_class_running_means_none(temp_db):
    assert current_session() is None


def test_an_ended_class_is_no_longer_current(temp_db):
    session_id = start_session("AI 301", minutes=60)
    end_session(session_id)

    assert current_session() is None


def test_the_authenticator_code_is_accepted_and_a_wrong_one_is_not(temp_db):
    session = get_session(start_session("AI 301", minutes=60))
    # what the teacher's authenticator app would be showing right now
    right_now = pyotp.TOTP(session["secret"]).now()

    assert verify_code(session, right_now)
    assert not verify_code(session, "000000" if right_now != "000000" else "111111")


def test_the_link_is_one_an_authenticator_app_can_read(temp_db):
    link = authenticator_link(get_session(start_session("AI 301", minutes=60)))

    assert link.startswith("otpauth://totp/")
    assert "secret=" in link


def test_two_classes_in_one_day_are_two_records(temp_db):
    maths = start_session("Maths", minutes=60)
    physics = start_session("Physics", minutes=60)

    assert check_in("Alice", 0.3, maths) == "checked_in"
    assert check_in("Alice", 0.3, maths) == "already_in"
    assert check_in("Alice", 0.3, physics) == "checked_in"


def test_checking_out_of_one_class_leaves_the_other_open(temp_db):
    maths = start_session("Maths", minutes=60)
    physics = start_session("Physics", minutes=60)
    check_in("Alice", 0.3, maths)
    check_in("Alice", 0.3, physics)

    assert check_out("Alice", maths) == "checked_out"
    assert check_out("Alice", maths) == "already_out"
    assert check_out("Alice", physics) == "checked_out"


def test_kiosk_check_in_is_unchanged_by_classes(temp_db):
    start_session("Maths", minutes=60)

    assert check_in("Alice", 0.3) == "checked_in"  # no session_id: the kiosk record
    assert check_in("Alice", 0.3) == "already_in"
    assert check_out("Alice") == "checked_out"


def test_the_previous_code_is_no_longer_accepted(temp_db):
    session = get_session(start_session("AI 301", minutes=60))
    totp = pyotp.TOTP(session["secret"])
    # the code that was on screen 30 seconds ago, i.e. one step back
    previous = totp.at(time.time() - 30)

    if previous != totp.now():  # the two can collide by chance, 1 in a million
        assert not verify_code(session, previous)
