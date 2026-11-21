"""Configuration: defaults, then config.local.json (git-ignored), then CCDR_* environment variables."""
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DEFAULTS = {
    # Module switches. The athletes module (phase 2) is not implemented yet.
    "athletes_enabled": False,
    "db_path": str(BASE_DIR / "data" / "app.db"),
    # Set to true only when served over HTTPS.
    "secure_cookies": False,
    # Booking limits: configurable assumptions to confirm with the CCDR.
    "slot_minutes": 30,
    "min_minutes": 60,
    "max_minutes": 240,
    "horizon_days": 60,
    "min_notice_hours": 24,
    # Anti-abuse.
    "request_rate_per_hour": 5,
    "status_rate_per_hour": 30,
    "login_rate_per_hour": 20,
    "login_max_failures": 5,
    "lockout_minutes": 15,
    # Privacy and sessions.
    "retention_months": 12,
    "session_idle_hours": 8,
    "consent_version": "2026-11-20-draft",
    "access_log": True,
    # Public demo (e.g. Render): reload fictitious data at start and every N hours.
    "demo_mode": False,
    "demo_reset_hours": 24,
    # Behind a reverse proxy, take the client IP from X-Forwarded-For.
    "trust_proxy": False,
}


def load_config(path=None, overrides=None):
    """Return the effective configuration dict."""
    cfg = dict(DEFAULTS)
    config_path = Path(path) if path else BASE_DIR / "config.local.json"
    if config_path.exists():
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("config.local.json must contain a JSON object")
        for key, value in data.items():
            if key not in DEFAULTS:
                raise ValueError(f"Unknown config key: {key}")
            cfg[key] = value
    for key, default in DEFAULTS.items():
        raw = os.environ.get("CCDR_" + key.upper())
        if raw is None:
            continue
        if isinstance(default, str):
            cfg[key] = raw
        else:
            value = json.loads(raw)
            if type(value) is not type(default):
                raise ValueError(f"CCDR_{key.upper()} must be a {type(default).__name__}")
            cfg[key] = value
    if overrides:
        cfg.update(overrides)
    return cfg
