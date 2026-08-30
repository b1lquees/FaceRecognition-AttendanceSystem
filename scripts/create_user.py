"""Create a login account for the attendance system.

Usage:
    python create_user.py alice                 # a viewer, which is the default
    python create_user.py alice --role admin
    python create_user.py station               # the account the camera signs in as

The password is prompted for rather than passed as an argument, so it never ends up
in this file, in your shell history, or in the process list.
"""

import argparse
import getpass
import sqlite3
import sys

from attendance import db
from attendance.auth_db import create_user


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username")
    parser.add_argument(
        "--role",
        choices=["admin", "viewer"],
        default="viewer",
        # Spelled out because this is where somebody decides what the camera station gets,
        # and "can export CSV" undersold it badly. An admin can also enrol a face -- which
        # means enrolling themselves under another person's name -- remove people, approve
        # accounts, and link accounts to people. A viewer reads attendance and works the
        # camera, which is everything the station needs and nothing it does not.
        help=(
            "admin: enrol and remove people, approve accounts, export the archive. "
            "viewer: read attendance and use the camera (default, and what a camera "
            "station should be)"
        ),
    )
    args = parser.parse_args()

    db.create_schema()  # no-op if the tables already exist

    password = getpass.getpass("Password: ")
    if not password:
        sys.exit("Aborted: password cannot be empty.")
    if password != getpass.getpass("Confirm password: "):
        sys.exit("Aborted: passwords did not match.")

    try:
        create_user(args.username, password, role=args.role)
    except sqlite3.IntegrityError:
        # username is UNIQUE in the schema, so a duplicate raises rather than overwriting
        sys.exit(f"Aborted: a user named {args.username!r} already exists.")

    print(f"Created {args.role} account {args.username!r}.")


if __name__ == "__main__":
    main()
