"""Rate limiting, security headers, API errors and input validation."""
import re
import threading
import time
from collections import deque

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}

MAX_BODY_BYTES = 16 * 1024


class ApiError(Exception):
    """Error returned to the client as JSON. `message` is user-facing Spanish text."""

    def __init__(self, status, message, fields=None, extra=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.fields = fields or {}
        self.extra = extra or {}

    def payload(self):
        body = {"error": self.message}
        if self.fields:
            body["fields"] = self.fields
        body.update(self.extra)
        return body


class RateLimiter:
    """In-memory sliding-window limiter keyed by client IP. Thread-safe."""

    def __init__(self, limit, window_seconds):
        self.limit = limit
        self.window = window_seconds
        self._hits = {}
        self._lock = threading.Lock()

    def allow(self, key):
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            if len(self._hits) > 10_000:
                self._prune(now)
            return True

    def _prune(self, now):
        for key in [k for k, v in self._hits.items() if not v or now - v[-1] > self.window]:
            del self._hits[key]


_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PHONE_RE = re.compile(r"^\+?[0-9][0-9 \-]{6,18}[0-9]$")
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,24}$")


class Validator:
    """Collects field errors so the client gets all problems at once."""

    def __init__(self, data):
        self.data = data if isinstance(data, dict) else {}
        self.errors = {}

    def text(self, field, max_len, required=True, min_len=1, multiline=False):
        value = self.data.get(field, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            self.errors[field] = "Valor inválido."
            return ""
        value = _CONTROL_RE.sub("", value).strip()
        if not multiline:
            value = re.sub(r"\s+", " ", value)
        if not value:
            if required:
                self.errors[field] = "Este campo es obligatorio."
            return ""
        if len(value) < min_len:
            self.errors[field] = f"Debe tener al menos {min_len} caracteres."
        elif len(value) > max_len:
            self.errors[field] = f"Debe tener como máximo {max_len} caracteres."
        return value

    def integer(self, field, minimum, maximum, required=True):
        value = self.data.get(field)
        if value in (None, ""):
            if required:
                self.errors[field] = "Este campo es obligatorio."
            return None
        if isinstance(value, bool):
            self.errors[field] = "Debe ser un número entero."
            return None
        if isinstance(value, str) and re.fullmatch(r"-?\d{1,9}", value.strip()):
            value = int(value.strip())
        if not isinstance(value, int):
            self.errors[field] = "Debe ser un número entero."
            return None
        if value < minimum or value > maximum:
            self.errors[field] = f"Debe estar entre {minimum} y {maximum}."
            return None
        return value

    def boolean(self, field):
        value = self.data.get(field)
        if not isinstance(value, bool):
            self.errors[field] = "Valor inválido."
            return False
        return value

    def phone(self, field):
        value = self.text(field, 20, min_len=8)
        if value and field not in self.errors and not _PHONE_RE.match(value):
            self.errors[field] = "Escriba un teléfono válido, por ejemplo 8888-8888."
        return value

    def email(self, field, required=False):
        value = self.text(field, 254, required=required)
        if value and field not in self.errors and not _EMAIL_RE.match(value):
            self.errors[field] = "Escriba un correo válido."
        return value

    def add(self, field, message):
        self.errors.setdefault(field, message)

    def raise_if_errors(self):
        if self.errors:
            raise ApiError(422, "Revise los datos marcados.", fields=self.errors)
