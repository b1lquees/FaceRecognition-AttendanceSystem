import sqlite3

import numpy as np
import pytest

from attendance.attendance_db import (
    check_in,
    check_out,
    get_todays_attendance,
    register_student,
)
from attendance.db import connect, get_db_path
from attendance.recognition import identify_face

# the temp_db fixture lives in conftest.py so test_auth.py can share it

# proves the env var is actually being honoured -- if this fails, the tests below would
# be writing into the real attendance.db
def test_temp_db_is_used(temp_db):
    assert get_db_path() == str(temp_db)
    assert temp_db.exists()

# no duplicate attendance on same day
def test_check_in_is_recorded_once_per_day(temp_db):
    assert check_in("TestPerson", 0.4) == "checked_in"
    assert check_in("TestPerson", 0.4) == "already_in"
    assert len(get_todays_attendance()) == 1


# the test above proves check_in() behaves. this one proves the rule survives even
# if something bypasses that function -- the database itself refuses the duplicate, so a
# race between two concurrent requests cannot produce two rows for the same person and day.
def test_duplicate_attendance_is_rejected_by_the_database(temp_db):
    check_in("TestPerson", 0.4)
    student_id = register_student("TestPerson")
    today = get_todays_attendance()[0][1]

    conn = connect()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO attendance (student_id, date, time_in, confidence) VALUES (?, ?, ?, ?)",
            (student_id, today, "23:59:59", 0.4),
        )
    conn.close()


# two people checking in on the same day is normal and must still work -- this guards
# against the unique index being written too broadly (e.g. on date alone)
def test_different_people_can_be_marked_on_the_same_day(temp_db):
    assert check_in("PersonOne", 0.4) == "checked_in"
    assert check_in("PersonTwo", 0.4) == "checked_in"
    assert len(get_todays_attendance()) == 2


# --- checking out ----------------------------------------------------------------

def test_check_out_records_a_leaving_time(temp_db):
    check_in("TestPerson", 0.4)

    assert check_out("TestPerson") == "checked_out"

    name, date, time_in, time_out, confidence = get_todays_attendance()[0]
    assert time_in is not None
    assert time_out is not None


# checking out twice must not move the recorded time. the "AND time_out IS NULL" in the
# UPDATE is what guarantees it, which also makes two simultaneous requests safe.
def test_checking_out_twice_keeps_the_first_time(temp_db):
    check_in("TestPerson", 0.4)
    check_out("TestPerson")
    first_time_out = get_todays_attendance()[0][3]

    assert check_out("TestPerson") == "already_out"
    assert get_todays_attendance()[0][3] == first_time_out


def test_cannot_check_out_without_checking_in(temp_db):
    register_student("TestPerson")

    assert check_out("TestPerson") == "not_checked_in"


def test_checking_out_someone_never_enrolled(temp_db):
    assert check_out("Nobody At All") == "not_checked_in"


# check-in and check-out share one row, which is what keeps the unique index meaningful
def test_check_in_and_out_share_a_single_row(temp_db):
    check_in("TestPerson", 0.4)
    check_out("TestPerson")

    assert len(get_todays_attendance()) == 1


# checking out one person must not touch anyone else's row
def test_check_out_only_affects_that_person(temp_db):
    check_in("PersonOne", 0.4)
    check_in("PersonTwo", 0.4)

    check_out("PersonOne")

    rows = {row[0]: row[3] for row in get_todays_attendance()}
    assert rows["PersonOne"] is not None
    assert rows["PersonTwo"] is None


# someone who has arrived but not left has no leaving time yet, and that is the normal
# state for most of the day -- it must not be filled in with anything
def test_time_out_starts_empty(temp_db):
    check_in("TestPerson", 0.4)

    assert get_todays_attendance()[0][3] is None


# register_student is called on every recognition of an unknown-to-the-db name, so it has
# to be safe to call repeatedly and always return the same id
def test_register_student_is_idempotent(temp_db):
    first_id = register_student("TestPerson")
    second_id = register_student("TestPerson")

    assert first_id == second_id


# makes sure identify_face returns "Unknown" when there is no reasonable match
def test_identify_face():
    fake_encoding = np.random.rand(128)
    known_encodings = {
        "Bilquees": [np.random.rand(128)]
    }
    name, distance = identify_face(fake_encoding, known_encodings)

    assert name == "Unknown"
