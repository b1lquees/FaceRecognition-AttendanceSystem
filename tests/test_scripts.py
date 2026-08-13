"""Smoke tests for the command-line scripts.

These exist because peek_db.py silently broke. Renaming attendance.time to time_in updated
every query in the package and missed the one this script kept for itself -- and nothing
noticed, because no test had ever run it. Ruff cannot help: the stale column name was
inside a SQL string.

They are deliberately shallow, and split by what is safe to execute. Four of the scripts
have no argument parser, and two of those open a camera -- so there is no set of arguments
that makes them exit early. Passing --help to those does not print usage, it just runs
them: the first version of this file took nine and a half minutes and rebuilt the
encodings cache, because it assumed otherwise. So the whole-directory checks are static,
and only the scripts that talk to a database are actually run.
"""

import ast
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
EVERY_SCRIPT = sorted(p.name for p in SCRIPTS.glob("*.py"))

# the ones where argparse runs before anything else, so --help exits before any camera is
# opened or any cache is rebuilt
PARSES_ARGUMENTS = [
    "calibrate_liveness.py",
    "create_user.py",
    "peek_db.py",
    "reset_attendance.py",
    "reset_password.py",
]


def run(script, *args, env=None, timeout=120):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        capture_output=True, text=True, env=env, timeout=timeout,
    )


# --- static checks over every script ----------------------------------------------

@pytest.mark.parametrize("script", EVERY_SCRIPT)
def test_the_script_is_valid_python(script):
    source = (SCRIPTS / script).read_text(encoding="utf-8")

    ast.parse(source)  # raises SyntaxError with the line number if it is not


# a script that acts at import time cannot be tested, and cannot be imported by another
# script either. the guard is what keeps "run it" and "read it" separate.
@pytest.mark.parametrize("script", EVERY_SCRIPT)
def test_the_script_only_acts_when_run_directly(script):
    source = (SCRIPTS / script).read_text(encoding="utf-8")

    assert '__name__ == "__main__"' in source, f"{script} has no main guard"


# keeps the list above honest: a new script with a parser should be exercised by the --help
# test below rather than quietly skipped, and one that loses its parser should not stay on
# the list and start really running
@pytest.mark.parametrize("script", EVERY_SCRIPT)
def test_the_argument_parser_list_matches_reality(script):
    uses_argparse = "argparse" in (SCRIPTS / script).read_text(encoding="utf-8")

    assert uses_argparse == (script in PARSES_ARGUMENTS), (
        f"{script}: argparse={uses_argparse} but PARSES_ARGUMENTS says "
        f"{script in PARSES_ARGUMENTS}. Update the list in this file."
    )


# the cheapest end-to-end check there is: the module imports, its top-level code runs, and
# argparse builds. An ImportError or a bad default shows up here.
@pytest.mark.parametrize("script", PARSES_ARGUMENTS)
def test_the_script_runs_and_explains_itself(script):
    result = run(script, "--help")

    assert result.returncode == 0, f"{script} --help failed:\n{result.stderr}"
    assert "usage:" in result.stdout.lower()


# --- the scripts that talk to the database ----------------------------------------

@pytest.fixture
def child_env(temp_db):
    """An environment for a child process, pointed at the throwaway database.

    temp_db sets ATTENDANCE_DB via monkeypatch, which only affects this process -- passing
    a copy of the environment on is what gets the child to the same file.
    """
    return dict(os.environ)


# THE test for the bug this file was written after. peek_db.py ran its own SELECT against
# a column that no longer existed; nothing short of running it would have shown that.
def test_peek_db_queries_a_real_database(child_env):
    from attendance.attendance_db import check_in

    check_in("Alice Chen", 0.42)

    result = run("peek_db.py", env=child_env)

    assert result.returncode == 0, f"peek_db.py failed:\n{result.stderr}"
    assert "Alice Chen" in result.stdout


def test_peek_db_on_an_empty_database(child_env):
    result = run("peek_db.py", env=child_env)

    assert result.returncode == 0
    assert "No attendance records" in result.stdout


