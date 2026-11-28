"""Set up a whole class at once: accounts, enrolled faces, and the links between them.

    python scripts/setup_class.py class.csv class_photos

class.csv lists one student per row, under a header row:

    name,username
    Ali Khan,ali
    Sara Ahmed,sara

class_photos has one folder per student, named exactly as in the name column, holding
3-5 photos of them (.jpg, .jpeg or .png, one face each):

    class_photos/Ali Khan/1.jpg, 2.jpg, 3.jpg
    class_photos/Sara Ahmed/1.jpg, ...

For each student this creates an approved student account with a random password,
enrols their photos, and links the account to them -- the three things the website
otherwise needs doing one by one. New passwords are written to passwords.csv to hand
out; students can change theirs under "Change password". Keep that file private and
delete it once handed out (git ignores it).

Safe to run again, e.g. after adding rows: existing accounts keep their passwords, and
people already enrolled are not enrolled twice.
"""

import argparse
import csv
import secrets
import sqlite3
import sys
from pathlib import Path

from attendance import db
from attendance.attendance_db import get_student_id
from attendance.auth_db import (
    create_user,
    link_user_to_student,
    list_approved_users,
    validate_credentials,
)
from attendance.enrolment import ALLOWED_SUFFIXES, EnrolmentError, enrol
from attendance.recognition import load_known_encodings


def account_ids():
    """username -> account id, for every approved account."""
    return {row[1]: row[0] for row in list_approved_users()}


def main():
    parser = argparse.ArgumentParser(description="Set up a whole class from a CSV file "
                                                 "and a folder of photos.")
    parser.add_argument("csv_file", help="name,username per student, with a header row")
    parser.add_argument("photos", help="one folder of photos per student, named as in the CSV")
    parser.add_argument("--out", default="passwords.csv", help="where new passwords go")
    args = parser.parse_args()

    db.create_schema()  # creates or upgrades the tables, like init_db.py
    with open(args.csv_file, newline="", encoding="utf-8-sig") as f:  # -sig: Excel's CSVs
        students = [(row["name"].strip(), row["username"].strip()) for row in csv.DictReader(f)]

    enrolled = load_known_encodings()
    new_passwords = []
    problems = 0

    for name, username in students:
        print(f"\n{name} ({username})")

        # 1. the account, with a random password unless it already exists
        if username not in account_ids():
            password = secrets.token_urlsafe(9)  # 12 random characters
            error = validate_credentials(username, password)
            if error:
                print(f"  account: NOT created ({error})")
                problems += 1
                continue
            try:
                create_user(username, password)
            except sqlite3.IntegrityError:  # taken by an account still waiting for approval
                print("  account: NOT created (that username is waiting for approval: "
                      "approve or reject it under Accounts first)")
                problems += 1
                continue
            new_passwords.append((name, username, password))
            print("  account: created")
        else:
            print("  account: already exists, password unchanged")

        # 2. the face, unless this person is already enrolled
        if name in enrolled:
            print("  photos: already enrolled, skipped")
        else:
            folder = Path(args.photos) / name
            photos = sorted(p for p in folder.glob("*") if p.suffix.lower() in ALLOWED_SUFFIXES)
            if not photos:
                print(f"  photos: NONE found in {folder}")
                problems += 1
                continue
            try:
                _, added, rejected, advice = enrol(
                    name, [(p.name, p.read_bytes()) for p in photos])
            except EnrolmentError as error:
                print(f"  photos: NOT enrolled ({error})")
                problems += 1
                continue
            print(f"  photos: {added} enrolled")
            for note in [*rejected, advice]:
                if note:
                    print(f"    note: {note}")

        # 3. the link between them, so phone check-in knows whose face to expect
        link_user_to_student(account_ids()[username], get_student_id(name))
        print("  linked")

    if new_passwords:
        new_file = not Path(args.out).exists()
        # added to, never overwritten: a later run must not lose passwords not yet handed out
        with open(args.out, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if new_file:
                writer.writerow(["name", "username", "password"])
            writer.writerows(new_passwords)
        print(f"\nNew passwords for {len(new_passwords)} students saved to {args.out}")
    print(f"\nDone: {len(students)} students, {problems} with problems (see above).")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
