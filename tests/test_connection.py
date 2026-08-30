"""How a connection is configured, as opposed to what is in it.

A PRAGMA that does not take is indistinguishable from one that does: SQLite accepts the
statement, ignores it, and carries on with the old behaviour. `PRAGMA journal_mode=WAL`
in particular fails quietly on filesystems that cannot support it. So each of these
settings is read back rather than assumed, because the failure they exist to prevent --
"database is locked" under a camera posting a frame every 1.5 seconds -- only shows up
under concurrency, in production, on somebody else's machine.
"""

import sqlite3
import threading
import time

import pytest

from attendance.db import BUSY_TIMEOUT_SECONDS, connect, create_schema


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "attendance.db"
    monkeypatch.setenv("ATTENDANCE_DB", str(path))
    create_schema()
    return path


def test_the_database_is_in_wal_mode(database):
    with connect() as connection:
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    assert mode.lower() == "wal", f"journal_mode is {mode!r}, so writers still lock readers"


def test_wal_survives_reconnecting(database):
    """It is a property of the file, so the second connection should not have to ask."""
    connect().close()

    plain = sqlite3.connect(database)
    mode = plain.execute("PRAGMA journal_mode").fetchone()[0]
    plain.close()

    assert mode.lower() == "wal"


def test_synchronous_is_relaxed_to_normal(database):
    with connect() as connection:
        # 1 is NORMAL; 2 (FULL) is the default and means an fsync per check-in
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 1


def test_a_connection_waits_for_a_busy_database_instead_of_failing(database):
    """The behaviour all of the above exists for: an overlapping write waits its turn.

    The lock is held from inside the thread rather than handed to it -- a sqlite3
    connection belongs to the thread that opened it and refuses to be used from another.
    """
    holding = threading.Event()
    HOLD_FOR = 0.3          # comfortably inside BUSY_TIMEOUT_SECONDS

    def hold_the_write_lock():
        connection = connect()
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO students (name, date_registered) VALUES ('A', '2026-01-01')"
        )
        holding.set()
        time.sleep(HOLD_FOR)
        connection.commit()
        connection.close()

    thread = threading.Thread(target=hold_the_write_lock)
    thread.start()
    assert holding.wait(timeout=5), "the holder never took the lock"

    # without the busy timeout this raises OperationalError("database is locked") at once
    started = time.monotonic()
    second = connect()
    second.execute("INSERT INTO students (name, date_registered) VALUES ('B', '2026-01-01')")
    second.commit()
    second.close()
    waited = time.monotonic() - started

    thread.join(timeout=5)

    assert waited >= HOLD_FOR / 2, (
        f"the second write returned in {waited:.2f}s, so it never actually contended"
    )

    with connect() as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM students")}
    assert names == {"A", "B"}, "one of the two writes was lost"


# a timeout of zero is SQLite's default and would put the failure straight back
def test_the_busy_timeout_is_long_enough_to_be_worth_having():
    assert BUSY_TIMEOUT_SECONDS >= 1


# Off by default in SQLite, for backwards compatibility, and per connection rather than
# stored in the file -- so it is set on every connection or on none of them. Until it was,
# every REFERENCES clause in the schema was decorative: a row could name a person who did
# not exist and nothing anywhere would object.
def test_foreign_keys_are_enforced(database):
    with connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_a_row_cannot_reference_a_person_who_does_not_exist(database):
    with connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute(
                "INSERT INTO attendance (student_id, date, time_in) VALUES (?, ?, ?)",
                (9999, "2026-08-27", "2026-08-27T09:15:42+05:00"),
            )


# The conversion has to happen at setup, not lazily on the first request. A database
# created in the default rollback journal is converted by whichever connection opens it
# first, under an exclusive lock -- and journal_mode changes do not wait on the busy
# timeout, so two requests arriving together on a fresh database raced and one of them got
# "database is locked" thrown out of connect() before running a query of its own.
def test_a_new_database_is_already_in_wal_before_any_request(database):
    plain = sqlite3.connect(str(database))  # not connect(), so nothing is set on the way in
    try:
        assert plain.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        plain.close()


def test_opening_many_connections_during_a_write_never_fails(database):
    holder = connect()
    holder.isolation_level = None
    holder.execute("BEGIN IMMEDIATE")  # an ordinary write, in progress

    failures = []
    ready = threading.Barrier(16)

    def open_one():
        ready.wait()  # all at the same instant, so any race is hit rather than hoped for
        try:
            connect().close()
        except sqlite3.OperationalError as error:
            failures.append(str(error))

    threads = [threading.Thread(target=open_one) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    holder.execute("COMMIT")
    holder.close()

    assert failures == []

