"""Where the database lives, and what shape it has.

connect() and get_db_path() used to sit in attendance_db.py while create_schema()
sat in db.py and imported them back, which made the two modules depend on each other
in a circle. Both now live here, and every other module imports the connection from
this one place.
"""

import os
import sqlite3
from pathlib import Path

# db.py lives in attendance/, so the project root is one level up
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# absolute, not the bare filename it used to be. sqlite creates any database it cannot
# find, so a relative path meant that running anything from a different working directory
# silently opened a brand new empty database instead of the real one -- which looks
# exactly like every attendance record having disappeared.
DEFAULT_DB = str(PROJECT_ROOT / "attendance.db")


def get_db_path():
    # read the env var on every call rather than once at import time, so a test can set
    # it after this module has already been imported and still be respected
    return os.environ.get("ATTENDANCE_DB", DEFAULT_DB)


# How long a connection waits for another one to finish writing before giving up.
#
# SQLite's default is zero: the moment two writes overlap, the loser raises "database is
# locked" immediately. With a camera posting a frame every 1.5 seconds and an admin
# clicking around at the same time, overlapping writes are ordinary rather than
# exceptional, and the failure surfaces as a 500 on somebody's check-in. Five seconds is
# far longer than any write here takes -- they are single-row inserts -- so in practice
# this converts a crash into a wait nobody notices.
BUSY_TIMEOUT_SECONDS = 5


def connect():
    # one place that opens the database, so nothing below has to know the path at all
    connection = sqlite3.connect(get_db_path(), timeout=BUSY_TIMEOUT_SECONDS)

    # Write-Ahead Logging, which is what lets a read run while a write is in progress.
    # The default rollback journal takes an exclusive lock over the whole database for
    # the duration of a write, so loading the register could fail simply because somebody
    # was being marked present at that instant.
    #
    # It is a property of the database file, not of the connection -- setting it here is
    # a no-op on every connection after the first, and it survives restarts. Kept here
    # anyway so that a fresh database created by any route gets it, rather than only the
    # ones that happen to go through create_schema().
    #
    # The one caveat worth knowing: WAL needs real shared memory, so it does not work on
    # a database file sitting on a network share. That is already outside what this
    # deployment supports -- see the SQLite note in the README's limitations.
    connection.execute("PRAGMA journal_mode=WAL")

    # With WAL, a crash can lose the most recent transactions but cannot corrupt the
    # database. For an attendance record that trade is worth taking: the alternative is
    # an fsync on every single check-in.
    connection.execute("PRAGMA synchronous=NORMAL")

    return connection


