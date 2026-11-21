"use strict";
// Admin SPA. All user-provided data is rendered with textContent through el().

const session = { csrf: null, username: null };
let facilitiesCache = [];
const VIEWS = ["inbox", "all", "facilities", "blocks"];

const flashNode = document.getElementById("flash");

function flash(kind, title, items) {
  clear(flashNode);
  const box = el("div", { class: `alert ${kind}` }, el("strong", null, title));
  if (items && items.length) box.append(el("ul", null, items.map((t) => el("li", null, t))));
  box.append(" ", el("button", { type: "button", class: "btn secondary small", onclick: () => flashNode.classList.add("hidden") }, "Cerrar"));
  flashNode.append(box);
  flashNode.classList.remove("hidden");
  flashNode.scrollIntoView({ block: "nearest" });
}

async function call(method, url, body) {
  const res = await api(method, url, body, method === "GET" ? null : session.csrf);
  if (res.status === 401) {
    showLogin("Su sesión expiró. Inicie sesión de nuevo.");
  }
  return res;
}

// ---- Login ----

function showLogin(message) {
  session.csrf = null;
  document.getElementById("nav").classList.add("hidden");
  VIEWS.forEach((v) => document.getElementById(`${v}-view`).classList.add("hidden"));
  flashNode.classList.add("hidden");
  document.getElementById("login-view").classList.remove("hidden");
  const err = document.getElementById("login-error");
  if (message) {
    err.textContent = message;
    err.classList.remove("hidden");
  } else {
    err.classList.add("hidden");
  }
}

document.getElementById("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  button.textContent = "Entrando…";
  const res = await api("POST", "/api/auth/login", {
    username: form.username.value, password: form.password.value,
  });
  button.disabled = false;
  button.textContent = "Entrar";
  form.password.value = "";
  if (!res.ok) {
    showLogin(res.data.error || "No se pudo iniciar sesión.");
    return;
  }
  startApp(res.data);
});

document.getElementById("logout").addEventListener("click", async () => {
  await call("POST", "/api/auth/logout");
  showLogin();
});

function startApp(me) {
  session.csrf = me.csrf_token;
  session.username = me.username;
  document.getElementById("login-view").classList.add("hidden");
  document.getElementById("nav").classList.remove("hidden");
  showView("inbox");
}