def test_peek_db_can_filter_by_name(child_env):
    from attendance.attendance_db import check_in

    check_in("Alice Chen", 0.42)
    check_in("Bob Adeyemi", 0.42)

    result = run("peek_db.py", "--name", "Alice", env=child_env)

    assert "Alice Chen" in result.stdout
    assert "Bob Adeyemi" not in result.stdout


# it prints the same columns the archive page shows, so a schema change that breaks one
# should not be able to leave the other looking fine
def test_peek_db_shows_the_check_out_columns(child_env):
    from attendance.attendance_db import check_in, check_out

    check_in("Alice Chen", 0.42)
    check_out("Alice Chen")

    result = run("peek_db.py", env=child_env)

    assert result.returncode == 0, result.stderr
    assert "Out" in result.stdout


def test_init_db_creates_the_tables(tmp_path):
    env = dict(os.environ)
    fresh = tmp_path / "brand-new.db"
    env["ATTENDANCE_DB"] = str(fresh)

    result = run("init_db.py", env=env)

    assert result.returncode == 0, result.stderr
    conn = sqlite3.connect(fresh)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"students", "attendance", "users"} <= tables


# running it twice is normal -- it is the documented first step, and people re-run it after
# pulling. it must not wipe anything or fail on the second go.
def test_init_db_is_safe_to_run_twice(child_env):
    from attendance.attendance_db import check_in, get_todays_attendance

    check_in("Alice Chen", 0.42)

    assert run("init_db.py", env=child_env).returncode == 0
    assert len(get_todays_attendance()) == 1


# --- reset_attendance --------------------------------------------------------------

def test_reset_attendance_refuses_an_unknown_person(child_env):
    result = run("reset_attendance.py", "Nobody At All", "--yes", env=child_env)

    assert result.returncode != 0
    assert "no enrolled person" in result.stderr.lower()


def test_reset_attendance_deletes_the_records(child_env):
    from attendance.attendance_db import check_in, get_todays_attendance

    check_in("Alice Chen", 0.42)

    result = run("reset_attendance.py", "Alice Chen", "--yes", env=child_env)

    assert result.returncode == 0, result.stderr
    assert get_todays_attendance() == []


# it takes the name on the command line. the version before this one had "Bilquees" written
# into it, so anybody else running it deleted nothing and was told it had worked.
def test_reset_attendance_only_touches_the_person_named(child_env):
    from attendance.attendance_db import check_in, get_todays_attendance

    check_in("Alice Chen", 0.42)
    check_in("Bob Adeyemi", 0.42)

    run("reset_attendance.py", "Alice Chen", "--yes", env=child_env)

    assert [row[0] for row in get_todays_attendance()] == ["Bob Adeyemi"]


# it deletes attendance, not the person -- un-enrolling is a different action, on a
# different page, with its own confirmation
def test_reset_attendance_leaves_the_person_enrolled(child_env):
    from attendance.attendance_db import check_in, list_students

    check_in("Alice Chen", 0.42)
    run("reset_attendance.py", "Alice Chen", "--yes", env=child_env)

    assert "Alice Chen" in str(list_students())


def test_reset_attendance_says_when_there_is_nothing_to_do(child_env):
    from attendance.attendance_db import register_student

    register_student("Alice Chen")

    result = run("reset_attendance.py", "Alice Chen", "--yes", env=child_env)

    assert result.returncode == 0
    assert "no matching records" in result.stdout.lower()


# deleting a day of attendance is not something to do because a key was pressed by accident
def test_reset_attendance_needs_confirmation(child_env):
    from attendance.attendance_db import check_in, get_todays_attendance

    check_in("Alice Chen", 0.42)

    # no --yes, and "no" on stdin. it prompts rather than acting.
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "reset_attendance.py"), "Alice Chen"],
        capture_output=True, text=True, env=child_env, input="no\n", timeout=120,
    )

    assert len(get_todays_attendance()) == 1, f"deleted without confirmation:\n{result.stdout}"
