"""Module 1: facility booking requests. Logic + HTTP handlers.

Invariant: an approved booking never overlaps another approved booking or a
block occurrence. Every write path that could break it (approve, create block,
change opening hours, deactivate facility) checks inside BEGIN IMMEDIATE.
"""
import secrets
from datetime import timedelta

from .auth import sha256_hex
from .db import immediate
from .security import ApiError, Validator
from .timeutil import (
    add_months, fmt_date, fmt_dt, minutes_of_day, overlaps, parse_date, parse_dt,
    parse_hm, week_monday,
)

STATUSES = ("pending", "approved", "rejected", "cancelled")
WEEK = timedelta(days=7)
BUSY_LABEL = "Ocupado"


# ---- Opening hours ----------------------------------------------------------


def load_hours(conn, facility_id):
    rows = conn.execute(
        "SELECT weekday, opens_at, closes_at FROM opening_hours WHERE facility_id = ? "
        "ORDER BY weekday, opens_at",
        (facility_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def fits_hours(hours, start, end):
    """True if [start, end) is on one day and inside one opening range."""
    if start.date() != end.date():
        return False
    s, e = minutes_of_day(start), minutes_of_day(end)
    for h in hours:
        if h["weekday"] == start.weekday():
            if parse_hm(h["opens_at"]) <= s and e <= parse_hm(h["closes_at"]):
                return True
    return False


# ---- Blocks and conflicts ---------------------------------------------------


def block_occurrences(block, range_start, range_end):
    """Yield (start, end) of each occurrence of `block` overlapping the range."""
    start, end = parse_dt(block["starts_at"]), parse_dt(block["ends_at"])
    if block["recurrence"] != "weekly":
        if overlaps(start, end, range_start, range_end):
            yield start, end
        return
    until = parse_date(block["recurrence_until"])
    first = max(0, (range_start - end).days // 7)
    i = first
    while True:
        occ_start, occ_end = start + WEEK * i, end + WEEK * i
        if occ_start >= range_end or occ_start.date() > until:
            return
        if overlaps(occ_start, occ_end, range_start, range_end):
            yield occ_start, occ_end
        i += 1


def blocks_in_range(conn, facility_id, range_start, range_end):
    """Return (block_row, occ_start, occ_end) for occurrences in the range."""
    rows = conn.execute(
        "SELECT * FROM blocks WHERE facility_id = ? AND starts_at < ? AND "
        "(ends_at > ? OR (recurrence = 'weekly' AND recurrence_until >= ?))",
        (facility_id, fmt_dt(range_end), fmt_dt(range_start), fmt_date(range_start)),
    ).fetchall()
    result = []
    for row in rows:
        for occ_start, occ_end in block_occurrences(row, range_start, range_end):
            result.append((row, occ_start, occ_end))
    result.sort(key=lambda item: item[1])
    return result


def committed_conflicts(conn, facility_id, start, end, exclude_request_id=None):
    """Approved bookings and block occurrences that overlap [start, end)."""
    conflicts = []
    rows = conn.execute(
        "SELECT id, starts_at, ends_at, requester_name, organization, phone "
        "FROM booking_requests WHERE facility_id = ? AND status = 'approved' "
        "AND starts_at < ? AND ends_at > ? AND id IS NOT ?",
        (facility_id, fmt_dt(end), fmt_dt(start), exclude_request_id),
    ).fetchall()
    for r in rows:
        conflicts.append({
            "kind": "approved", "id": r["id"], "starts_at": r["starts_at"],
            "ends_at": r["ends_at"], "label": r["requester_name"] or "(anonimizado)",
        })
    for block, occ_start, occ_end in blocks_in_range(conn, facility_id, start, end):
        conflicts.append({
            "kind": "block", "id": block["id"], "starts_at": fmt_dt(occ_start),
            "ends_at": fmt_dt(occ_end), "label": block["reason"],
        })
    return conflicts


def pending_overlaps(conn, facility_id, start, end, exclude_request_id, now):
    rows = conn.execute(
        "SELECT id, starts_at, ends_at, requester_name, organization, phone "
        "FROM booking_requests WHERE facility_id = ? AND status = 'pending' "
        "AND starts_at < ? AND ends_at > ? AND starts_at > ? AND id != ? ORDER BY starts_at",
        (facility_id, fmt_dt(end), fmt_dt(start), fmt_dt(now), exclude_request_id),
    ).fetchall()
    return [dict(r) for r in rows]


def is_expired(row, now):
    return row["status"] == "pending" and row["starts_at"] <= fmt_dt(now)


def display_status(row, now):
    return "expired" if is_expired(row, now) else row["status"]


def get_facility(conn, facility_id, require_active=True):
    row = conn.execute("SELECT * FROM facilities WHERE id = ?", (facility_id,)).fetchone()
    if row is None or (require_active and not row["active"]):
        raise ApiError(404, "Instalación no encontrada.")
    return row


def booking_rules(cfg):
    return {
        "slot_minutes": cfg["slot_minutes"],
        "min_minutes": cfg["min_minutes"],
        "max_minutes": cfg["max_minutes"],
        "horizon_days": cfg["horizon_days"],
        "min_notice_hours": cfg["min_notice_hours"],
    }


def validate_slot(v, cfg, now, start_field="starts_at", end_field="ends_at"):
    """Parse and check a booking interval against the configured limits."""
    start = parse_dt(v.data.get(start_field))
    end = parse_dt(v.data.get(end_field))
    if start is None:
        v.add(start_field, "Fecha u hora de inicio inválida.")
    if end is None:
        v.add(end_field, "Hora de fin inválida.")
    if start is None or end is None:
        return None, None
    slot = cfg["slot_minutes"]
    if minutes_of_day(start) % slot or minutes_of_day(end) % slot:
        v.add(start_field, f"Las horas deben ir en bloques de {slot} minutos.")
    if start.date() != end.date():
        v.add(end_field, "La reserva debe empezar y terminar el mismo día.")
    duration = (end - start).total_seconds() / 60
    if duration <= 0:
        v.add(end_field, "La hora de fin debe ser posterior a la de inicio.")
    elif duration < cfg["min_minutes"]:
        v.add(end_field, f"La reserva mínima es de {cfg['min_minutes'] // 60} hora(s).")
    elif duration > cfg["max_minutes"]:
        v.add(end_field, f"La reserva máxima es de {cfg['max_minutes'] // 60} horas.")
    if start < now + timedelta(hours=cfg["min_notice_hours"]):
        v.add(start_field, f"Debe pedirse con al menos {cfg['min_notice_hours']} horas de anticipación.")
    elif start > now + timedelta(days=cfg["horizon_days"]):
        v.add(start_field, f"Solo se puede pedir con hasta {cfg['horizon_days']} días de anticipación.")
    return start, end


# ---- Public handlers --------------------------------------------------------


def handle_public_facilities(app, req):
    rows = req.conn.execute(
        "SELECT id, name, location, sports, capacity, rules_text FROM facilities "
        "WHERE active = 1 ORDER BY name"
    ).fetchall()
    facilities = []
    for r in rows:
        item = dict(r)
        item["opening_hours"] = load_hours(req.conn, r["id"])
        facilities.append(item)
    return 200, {
        "facilities": facilities,
        "rules": booking_rules(app.config),
        "now": fmt_dt(req.now),
    }


def handle_public_availability(app, req):
    try:
        facility_id = int(req.query.get("facility_id", [""])[0])
    except ValueError:
        raise ApiError(422, "Instalación inválida.")
    week_start = parse_date(req.query.get("week_start", [""])[0]) or req.now.date()
    week_start = week_monday(week_start)
    if abs((week_start - req.now.date()).days) > 400:
        raise ApiError(422, "Fecha fuera de rango.")
    get_facility(req.conn, facility_id)
    range_start = parse_dt(fmt_date(week_start) + "T00:00")
    range_end = range_start + WEEK
    busy = []
    rows = req.conn.execute(
        "SELECT starts_at, ends_at FROM booking_requests WHERE facility_id = ? "
        "AND status = 'approved' AND starts_at < ? AND ends_at > ?",
        (facility_id, fmt_dt(range_end), fmt_dt(range_start)),
    ).fetchall()
    # Only times and a generic label: never names, phones or organizations.
    for r in rows:
        busy.append({"starts_at": r["starts_at"], "ends_at": r["ends_at"], "label": BUSY_LABEL})
    for block, occ_start, occ_end in blocks_in_range(req.conn, facility_id, range_start, range_end):
        busy.append({
            "starts_at": fmt_dt(occ_start), "ends_at": fmt_dt(occ_end),
            "label": block["public_label"] or BUSY_LABEL,
        })
    busy.sort(key=lambda b: b["starts_at"])
    return 200, {
        "facility_id": facility_id,
        "week_start": fmt_date(week_start),
        "opening_hours": load_hours(req.conn, facility_id),
        "busy": busy,
        "rules": booking_rules(app.config),
        "now": fmt_dt(req.now),
    }


def handle_public_create_request(app, req):
    if not app.request_limiter.allow(req.ip):
        raise ApiError(429, "Ha enviado muchas solicitudes en poco tiempo. Intente de nuevo en una hora.")
    if req.body.get("website"):
        # Honeypot filled: pretend success, store nothing.
        fake = secrets.token_urlsafe(32)
        return 201, {"token": fake, "status_path": "/estado#" + fake}
    cfg = app.config
    v = Validator(req.body)
    facility_id = v.integer("facility_id", 1, 10**9)
    start, end = validate_slot(v, cfg, req.now)
    name = v.text("requester_name", 100, min_len=3)
    organization = v.text("organization", 120, required=False)
    phone = v.phone("phone")
    email = v.email("email")
    purpose = v.text("purpose", 500, min_len=5, multiline=True)
    attendees = v.integer("expected_attendees", 1, 10_000)
    if req.body.get("consent") is not True:
        v.add("consent", "Debe aceptar el aviso de privacidad para enviar la solicitud.")
    facility = None
    if facility_id is not None:
        facility = req.conn.execute(
            "SELECT * FROM facilities WHERE id = ? AND active = 1", (facility_id,)
        ).fetchone()
        if facility is None:
            v.add("facility_id", "Instalación no encontrada.")
        elif attendees and facility["capacity"] and attendees > facility["capacity"]:
            v.add("expected_attendees", f"La capacidad máxima es de {facility['capacity']} personas.")
    if facility is not None and start and end and "starts_at" not in v.errors and "ends_at" not in v.errors:
        if not fits_hours(load_hours(req.conn, facility_id), start, end):
            v.add("starts_at", "Ese horario está fuera del horario de la instalación.")
    v.raise_if_errors()

    token = secrets.token_urlsafe(32)
    with immediate(req.conn):
        if committed_conflicts(req.conn, facility_id, start, end):
            raise ApiError(409, "Ese horario ya está ocupado. Elija otro en el calendario.")
        req.conn.execute(
            "INSERT INTO booking_requests (facility_id, starts_at, ends_at, requester_name, "
            "organization, phone, email, purpose, expected_attendees, status, "
            "public_token_hash, consent_at, consent_version, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
            (facility_id, fmt_dt(start), fmt_dt(end), name, organization, phone, email,
             purpose, attendees, sha256_hex(token), fmt_dt(req.now), cfg["consent_version"],
             fmt_dt(req.now)),
        )
    return 201, {"token": token, "status_path": "/estado#" + token}


def handle_public_status(app, req):
    if not app.status_limiter.allow(req.ip):
        raise ApiError(429, "Demasiadas consultas. Intente de nuevo más tarde.")
    token = req.body.get("token")
    not_found = ApiError(404, "No encontramos una solicitud con ese enlace.")
    if not isinstance(token, str) or not 20 <= len(token) <= 100:
        raise not_found
    row = req.conn.execute(
        "SELECT r.starts_at, r.ends_at, r.status, r.admin_note, r.created_at, f.name AS facility_name "
        "FROM booking_requests r JOIN facilities f ON f.id = r.facility_id "
        "WHERE r.public_token_hash = ?",
        (sha256_hex(token),),
    ).fetchone()
    if row is None:
        raise not_found
    return 200, {
        "facility_name": row["facility_name"],
        "starts_at": row["starts_at"],
        "ends_at": row["ends_at"],
        "status": display_status(row, req.now),
        "admin_note": row["admin_note"],
        "created_at": row["created_at"],
    }


# ---- Admin: requests ----------------------------------------------------------


def _request_row_to_admin(conn, row, now, with_conflicts):
    item = dict(row)
    item.pop("public_token_hash", None)
    item["display_status"] = display_status(row, now)
    if with_conflicts and item["display_status"] == "pending":
        start, end = parse_dt(row["starts_at"]), parse_dt(row["ends_at"])
        conflicts = committed_conflicts(conn, row["facility_id"], start, end, row["id"])
        for p in pending_overlaps(conn, row["facility_id"], start, end, row["id"], now):
            conflicts.append({
                "kind": "pending", "id": p["id"], "starts_at": p["starts_at"],
                "ends_at": p["ends_at"], "label": p["requester_name"],
            })
        item["conflicts"] = conflicts
        item["fits_hours"] = fits_hours(load_hours(conn, row["facility_id"]), start, end)
    return item


def handle_admin_list_requests(app, req):
    status = req.query.get("status", ["pending"])[0]
    clauses, params = [], []
    if status == "pending":
        clauses.append("r.status = 'pending' AND r.starts_at > ?")
        params.append(fmt_dt(req.now))
    elif status == "expired":
        clauses.append("r.status = 'pending' AND r.starts_at <= ?")
        params.append(fmt_dt(req.now))
    elif status in ("approved", "rejected", "cancelled"):
        clauses.append("r.status = ?")
        params.append(status)
    elif status != "all":
        raise ApiError(422, "Estado inválido.")
    facility = req.query.get("facility_id", [""])[0]
    if facility:
        if not facility.isdigit():
            raise ApiError(422, "Instalación inválida.")
        clauses.append("r.facility_id = ?")
        params.append(int(facility))
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    order = "ASC" if status == "pending" else "DESC"
    rows = req.conn.execute(
        f"SELECT r.*, f.name AS facility_name FROM booking_requests r "
        f"JOIN facilities f ON f.id = r.facility_id {where} ORDER BY r.starts_at {order} LIMIT 500",
        params,
    ).fetchall()
    items = [_request_row_to_admin(req.conn, r, req.now, with_conflicts=True) for r in rows]
    return 200, {"requests": items}


def _load_request(conn, request_id):
    row = conn.execute("SELECT * FROM booking_requests WHERE id = ?", (request_id,)).fetchone()
    if row is None:
        raise ApiError(404, "Solicitud no encontrada.")
    return row


def _admin_note(req):
    v = Validator(req.body)
    note = v.text("admin_note", 500, required=False, multiline=True)
    v.raise_if_errors()
    return note


def handle_admin_approve(app, req):
    request_id = int(req.params[0])
    note = _admin_note(req)
    with immediate(req.conn):
        row = _load_request(req.conn, request_id)
        if row["status"] != "pending":
            raise ApiError(409, "Esta solicitud ya fue resuelta por otra persona. Recargue la bandeja.")
        if is_expired(row, req.now):
            raise ApiError(409, "La fecha de esta solicitud ya pasó; no se puede aprobar.")
        facility = req.conn.execute(
            "SELECT active FROM facilities WHERE id = ?", (row["facility_id"],)
        ).fetchone()
        start, end = parse_dt(row["starts_at"]), parse_dt(row["ends_at"])
        if not facility["active"] or not fits_hours(load_hours(req.conn, row["facility_id"]), start, end):
            raise ApiError(409, "La instalación está inactiva o el horario quedó fuera de su horario.")
        conflicts = committed_conflicts(req.conn, row["facility_id"], start, end, request_id)
        if conflicts:
            raise ApiError(
                409, "No se puede aprobar: choca con una reserva aprobada o un bloqueo.",
                extra={"conflicts": conflicts},
            )
        req.conn.execute(
            "UPDATE booking_requests SET status = 'approved', admin_note = ?, decided_by = ?, "
            "decided_at = ? WHERE id = ?",
            (note, req.session["user_id"], fmt_dt(req.now), request_id),
        )
        overlapping = pending_overlaps(req.conn, row["facility_id"], start, end, request_id, req.now)
    return 200, {"ok": True, "overlapping_pending": overlapping}


def _decide(req, new_status, allowed_from):
    request_id = int(req.params[0])
    note = _admin_note(req)
    with immediate(req.conn):
        row = _load_request(req.conn, request_id)
        if row["status"] not in allowed_from:
            raise ApiError(409, "El estado de esta solicitud cambió. Recargue la bandeja.")
        req.conn.execute(
            "UPDATE booking_requests SET status = ?, admin_note = ?, decided_by = ?, "
            "decided_at = ? WHERE id = ?",
            (new_status, note, req.session["user_id"], fmt_dt(req.now), request_id),
        )
    return 200, {"ok": True}


def handle_admin_reject(app, req):
    return _decide(req, "rejected", ("pending",))


def handle_admin_cancel(app, req):
    return _decide(req, "cancelled", ("pending", "approved"))


# ---- Admin: facilities and hours -------------------------------------------


def _future_approved(conn, facility_id, now):
    rows = conn.execute(
        "SELECT id, starts_at, ends_at, requester_name, phone FROM booking_requests "
        "WHERE facility_id = ? AND status = 'approved' AND ends_at > ? ORDER BY starts_at",
        (facility_id, fmt_dt(now)),
    ).fetchall()
    return [dict(r) for r in rows]


def handle_admin_list_facilities(app, req):
    rows = req.conn.execute("SELECT * FROM facilities ORDER BY active DESC, name").fetchall()
    items = []
    for r in rows:
        item = dict(r)
        item["opening_hours"] = load_hours(req.conn, r["id"])
        items.append(item)
    return 200, {"facilities": items}


def _validate_facility(body):
    v = Validator(body)
    data = {
        "name": v.text("name", 100, min_len=2),
        "location": v.text("location", 150, required=False),
        "sports": v.text("sports", 150, required=False),
        "capacity": v.integer("capacity", 1, 100_000, required=False),
        "rules_text": v.text("rules_text", 2000, required=False, multiline=True),
        "active": v.boolean("active") if "active" in body else True,
    }
    v.raise_if_errors()
    return data


def handle_admin_create_facility(app, req):
    d = _validate_facility(req.body)
    cur = req.conn.execute(
        "INSERT INTO facilities (name, location, sports, capacity, rules_text, active) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (d["name"], d["location"], d["sports"], d["capacity"], d["rules_text"], int(d["active"])),
    )
    return 201, {"id": cur.lastrowid}


def handle_admin_update_facility(app, req):
    facility_id = int(req.params[0])
    d = _validate_facility(req.body)
    with immediate(req.conn):
        get_facility(req.conn, facility_id, require_active=False)
        if not d["active"]:
            future = _future_approved(req.conn, facility_id, req.now)
            if future:
                raise ApiError(
                    409, "No se puede desactivar: tiene reservas aprobadas a futuro. Cancélelas primero.",
                    extra={"affected": future},
                )
        req.conn.execute(
            "UPDATE facilities SET name = ?, location = ?, sports = ?, capacity = ?, "
            "rules_text = ?, active = ? WHERE id = ?",
            (d["name"], d["location"], d["sports"], d["capacity"], d["rules_text"],
             int(d["active"]), facility_id),
        )
    return 200, {"ok": True}


def handle_admin_set_hours(app, req):
    facility_id = int(req.params[0])
    hours = req.body.get("hours")
    if not isinstance(hours, list) or len(hours) > 50:
        raise ApiError(422, "Horario inválido.")
    clean = []
    for i, h in enumerate(hours):
        if not isinstance(h, dict):
            raise ApiError(422, "Horario inválido.")
        weekday = h.get("weekday")
        opens, closes = parse_hm(h.get("opens_at")), parse_hm(h.get("closes_at"))
        if not isinstance(weekday, int) or isinstance(weekday, bool) or not 0 <= weekday <= 6:
            raise ApiError(422, f"Fila {i + 1}: día inválido.")
        if opens is None or closes is None or opens >= closes:
            raise ApiError(422, f"Fila {i + 1}: la hora de apertura debe ser anterior al cierre.")
        clean.append((weekday, h["opens_at"], h["closes_at"]))
    clean.sort()
    for a, b in zip(clean, clean[1:]):
        if a[0] == b[0] and b[1] < a[2]:
            raise ApiError(422, "Hay franjas del mismo día que se traslapan.")
    with immediate(req.conn):
        get_facility(req.conn, facility_id, require_active=False)
        req.conn.execute("DELETE FROM opening_hours WHERE facility_id = ?", (facility_id,))
        req.conn.executemany(
            "INSERT INTO opening_hours (facility_id, weekday, opens_at, closes_at) VALUES (?, ?, ?, ?)",
            [(facility_id, *row) for row in clean],
        )
        new_hours = [{"weekday": w, "opens_at": o, "closes_at": c} for w, o, c in clean]
        outside = [
            b for b in _future_approved(req.conn, facility_id, req.now)
            if not fits_hours(new_hours, parse_dt(b["starts_at"]), parse_dt(b["ends_at"]))
        ]
        if outside:
            raise ApiError(
                409, "El nuevo horario deja fuera reservas aprobadas. Cancélelas primero.",
                extra={"affected": outside},
            )
    return 200, {"ok": True}


# ---- Admin: blocks -------------------------------------------------------------


def handle_admin_list_blocks(app, req):
    clauses = ["(b.ends_at >= ? OR (b.recurrence = 'weekly' AND b.recurrence_until >= ?))"]
    since = req.now - timedelta(days=30)
    params = [fmt_dt(since), fmt_date(since)]
    facility = req.query.get("facility_id", [""])[0]
    if facility:
        if not facility.isdigit():
            raise ApiError(422, "Instalación inválida.")
        clauses.append("b.facility_id = ?")
        params.append(int(facility))
    rows = req.conn.execute(
        "SELECT b.*, f.name AS facility_name FROM blocks b JOIN facilities f ON f.id = b.facility_id "
        "WHERE " + " AND ".join(clauses) + " ORDER BY b.starts_at LIMIT 500",
        params,
    ).fetchall()
    return 200, {"blocks": [dict(r) for r in rows]}


def handle_admin_create_block(app, req):
    v = Validator(req.body)
    facility_id = v.integer("facility_id", 1, 10**9)
    start, end = parse_dt(req.body.get("starts_at")), parse_dt(req.body.get("ends_at"))
    if start is None:
        v.add("starts_at", "Fecha u hora de inicio inválida.")
    if end is None:
        v.add("ends_at", "Fecha u hora de fin inválida.")
    reason = v.text("reason", 200, min_len=3)
    public_label = v.text("public_label", 80, required=False)
    recurrence = req.body.get("recurrence", "none")
    until = None
    if recurrence not in ("none", "weekly"):
        v.add("recurrence", "Repetición inválida.")
    if start and end:
        if end <= start:
            v.add("ends_at", "El fin debe ser posterior al inicio.")
        elif end - start > timedelta(days=31):
            v.add("ends_at", "Un bloqueo puede durar como máximo 31 días.")
        if recurrence == "weekly":
            until = parse_date(req.body.get("recurrence_until"))
            if start.date() != end.date():
                v.add("ends_at", "Un bloqueo semanal debe empezar y terminar el mismo día.")
            if until is None:
                v.add("recurrence_until", "Indique hasta qué fecha se repite.")
            elif until < start.date() or until > start.date() + timedelta(days=731):
                v.add("recurrence_until", "La fecha final debe estar entre el inicio y dos años después.")
    v.raise_if_errors()
    block = {
        "starts_at": fmt_dt(start), "ends_at": fmt_dt(end), "recurrence": recurrence,
        "recurrence_until": fmt_date(until) if until else None,
    }
    with immediate(req.conn):
        get_facility(req.conn, facility_id, require_active=False)
        last_end = parse_dt(fmt_date(until) + "T23:59") + timedelta(minutes=1) if until else end
        candidates = req.conn.execute(
            "SELECT id, starts_at, ends_at, requester_name, phone FROM booking_requests "
            "WHERE facility_id = ? AND status = 'approved' AND starts_at < ? AND ends_at > ?",
            (facility_id, fmt_dt(last_end), fmt_dt(start)),
        ).fetchall()
        affected = [
            dict(b) for b in candidates
            if any(True for _ in block_occurrences(block, parse_dt(b["starts_at"]), parse_dt(b["ends_at"])))
        ]
        if affected:
            raise ApiError(
                409, "El bloqueo choca con reservas aprobadas. Cancélelas primero (y avise a las personas).",
                extra={"affected": affected},
            )
        cur = req.conn.execute(
            "INSERT INTO blocks (facility_id, starts_at, ends_at, reason, public_label, recurrence, "
            "recurrence_until, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (facility_id, block["starts_at"], block["ends_at"], reason, public_label, recurrence,
             block["recurrence_until"], req.session["user_id"], fmt_dt(req.now)),
        )
    return 201, {"id": cur.lastrowid}


def handle_admin_delete_block(app, req):
    cur = req.conn.execute("DELETE FROM blocks WHERE id = ?", (int(req.params[0]),))
    if cur.rowcount == 0:
        raise ApiError(404, "Bloqueo no encontrado.")
    return 200, {"ok": True}


# ---- Retention -------------------------------------------------------------------


def anonymize_old_requests(conn, now, months):
    """Wipe personal data of rejected, cancelled or expired requests older than `months`.

    Approved requests are kept until the CCDR defines their retention period.
    """
    cutoff = fmt_dt(add_months(now, -months))
    with immediate(conn):
        cur = conn.execute(
            "UPDATE booking_requests SET requester_name = '', organization = '', phone = '', "
            "email = '', purpose = '', admin_note = '', anonymized_at = ? "
            "WHERE anonymized_at IS NULL AND ("
            "  (status IN ('rejected', 'cancelled') AND COALESCE(decided_at, created_at) < ?)"
            "  OR (status = 'pending' AND ends_at < ?))",
            (fmt_dt(now), cutoff, cutoff),
        )
    return cur.rowcount
