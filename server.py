"""CCDR Heredia booking prototype: HTTP server and router (stdlib only)."""
import json
import os
import re
import sys
import threading
import traceback
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app import auth, bookings, demo
from app.config import BASE_DIR, load_config
from app.db import connect, init_db
from app.security import MAX_BODY_BYTES, SECURITY_HEADERS, ApiError, RateLimiter
from app.timeutil import now_local

PORT = 8770
HOST = "127.0.0.1"

STATIC_DIR = BASE_DIR / "static"
PAGES = {
    "/": "public/index.html",
    "/solicitar": "public/solicitar.html",
    "/estado": "public/estado.html",
    "/presentacion": "public/presentacion.html",
    "/admin/": "admin/index.html",
}
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
}

# (method, pattern, handler, access). access: "public" or "admin".
# Every non-GET admin route also requires a valid X-CSRF-Token header.
ROUTES = [
    ("GET", r"/api/public/facilities", bookings.handle_public_facilities, "public"),
    ("GET", r"/api/public/availability", bookings.handle_public_availability, "public"),
    ("POST", r"/api/public/requests", bookings.handle_public_create_request, "public"),
    ("POST", r"/api/public/requests/status", bookings.handle_public_status, "public"),
    ("POST", r"/api/auth/login", auth.handle_login, "public"),
    ("POST", r"/api/auth/logout", auth.handle_logout, "admin"),
    ("GET", r"/api/auth/me", auth.handle_me, "public"),
    ("GET", r"/api/admin/requests", bookings.handle_admin_list_requests, "admin"),
    ("POST", r"/api/admin/requests/(\d{1,9})/approve", bookings.handle_admin_approve, "admin"),
    ("POST", r"/api/admin/requests/(\d{1,9})/reject", bookings.handle_admin_reject, "admin"),
    ("POST", r"/api/admin/requests/(\d{1,9})/cancel", bookings.handle_admin_cancel, "admin"),
    ("GET", r"/api/admin/facilities", bookings.handle_admin_list_facilities, "admin"),
    ("POST", r"/api/admin/facilities", bookings.handle_admin_create_facility, "admin"),
    ("PUT", r"/api/admin/facilities/(\d{1,9})", bookings.handle_admin_update_facility, "admin"),
    ("PUT", r"/api/admin/facilities/(\d{1,9})/hours", bookings.handle_admin_set_hours, "admin"),
    ("GET", r"/api/admin/blocks", bookings.handle_admin_list_blocks, "admin"),
    ("POST", r"/api/admin/blocks", bookings.handle_admin_create_block, "admin"),
    ("DELETE", r"/api/admin/blocks/(\d{1,9})", bookings.handle_admin_delete_block, "admin"),
]
# Static paths must match this before touching the filesystem (blocks "..",
# absolute paths, drive letters and UNC shares on Windows).
STATIC_PATH_RE = re.compile(
    r"^(?:(?:public|admin)/)?[a-z0-9_-]{1,40}\.(?:css|js|svg)$|^img/[a-z0-9_-]{1,40}\.(?:jpg|png)$"
)
COMPILED_ROUTES = [(m, re.compile("^" + p + "$"), h, a) for m, p, h, a in ROUTES]


class App:
    """Shared state for all request threads."""

    def __init__(self, config, now_func=None):
        self.config = config
        self.now = now_func or now_local
        self.db_path = config["db_path"]
        init_db(self.db_path)
        self.request_limiter = RateLimiter(config["request_rate_per_hour"], 3600)
        self.status_limiter = RateLimiter(config["status_rate_per_hour"], 3600)
        self.login_limiter = RateLimiter(config["login_rate_per_hour"], 3600)

    def load_demo(self):
        """Demo mode: wipe everything and reload fictitious data (admin password from env)."""
        password = os.environ.get("CCDR_DEMO_ADMIN_PASSWORD", "")
        if len(password) < 12:
            raise SystemExit("Demo mode needs CCDR_DEMO_ADMIN_PASSWORD (12+ characters).")
        conn = connect(self.db_path)
        try:
            demo.load_demo_data(conn, self.now(), password, self.config["consent_version"], wipe=True)
        finally:
            conn.close()
        print("[demo] fictitious data reloaded", file=sys.stderr, flush=True)

    def run_maintenance(self):
        conn = connect(self.db_path)
        try:
            now = self.now()
            count = bookings.anonymize_old_requests(conn, now, self.config["retention_months"])
            auth.purge_expired_sessions(conn, now)
            if count:
                print(f"[maintenance] anonymized {count} old requests", file=sys.stderr)
        finally:
            conn.close()


