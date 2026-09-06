from ipaddress import ip_address
from pathlib import Path

import pytest

from attendance.config import (
    Config,
    DevelopmentConfig,
    ProductionConfig,
    env_flag,
    env_float,
    env_int,
    env_networks,
    env_path,
    get_config,
)


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


@pytest.mark.parametrize("value, expected", [("0", 0), ("1", 1), ("2", 2)])
def test_whole_numbers_are_parsed(monkeypatch, value, expected):
    monkeypatch.setenv("A_COUNT", value)

    assert env_int("A_COUNT", default=99) == expected


@pytest.mark.parametrize("value", ["", "  "])
def test_an_empty_whole_number_uses_the_default(monkeypatch, value):
    monkeypatch.setenv("A_COUNT", value)

    assert env_int("A_COUNT", default=0) == 0


# It matters more here than for a threshold. The one setting read this way is how many
# proxy hops to trust, and quietly falling back to the default because somebody wrote
# "one" would change who the rate limiter believes it is talking to without saying so.
@pytest.mark.parametrize("value", ["one", "1.5", "0x1"])
def test_a_malformed_whole_number_is_refused(monkeypatch, value):
    monkeypatch.setenv("A_COUNT", value)

    with pytest.raises(RuntimeError, match="must be a whole number"):
        env_int("A_COUNT", default=0)


# --- paths ------------------------------------------------------------------------

# What this is for: in a container the source lives in an image that gets rebuilt and
# thrown away, while the database, the photos and the encoding cache have to outlive it.
# Each of the three therefore has to be placeable from the environment rather than pinned
# next to the source.
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


# --- choosing a config ----------------------------------------------------------

@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)


@pytest.mark.parametrize("value", ["production", "PRODUCTION", " production "])
def test_app_env_selects_production(no_env, monkeypatch, value):
    monkeypatch.setenv("APP_ENV", value)

    assert get_config() is ProductionConfig


@pytest.mark.parametrize("value", ["", "development", "prod", "producton"])
def test_anything_else_is_development(no_env, monkeypatch, value):
    monkeypatch.setenv("APP_ENV", value)

    assert get_config() is DevelopmentConfig


def test_an_unset_environment_is_development(no_env):
    assert get_config() is DevelopmentConfig


# FLASK_ENV is accepted as an alias because dropping it would fail in the worst
# direction: a deployment that sets only that name would come back up on
# DevelopmentConfig, where a missing secret key is generated instead of fatal and the
# session cookie stops being marked HTTPS-only.
def test_the_old_flask_env_name_still_selects_production(no_env, monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")

    assert get_config() is ProductionConfig


def test_the_old_name_warns_so_it_can_eventually_be_removed(no_env, monkeypatch, caplog):
    monkeypatch.setenv("FLASK_ENV", "production")

    get_config()

    assert "FLASK_ENV" in caplog.text
    assert "APP_ENV" in caplog.text


def test_app_env_wins_over_the_old_name(no_env, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("FLASK_ENV", "production")

    assert get_config() is DevelopmentConfig


# --- CHECKIN_NETWORKS -----------------------------------------------------------------
#
# The gate that answers the one question recognition cannot: not who is at the camera,
# but where the camera is. Parsing it wrongly fails in the dangerous direction, so the
# helper refuses a typo rather than falling back to "no restriction".

def test_networks_are_parsed_from_a_comma_separated_list(monkeypatch):
    monkeypatch.setenv("NETS", "10.0.0.0/8, 192.168.1.0/24")

    networks = env_networks("NETS")

    assert len(networks) == 2
    assert ip_address("10.1.2.3") in networks[0]
    assert ip_address("192.168.1.50") in networks[1]


def test_a_bare_address_is_a_single_host(monkeypatch):
    monkeypatch.setenv("NETS", "203.0.113.7")

    networks = env_networks("NETS")

    assert ip_address("203.0.113.7") in networks[0]
    assert ip_address("203.0.113.8") not in networks[0]


# how somebody writes down the network their own machine is on. the meaning is not
# ambiguous, so refusing it would be pedantry rather than safety
def test_host_bits_are_allowed_and_masked_off(monkeypatch):
    monkeypatch.setenv("NETS", "192.168.1.55/24")

    assert str(env_networks("NETS")[0]) == "192.168.1.0/24"


@pytest.mark.parametrize("value", ["", " ", "\t"])
def test_an_empty_variable_means_no_restriction(monkeypatch, value):
    monkeypatch.setenv("NETS", value)

    assert env_networks("NETS") == ()


def test_an_unset_variable_means_no_restriction(monkeypatch):
    monkeypatch.delenv("NETS", raising=False)

    assert env_networks("NETS") == ()


def test_a_trailing_comma_is_ignored(monkeypatch):
    monkeypatch.setenv("NETS", "10.0.0.0/8,")

    assert len(env_networks("NETS")) == 1


# The important one. A typo that fell back to the default would remove the restriction
# entirely -- the single direction this setting must never fail in.
@pytest.mark.parametrize("value", ["10.0.0.0/33", "not-an-address", "10.0.0.0/8,oops"])
def test_an_unparseable_entry_stops_the_app(monkeypatch, value):
    monkeypatch.setenv("NETS", value)

    with pytest.raises(RuntimeError, match="NETS"):
        env_networks("NETS")


def test_the_default_is_no_restriction_at_all():
    assert Config.CHECKIN_NETWORKS == ()
