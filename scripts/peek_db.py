"""Print every attendance row, newest first.

    python scripts/peek_db.py
    python scripts/peek_db.py --name "Alice Chen"
"""

import argparse

from attendance.attendance_db import get_all_attendance
from attendance.formatting import duration, short_time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", help="only this person (partial names work)")
    args = parser.parse_args()

    # goes through get_all_attendance() rather than writing its own SELECT. the previous
    # version had its own copy of the query and referenced attendance.time, which stopped
    # existing when check-out was added and the column became time_in -- so this script
    # broke and nothing noticed, because no test runs it. Sharing the query means the
    # schema can only drift in one place.
    rows = get_all_attendance(query=args.name or "")

    if not rows:
        print("No attendance records." if not args.name else f"No records matching {args.name!r}.")
        return

    print(f"{'Name':<24} {'Date':<12} {'In':<6} {'Out':<6} {'For':<8} Match")
    print("-" * 68)
    for name, date, time_in, time_out, confidence in rows:
        match = "n/a" if confidence is None else f"{confidence:.2f}"
        print(
            f"{name[:23]:<24} {date:<12} {short_time(time_in):<6} "
            f"{short_time(time_out):<6} {duration(time_in, time_out):<8} {match}"
        )

    print(f"\n{len(rows)} record(s).")


if __name__ == "__main__":
    main()
