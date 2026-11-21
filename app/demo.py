"""Fictitious demo data. Used by scripts/seed_demo.py and by the server in demo mode.

All names, phones and emails are invented. Dates are relative to "today" so the
demo always looks current.
"""
import secrets
from datetime import timedelta

from .auth import create_user, sha256_hex
from .db import immediate
from .timeutil import fmt_date, fmt_dt

LOCATION = "Polideportivo de Fátima (ubicación de ejemplo)"

FACILITIES = [
    ("Gimnasio multiuso (demo)", "Baloncesto, voleibol, futsal", 150,
     "Calzado deportivo de suela clara.\nNo se permite comida en la cancha."),
    ("Cancha sintética de fútbol 5 (demo)", "Fútbol 5", 20,
     "Solo tacos de hule o tenis.\nLlegar 10 minutos antes."),
    ("Sala de gimnasia (demo)", "Gimnasia rítmica, artística, baile", 30,
     "Ingresar sin zapatos al tapiz."),
    ("Cancha de tenis (demo)", "Tenis", 4, "Traer raquetas y bolas propias."),
]

# weekday: list of (opens, closes). Monday = 0.
HOURS = {
    0: [("06:00", "21:00")], 1: [("06:00", "21:00")], 2: [("06:00", "21:00")],
    3: [("06:00", "21:00")], 4: [("06:00", "21:00")], 5: [("07:00", "18:00")],
    6: [("08:00", "14:00")],
}

FAKE_PEOPLE = [
    ("Ana Ficticia Mora", "Club Demo Los Halcones", "8000-0001", "ana.demo@example.com"),
    ("Luis Ejemplo Solano", "", "8000-0002", ""),
    ("Carla Prueba Jiménez", "Asociación Demo de Vecinos", "8000-0003", "carla.demo@example.com"),
    ("Diego Inventado Rojas", "Equipo Demo Juvenil", "8000-0004", ""),
    ("Sofía Simulada Vargas", "Escuela Demo San Pablo", "8000-0005", "sofia.demo@example.com"),
    ("Marco Ficticio Arias", "", "8000-0006", ""),
]

TABLES_TO_WIPE = ("sessions", "booking_requests", "blocks", "opening_hours", "facilities", "users")


def next_weekday(today, weekday, min_days=2):
    days = (weekday - today.weekday()) % 7
    if days < min_days:
        days += 7
    return today + timedelta(days=days)


def load_demo_data(conn, now, admin_password, consent_version, wipe=False):
    """Insert the demo dataset (optionally wiping everything first) in one transaction."""
    today = now.date()
    with immediate(conn):
        if wipe:
            for table in TABLES_TO_WIPE:
                conn.execute(f"DELETE FROM {table}")  # fixed table names, not user input
        create_user(conn, "admin", admin_password, "admin", now)
        admin_id = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
        ids = []
        for name, sports, capacity, rules in FACILITIES:
            cur = conn.execute(
                "INSERT INTO facilities (name, location, sports, capacity, rules_text, active) "
                "VALUES (?, ?, ?, ?, ?, 1)",
                (name, LOCATION, sports, capacity, rules),
            )
            ids.append(cur.lastrowid)
            for weekday, ranges in HOURS.items():
                for opens, closes in ranges:
                    conn.execute(
                        "INSERT INTO opening_hours (facility_id, weekday, opens_at, closes_at) "
                        "VALUES (?, ?, ?, ?)",
                        (cur.lastrowid, weekday, opens, closes),
                    )
        gym, futbol, gimnasia, tenis = ids

        def block(facility, day, start, end, reason, label="", until=None):
            conn.execute(
                "INSERT INTO blocks (facility_id, starts_at, ends_at, reason, public_label, "
                "recurrence, recurrence_until, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (facility, f"{fmt_date(day)}T{start}", f"{fmt_date(day)}T{end}", reason, label,
                 "weekly" if until else "none", fmt_date(until) if until else None, admin_id, fmt_dt(now)),
            )

        monday = today - timedelta(days=today.weekday())
        season_end = today + timedelta(days=120)
        block(gym, monday + timedelta(days=1), "16:00", "18:00", "Escuela deportiva de voleibol (programa CCDR)",
              "Escuela de voleibol", season_end)
        block(gym, monday + timedelta(days=3), "16:00", "18:00", "Escuela deportiva de voleibol (programa CCDR)",
              "Escuela de voleibol", season_end)
        block(gimnasia, monday, "15:00", "19:00", "Entrenamiento selección de gimnasia rítmica",
              "Gimnasia rítmica CCDR", season_end)
        block(futbol, next_weekday(today, 2, 3), "06:00", "12:00", "Mantenimiento de césped sintético")

        def booking(facility, day, start, end, person, purpose, attendees, status, note=""):
            name, org, phone, email = FAKE_PEOPLE[person]
            created_at = now - timedelta(days=1)
            decided = status in ("approved", "rejected", "cancelled")
            conn.execute(
                "INSERT INTO booking_requests (facility_id, starts_at, ends_at, requester_name, organization, "
                "phone, email, purpose, expected_attendees, status, admin_note, public_token_hash, consent_at, "
                "consent_version, created_at, decided_by, decided_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (facility, f"{fmt_date(day)}T{start}", f"{fmt_date(day)}T{end}", name, org, phone, email,
                 purpose, attendees, status, note, sha256_hex(secrets.token_urlsafe(32)),
                 fmt_dt(created_at), consent_version, fmt_dt(created_at),
                 admin_id if decided else None, fmt_dt(created_at + timedelta(hours=2)) if decided else None),
            )

        sat = next_weekday(today, 5)
        thu = next_weekday(today, 3)
        mon = next_weekday(today, 0)
        booking(futbol, sat, "08:00", "10:00", 0, "Partido amistoso de liga distrital (demo)", 14, "approved",
                "Confirmado por teléfono.")
        booking(gym, thu, "18:00", "20:00", 2, "Torneo vecinal de baloncesto (demo)", 60, "approved")
        booking(futbol, sat, "10:00", "12:00", 3, "Entrenamiento del equipo juvenil (demo)", 16, "pending")
        booking(futbol, sat, "11:00", "13:00", 5, "Mejenga de compañeros de trabajo (demo)", 10, "pending")
        booking(gym, mon + timedelta(days=7), "19:00", "21:00", 1, "Clase de baile popular (demo)", 25, "pending")
        booking(tenis, sat, "09:00", "10:00", 4, "Práctica de tenis escolar (demo)", 4, "pending")
        booking(gimnasia, mon, "19:00", "21:00", 4, "Ensayo de presentación escolar (demo)", 20, "rejected",
                "Ese horario lo usa la selección. Le ofrecimos el miércoles.")
