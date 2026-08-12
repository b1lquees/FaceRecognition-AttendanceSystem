"""Delete one person's attendance records.

    python scripts/reset_attendance.py "Alice Chen"
    python scripts/reset_attendance.py "Alice Chen" --date 2026-08-12

Their enrolment is untouched -- this removes attendance rows, not the person. To stop
recognising somebody entirely, use the Remove button on /admin/enrol.
"""

import argparse
import sys

from attendance.db import connect  # uses ATTENDANCE_DB if set, else attendance.db


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    # a positional argument rather than a name baked into the source. this script used to
    # have "Bilquees" hardcoded, which made it useless to anyone else and a trap for
    # anyone who ran it expecting a prompt.
    parser.add_argument("name", help="the enrolled person whose records to delete")
    parser.add_argument(
        "--date",
        help="only this date (YYYY-MM-DD). omit to delete every record they have",
    )
    parser.add_argument(
        "--yes", action="store_true", help="skip the confirmation prompt"
    )
    args = parser.parse_args()

    conn = connect()

    student = conn.execute(
        "SELECT id FROM students WHERE name = ?", (args.name,)
    ).fetchone()
    if student is None:
        conn.close()
        sys.exit(f"No enrolled person named {args.name!r}.")

    where = "student_id = ?"
    params = [student[0]]
    if args.date:
        where += " AND date = ?"
        params.append(args.date)

    # counted first so the confirmation can say what it is about to do. "delete 47 rows"
    # is a decision; "delete some rows" is a gamble.
    count = conn.execute(
        f"SELECT COUNT(*) FROM attendance WHERE {where}", params
    ).fetchone()[0]

    if count == 0:
        conn.close()
        print(f"{args.name} has no matching records. Nothing to do.")
        return

    scope = f"on {args.date}" if args.date else "in total"
    if not args.yes:
        answer = input(f"Delete {count} record(s) for {args.name} {scope}? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            conn.close()
            sys.exit("Aborted.")

    conn.execute(f"DELETE FROM attendance WHERE {where}", params)
    conn.commit()
    conn.close()
    print(f"Deleted {count} record(s) for {args.name}.")


if __name__ == "__main__":
    main()
