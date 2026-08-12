"""Small display helpers, registered as Jinja filters in create_app().

They live here rather than in the templates because a template is a bad place for
arithmetic, and because logic in a .py file can be tested directly.
"""

from .clock import clock_time, parse

MISSING = "—"


def duration(time_in, time_out):
    """Human-readable time between two stored timestamps.

    Computed from the parsed instants rather than by subtracting wall-clock strings.
    That difference is the whole reason timestamps carry an offset: a shift spanning a
    daylight-saving change is an hour longer or shorter than the clock faces suggest, and
    string subtraction reports the clock faces.

    Returns an em dash when either is missing, which is the normal state for most of the
    day: someone who has arrived but not left has no duration, and "0h 0m" would wrongly
    suggest they were here for no time at all.
    """
    start = parse(time_in)
    end = parse(time_out)
    if start is None or end is None:
        return MISSING

    minutes = int((end - start).total_seconds() // 60)
    if minutes < 0:
        # a leaving time before an arrival time is not a duration worth printing. it
        # should not happen, and inventing a negative number would hide that it did.
        return MISSING

    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def short_time(value):
    """The wall-clock part of a timestamp, "09:15", for a table cell.

    This used to be value[:5], which was correct while times were stored as "09:15:42"
    and returns "2026-" now that they are full timestamps. Parsing rather than slicing is
    what makes it independent of the stored format.
    """
    return clock_time(value) or MISSING
