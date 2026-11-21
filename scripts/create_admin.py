"""Create an admin user for real use. The password is typed, never stored in plain text.

Usage:  python scripts/create_admin.py <username>
"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth import create_user  # noqa: E402
from app.config import load_config  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.timeutil import now_local  # noqa: E402


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    username = sys.argv[1].strip()
    if not 3 <= len(username) <= 64:
        print("Username must have 3 to 64 characters.")
        return 2
    password = getpass.getpass("Password (min. 12 characters): ")
    if password != getpass.getpass("Repeat password: "):
        print("Passwords do not match.")
        return 1
    cfg = load_config()
    init_db(cfg["db_path"])
    conn = connect(cfg["db_path"])
    try:
        create_user(conn, username, password, "admin", now_local())
    except ValueError as err:
        print(err)
        return 1
    except Exception as err:  # e.g. duplicate username
        print(f"Could not create user: {err}")
        return 1
    print(f"Admin user '{username}' created.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
