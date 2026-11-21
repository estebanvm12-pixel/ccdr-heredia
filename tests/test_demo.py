"""Demo mode, environment configuration and proxy-aware client IP."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.config import load_config
from app.db import connect, init_db
from app.demo import load_demo_data
from app.timeutil import now_local
from tests.helpers import ServerTestCase


class EnvConfigTests(unittest.TestCase):
    def test_env_overrides_with_types(self):
        env = {"CCDR_DEMO_MODE": "true", "CCDR_MIN_NOTICE_HOURS": "48", "CCDR_CONSENT_VERSION": "v9"}
        with mock.patch.dict(os.environ, env):
            cfg = load_config(path="does-not-exist.json")
        self.assertIs(cfg["demo_mode"], True)
        self.assertEqual(cfg["min_notice_hours"], 48)
        self.assertEqual(cfg["consent_version"], "v9")

    def test_env_wrong_type_rejected(self):
        with mock.patch.dict(os.environ, {"CCDR_MIN_NOTICE_HOURS": '"48"'}):
            with self.assertRaises(ValueError):
                load_config(path="does-not-exist.json")


class DemoDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = str(Path(self.tmp) / "demo.db")
        init_db(self.path)
        self.conn = connect(self.path)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def counts(self):
        return {t: self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("users", "facilities", "blocks", "booking_requests")}

    def test_reload_wipes_and_reseeds(self):
        load_demo_data(self.conn, now_local(), "demo-password-123", "v1")
        first = self.counts()
        # Visitor activity that must disappear on reset.
        self.conn.execute("INSERT INTO facilities (name) VALUES ('Creada por visitante')")
        load_demo_data(self.conn, now_local(), "demo-password-456", "v1", wipe=True)
        self.assertEqual(self.counts(), first)
        self.assertEqual(first["facilities"], 4)
        # No approved bookings overlap each other or a block in the demo set.
        clashes = self.conn.execute(
            "SELECT COUNT(*) FROM booking_requests a JOIN booking_requests b ON a.facility_id = b.facility_id "
            "AND a.id < b.id AND a.status = 'approved' AND b.status = 'approved' "
            "AND a.starts_at < b.ends_at AND b.starts_at < a.ends_at").fetchone()[0]
        self.assertEqual(clashes, 0)


class ProxyIpTests(ServerTestCase):
    CONFIG = {"trust_proxy": True, "request_rate_per_hour": 2}

    def test_rate_limit_uses_forwarded_ip(self):
        def post(ip, offset):
            return self.client().request("POST", "/api/public/requests", self.request_payload(offset=offset),
                                         headers={"X-Forwarded-For": f"{ip}, 10.0.0.1"})[0]
        self.assertEqual([post("203.0.113.5", 3), post("203.0.113.5", 4), post("203.0.113.5", 5)],
                         [201, 201, 429])
        self.assertEqual(post("198.51.100.7", 6), 201)  # another visitor is not blocked


class HstsTests(ServerTestCase):
    CONFIG = {"secure_cookies": True}

    def test_hsts_and_secure_cookie(self):
        _, _, resp = self.client().request("GET", "/")
        self.assertIn("max-age", resp.getheader("Strict-Transport-Security"))
        _, _, resp = self.client().request("POST", "/api/auth/login",
                                           {"username": "test-admin", "password": "test-password-only-for-tests"})
        self.assertIn("Secure", resp.getheader("Set-Cookie"))


if __name__ == "__main__":
    unittest.main()