class Request:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class Handler(BaseHTTPRequestHandler):
    server_version = "CCDRH"
    sys_version = ""
    timeout = 30  # seconds; drops slow or idle clients (slowloris)
    app = None  # set by make_server

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        # Link previews and uptime checks use HEAD; same as GET without a body.
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def log_message(self, fmt, *args):
        if not self.app.config.get("access_log", True):
            return
        # Paths never contain tracking tokens (they travel in the URL fragment).
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---- responses ----

    def _drain_body(self):
        """Discard an unread request body so the client receives our response.

        Closing a socket with unread data makes Windows reset the connection.
        """
        if getattr(self, "_body_read", True):
            return
        self._body_read = True
        length = self.headers.get("Content-Length", "0")
        remaining = min(int(length), 1024 * 1024) if length.isdigit() else 0
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                break
            remaining -= len(chunk)

    def _send(self, status, body, content_type, extra_headers=(), cache="no-store"):
        self._drain_body()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        if self.app.config["secure_cookies"]:
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status, payload, extra_headers=()):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", extra_headers)

    # ---- dispatch ----

    def _dispatch(self, method):
        self._body_read = method == "GET"
        try:
            path = urlsplit(self.path).path
            if path.startswith("/api/"):
                self._handle_api(method, path)
            elif method == "GET":
                self._serve_static(path)
            else:
                self._send_json(405, {"error": "Método no permitido."})
        except Exception:
            traceback.print_exc()
            try:
                self._send_json(500, {"error": "Error interno. Intente de nuevo."})
            except Exception:
                pass

    def _read_body(self, method):
        if method == "GET":
            return {}
        length_header = self.headers.get("Content-Length", "0")
        if not length_header.isdigit():
            raise ApiError(400, "Solicitud inválida.")
        length = int(length_header)
        if length > MAX_BODY_BYTES:
            raise ApiError(413, "La solicitud es demasiado grande.")
        if length == 0:
            self._body_read = True
            return {}
        content_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type != "application/json":
            raise ApiError(415, "Formato no soportado.")
        try:
            raw = self.rfile.read(length)
            self._body_read = True
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "JSON inválido.")
        if not isinstance(data, dict):
            raise ApiError(400, "JSON inválido.")
        return data

    def _session_token(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            cookie = SimpleCookie(raw)
        except CookieError:
            return None
        morsel = cookie.get(auth.SESSION_COOKIE)
        return morsel.value if morsel else None

    def _client_ip(self):
        """Socket address, or the first X-Forwarded-For entry when behind a trusted proxy."""
        if self.app.config["trust_proxy"]:
            forwarded = self.headers.get("X-Forwarded-For", "")
            first = forwarded.split(",")[0].strip()
            if first and len(first) <= 45:
                return first
        return self.client_address[0]

    def _check_origin(self):
        """Reject cross-site state-changing requests (defense in depth with CSRF)."""
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host", ""):
            raise ApiError(403, "Origen no permitido.")

    def _handle_api(self, method, path):
        app = self.app
        conn = None
        try:
            matched, params = None, ()
            path_known = False
            for m, pattern, handler, access in COMPILED_ROUTES:
                match = pattern.match(path)
                if match:
                    path_known = True
                    if m == method:
                        matched, params = (handler, access), match.groups()
                        break
            if matched is None:
                raise ApiError(405 if path_known else 404,
                               "Método no permitido." if path_known else "No encontrado.")
            handler, access = matched
            if method != "GET":
                self._check_origin()
            body = self._read_body(method)
            conn = connect(app.db_path)
            now = app.now()
            token = self._session_token()
            session = auth.get_session(conn, token, now, app.config) if token else None
            if access == "admin":
                if session is None:
                    raise ApiError(401, "Su sesión expiró. Inicie sesión de nuevo.")
                if session["role"] != "admin":
                    raise ApiError(403, "No tiene permiso para esta acción.")
                if method != "GET":
                    sent = self.headers.get("X-CSRF-Token", "")
                    if not auth.hmac.compare_digest(sent.encode(), session["csrf_token"].encode()):
                        raise ApiError(403, "Token de seguridad inválido. Recargue la página.")
            req = Request(
                method=method, path=path, params=params,
                query=parse_qs(urlsplit(self.path).query, max_num_fields=20),
                body=body, ip=self._client_ip(), conn=conn, now=now,
                session=session, session_token=token,
            )
            result = handler(app, req)
            status, payload = result[0], result[1]
            extra = result[2] if len(result) > 2 else ()
            self._send_json(status, payload, extra)
        except ApiError as err:
            self._send_json(err.status, err.payload())
        finally:
            if conn is not None:
                conn.close()

    def _serve_static(self, path):
        if path == "/admin":
            self.send_response(301)
            self.send_header("Location", "/admin/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/favicon.ico":
            file_path = STATIC_DIR / "favicon.svg"
        elif path in PAGES:
            file_path = STATIC_DIR / PAGES[path]
        elif path.startswith("/static/") and STATIC_PATH_RE.match(path[len("/static/"):]):
            file_path = STATIC_DIR / path[len("/static/"):]
        else:
            return self._send_json(404, {"error": "No encontrado."})
        content_type = STATIC_TYPES.get(file_path.suffix.lower())
        if content_type is None or not file_path.is_file():
            return self._send_json(404, {"error": "No encontrado."})
        self._send(200, file_path.read_bytes(), content_type, cache="no-cache")


def make_server(app, host=HOST, port=PORT):
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def _maintenance_loop(app, stop_event):
    hours = app.config["demo_reset_hours"] if app.config["demo_mode"] else 24
    while not stop_event.wait(hours * 3600):
        if app.config["demo_mode"]:
            app.load_demo()
        app.run_maintenance()


def main():
    config = load_config()
    if config["athletes_enabled"]:
        print("athletes_enabled=true but the athletes module is not implemented yet (phase 2).",
              file=sys.stderr)
    app = App(config)
    if config["demo_mode"]:
        app.load_demo()
    app.run_maintenance()
    stop = threading.Event()
    threading.Thread(target=_maintenance_loop, args=(app, stop), daemon=True).start()
    host = os.environ.get("HOST", HOST)
    port = int(os.environ.get("PORT", PORT))
    server = make_server(app, host, port)
    print(f"CCDR Heredia prototype on http://{host}:{port}/ (admin: /admin/)"
          f"{' [demo mode]' if config['demo_mode'] else ''}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
