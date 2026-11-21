"""Acceptance criteria 3.5: no personal data in public output; unguessable tokens."""
import math
import re
from collections import Counter

from app.auth import sha256_hex
from tests.helpers import ServerTestCase

PII = {
    "requester_name": "ZZPRIVADO Nombre Secreto",
    "organization": "ZZPRIVADO Organizacion",
    "phone": "8123-4567",
    "email": "zzprivado@example.com",
    "purpose": "ZZPRIVADO proposito secreto",
}
MARKERS = ["ZZPRIVADO", "zzprivado", "8123-4567", "81234567"]


class PrivacyTests(ServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    def test_public_outputs_have_no_personal_data(self):
        approved_id, _ = self.create_request(offset=5, start="08:00", end="10:00", **PII)
        _pending_id, token = self.create_request(offset=5, start="12:00", end="14:00", **PII)
        admin = self.admin()
        status, _, _ = admin.request("POST", f"/api/admin/requests/{approved_id}/approve",
                                     {"admin_note": "Nota interna ZZPRIVADO"})
        self.assertEqual(status, 200)

        c = self.client()
        bodies = []
        for path in ["/", "/solicitar", "/estado", "/presentacion", "/static/public/calendar.js",
                     "/static/public/request.js", "/static/public/status.js",
                     "/api/public/facilities",
                     f"/api/public/availability?facility_id={self.facility_id}&week_start={self.day(5)}"]:
            status, payload, resp = c.request("GET", path)
            self.assertEqual(status, 200, path)
            bodies.append((path, str(payload)))
        status, payload, _ = c.request("POST", "/api/public/requests/status", {"token": token})
        self.assertEqual(status, 200)
        bodies.append(("status", str(payload)))
        for path, body in bodies:
            for marker in MARKERS:
                self.assertNotIn(marker, body, f"{marker} leaked in {path}")

        status, avail, _ = c.request(
            "GET", f"/api/public/availability?facility_id={self.facility_id}&week_start={self.day(5)}")
        busy = [b for b in avail["busy"] if b["starts_at"].startswith(self.day(5))]
        self.assertEqual(len(busy), 1)  # approved shows, pending does not
        self.assertEqual(set(busy[0]), {"starts_at", "ends_at", "label"})  # no ids
        self.assertEqual(busy[0]["label"], "Ocupado")
        self.assertEqual(set(payload), {"facility_name", "starts_at", "ends_at", "status",
                                        "admin_note", "created_at"})

    def test_block_public_label_vs_internal_reason(self):
        admin = self.admin()
        d = self.day(7)
        admin.request("POST", "/api/admin/blocks", {
            "facility_id": self.facility_id, "starts_at": f"{d}T08:00", "ends_at": f"{d}T10:00",
            "reason": "ZZPRIVADO motivo interno", "public_label": "Escuela de voleibol", "recurrence": "none"})
        admin.request("POST", "/api/admin/blocks", {
            "facility_id": self.facility_id, "starts_at": f"{d}T11:00", "ends_at": f"{d}T12:00",
            "reason": "ZZPRIVADO reparacion", "recurrence": "none"})
        status, avail, _ = self.client().request(
            "GET", f"/api/public/availability?facility_id={self.facility_id}&week_start={d}")
        labels = [b["label"] for b in avail["busy"] if b["starts_at"].startswith(d)]
        self.assertEqual(labels, ["Escuela de voleibol", "Ocupado"])
        self.assertNotIn("ZZPRIVADO", str(avail))


class TokenTests(ServerTestCase):
    def test_token_strength_and_storage(self):
        tokens = [self.create_request(offset=20, start=f"{8 + 2 * i:02d}:00", end=f"{9 + 2 * i:02d}:00")[1]
                  for i in range(5)]
        for t in tokens:
            self.assertRegex(t, r"^[A-Za-z0-9_-]{43}$")  # token_urlsafe(32) = 256 bits
        self.assertEqual(len(set(tokens)), 5)
        # Rough entropy check across all characters.
        counts = Counter("".join(tokens))
        total = sum(counts.values())
        entropy = -sum(n / total * math.log2(n / total) for n in counts.values())
        self.assertGreater(entropy, 5.0)
        conn = self.db()
        stored = [r[0] for r in conn.execute("SELECT public_token_hash FROM booking_requests")]
        dump = "\n".join(conn.iterdump())
        conn.close()
        for t in tokens:
            self.assertNotIn(t, dump)  # never stored in clear
            self.assertIn(sha256_hex(t), stored)

    def test_guessed_or_enumerated_tokens_fail(self):
        _, token = self.create_request(offset=21)
        c = self.client()
        guesses = ["1", "2", "a" * 43, token[:-1] + ("A" if token[-1] != "A" else "B"),
                   token.upper() if token.upper() != token else token.lower(), "", None, 123,
                   "' OR '1'='1' --" + "x" * 20]
        for g in guesses:
            status, payload, _ = c.request("POST", "/api/public/requests/status", {"token": g})
            self.assertEqual(status, 404, g)
            self.assertEqual(payload, {"error": "No encontramos una solicitud con ese enlace."})
        status, _, _ = c.request("POST", "/api/public/requests/status", {"token": token})
        self.assertEqual(status, 200)
        # There is no GET route that takes a token or an id.
        for path in [f"/api/public/requests/{token}", "/api/public/requests/1", f"/estado/{token}"]:
            status, _, _ = c.request("GET", path)
            self.assertIn(status, (404, 405), path)

    def test_pages_do_not_reference_token_in_server_paths(self):
        status, body, _ = self.client().request("GET", "/static/public/status.js")
        self.assertTrue(re.search(r"location\.hash", body))
