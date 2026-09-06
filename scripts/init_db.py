"""Create the database tables. Safe to re-run: everything is IF NOT EXISTS.

    python init_db.py

db.py lives inside the attendance package and is imported, not run, so this is the entry
point for creating the schema from a shell. create_user.py calls the same function, so
running that is enough on a fresh setup -- this script is for when you want the schema
without also creating an account.
"""

from attendance.db import create_schema

if __name__ == "__main__":
    print(f"Schema created (or already existed) in {create_schema()}.")