function showView(name) {
  flashNode.classList.add("hidden");
  VIEWS.forEach((v) => document.getElementById(`${v}-view`).classList.toggle("hidden", v !== name));
  document.querySelectorAll("#nav [data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  if (name === "inbox") loadInbox();
  if (name === "all") loadAll();
  if (name === "facilities") loadFacilities();
  if (name === "blocks") loadBlocks();
}

document.querySelectorAll("#nav [data-view]").forEach((b) =>
  b.addEventListener("click", () => showView(b.dataset.view)));

// ---- Requests ----

function phoneLinks(phone) {
  if (!phone) return null;
  const digits = phone.replace(/[^0-9]/g, "");
  const wa = digits.length === 8 ? `506${digits}` : digits;
  return el("span", { class: "contact" },
    el("a", { class: "btn secondary small", href: `tel:${digits}` }, `Llamar ${phone}`),
    el("a", { class: "btn secondary small", href: `https://wa.me/${wa}`, target: "_blank", rel: "noopener noreferrer" }, "WhatsApp"));
}

const CONFLICT_KIND = { approved: "Reserva aprobada", block: "Bloqueo", pending: "Otra solicitud pendiente" };

function conflictText(c) {
  return `${CONFLICT_KIND[c.kind] || c.kind}: ${formatRange(c.starts_at, c.ends_at)} — ${c.label || "(anonimizado)"}`;
}

function requestCard(r, withActions) {
  const anonymized = Boolean(r.anonymized_at);
  const card = el("article", { class: "card request" },
    el("div", { class: "request-head" },
      el("div", null,
        el("h2", null, r.facility_name),
        el("div", { class: "when" }, formatRange(r.starts_at, r.ends_at))),
      statusBadge(r.display_status)),
    el("dl", null,
      el("dt", null, "Nombre"), el("dd", null, anonymized ? "(anonimizado)" : r.requester_name),
      r.organization ? [el("dt", null, "Organización"), el("dd", null, r.organization)] : null,
      el("dt", null, "Teléfono"), el("dd", null, anonymized ? "—" : phoneLinks(r.phone)),
      r.email ? [el("dt", null, "Correo"), el("dd", null, el("a", { href: `mailto:${r.email}` }, r.email))] : null,
      el("dt", null, "Personas"), el("dd", null, String(r.expected_attendees)),
      el("dt", null, "Propósito"), el("dd", { class: "purpose" }, r.purpose || "—"),
      r.admin_note ? [el("dt", null, "Nota"), el("dd", { class: "purpose" }, r.admin_note)] : null,
      el("dt", null, "Recibida"), el("dd", null, formatDate(r.created_at.slice(0, 10), true))));

  const conflicts = r.conflicts || [];
  const hard = conflicts.filter((c) => c.kind !== "pending");
  const soft = conflicts.filter((c) => c.kind === "pending");
  if (hard.length || r.fits_hours === false) {
    const items = hard.map(conflictText);
    if (r.fits_hours === false) items.push("Queda fuera del horario actual de la instalación.");
    card.append(el("div", { class: "alert error" },
      el("strong", null, "No se puede aprobar:"), el("ul", { class: "conflict-list" }, items.map((t) => el("li", null, t)))));
  }
  if (soft.length) {
    card.append(el("div", { class: "alert warn" },
      el("strong", null, "Choca con otras solicitudes pendientes (si aprueba esta, habrá que rechazar esas):"),
      el("ul", { class: "conflict-list" }, soft.map((c) => el("li", null, conflictText(c))))));
  }

  if (withActions) {
    const note = el("textarea", { maxLength: 500, placeholder: "Nota para la persona solicitante (opcional, la verá en su enlace)", "aria-label": "Nota para la persona solicitante" });
    const approve = el("button", { type: "button", class: "btn success", disabled: hard.length > 0 || r.fits_hours === false }, "Aprobar");
    const reject = el("button", { type: "button", class: "btn danger" }, "Rechazar");
    approve.addEventListener("click", () => decide(r, "approve", note.value, [approve, reject]));
    reject.addEventListener("click", () => decide(r, "reject", note.value, [approve, reject]));
    card.append(el("div", { class: "decision field" }, note), el("div", { class: "actions" }, approve, reject));
  } else if (r.display_status === "approved" || r.display_status === "pending") {
    const label = r.display_status === "approved" ? "Cancelar reserva" : "Cancelar solicitud";
    const cancel = el("button", { type: "button", class: "btn danger small" }, label);
    cancel.addEventListener("click", () => decide(r, "cancel", "", [cancel]));
    card.append(el("div", { class: "actions" }, cancel));
  }
  return card;
}

async function decide(r, action, note, buttons) {
  if (action === "reject" && !confirm("¿Rechazar esta solicitud?")) return;
  if (action === "cancel" && !confirm("¿Cancelar esta reserva? Recuerde avisarle a la persona.")) return;
  buttons.forEach((b) => { b.disabled = true; });
  const res = await call("POST", `/api/admin/requests/${r.id}/${action}`, { admin_note: note });
  if (!res.ok) {
    buttons.forEach((b) => { b.disabled = false; });
    if (res.status === 401) return;
    flash("error", res.data.error || "No se pudo completar la acción.", (res.data.conflicts || []).map(conflictText));
    if (res.status === 409) reloadCurrent();
    return;
  }
  const labels = { approve: "Solicitud aprobada.", reject: "Solicitud rechazada.", cancel: "Reserva cancelada." };
  const overlapping = res.data.overlapping_pending || [];
  if (overlapping.length) {
    flash("warn", `${labels[action]} Estas solicitudes pendientes chocan con ella; recházelas y avise a las personas:`,
      overlapping.map((p) => `${formatRange(p.starts_at, p.ends_at)} — ${p.requester_name} (${p.phone})`));
  } else {
    flash("ok", `${labels[action]} Llame a la persona para avisarle.`);
  }
  reloadCurrent();
}

function reloadCurrent() {
  if (!document.getElementById("inbox-view").classList.contains("hidden")) loadInbox();
  if (!document.getElementById("all-view").classList.contains("hidden")) loadAll();
}

async function loadInbox() {
  const list = document.getElementById("inbox-list");
  const res = await call("GET", "/api/admin/requests?status=pending");
  if (!res.ok) return;
  clear(list);
  if (!res.data.requests.length) list.append(el("p", { class: "card muted" }, "No hay solicitudes pendientes."));
  res.data.requests.forEach((r) => list.append(requestCard(r, true)));
}
document.getElementById("reload-inbox").addEventListener("click", loadInbox);

async function loadAll() {
  const list = document.getElementById("all-list");
  const status = document.getElementById("all-status").value;
  const res = await call("GET", `/api/admin/requests?status=${encodeURIComponent(status)}`);
  if (!res.ok) return;
  clear(list);
  if (!res.data.requests.length) list.append(el("p", { class: "card muted" }, "No hay solicitudes con ese estado."));
  res.data.requests.forEach((r) => list.append(requestCard(r, false)));
}
document.getElementById("all-status").addEventListener("change", loadAll);

// ---- Facilities and hours ----

const WEEKDAY_OPTIONS = WEEKDAYS.map((name, i) => [i, name.charAt(0).toUpperCase() + name.slice(1)]);

function hoursRow(h) {
  const day = el("select", { "aria-label": "Día" }, WEEKDAY_OPTIONS.map(([i, name]) => el("option", { value: String(i) }, name)));
  day.value = String(h ? h.weekday : 0);
  const opens = el("input", { type: "time", value: h ? h.opens_at : "08:00", step: 1800, "aria-label": "Abre" });
  const closes = el("input", { type: "time", value: h ? h.closes_at : "17:00", step: 1800, "aria-label": "Cierra" });
  const row = el("div", { class: "hours-row" }, day, opens, closes);
  row.append(el("button", { type: "button", class: "btn danger small", onclick: () => row.remove(), "aria-label": "Quitar franja" }, "✕"));
  row.read = () => ({ weekday: Number(day.value), opens_at: opens.value, closes_at: closes.value });
  return row;
}

function facilityCard(f) {
  const isNew = !f.id;
  const errorBox = el("div", { class: "alert error hidden", role: "alert" });
  const form = el("form", { novalidate: true },
    el("div", { class: "row two" },
      el("div", { class: "field" }, el("label", null, "Nombre"), el("input", { type: "text", name: "name", value: f.name || "", maxLength: 100 })),
      el("div", { class: "field" }, el("label", null, "Ubicación"), el("input", { type: "text", name: "location", value: f.location || "", maxLength: 150 }))),
    el("div", { class: "row three" },
      el("div", { class: "field" }, el("label", null, "Deportes"), el("input", { type: "text", name: "sports", value: f.sports || "", maxLength: 150 })),
      el("div", { class: "field" }, el("label", null, "Capacidad (personas)"), el("input", { type: "number", name: "capacity", value: f.capacity ? String(f.capacity) : "", min: 1 })),
      el("div", { class: "field" }, el("label", { class: "checkbox" }, el("input", { type: "checkbox", name: "active", checked: isNew || Boolean(f.active) }), el("span", null, "Activa (visible al público)")))),
    el("div", { class: "field" }, el("label", null, "Reglas de uso"), el("textarea", { name: "rules_text", maxLength: 2000 }, f.rules_text || "")),
    el("button", { type: "submit", class: "btn" }, isNew ? "Crear instalación" : "Guardar datos"));

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorBox.classList.add("hidden");
    const field = (n) => form.querySelector(`[name="${n}"]`);
    const body = {
      name: field("name").value, location: field("location").value, sports: field("sports").value,
      capacity: field("capacity").value, rules_text: field("rules_text").value, active: field("active").checked,
    };
    const res = isNew
      ? await call("POST", "/api/admin/facilities", body)
      : await call("PUT", `/api/admin/facilities/${f.id}`, body);
    if (!res.ok) {
      errorBox.textContent = res.data.error || "No se pudo guardar.";
      if (res.data.affected) errorBox.append(el("ul", null, res.data.affected.map((b) => el("li", null, `${formatRange(b.starts_at, b.ends_at)} — ${b.requester_name} (${b.phone})`))));
      errorBox.classList.remove("hidden");
      showFieldErrors(form, res.data.fields);
      return;
    }
    flash("ok", isNew ? "Instalación creada. Ahora defina su horario." : "Datos guardados.");
    loadFacilities();
  });

  const card = el("article", { class: "card facility-card" },
    el("h2", null, isNew ? "Nueva instalación" : f.name, !isNew && !f.active ? el("span", { class: "inactive" }, " (inactiva)") : null),
    errorBox, form);

  if (!isNew) {
    const rows = el("div");
    (f.opening_hours || []).forEach((h) => rows.append(hoursRow(h)));
    const hoursError = el("div", { class: "alert error hidden", role: "alert" });
    const save = el("button", { type: "button", class: "btn" }, "Guardar horario");
    save.addEventListener("click", async () => {
      hoursError.classList.add("hidden");
      const hours = [...rows.children].map((r) => r.read());
      const res = await call("PUT", `/api/admin/facilities/${f.id}/hours`, { hours });
      if (!res.ok) {
        hoursError.textContent = res.data.error || "No se pudo guardar el horario.";
        if (res.data.affected) hoursError.append(el("ul", null, res.data.affected.map((b) => el("li", null, `${formatRange(b.starts_at, b.ends_at)} — ${b.requester_name} (${b.phone})`))));
        hoursError.classList.remove("hidden");
        return;
      }
      flash("ok", `Horario de ${f.name} guardado.`);
    });
    card.append(el("h3", null, "Horario semanal"),
      el("p", { class: "muted small" }, "Día · abre · cierra. Puede haber varias franjas el mismo día."),
      hoursError, rows,
      el("div", { class: "actions" },
        el("button", { type: "button", class: "btn secondary small", onclick: () => rows.append(hoursRow()) }, "Agregar franja"),
        save));
  }
  return card;
}

