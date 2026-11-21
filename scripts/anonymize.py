"""Anonymize old rejected, cancelled or expired requests (also runs daily inside the server).

Usage:  python scripts/anonymize.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bookings import anonymize_old_requests  # noqa: E402
from app.config import load_config  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.timeutil import now_local  # noqa: E402


def main():
    cfg = load_config()
    init_db(cfg["db_path"])
    conn = connect(cfg["db_path"])
    count = anonymize_old_requests(conn, now_local(), cfg["retention_months"])
    print(f"Anonymized {count} requests older than {cfg['retention_months']} months.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
