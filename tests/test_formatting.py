import pytest

from attendance.formatting import MISSING, duration, short_time


def at(text):
    """A stored timestamp, offset included, the way the database holds them."""
    return f"2026-08-12T{text}+05:00"


@pytest.mark.parametrize(
    "time_in, time_out, expected",
    [
        ("09:00:00", "17:30:00", "8h 30m"),
        ("09:00:00", "17:00:00", "8h"),
        ("09:00:00", "09:45:00", "45m"),
        ("09:00:00", "09:00:30", "0m"),   # under a minute, but they did leave
        ("09:00:00", "10:00:00", "1h"),
    ],
)
def test_duration_between_two_times(time_in, time_out, expected):
    assert duration(at(time_in), at(time_out)) == expected


# THE test this change exists for. On the morning UK clocks go forward, 00:30 to 03:30
# reads as three hours on the wall but is two hours of elapsed time. Subtracting the
# clock faces -- which is what the old naive strings forced -- reports the wrong answer,
# and the offsets are what make the right one available.
def test_a_shift_spanning_a_clock_change_is_measured_in_real_time():
    before_the_change = "2026-03-29T00:30:00+00:00"  # GMT
    after_the_change = "2026-03-29T03:30:00+01:00"   # BST, one hour later on the clock

    assert duration(before_the_change, after_the_change) == "2h"


# and the same instants written in one zone give the same answer, because an offset is
# an offset however it is spelled
def test_the_same_instants_in_utc_agree():
    assert duration("2026-03-29T00:30:00+00:00", "2026-03-29T02:30:00+00:00") == "2h"


# the normal state for most of the day: arrived, not left yet. "0h 0m" would wrongly
# suggest they were here for no time at all.
@pytest.mark.parametrize(
    "time_in, time_out",
    [(at("09:00:00"), None), (None, at("17:00:00")), (None, None), (at("09:00:00"), "")],
)
def test_duration_is_a_dash_when_a_time_is_missing(time_in, time_out):
    assert duration(time_in, time_out) == MISSING


# a leaving time before an arrival should not happen; printing a negative would hide
# that it had
def test_duration_refuses_to_print_a_negative():
    assert duration(at("17:00:00"), at("09:00:00")) == MISSING


def test_duration_survives_malformed_input():
    assert duration("not a time", at("17:00:00")) == MISSING


# --- the wall-clock part ---------------------------------------------------------

# this used to be value[:5], which was right for "09:15:42" and returns "2026-" for a
# full timestamp. parsing rather than slicing is what makes it survive the format change.
def test_short_time_shows_the_wall_clock():
    assert short_time(at("09:15:42")) == "09:15"


def test_short_time_shows_local_time_not_utc():
    # 09:15 in a +05:00 zone is 04:15 UTC. the table should say what the clock on the
    # wall said, which is the whole reason the offset is stored rather than normalised away
    assert short_time("2026-08-12T09:15:42+05:00") == "09:15"


def test_short_time_of_nothing():
    assert short_time(None) == MISSING
    assert short_time("") == MISSING


def test_short_time_of_something_unreadable():
    assert short_time("not a timestamp") == MISSING
