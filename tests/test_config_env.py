from pathlib import Path

import pytest

from attendance.config import env_flag, env_float, env_path


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "No", "off", " off "])
def test_falsy_values_are_false(monkeypatch, value):
    monkeypatch.setenv("A_FLAG", value)

    assert env_flag("A_FLAG", default=True) is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "anything", " 1 "])
def test_everything_else_is_true(monkeypatch, value):
    monkeypatch.setenv("A_FLAG", value)

    assert env_flag("A_FLAG", default=False) is True


@pytest.mark.parametrize("default", [True, False])
def test_an_unset_variable_uses_the_default(monkeypatch, default):
    monkeypatch.delenv("A_FLAG", raising=False)

    assert env_flag("A_FLAG", default=default) is default


# The reason this helper exists. `setx NAME ""` is the ordinary way to clear a variable
# on Windows, and it leaves an empty string rather than removing it. A bare
# `value not in FALSY` reads that as true, so the obvious way to turn a setting off would
# have turned it on -- and for LIVENESS_ENABLED that means silently enabling a gate
# somebody was trying to disable.
@pytest.mark.parametrize("value", ["", " ", "\t"])
@pytest.mark.parametrize("default", [True, False])
def test_an_empty_variable_is_treated_as_unset(monkeypatch, value, default):
    monkeypatch.setenv("A_FLAG", value)

    assert env_flag("A_FLAG", default=default) is default


# --- numeric settings -------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [("0", 0.0), ("1.5", 1.5), ("-2.39", -2.39)])
def test_numbers_are_parsed(monkeypatch, value, expected):
    monkeypatch.setenv("A_NUMBER", value)

    assert env_float("A_NUMBER", default=99.0) == expected


@pytest.mark.parametrize("value", ["", "  "])
def test_an_empty_number_uses_the_default(monkeypatch, value):
    monkeypatch.setenv("A_NUMBER", value)

    assert env_float("A_NUMBER", default=0.0) == 0.0


# a typo'd threshold should stop the app rather than quietly reverting to a default that
# behaves differently from what somebody believed they had configured
def test_a_malformed_number_is_refused(monkeypatch):
    monkeypatch.setenv("A_NUMBER", "0..5")

    with pytest.raises(RuntimeError, match="must be a number"):
        env_float("A_NUMBER", default=0.0)


# --- paths ------------------------------------------------------------------------

# What this is for: in a container the source lives in an image that gets rebuilt and
# thrown away, while the database, the photos and the encoding cache have to outlive it.
# All three used to be pinned next to the source with no way to say otherwise.
def test_a_path_comes_from_the_environment_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("A_PATH", str(tmp_path / "somewhere.db"))

    assert env_path("A_PATH", default="/default/place.db") == tmp_path / "somewhere.db"


def test_an_unset_path_uses_the_default(monkeypatch):
    monkeypatch.delenv("A_PATH", raising=False)

    assert env_path("A_PATH", default="/default/place.db") == Path("/default/place.db")


# same rule as env_flag, and the same reason: `setx NAME ""` leaves an empty string, and
# reading that as a path would quietly put the database in the working directory
@pytest.mark.parametrize("value", ["", " ", "\t"])
def test_an_empty_path_is_treated_as_unset(monkeypatch, value):
    monkeypatch.setenv("A_PATH", value)

    assert env_path("A_PATH", default="/default/place.db") == Path("/default/place.db")


# a trailing space is invisible in a Dockerfile and legal in a filename, so an untrimmed
# value would create a directory that looks identical to the one that was meant
def test_surrounding_whitespace_is_trimmed(monkeypatch):
    monkeypatch.setenv("A_PATH", "  /data/known_faces  ")

    assert env_path("A_PATH", default="/somewhere/else") == Path("/data/known_faces")
