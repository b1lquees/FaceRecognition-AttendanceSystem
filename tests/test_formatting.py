import pytest

from attendance.formatting import duration, short_time


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
    assert duration(time_in, time_out) == expected


# the normal state for most of the day: arrived, not left yet. "0h 0m" would wrongly
# suggest they were here for no time at all.
@pytest.mark.parametrize(
    "time_in, time_out",
    [
        ("09:00:00", None),
        (None, "17:00:00"),
        (None, None),
        ("09:00:00", ""),
    ],
)
def test_duration_is_a_dash_when_a_time_is_missing(time_in, time_out):
    assert duration(time_in, time_out) == "—"


# times are stored without a date, so a check-out after midnight subtracts to a negative.
# printing "-7h" would be worse than admitting the value is not usable.
def test_duration_refuses_to_print_a_negative():
    assert duration("23:00:00", "01:00:00") == "—"


def test_duration_survives_malformed_input():
    assert duration("not a time", "17:00:00") == "—"


def test_short_time_drops_the_seconds():
    assert short_time("09:15:42") == "09:15"


def test_short_time_of_nothing():
    assert short_time(None) == "—"
    assert short_time("") == "—"
