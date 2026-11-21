"""Admin API: auth, CSRF, lockout, decisions and the no-overlap invariant."""
from app.timeutil import fmt_dt, now_local
from datetime import timedelta
from tests.helpers import ADMIN_PASSWORD, ServerTestCase

ADMIN_ROUTES = [
    ("GET", "/api/admin/requests"),
    ("POST", "/api/admin/requests/1/approve"),
    ("POST", "/api/admin/requests/1/reject"),
    ("POST", "/api/admin/requests/1/cancel"),
    ("GET", "/api/admin/facilities"),
    ("POST", "/api/admin/facilities"),
    ("PUT", "/api/admin/facilities/1"),
    ("PUT", "/api/admin/facilities/1/hours"),
    ("GET", "/api/admin/blocks"),
    ("POST", "/api/admin/blocks"),
    ("DELETE", "/api/admin/blocks/1"),
    ("POST", "/api/auth/logout"),
]


class AuthTests(ServerTestCase):
    CONFIG = {"login_max_failures": 5}

    def test_admin_routes_require_session(self):
        c = self.client()
        for method, path in ADMIN_ROUTES:
            status, _, _ = c.request(method, path, {} if method != "GET" else None)
            self.assertEqual(status, 401, (method, path))
        c.cookie = "ccdr_session=forged-token-value"
        for method, path in ADMIN_ROUTES:
            status, _, _ = c.request(method, path, {} if method != "GET" else None)
            self.assertEqual(status, 401, (method, path))

    def test_csrf_required_on_writes(self):
        c = self.admin()
        good = c.csrf
        for value in (None, "wrong-token"):
            c.csrf = value
            for method, path in ADMIN_ROUTES:
                if method == "GET":
                    continue
                status, _, _ = c.request(method, path, {})
                self.assertEqual(status, 403, (value, method, path))
        c.csrf = good
        status, _, _ = c.request("GET", "/api/admin/requests")
        self.assertEqual(status, 200)

    def test_cookie_flags(self):
        c = self.client()
        status, _, resp = c.request("POST", "/api/auth/login",
                                    {"username": "test-admin", "password": ADMIN_PASSWORD})
        self.assertEqual(status, 200)
        cookie = resp.getheader("Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

    def test_logout_invalidates_session(self):
        c = self.admin()
        old_cookie = c.cookie
        status, _, _ = c.request("POST", "/api/auth/logout", {})
        self.assertEqual(status, 200)
        c.cookie = old_cookie
        status, _, _ = c.request("GET", "/api/admin/requests")
        self.assertEqual(status, 401)

    def test_lockout_after_five_failures(self):
        conn = self.db()
        from app.auth import create_user
        create_user(conn, "lock-user", "lock-user-password-123", "admin", now_local())
        conn.close()
        c = self.client()
        for _ in range(5):
            status, data = c.login("lock-user", "wrong-password")
            self.assertEqual(status, 401)
        # Correct password is refused while locked, with the same generic message.
        status, data = c.login("lock-user", "lock-user-password-123")
        self.assertEqual(status, 401)
        conn = self.db()
        conn.execute("UPDATE users SET locked_until = ? WHERE username = 'lock-user'",
                     (fmt_dt(now_local() - timedelta(minutes=1)),))
        conn.close()
        status, _ = c.login("lock-user", "lock-user-password-123")
        self.assertEqual(status, 200)

    def test_me_without_session_reveals_nothing(self):
        status, data, _ = self.client().request("GET", "/api/auth/me")
        self.assertEqual((status, data), (200, {"authenticated": False}))
        c = self.admin()
        status, data, _ = c.request("GET", "/api/auth/me")
        self.assertTrue(data["authenticated"])
        self.assertEqual(data["csrf_token"], c.csrf)

    def test_unknown_user_same_message(self):
        _, a = self.client().login("nobody-here", "whatever-password")
        _, b = self.client().login("test-admin", "wrong-password-x")
        self.assertEqual(a, b)

    def test_session_idle_expiry(self):
        c = self.admin()
        conn = self.db()
        conn.execute("UPDATE sessions SET expires_at = ?", (fmt_dt(now_local() - timedelta(minutes=1)),))
        conn.close()
        status, _, _ = c.request("GET", "/api/admin/requests")
        self.assertEqual(status, 401)


class DecisionTests(ServerTestCase):
    def test_approve_warns_about_overlapping_pending(self):
        a, _ = self.create_request(offset=5, start="08:00", end="10:00")
        b, _ = self.create_request(offset=5, start="09:00", end="11:00")
        c_id, _ = self.create_request(offset=5, start="10:00", end="12:00")
        admin = self.admin()
        status, inbox, _ = admin.request("GET", "/api/admin/requests?status=pending")
        item = next(r for r in inbox["requests"] if r["id"] == a)
        self.assertEqual([x["id"] for x in item["conflicts"] if x["kind"] == "pending"], [b])
        self.assertNotIn("public_token_hash", item)
        status, data, _ = admin.request("POST", f"/api/admin/requests/{a}/approve", {"admin_note": "OK"})
        self.assertEqual(status, 200)
        self.assertEqual([p["id"] for p in data["overlapping_pending"]], [b])  # not rejected automatically
        status, data, _ = admin.request("POST", f"/api/admin/requests/{b}/approve", {})
        self.assertEqual(status, 409)
        self.assertEqual(data["conflicts"][0]["kind"], "approved")
        status, _, _ = admin.request("POST", f"/api/admin/requests/{c_id}/approve", {})
        self.assertEqual(status, 200)  # touches a at 10:00, allowed
        status, _, _ = admin.request("POST", f"/api/admin/requests/{b}/reject", {"admin_note": "Choca"})
        self.assertEqual(status, 200)
        status, _, _ = admin.request("POST", f"/api/admin/requests/{b}/approve", {})
        self.assertEqual(status, 409)  # already decided

    def test_status_page_reflects_decision(self):
        rid, token = self.create_request(offset=6)
        c = self.client()
        self.assertEqual(c.request("POST", "/api/public/requests/status", {"token": token})[1]["status"], "pending")
        admin = self.admin()
        admin.request("POST", f"/api/admin/requests/{rid}/reject", {"admin_note": "Pruebe otro día"})
        status, data, _ = c.request("POST", "/api/public/requests/status", {"token": token})
        self.assertEqual((data["status"], data["admin_note"]), ("rejected", "Pruebe otro día"))

    def test_cancel_frees_the_slot(self):
        rid, _ = self.create_request(offset=7)
        admin = self.admin()
        admin.request("POST", f"/api/admin/requests/{rid}/approve", {})
        status, _, _ = self.client().request("POST", "/api/public/requests", self.request_payload(offset=7))
        self.assertEqual(status, 409)
        status, _, _ = admin.request("POST", f"/api/admin/requests/{rid}/cancel", {})
        self.assertEqual(status, 200)
        status, _, _ = self.client().request("POST", "/api/public/requests", self.request_payload(offset=7))
        self.assertEqual(status, 201)

    def test_expired_request_cannot_be_approved(self):
        conn = self.db()
        past = now_local() - timedelta(days=1)
        cur = conn.execute(
            "INSERT INTO booking_requests (facility_id, starts_at, ends_at, requester_name, phone, purpose, "
            "expected_attendees, status, public_token_hash, consent_at, consent_version, created_at) "
            "VALUES (?, ?, ?, 'X', '8888-0000', 'p', 1, 'pending', 'expired-hash', ?, 'v', ?)",
            (self.facility_id, fmt_dt(past.replace(hour=10, minute=0)), fmt_dt(past.replace(hour=11, minute=0)),
             fmt_dt(past), fmt_dt(past)))
        rid = cur.lastrowid
        conn.close()
        admin = self.admin()
        status, data, _ = admin.request("GET", "/api/admin/requests?status=expired")
        self.assertIn(rid, [r["id"] for r in data["requests"]])
        status, _, _ = admin.request("POST", f"/api/admin/requests/{rid}/approve", {})
        self.assertEqual(status, 409)

    def test_block_over_approved_is_refused(self):
        rid, _ = self.create_request(offset=8, start="16:00", end="18:00")
        admin = self.admin()
        admin.request("POST", f"/api/admin/requests/{rid}/approve", {})
        d = self.day(8)
        status, data, _ = admin.request("POST", "/api/admin/blocks", {
            "facility_id": self.facility_id, "starts_at": f"{d}T17:00", "ends_at": f"{d}T19:00",
            "reason": "Mantenimiento", "recurrence": "none"})
        self.assertEqual(status, 409)
        self.assertEqual([a["id"] for a in data["affected"]], [rid])
        # A weekly block starting a week earlier also reaches the booking.
        earlier = self.day(1)
        status, data, _ = admin.request("POST", "/api/admin/blocks", {
            "facility_id": self.facility_id, "starts_at": f"{earlier}T17:30", "ends_at": f"{earlier}T18:30",
            "reason": "Escuela", "public_label": "Escuela", "recurrence": "weekly",
            "recurrence_until": self.day(30)})
        self.assertEqual(status, 409)

    def test_pending_over_new_block_cannot_be_approved(self):
        rid, _ = self.create_request(offset=9, start="16:00", end="18:00")
        admin = self.admin()
        d = self.day(9)
        status, created, _ = admin.request("POST", "/api/admin/blocks", {
            "facility_id": self.facility_id, "starts_at": f"{d}T17:00", "ends_at": f"{d}T19:00",
            "reason": "Mantenimiento", "recurrence": "none"})
        self.assertEqual(status, 201)
        status, data, _ = admin.request("POST", f"/api/admin/requests/{rid}/approve", {})
        self.assertEqual(status, 409)
        self.assertEqual(data["conflicts"][0]["kind"], "block")
        status, _, _ = admin.request("DELETE", f"/api/admin/blocks/{created['id']}", {})
        self.assertEqual(status, 200)
        status, _, _ = admin.request("POST", f"/api/admin/requests/{rid}/approve", {})
        self.assertEqual(status, 200)

    def test_hours_change_and_deactivation_protect_approved(self):
        admin = self.admin()
        status, created, _ = admin.request("POST", "/api/admin/facilities", {
            "name": "Cancha temporal", "capacity": 10, "active": True})
        self.assertEqual(status, 201)
        fid = created["id"]
        all_day = [{"weekday": w, "opens_at": "06:00", "closes_at": "22:00"} for w in range(7)]
        self.assertEqual(admin.request("PUT", f"/api/admin/facilities/{fid}/hours", {"hours": all_day})[0], 200)
        status, data, _ = self.client().request("POST", "/api/public/requests",
                                                self.request_payload(offset=10, start="20:00", end="21:00",
                                                                     facility_id=fid, expected_attendees=5))
        self.assertEqual(status, 201)
        conn = self.db()
        rid = conn.execute("SELECT id FROM booking_requests WHERE facility_id = ?", (fid,)).fetchone()[0]
        conn.close()
        self.assertEqual(admin.request("POST", f"/api/admin/requests/{rid}/approve", {})[0], 200)
        short = [{"weekday": w, "opens_at": "06:00", "closes_at": "18:00"} for w in range(7)]
        status, data, _ = admin.request("PUT", f"/api/admin/facilities/{fid}/hours", {"hours": short})
        self.assertEqual(status, 409)
        status, fac, _ = admin.request("GET", "/api/admin/facilities")
        hours = next(f for f in fac["facilities"] if f["id"] == fid)["opening_hours"]
        self.assertEqual(hours[0]["closes_at"], "22:00")  # rolled back
        status, _, _ = admin.request("PUT", f"/api/admin/facilities/{fid}", {
            "name": "Cancha temporal", "capacity": 10, "active": False})
        self.assertEqual(status, 409)
        bad = [{"weekday": 0, "opens_at": "08:00", "closes_at": "12:00"},
               {"weekday": 0, "opens_at": "11:00", "closes_at": "14:00"}]
        self.assertEqual(admin.request("PUT", f"/api/admin/facilities/{fid}/hours", {"hours": bad})[0], 422)
