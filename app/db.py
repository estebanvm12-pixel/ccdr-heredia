"""SQLite connection (WAL) and schema migrations."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS = [
    # Version 1: shared users/sessions + booking module.
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        salt TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('admin', 'coach')),
        active INTEGER NOT NULL DEFAULT 1,
        failed_logins INTEGER NOT NULL DEFAULT 0,
        locked_until TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        csrf_token TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    CREATE INDEX idx_sessions_expires ON sessions(expires_at);
    CREATE TABLE facilities (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        location TEXT NOT NULL DEFAULT '',
        sports TEXT NOT NULL DEFAULT '',
        capacity INTEGER,
        rules_text TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE opening_hours (
        id INTEGER PRIMARY KEY,
        facility_id INTEGER NOT NULL REFERENCES facilities(id) ON DELETE CASCADE,
        weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
        opens_at TEXT NOT NULL,
        closes_at TEXT NOT NULL,
        CHECK (opens_at < closes_at)
    );
    CREATE INDEX idx_hours_facility ON opening_hours(facility_id, weekday);
    CREATE TABLE blocks (
        id INTEGER PRIMARY KEY,
        facility_id INTEGER NOT NULL REFERENCES facilities(id) ON DELETE CASCADE,
        starts_at TEXT NOT NULL,
        ends_at TEXT NOT NULL,
        reason TEXT NOT NULL,
        public_label TEXT NOT NULL DEFAULT '',
        recurrence TEXT NOT NULL DEFAULT 'none' CHECK (recurrence IN ('none', 'weekly')),
        recurrence_until TEXT,
        created_by INTEGER REFERENCES users(id),
        created_at TEXT NOT NULL,
        CHECK (starts_at < ends_at),
        CHECK (recurrence = 'none' OR recurrence_until IS NOT NULL)
    );
    CREATE INDEX idx_blocks_facility ON blocks(facility_id, starts_at);
    CREATE TABLE booking_requests (
        id INTEGER PRIMARY KEY,
        facility_id INTEGER NOT NULL REFERENCES facilities(id),
        starts_at TEXT NOT NULL,
        ends_at TEXT NOT NULL,
        requester_name TEXT NOT NULL,
        organization TEXT NOT NULL DEFAULT '',
        phone TEXT NOT NULL,
        email TEXT NOT NULL DEFAULT '',
        purpose TEXT NOT NULL,
        expected_attendees INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled')),
        admin_note TEXT NOT NULL DEFAULT '',
        public_token_hash TEXT NOT NULL UNIQUE,
        consent_at TEXT NOT NULL,
        consent_version TEXT NOT NULL,
        created_at TEXT NOT NULL,
        decided_by INTEGER REFERENCES users(id),
        decided_at TEXT,
        anonymized_at TEXT,
        CHECK (starts_at < ends_at)
    );
    CREATE INDEX idx_requests_lookup ON booking_requests(facility_id, status, starts_at);
    """,
]


def connect(path):
    """Open a connection in autocommit mode; transactions are explicit."""
    conn = sqlite3.connect(path, timeout=15, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def init_db(path):
    """Create the database file if needed, enable WAL and apply migrations."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for index in range(version, len(MIGRATIONS)):
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in MIGRATIONS[index].split(";"):
                    if statement.strip():
                        conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {index + 1}")
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
    finally:
        conn.close()


@contextmanager
def immediate(conn):
    """Write transaction that takes the write lock up front (BEGIN IMMEDIATE).

    Any exception inside the block rolls the transaction back.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
