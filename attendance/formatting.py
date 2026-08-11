"""Small display helpers, registered as Jinja filters in create_app().

They live here rather than in the templates because a template is a bad place for
arithmetic, and because logic in a .py file can be tested directly.
"""

from datetime import datetime

TIME_FORMAT = "%H:%M:%S"


def duration(time_in, time_out):
    """Human-readable time between two "HH:MM:SS" strings.

    Returns an em dash when either is missing, which is the normal state for most of the
    day: someone who has arrived but not left yet has no duration, and showing "0h 0m"
    would wrongly suggest they were here for no time at all.
    """
    if not time_in or not time_out:
        return "—"

    try:
        start = datetime.strptime(time_in, TIME_FORMAT)
        end = datetime.strptime(time_out, TIME_FORMAT)
    except (ValueError, TypeError):
        return "—"

    minutes = int((end - start).total_seconds() // 60)
    if minutes < 0:
        # times are stored without a date, so a check-out after midnight subtracts to a
        # negative. rather than print something absurd, say it is not usable.
        return "—"

    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def short_time(value):
    """"09:15:42" -> "09:15". Seconds are noise in a table of arrival times."""
    return value[:5] if value else "—"
