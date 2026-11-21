"""Consistent SQLite backup using the online backup API.

Usage:  python scripts/backup.py [destination_dir] [--keep N]

WARNING: the backup is NOT encrypted (stdlib has no symmetric cipher). Store it
on an encrypted disk (e.g. BitLocker) until SPEC section 7.4 is decided.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BASE_DIR, load_config  # noqa: E402
from app.timeutil import now_local  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", nargs="?", default=str(BASE_DIR / "data" / "backups"))
    parser.add_argument("--keep", type=int, default=14, help="number of backups to keep")
    args = parser.parse_args()
    cfg = load_config()
    source_path = Path(cfg["db_path"])
    if not source_path.exists():
        print(f"Database not found: {source_path}")
        return 1
    dest_dir = Path(args.destination)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"app-{now_local().strftime('%Y%m%d-%H%M')}.db"
    source = sqlite3.connect(str(source_path))
    dest = sqlite3.connect(str(target))
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()
    backups = sorted(dest_dir.glob("app-*.db"))
    for old in backups[:-args.keep] if args.keep > 0 else []:
        old.unlink()
    print(f"Backup written to {target} (not encrypted, see SPEC 7.4).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
