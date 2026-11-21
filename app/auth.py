"""Users, PBKDF2 password hashing, sessions and CSRF."""
import hashlib
import hmac
import secrets
from datetime import timedelta

from .security import ApiError, Validator
from .timeutil import fmt_dt

PBKDF2_ITERATIONS = 600_000
SESSION_COOKIE = "ccdr_session"
# Used to keep login timing similar when the username does not exist.
_DUMMY_SALT = secrets.token_hex(16)


def hash_password(password, salt_hex):
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), PBKDF2_ITERATIONS
    )
    return digest.hex()


def sha256_hex(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_new_password(password):
    if not isinstance(password, str) or len(password) < 12:
        raise ValueError("Password must have at least 12 characters")
    if len(password) > 256:
        raise ValueError("Password is too long")


def create_user(conn, username, password, role, now):
    validate_new_password(password)
    salt = secrets.token_hex(16)
    conn.execute(
        "INSERT INTO users (username, password_hash, salt, role, active, created_at) "
        "VALUES (?, ?, ?, ?, 1, ?)",
        (username, hash_password(password, salt), salt, role, fmt_dt(now)),
    )


def authenticate(conn, username, password, now, cfg):
    """Return the user row on success, None otherwise. Applies lockout."""
    row = conn.execute(
        "SELECT * FROM users WHERE username = ? AND active = 1", (username,)
    ).fetchone()
    if row is None:
        hash_password(password, _DUMMY_SALT)
        return None
    if row["locked_until"] and row["locked_until"] > fmt_dt(now):
        hash_password(password, _DUMMY_SALT)
        return None
    candidate = hash_password(password, row["salt"])
    if hmac.compare_digest(candidate, row["password_hash"]):
        conn.execute(
            "UPDATE users SET failed_logins = 0, locked_until = NULL WHERE id = ?", (row["id"],)
        )
        return row
    failures = row["failed_logins"] + 1
    locked_until = None
    if failures >= cfg["login_max_failures"]:
        locked_until = fmt_dt(now + timedelta(minutes=cfg["lockout_minutes"]))
        failures = 0
    conn.execute(
        "UPDATE users SET failed_logins = ?, locked_until = ? WHERE id = ?",
        (failures, locked_until, row["id"]),
    )
    return None


def create_session(conn, user_id, now, cfg):
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    expires = now + timedelta(hours=cfg["session_idle_hours"])
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, csrf_token, last_seen_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (sha256_hex(token), user_id, csrf, fmt_dt(now), fmt_dt(expires)),
    )
    return token, csrf


def get_session(conn, token, now, cfg):
    """Return a dict with session + user data, or None. Slides the idle expiry."""
    if not token or len(token) > 200:
        return None
    token_hash = sha256_hex(token)
    row = conn.execute(
        "SELECT s.token_hash, s.csrf_token, s.expires_at, u.id AS user_id, u.username, u.role "
        "FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash = ? AND u.active = 1",
        (token_hash,),
    ).fetchone()
    if row is None:
        return None
    if row["expires_at"] <= fmt_dt(now):
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        return None
    expires = now + timedelta(hours=cfg["session_idle_hours"])
    conn.execute(
        "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
        (fmt_dt(now), fmt_dt(expires), token_hash),
    )
    return dict(row)


def delete_session(conn, token):
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (sha256_hex(token),))


def purge_expired_sessions(conn, now):
    conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (fmt_dt(now),))


def session_cookie(token, cfg, clear=False):
    parts = [f"{SESSION_COOKIE}={'' if clear else token}", "Path=/", "HttpOnly", "SameSite=Strict"]
    if clear:
        parts.append("Max-Age=0")
    if cfg["secure_cookies"]:
        parts.append("Secure")
    return "; ".join(parts)


# ---- HTTP handlers -------------------------------------------------------


def handle_login(app, req):
    if not app.login_limiter.allow(req.ip):
        raise ApiError(429, "Demasiados intentos. Espere un momento e intente de nuevo.")
    v = Validator(req.body)
    username = v.text("username", 64)
    password = req.body.get("password") if isinstance(req.body.get("password"), str) else ""
    if not password or len(password) > 256:
        v.add("password", "Este campo es obligatorio.")
    v.raise_if_errors()
    user = authenticate(req.conn, username, password, req.now, app.config)
    if user is None:
        raise ApiError(
            401,
            "Usuario o contraseña incorrectos, o cuenta bloqueada temporalmente "
            f"tras {app.config['login_max_failures']} intentos fallidos.",
        )
    token, csrf = create_session(req.conn, user["id"], req.now, app.config)
    body = {"username": user["username"], "role": user["role"], "csrf_token": csrf}
    return 200, body, [("Set-Cookie", session_cookie(token, app.config))]


def handle_logout(app, req):
    delete_session(req.conn, req.session_token)
    return 200, {"ok": True}, [("Set-Cookie", session_cookie("", app.config, clear=True))]


def handle_me(app, req):
    # Always 200 so the admin page can check its login state without console errors.
    if req.session is None or req.session["role"] != "admin":
        return 200, {"authenticated": False}
    s = req.session
    return 200, {"authenticated": True, "username": s["username"], "role": s["role"],
                 "csrf_token": s["csrf_token"]}
