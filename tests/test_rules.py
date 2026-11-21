"""Unit tests for time and booking rules (no HTTP)."""
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app.bookings import anonymize_old_requests, block_occurrences, fits_hours
from app.db import connect, init_db
from app.timeutil import add_months, overlaps, parse_dt, parse_hm

HOURS = [
    {"weekday": 0, "opens_at": "06:00", "closes_at": "12:00"},
    {"weekday": 0, "opens_at": "14:00", "closes_at": "21:00"},
]


def dt(s):
    return parse_dt(s)


class TimeTests(unittest.TestCase):
    def test_parse_strict(self):
        self.assertIsNotNone(parse_dt("2026-11-23T10:00"))
        for bad in ["2026-11-23 10:00", "2026-13-01T10:00", "2026-11-23T10:00:00", "", None, 5, "x' OR 1=1"]:
            self.assertIsNone(parse_dt(bad), bad)
        self.assertEqual(parse_hm("06:30"), 390)
        self.assertIsNone(parse_hm("24:00"))

    def test_half_open_overlap(self):
        a, b, c = dt("2026-11-23T08:00"), dt("2026-11-23T09:00"), dt("2026-11-23T10:00")
        self.assertFalse(overlaps(a, b, b, c))  # touching is not a clash
        self.assertTrue(overlaps(a, c, b, c))

    def test_add_months(self):
        self.assertEqual(add_months(datetime(2026, 1, 31, 10, 0), -12), datetime(2025, 1, 28, 10, 0))
        self.assertEqual(add_months(datetime(2026, 3, 15), -3), datetime(2025, 12, 15))


class HoursTests(unittest.TestCase):
    # 2026-11-23 is a Monday.
    def test_inside_one_range(self):
        self.assertTrue(fits_hours(HOURS, dt("2026-11-23T06:00"), dt("2026-11-23T12:00")))
        self.assertTrue(fits_hours(HOURS, dt("2026-11-23T19:00"), dt("2026-11-23T21:00")))

    def test_across_gap_or_outside(self):
        self.assertFalse(fits_hours(HOURS, dt("2026-11-23T11:00"), dt("2026-11-23T15:00")))
        self.assertFalse(fits_hours(HOURS, dt("2026-11-23T05:30"), dt("2026-11-23T07:00")))
        self.assertFalse(fits_hours(HOURS, dt("2026-11-23T20:00"), dt("2026-11-23T21:30")))

    def test_closed_day_and_midnight(self):
        self.assertFalse(fits_hours(HOURS, dt("2026-11-24T08:00"), dt("2026-11-24T09:00")))
        self.assertFalse(fits_hours(HOURS, dt("2026-11-23T20:00"), dt("2026-11-24T01:00")))


class BlockOccurrenceTests(unittest.TestCase):
    weekly = {"starts_at": "2026-11-03T16:00", "ends_at": "2026-11-03T18:00",
              "recurrence": "weekly", "recurrence_until": "2026-12-01"}

    def test_weekly_occurrence_found_far_from_start(self):
        occ = list(block_occurrences(self.weekly, dt("2026-11-24T17:00"), dt("2026-11-24T19:00")))
        self.assertEqual(occ, [(dt("2026-11-24T16:00"), dt("2026-11-24T18:00"))])

    def test_weekly_respects_until(self):
        self.assertEqual(list(block_occurrences(self.weekly, dt("2026-12-01T16:00"), dt("2026-12-01T17:00"))),
                         [(dt("2026-12-01T16:00"), dt("2026-12-01T18:00"))])
        self.assertEqual(list(block_occurrences(self.weekly, dt("2026-12-08T16:00"), dt("2026-12-08T17:00"))), [])

    def test_weekly_other_day_and_before_start(self):
        self.assertEqual(list(block_occurrences(self.weekly, dt("2026-11-25T16:00"), dt("2026-11-25T18:00"))), [])
        self.assertEqual(list(block_occurrences(self.weekly, dt("2026-10-27T16:00"), dt("2026-10-27T18:00"))), [])

    def test_week_range_returns_each_occurrence(self):
        occ = list(block_occurrences(self.weekly, dt("2026-11-01T00:00"), dt("2026-12-31T00:00")))
        self.assertEqual(len(occ), 5)  # Nov 3, 10, 17, 24, Dec 1

    def test_one_off(self):
        block = {"starts_at": "2026-11-03T16:00", "ends_at": "2026-11-05T18:00", "recurrence": "none",
                 "recurrence_until": None}
        self.assertEqual(len(list(block_occurrences(block, dt("2026-11-04T08:00"), dt("2026-11-04T09:00")))), 1)
        self.assertEqual(len(list(block_occurrences(block, dt("2026-11-05T18:00"), dt("2026-11-05T19:00")))), 0)


class AnonymizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = str(Path(self.tmp) / "a.db")
        init_db(self.path)
        self.conn = connect(self.path)
        self.conn.execute("INSERT INTO facilities (id, name) VALUES (1, 'X')")

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, status, starts, created, decided=None):
        self.conn.execute(
            "INSERT INTO booking_requests (facility_id, starts_at, ends_at, requester_name, phone, purpose, "
            "expected_attendees, status, public_token_hash, consent_at, consent_version, created_at, decided_at) "
            "VALUES (1, ?, ?, 'Nombre Secreto', '8888-1111', 'p', 1, ?, ?, ?, 'v', ?, ?)",
            (starts, starts[:11] + "23:00", status, status + starts + created, created, created, decided),
        )

    def test_only_old_closed_requests_are_wiped(self):
        now = datetime(2026, 11, 20, 12, 0)
        self.add("rejected", "2025-10-01T10:00", "2025-09-20T10:00", "2025-09-21T10:00")   # old -> wipe
        self.add("cancelled", "2025-10-02T10:00", "2025-09-20T10:00", "2025-09-22T10:00")  # old -> wipe
        self.add("pending", "2025-10-03T10:00", "2025-09-20T10:00")                        # expired old -> wipe
        self.add("rejected", "2026-06-01T10:00", "2026-05-20T10:00", "2026-05-21T10:00")   # recent -> keep
        self.add("approved", "2025-10-04T10:00", "2025-09-20T10:00", "2025-09-21T10:00")   # approved -> keep
        self.assertEqual(anonymize_old_requests(self.conn, now, 12), 3)
        rows = self.conn.execute(
            "SELECT status, requester_name, phone, anonymized_at FROM booking_requests ORDER BY id").fetchall()
        self.assertEqual([r["requester_name"] for r in rows], ["", "", "", "Nombre Secreto", "Nombre Secreto"])
        self.assertTrue(all(r["anonymized_at"] for r in rows[:3]))
        self.assertEqual(anonymize_old_requests(self.conn, now, 12), 0)  # idempotent


if __name__ == "__main__":
    unittest.main()
