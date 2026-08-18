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


# --- the match column ---------------------------------------------------------------
#
# Both templates used to do this arithmetic inline, dividing by a hardcoded 0.6 and
# comparing against a hardcoded 0.4 and 0.5. Changing TOLERANCE left all three behind:
# the bar filled against a cutoff that no longer applied and the colours described bands
# that no longer existed, on two pages, silently.
#
# The bands are fractions of the tolerance rather than fixed distances, because a
# distance only means anything next to the cutoff -- 0.45 is a comfortable match at a
# tolerance of 0.6 and a near miss at 0.5. At the original 0.6 these two work out to
# exactly the 0.40 and 0.50 the pages used to name.
STRONG_FRACTION = 2 / 3
FAIR_FRACTION = 5 / 6


def match_bands(tolerance):
    """The two distances where strong becomes fair, and fair becomes borderline."""
    return tolerance * STRONG_FRACTION, tolerance * FAIR_FRACTION


def match_quality(distance, tolerance):
    """How good a match is, as the css class the match column colours itself with.

    An empty string for a strong match, because that is the unmodified style: the meter
    and the number are green unless something says otherwise.
    """
    strong, fair = match_bands(tolerance)
    if distance <= strong:
        return ""
    if distance <= fair:
        return "good"
    return "weak"


def match_strength(distance, tolerance):
    """The distance as a 0-100 bar, where 100 is identical and 0 is at the cutoff.

    Clamped at both ends, and the lower clamp is not hypothetical: rows recorded while
    the tolerance was more forgiving are still in the database, and a distance of 0.55
    stored when the cutoff was 0.6 computes to -10% now that it is 0.5.
    """
    if not tolerance:
        # no cutoff means no scale to draw the bar against; an empty bar beats a
        # ZeroDivisionError on a page that is only reporting history
        return 0

    percent = round((1 - distance / tolerance) * 100)
    return max(0, min(100, percent))


def short_time(value):
    """The wall-clock part of a timestamp, "09:15", for a table cell.

    This used to be value[:5], which was correct while times were stored as "09:15:42"
    and returns "2026-" now that they are full timestamps. Parsing rather than slicing is
    what makes it independent of the stored format.
    """
    return clock_time(value) or MISSING
