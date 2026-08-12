"""Set a new password for an account, as an administrator.

    python scripts/reset_password.py alice

This is the "I have forgotten my password" path, and it is deliberately a shell command
rather than a page. The alternative -- an email reset link -- needs a mail server this
project does not have, and faking it with a link shown in a browser would be worse than
useless: anyone who could reach the page could reset anyone's password.

Requiring shell access makes the authority explicit. Whoever runs this already controls
the machine the database is on, so they could change the password by other means anyway.

The new password is prompted for, so it never reaches shell history or the process list.
Tell the person their new password over a channel you trust, and have them change it from
/account/password afterwards -- until they do, you know it too.
"""

import argparse
import getpass
import sys

from attendance.auth_db import change_password, validate_credentials


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username")
    args = parser.parse_args()

    new = getpass.getpass(f"New password for {args.username}: ")
    if new != getpass.getpass("Confirm: "):
        sys.exit("Aborted: passwords did not match.")

    # the same rules the web forms use, so a password set here cannot be weaker than one
    # the person could have chosen themselves
    error = validate_credentials(args.username, new)
    if error:
        sys.exit(f"Aborted: {error}")

    if not change_password(args.username, new):
        sys.exit(f"Aborted: no account named {args.username!r}.")

    print(f"Password changed for {args.username}.")
    print("Ask them to change it themselves at /account/password -- you know it until they do.")


if __name__ == "__main__":
    main()
