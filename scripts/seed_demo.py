"""Load FAKE demo data (facilities, hours, blocks, requests) and a demo admin.

Usage:  python scripts/seed_demo.py [--reset]

--reset deletes data/app.db first. The admin password is random and written to
data/demo-credentials.txt (git-ignored); it is never printed.
All names and phones are fictitious.
"""
import argparse
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BASE_DIR, load_config  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.demo import load_demo_data  # noqa: E402
from app.timeutil import now_local  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="delete the database first")
    args = parser.parse_args()
    cfg = load_config()
    db_path = Path(cfg["db_path"])
    if args.reset:
        for suffix in ("", "-wal", "-shm"):
            Path(str(db_path) + suffix).unlink(missing_ok=True)
    init_db(str(db_path))
    conn = connect(str(db_path))
    if conn.execute("SELECT COUNT(*) FROM facilities").fetchone()[0]:
        print("Database already has data. Use --reset to start over.")
        return 1
    password = secrets.token_urlsafe(12)
    load_demo_data(conn, now_local(), password, cfg["consent_version"])
    conn.close()
    creds = BASE_DIR / "data" / "demo-credentials.txt"
    creds.write_text(
        "DEMO ONLY - fictitious data. Do not reuse this password anywhere.\n"
        f"username: admin\npassword: {password}\n",
        encoding="utf-8",
    )
    print(f"Demo data loaded into {db_path}.")
    print(f"Demo admin credentials written to {creds} (git-ignored).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