async function loadFacilities() {
  const res = await call("GET", "/api/admin/facilities");
  if (!res.ok) return;
  facilitiesCache = res.data.facilities;
  const list = clear(document.getElementById("facilities-list"));
  facilitiesCache.forEach((f) => list.append(facilityCard(f)));
}

document.getElementById("new-facility").addEventListener("click", () => {
  const list = document.getElementById("facilities-list");
  list.prepend(facilityCard({}));
});

// ---- Blocks ----

const blockForm = document.getElementById("block-form");
const blockError = document.getElementById("block-error");
document.getElementById("block-recurrence").addEventListener("change", (e) => {
  document.getElementById("block-until").disabled = e.target.value !== "weekly";
});

async function loadBlocks() {
  const facRes = await call("GET", "/api/admin/facilities");
  if (!facRes.ok) return;
  facilitiesCache = facRes.data.facilities;
  const select = document.getElementById("block-facility");
  const previous = select.value;
  clear(select);
  facilitiesCache.forEach((f) => select.append(el("option", { value: String(f.id) }, f.name)));
  if (previous) select.value = previous;

  const res = await call("GET", "/api/admin/blocks");
  if (!res.ok) return;
  const list = clear(document.getElementById("blocks-list"));
  if (!res.data.blocks.length) {
    list.append(el("p", { class: "card muted" }, "No hay bloqueos vigentes."));
    return;
  }
  const table = el("table", { class: "simple" },
    el("thead", null, el("tr", null, ["Instalación", "Cuándo", "Repetición", "Motivo", "Nombre público", ""].map((h) => el("th", null, h)))),
    el("tbody", null, res.data.blocks.map((b) => {
      const del = el("button", { type: "button", class: "btn danger small" }, "Quitar");
      del.addEventListener("click", async () => {
        if (!confirm("¿Quitar este bloqueo? El espacio quedará disponible.")) return;
        const r = await call("DELETE", `/api/admin/blocks/${b.id}`);
        if (r.ok) { flash("ok", "Bloqueo quitado."); loadBlocks(); }
        else if (r.status !== 401) flash("error", r.data.error || "No se pudo quitar.");
      });
      return el("tr", null,
        el("td", null, b.facility_name),
        el("td", null, formatRange(b.starts_at, b.ends_at)),
        el("td", null, b.recurrence === "weekly" ? `Semanal hasta ${formatDate(b.recurrence_until, true)}` : "Una vez"),
        el("td", null, b.reason),
        el("td", null, b.public_label || "— (Ocupado)"),
        el("td", null, del));
    })));
  list.append(el("div", { class: "card table-wrap" }, table));
}

blockForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  blockError.classList.add("hidden");
  const body = {
    facility_id: Number(blockForm.facility_id.value),
    starts_at: blockForm.starts_at.value,
    ends_at: blockForm.ends_at.value,
    reason: blockForm.reason.value,
    public_label: blockForm.public_label.value,
    recurrence: blockForm.recurrence.value,
    recurrence_until: blockForm.recurrence_until.value || null,
  };
  const res = await call("POST", "/api/admin/blocks", body);
  if (!res.ok) {
    blockError.textContent = res.data.error || "No se pudo crear el bloqueo.";
    if (res.data.affected) blockError.append(el("ul", null, res.data.affected.map((b) => el("li", null, `${formatRange(b.starts_at, b.ends_at)} — ${b.requester_name} (${b.phone})`))));
    blockError.classList.remove("hidden");
    showFieldErrors(blockForm, res.data.fields);
    return;
  }
  showFieldErrors(blockForm, {});
  blockForm.reset();
  document.getElementById("block-until").disabled = true;
  flash("ok", "Bloqueo creado.");
  loadBlocks();
});

// ---- Boot ----

(async function boot() {
  const res = await api("GET", "/api/auth/me");
  if (res.ok && res.data.authenticated) startApp(res.data);
  else showLogin();
})();
