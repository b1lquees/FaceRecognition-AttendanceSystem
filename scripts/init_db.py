"""Create the database tables. Safe to re-run: everything is IF NOT EXISTS.

    python init_db.py

This replaces `python db.py`, which stopped being runnable directly when db.py moved
into the attendance package. create_user.py calls the same function, so running that is
enough on a fresh setup -- this script exists for when you want to create the schema
without also creating an account.
"""

from attendance.db import create_schema

if __name__ == "__main__":
    print(f"Schema created (or already existed) in {create_schema()}.")
