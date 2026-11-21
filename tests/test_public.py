"""Public API: request creation, validation and anti-abuse."""
import json

from tests.helpers import ServerTestCase


class PublicRequestTests(ServerTestCase):
    def post(self, payload, **kw):
        return self.client().request("POST", "/api/public/requests", payload, **kw)

    def test_create_request(self):
        status, data, _ = self.post(self.request_payload(offset=3))
        self.assertEqual(status, 201)
        self.assertTrue(data["status_path"].startswith("/estado#"))
        conn = self.db()
        row = conn.execute("SELECT * FROM booking_requests ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        self.assertEqual(row["status"], "pending")
        self.assertIsNotNone(row["consent_at"])

    def test_missing_consent(self):
        status, data, _ = self.post(self.request_payload(offset=4, consent=False))
        self.assertEqual(status, 422)
        self.assertIn("consent", data["fields"])

    def test_field_validation(self):
        status, data, _ = self.post(self.request_payload(
            offset=4, requester_name="x" * 101, phone="abc", email="no-es-correo",
            purpose="", expected_attendees=0))
        self.assertEqual(status, 422)
        for field in ("requester_name", "phone", "email", "purpose", "expected_attendees"):
            self.assertIn(field, data["fields"])

    def test_capacity(self):
        status, data, _ = self.post(self.request_payload(offset=4, expected_attendees=51))
        self.assertEqual(status, 422)
        self.assertIn("expected_attendees", data["fields"])

    def test_outside_hours_and_limits(self):
        cases = [
            dict(start="05:00", end="07:00"),          # before opening
            dict(start="21:00", end="23:00"),          # after closing
            dict(start="10:15", end="11:15"),          # not on a 30-min slot
            dict(start="10:00", end="10:30"),          # shorter than minimum
            dict(start="08:00", end="13:00"),          # longer than maximum
            dict(start="12:00", end="10:00"),          # end before start
        ]
        for case in cases:
            status, data, _ = self.post(self.request_payload(offset=4, **case))
            self.assertEqual(status, 422, (case, data))
        status, _, _ = self.post(self.request_payload(offset=0, start="10:00", end="12:00"))
        self.assertEqual(status, 422)  # less than 24 h notice
        status, _, _ = self.post(self.request_payload(offset=61))
        self.assertEqual(status, 422)  # beyond horizon

    def test_cross_midnight_rejected(self):
        p = self.request_payload(offset=4)
        p["ends_at"] = self.day(5) + "T07:00"
        status, _, _ = self.post(p)
        self.assertEqual(status, 422)

    def test_conflict_with_approved_and_block(self):
        request_id, _ = self.create_request(offset=6, start="14:00", end="16:00")
        admin = self.admin()
        status, _, _ = admin.request("POST", f"/api/admin/requests/{request_id}/approve", {})
        self.assertEqual(status, 200)
        status, _, _ = self.post(self.request_payload(offset=6, start="15:00", end="17:00"))
        self.assertEqual(status, 409)
        # Touching the end is fine (half-open intervals).
        status, _, _ = self.post(self.request_payload(offset=6, start="16:00", end="17:00"))
        self.assertEqual(status, 201)
        # Several pending requests may ask for the same slot.
        status, _, _ = self.post(self.request_payload(offset=6, start="16:00", end="17:00"))
        self.assertEqual(status, 201)
        d = self.day(6)
        status, _, _ = admin.request("POST", "/api/admin/blocks", {
            "facility_id": self.facility_id, "starts_at": f"{d}T18:00", "ends_at": f"{d}T20:00",
            "reason": "Mantenimiento", "recurrence": "none"})
        self.assertEqual(status, 201)
        status, _, _ = self.post(self.request_payload(offset=6, start="19:00", end="20:00"))
        self.assertEqual(status, 409)

    def test_honeypot_stores_nothing(self):
        conn = self.db()
        before = conn.execute("SELECT COUNT(*) FROM booking_requests").fetchone()[0]
        status, data, _ = self.post(self.request_payload(offset=7, website="http://spam.example"))
        after = conn.execute("SELECT COUNT(*) FROM booking_requests").fetchone()[0]
        conn.close()
        self.assertEqual(status, 201)
        self.assertIn("token", data)
        self.assertEqual(before, after)
        status, _, _ = self.client().request("POST", "/api/public/requests/status", {"token": data["token"]})
        self.assertEqual(status, 404)

    def test_body_limits_and_content_type(self):
        big = json.dumps({"purpose": "x" * 20000}).encode()
        status, _, _ = self.client().request("POST", "/api/public/requests", raw=big,
                                             headers={"Content-Type": "application/json"})
        self.assertEqual(status, 413)
        status, _, _ = self.client().request("POST", "/api/public/requests", raw=b"a=b",
                                             headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 415)
        status, _, _ = self.client().request("POST", "/api/public/requests", raw=b"[1,2]",
                                             headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        status, _, _ = self.client().request("POST", "/api/public/requests", raw=b"{bad",
                                             headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400)

    def test_cross_origin_post_rejected(self):
        status, _, _ = self.post(self.request_payload(offset=8), headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)

    def test_sql_injection_strings_are_plain_data(self):
        status, _, _ = self.post(self.request_payload(
            offset=9, requester_name="Robert'); DROP TABLE booking_requests;--"))
        self.assertEqual(status, 201)
        status, _, _ = self.client().request("GET", "/api/public/availability?facility_id=1%20OR%201=1")
        self.assertEqual(status, 422)
        conn = self.db()
        self.assertGreater(conn.execute("SELECT COUNT(*) FROM booking_requests").fetchone()[0], 0)
        conn.close()


class RateLimitTests(ServerTestCase):
    CONFIG = {"request_rate_per_hour": 3, "status_rate_per_hour": 3}

    def test_request_rate_limit(self):
        codes = []
        for i in range(5):
            status, _, _ = self.client().request(
                "POST", "/api/public/requests", self.request_payload(offset=10 + i))
            codes.append(status)
        self.assertEqual(codes, [201, 201, 201, 429, 429])

    def test_status_rate_limit(self):
        codes = [self.client().request("POST", "/api/public/requests/status", {"token": "x" * 43})[0]
                 for _ in range(5)]
        self.assertEqual(codes, [404, 404, 404, 429, 429])
