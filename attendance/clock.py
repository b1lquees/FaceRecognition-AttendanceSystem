"""What time it is, and how times are written down.

Every timestamp used to come from datetime.now() -- naive local time, stored as
"09:15:42" with nothing recording which offset that was. Three things were wrong with
that, and they get worse the longer the system runs:

  - a row is ambiguous. 09:15 in June and 09:15 in December are different instants in
    any place that observes daylight saving, and nothing said which was which.
  - moving the server to another machine, or another country, silently changed what new
    rows meant relative to old ones.
  - a duration spanning a clock change was wrong by an hour, in whichever direction.

Timestamps are now ISO-8601 with the offset attached: "2026-08-12T09:15:42+05:00". That
is an exact instant, readable as wall-clock time without conversion, and sortable as a
string within a single zone.

The `date` column stays the LOCAL calendar date, deliberately. "Who was here on the 11th"
is a local question, and the one-row-per-person-per-day rule is a statement about local
days. Storing UTC dates would put a late shift on the wrong day for half the world.
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

# ISO-8601 to the second. Sub-second precision would be noise: the camera posts a frame
# every 1.5 seconds and nobody cares which millisecond someone arrived.
PRECISION = "seconds"


def get_timezone():
    """The zone times are recorded in.

    Read on every call rather than once at import, so a test can set it afterwards -- the
    same reasoning as get_db_path(). Falls back to whatever the machine is set to, which
    is what a single-site deployment wants and what the old behaviour effectively was.
    """
    name = os.environ.get("TIMEZONE")
    if name:
        return ZoneInfo(name)
    return datetime.now().astimezone().tzinfo


def now():
    """The current moment, with its offset attached."""
    return datetime.now(get_timezone())


def local_date(moment=None):
    """The calendar date in the configured zone, as "YYYY-MM-DD"."""
    return (moment or now()).strftime("%Y-%m-%d")


def stamp(moment=None):
    """A timestamp for storage: "2026-08-12T09:15:42+05:00"."""
    return (moment or now()).isoformat(timespec=PRECISION)


def parse(value):
    """Read a stored timestamp back. Returns None for anything unreadable.

    Returns None rather than raising because this is called from display code, and a
    single malformed row should show a dash rather than break the page it appears on.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def clock_time(value):
    """Just the wall-clock part, "09:15", for a table cell."""
    moment = parse(value)
    return moment.strftime("%H:%M") if moment else None


def combine(date_text, time_text, zone=None):
    """Build a timestamp from a separate date and "HH:MM:SS", in the given zone.

    Used by the migration to give the old naive rows an offset. Returns None if either
    part is unreadable, so a bad row is left alone rather than replaced with a guess.
    """
    try:
        naive = datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    return naive.replace(tzinfo=zone or get_timezone())
