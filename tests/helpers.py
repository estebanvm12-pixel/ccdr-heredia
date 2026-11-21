"""Test helpers: a real ThreadingHTTPServer on a temporary SQLite file."""
import http.client
import json
import shutil
import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path

from app.auth import create_user
from app.config import load_config
from app.db import connect
from app.timeutil import fmt_date, now_local
from server import App, make_server

ADMIN_USER = "test-admin"
ADMIN_PASSWORD = "test-password-only-for-tests"


class Client:
    """Minimal HTTP client that keeps a cookie and the CSRF token."""

    def __init__(self, port):
        self.port = port
        self.cookie = None
        self.csrf = None

    def request(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        hdrs = {"Host": f"127.0.0.1:{self.port}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        if raw is not None:
            data = raw
        if self.cookie:
            hdrs["Cookie"] = self.cookie
        if self.csrf and method != "GET":
            hdrs["X-CSRF-Token"] = self.csrf
        hdrs.update(headers or {})
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        text = resp.read().decode("utf-8", errors="replace")
        set_cookie = resp.getheader("Set-Cookie")
        if set_cookie and set_cookie.startswith("ccdr_session="):
            value = set_cookie.split(";")[0]
            self.cookie = None if value == "ccdr_session=" else value
        conn.close()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = text
        return resp.status, payload, resp

    def login(self, username=ADMIN_USER, password=ADMIN_PASSWORD):
        status, data, _ = self.request("POST", "/api/auth/login", {"username": username, "password": password})
        if status == 200:
            self.csrf = data["csrf_token"]
        return status, data


class ServerTestCase(unittest.TestCase):
    """Starts one server per test class. Override CONFIG to tweak limits."""

    CONFIG = {}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="ccdr-test-")
        overrides = {
            "db_path": str(Path(cls.tmp) / "test.db"),
            "request_rate_per_hour": 10_000,
            "status_rate_per_hour": 10_000,
            "login_rate_per_hour": 10_000,
            "access_log": False,
        }
        overrides.update(cls.CONFIG)
        cls.app = App(load_config(path=Path(cls.tmp) / "missing.json", overrides=overrides))
        cls.server = make_server(cls.app, port=0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        conn = connect(cls.app.db_path)
        create_user(conn, ADMIN_USER, ADMIN_PASSWORD, "admin", now_local())
        cur = conn.execute(
            "INSERT INTO facilities (name, location, sports, capacity, rules_text, active) "
            "VALUES ('Cancha de prueba', 'Lugar de prueba', 'Fútbol', 50, 'Reglas', 1)"
        )
        cls.facility_id = cur.lastrowid
        for weekday in range(7):
            conn.execute(
                "INSERT INTO opening_hours (facility_id, weekday, opens_at, closes_at) "
                "VALUES (?, ?, '06:00', '22:00')",
                (cls.facility_id, weekday),
            )
        conn.close()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def client(self):
        return Client(self.port)

    def admin(self):
        c = self.client()
        status, _ = c.login()
        self.assertEqual(status, 200)
        return c

    def db(self):
        return connect(self.app.db_path)

    @staticmethod
    def day(offset):
        return fmt_date(now_local().date() + timedelta(days=offset))

    def request_payload(self, offset=5, start="10:00", end="12:00", **extra):
        d = self.day(offset)
        payload = {
            "facility_id": self.facility_id,
            "starts_at": f"{d}T{start}",
            "ends_at": f"{d}T{end}",
            "requester_name": "Persona de Prueba",
            "organization": "Club de Prueba",
            "phone": "8888-0000",
            "email": "prueba@example.com",
            "purpose": "Entrenamiento de prueba",
            "expected_attendees": 10,
            "consent": True,
            "website": "",
        }
        payload.update(extra)
        return payload

    def create_request(self, **kwargs):
        status, data, _ = self.client().request("POST", "/api/public/requests", self.request_payload(**kwargs))
        self.assertEqual(status, 201, data)
        conn = self.db()
        row = conn.execute("SELECT id FROM booking_requests ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        return row["id"], data["token"]