def create_schema(db_path=None):
    db_path = db_path or get_db_path()  # default to the configured path, but allow an explicit one

    conn = sqlite3.connect(db_path)  # opening the database
    # after the connection opens the database, the cursor sends commands to it
    cursor = conn.cursor()

    # date_registered is when they were enrolled
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        date_registered TEXT NOT NULL
    )
    """)

    # confidence is how sure the system is that it recognised the correct person.
    # REAL means a decimal number.
    # one row per person per day: time_in is set when they first check in, time_out when
    # they check out. time_out is nullable because "here but not gone yet" is the normal
    # state for most of the day.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        date TEXT NOT NULL,
        time_in TEXT NOT NULL,
        time_out TEXT,
        confidence REAL,
        FOREIGN KEY (student_id) REFERENCES students(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL
    )
    """)

    # this is what actually enforces "one attendance record per person per day".
    # it is a UNIQUE INDEX rather than a UNIQUE(...) constraint inside CREATE TABLE
    # because an index can be added to a database that already has data in it, whereas
    # changing a table constraint would mean rebuilding the whole table.
    # the column order is (date, student_id) rather than (student_id, date) so the same
    # index also speeds up the date lookup in get_todays_attendance() and the date
    # ordering in get_all_attendance() -- sqlite can only use an index starting from
    # its leftmost column, so putting date first makes it useful for both jobs.
    cursor.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_attendance_day_student
    ON attendance (date, student_id)
    """)

    run_migrations(cursor)

    conn.commit()  # saving the changes
    conn.close()  # closing the connection
    return db_path


def column_exists(cursor, table, column):
    # PRAGMA table_info returns one row per column: (cid, name, type, notnull, default, pk)
    return any(row[1] == column for row in cursor.execute(f"PRAGMA table_info({table})"))


def run_migrations(cursor):
    """Bring an existing database up to date.

    CREATE TABLE IF NOT EXISTS silently does nothing when the table is already there,
    so it cannot add a column to a database that predates it. Anything that changes an
    existing table has to go here instead.
    """
    # signup from the web creates accounts that an admin has to approve before they can
    # log in, so users needs a flag saying whether that has happened.
    if not column_exists(cursor, "users", "is_approved"):
        cursor.execute(
            "ALTER TABLE users ADD COLUMN is_approved INTEGER NOT NULL DEFAULT 0"
        )
        # the DEFAULT 0 above applies to every existing row, which would lock the
        # current admin out of their own system the moment this migration ran. accounts
        # that predate the approval feature were all created deliberately from the
        # command line, so they are approved retroactively.
        cursor.execute("UPDATE users SET is_approved = 1")

    # which enrolled person an account belongs to, for personal check-in mode.
    # nullable on purpose: an admin account does not have to be an enrolled face, and in
    # kiosk mode nothing uses this at all. no backfill is needed here -- unlike
    # is_approved, a NULL is the correct value for every existing row, because nobody
    # has been linked to anyone yet.
    if not column_exists(cursor, "users", "student_id"):
        cursor.execute(
            "ALTER TABLE users ADD COLUMN student_id INTEGER REFERENCES students(id)"
        )

    # attendance.time became attendance.time_in when check-out was added. renaming rather
    # than leaving a column called "time" that silently means "one of the two times" --
    # RENAME COLUMN needs sqlite 3.25+ (2018), and it keeps the existing values, so no
    # data moves and the unique index on (date, student_id) is untouched.
    if column_exists(cursor, "attendance", "time") and not column_exists(
        cursor, "attendance", "time_in"
    ):
        cursor.execute("ALTER TABLE attendance RENAME COLUMN time TO time_in")

    # nullable with no backfill: every existing row is someone who checked in before
    # check-out existed, and NULL correctly says "no check-out recorded" rather than
    # inventing a time they left
    if not column_exists(cursor, "attendance", "time_out"):
        cursor.execute("ALTER TABLE attendance ADD COLUMN time_out TEXT")

    give_old_timestamps_an_offset(cursor)


def give_old_timestamps_an_offset(cursor):
    """Rewrite "09:15:42" as "2026-08-12T09:15:42+05:00".

    Times used to be stored naive, with nothing recording which offset they were written
    in. This is the one migration in the project that cannot be exact: the information
    was never captured, so it cannot be recovered. Existing rows are interpreted in the
    configured timezone, which is right if the server has not moved and is the only
    defensible guess if it has.

    New rows carry their offset, so this is a one-time problem that does not recur.
    """
    from .clock import combine, get_timezone, stamp

    # a converted value contains a date and a "T"; an unconverted one is just "HH:MM:SS"
    rows = cursor.execute("""
        SELECT id, date, time_in, time_out FROM attendance
        WHERE time_in IS NOT NULL AND instr(time_in, 'T') = 0
    """).fetchall()

    if not rows:
        return

    zone = get_timezone()
    for row_id, date_text, time_in, time_out in rows:
        moment_in = combine(date_text, time_in, zone)
        if moment_in is None:
            continue  # unreadable: leave it rather than replace it with a guess

        moment_out = combine(date_text, time_out, zone) if time_out else None
        cursor.execute(
            "UPDATE attendance SET time_in = ?, time_out = ? WHERE id = ?",
            (stamp(moment_in), stamp(moment_out) if moment_out else None, row_id),
        )
