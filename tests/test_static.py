"""Static serving, security headers and that data/ is never reachable."""
import re
from pathlib import Path

from tests.helpers import ServerTestCase

STATIC = Path(__file__).resolve().parent.parent / "static"


class StaticTests(ServerTestCase):
    def test_security_headers(self):
        for path in ["/", "/api/public/facilities", "/admin/"]:
            _, _, resp = self.client().request("GET", path)
            csp = resp.getheader("Content-Security-Policy")
            self.assertIn("default-src 'self'", csp)
            self.assertIn("script-src 'self'", csp)
            self.assertNotIn("unsafe-inline", csp)
            self.assertEqual(resp.getheader("X-Content-Type-Options"), "nosniff")
            self.assertEqual(resp.getheader("Referrer-Policy"), "no-referrer")
            self.assertEqual(resp.getheader("X-Frame-Options"), "DENY")

    def test_traversal_and_data_dir_blocked(self):
        for path in ["/static/../server.py", "/static/..%2fserver.py", "/static/../data/app.db",
                     "/data/app.db", "/static/..\\server.py", "/config.local.json", "/SPEC.md",
                     "/static/../app/auth.py", "/.git/config", "/static///localhost/share/x.js",
                     "/static/C:/Windows/win.ini", "/static/C:%5CWindows%5Cwin.ini",
                     "/static/public/index.html", "/static/public/../../server.py",
                     "/static/%2e%2e/server.py", "/static/common.js/", "/static/img/../server.py",
                     "/static/img/x.js", "/static/public/x.jpg"]:
            status, _, _ = self.client().request("GET", path)
            self.assertEqual(status, 404, path)

    def test_allowed_static_files_still_served(self):
        for path in ["/static/common.js", "/static/common.css", "/static/favicon.svg",
                     "/static/public/calendar.js", "/static/admin/admin.js", "/favicon.ico",
                     "/presentacion", "/static/img/calendario.jpg", "/static/public/presentacion.js"]:
            status, _, _ = self.client().request("GET", path)
            self.assertEqual(status, 200, path)

    def test_head_requests(self):
        status, body, resp = self.client().request("HEAD", "/")
        self.assertEqual(status, 200)
        self.assertEqual(body, "")
        self.assertIsNotNone(resp.getheader("Content-Security-Policy"))

    def test_admin_redirect(self):
        status, _, resp = self.client().request("GET", "/admin")
        self.assertEqual(status, 301)
        self.assertEqual(resp.getheader("Location"), "/admin/")

    def test_frontend_never_uses_innerhtml(self):
        for js in STATIC.rglob("*.js"):
            text = js.read_text(encoding="utf-8")
            code = [line for line in text.splitlines() if "innerHTML is not allowed" not in line
                    and 'key === "innerHTML"' not in line]
            joined = "\n".join(code)
            self.assertNotRegex(joined, r"\.(inner|outer)HTML\s*=", js.name)
            self.assertNotIn("insertAdjacentHTML", joined, js.name)
            self.assertNotIn("document.write", joined, js.name)
            self.assertNotRegex(joined, r"\beval\(", js.name)

    def test_no_inline_scripts_or_styles(self):
        for html in STATIC.rglob("*.html"):
            text = html.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"<script(?![^>]*\bsrc=)[^>]*>", text), html.name)
            self.assertNotIn("style=", text, html.name)
            self.assertNotRegex(text, r"\son[a-z]+=", html.name)

    def test_no_price_fields(self):
        root = STATIC.parent
        for path in list(root.glob("app/*.py")) + list(STATIC.rglob("*.*")):
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            for word in ("price", "fee", "tarifa", "precio", "₡"):
                self.assertNotIn(word, text, f"{word} in {path.name}")
