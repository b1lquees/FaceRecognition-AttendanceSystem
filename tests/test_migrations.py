import sqlite3

from attendance.auth_db import verify_user
from attendance.db import column_exists, create_schema

# The schema before is_approved existed. Recreating it by hand is the only honest way to
# test the migration: create_schema() on an empty file produces the *new* shape, which
# would never exercise the ALTER TABLE path.
OLD_USERS_TABLE = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL
)
"""


def make_old_database(path, username="existing_admin"):
    """A database in the pre-approval shape, with one admin already in it."""
    conn = sqlite3.connect(path)
    conn.execute(OLD_USERS_TABLE)
    conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
        # a real werkzeug scrypt hash of "existing-password"
        (username, generate_hash("existing-password"), "admin"),
    )
    conn.commit()
    conn.close()


def generate_hash(password):
    from werkzeug.security import generate_password_hash

    return generate_password_hash(password)


def test_migration_adds_the_column(tmp_path, monkeypatch):
    db_path = tmp_path / "old.db"
    make_old_database(db_path)
    monkeypatch.setenv("ATTENDANCE_DB", str(db_path))

    create_schema()

    conn = sqlite3.connect(db_path)
    assert column_exists(conn.cursor(), "users", "is_approved")
    conn.close()


# The column is added with DEFAULT 0, which applies to every row already in the table.
# Left there, the migration would lock the existing administrator out of their own
# system the first time it ran. This is the test for the UPDATE that follows it.
def test_migration_does_not_lock_out_existing_accounts(tmp_path, monkeypatch):
    db_path = tmp_path / "old.db"
    make_old_database(db_path)
    monkeypatch.setenv("ATTENDANCE_DB", str(db_path))

    create_schema()

    result = verify_user("existing_admin", "existing-password")
    assert result.status == "ok"
    assert result.role == "admin"


def test_migration_is_safe_to_run_twice(tmp_path, monkeypatch):
    db_path = tmp_path / "old.db"
    make_old_database(db_path)
    monkeypatch.setenv("ATTENDANCE_DB", str(db_path))

    create_schema()
    create_schema()  # would raise "duplicate column name" without the column_exists check

    assert verify_user("existing_admin", "existing-password").status == "ok"


# unlike is_approved, this one needs no backfill: NULL is the correct value for every
# existing row, because nobody has been linked to an enrolled person yet
def test_migration_adds_the_student_link_as_null(tmp_path, monkeypatch):
    db_path = tmp_path / "old.db"
    make_old_database(db_path)
    monkeypatch.setenv("ATTENDANCE_DB", str(db_path))

    create_schema()

    conn = sqlite3.connect(db_path)
    assert column_exists(conn.cursor(), "users", "student_id")
    assert conn.execute("SELECT student_id FROM users").fetchone()[0] is None
    conn.close()


# --- attendance.time -> attendance.time_in ----------------------------------------

# the pre-checkout shape, with a real row in it. this is the only migration so far that
# renames a column holding data, so "the values survived" is the thing to prove.
OLD_ATTENDANCE_TABLE = """
CREATE TABLE attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    confidence REAL,
    FOREIGN KEY (student_id) REFERENCES students(id)
)
"""


def make_old_attendance(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE students (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name TEXT NOT NULL UNIQUE, date_registered TEXT NOT NULL)"
    )
    conn.execute(OLD_ATTENDANCE_TABLE)
    conn.execute("INSERT INTO students (name, date_registered) VALUES ('Alice', '2026-01-01')")
    conn.execute(
        "INSERT INTO attendance (student_id, date, time, confidence) VALUES (1, ?, ?, ?)",
        ("2026-01-02", "09:15:00", 0.41),
    )
    conn.commit()
    conn.close()


def test_renaming_time_keeps_the_recorded_values(tmp_path, monkeypatch):
    db_path = tmp_path / "old.db"
    make_old_attendance(db_path)
    monkeypatch.setenv("ATTENDANCE_DB", str(db_path))

    create_schema()

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT date, time_in, time_out, confidence FROM attendance").fetchone()
    conn.close()

    date, time_in, time_out, confidence = row
    assert (date, confidence) == ("2026-01-02", 0.41)
    # the wall-clock reading survives; what is added is the offset that was missing
    assert time_in.startswith("2026-01-02T09:15:00")
    assert time_in != "09:15:00"
    assert time_out is None


# every pre-existing row is someone who checked in before check-out existed. NULL says
# "no departure recorded", which is true; inventing a time would not be.
def test_existing_rows_get_no_invented_leaving_time(tmp_path, monkeypatch):
    db_path = tmp_path / "old.db"
    make_old_attendance(db_path)
    monkeypatch.setenv("ATTENDANCE_DB", str(db_path))

    create_schema()

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT time_out FROM attendance").fetchone()[0] is None
    conn.close()


def test_the_rename_is_safe_to_run_twice(tmp_path, monkeypatch):
    db_path = tmp_path / "old.db"
    make_old_attendance(db_path)
    monkeypatch.setenv("ATTENDANCE_DB", str(db_path))

    create_schema()
    create_schema()  # would raise "no such column: time" without the column_exists guard

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 1
    conn.close()


# a brand new database should come out with the column already present, without the
# ALTER TABLE path being involved at all
def test_fresh_database_has_the_column(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    monkeypatch.setenv("ATTENDANCE_DB", str(db_path))

    create_schema()

    conn = sqlite3.connect(db_path)
    assert column_exists(conn.cursor(), "users", "is_approved")
    conn.close()


# --- naive times gaining an offset ------------------------------------------------

# The one migration here that cannot be exact. Times were stored with no record of which
# offset they were written in, so that information is gone and cannot be recovered. Rows
# are interpreted in the configured zone, which is right if the server has not moved and
# is the only defensible guess if it has.
def test_old_times_are_given_the_configured_offset(tmp_path, monkeypatch):
    db_path = tmp_path / "old.db"
    make_old_attendance(db_path)
    monkeypatch.setenv("ATTENDANCE_DB", str(db_path))
    monkeypatch.setenv("TIMEZONE", "Asia/Karachi")  # +05:00, no daylight saving

    create_schema()

    conn = sqlite3.connect(db_path)
    time_in = conn.execute("SELECT time_in FROM attendance").fetchone()[0]
    conn.close()

    assert time_in == "2026-01-02T09:15:00+05:00"


def test_a_zone_with_daylight_saving_gets_the_offset_for_that_date(tmp_path, monkeypatch):
    db_path = tmp_path / "old.db"
    make_old_attendance(db_path)
    monkeypatch.setenv("ATTENDANCE_DB", str(db_path))
    monkeypatch.setenv("TIMEZONE", "Europe/London")

    create_schema()

    conn = sqlite3.connect(db_path)
    time_in = conn.execute("SELECT time_in FROM attendance").fetchone()[0]
    conn.close()

    # 2 January is winter, so GMT rather than BST -- the offset is worked out per row
    # rather than applied as one blanket value
    assert time_in == "2026-01-02T09:15:00+00:00"


# already-converted rows must not be converted again, or every restart would stack
# another offset onto them
def test_converting_twice_changes_nothing(tmp_path, monkeypatch):
    db_path = tmp_path / "old.db"
    make_old_attendance(db_path)
    monkeypatch.setenv("ATTENDANCE_DB", str(db_path))
    monkeypatch.setenv("TIMEZONE", "Asia/Karachi")

    create_schema()
    conn = sqlite3.connect(db_path)
    once = conn.execute("SELECT time_in FROM attendance").fetchone()[0]
    conn.close()

    create_schema()
    conn = sqlite3.connect(db_path)
    twice = conn.execute("SELECT time_in FROM attendance").fetchone()[0]
    conn.close()

    assert once == twice
