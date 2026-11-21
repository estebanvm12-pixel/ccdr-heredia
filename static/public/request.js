"use strict";
// Public request form. The server re-validates everything; this only helps the user.

const form = document.getElementById("request-form");
const facilitySelect = document.getElementById("facility_id");
const dateInput = document.getElementById("date");
const startSelect = document.getElementById("start");
const endSelect = document.getElementById("end");
const hoursHint = document.getElementById("hours-hint");
const formError = document.getElementById("form-error");
const ctx = { facilities: [], rules: null, now: null };

function currentFacility() {
  return ctx.facilities.find((f) => String(f.id) === facilitySelect.value);
}

function rangesFor(dateStr) {
  const f = currentFacility();
  if (!f || !dateStr) return [];
  return f.opening_hours.filter((h) => h.weekday === weekdayIndex(dateStr));
}

function fillStarts(preferred) {
  clear(startSelect);
  const ranges = rangesFor(dateInput.value);
  const { slot_minutes: slot, min_minutes: min } = ctx.rules;
  for (const r of ranges) {
    for (let t = toMinutes(r.opens_at); t + min <= toMinutes(r.closes_at); t += slot) {
      const hm = fromMinutes(t);
      startSelect.append(el("option", { value: hm }, formatTime(hm)));
    }
  }
  if (preferred && [...startSelect.options].some((o) => o.value === preferred)) startSelect.value = preferred;
  if (dateInput.value) {
    hoursHint.textContent = ranges.length
      ? "Horario ese día: " + ranges.map((r) => `${formatTime(r.opens_at)} a ${formatTime(r.closes_at)}`).join(" y ")
      : "La instalación está cerrada ese día. Elija otra fecha.";
  } else {
    hoursHint.textContent = "";
  }
  fillEnds();
}

function fillEnds() {
  const previous = endSelect.value;
  clear(endSelect);
  if (!startSelect.value) return;
  const start = toMinutes(startSelect.value);
  const range = rangesFor(dateInput.value).find(
    (r) => toMinutes(r.opens_at) <= start && start < toMinutes(r.closes_at));
  if (!range) return;
  const { slot_minutes: slot, min_minutes: min, max_minutes: max } = ctx.rules;
  for (let t = start + min; t <= Math.min(start + max, toMinutes(range.closes_at)); t += slot) {
    const hm = fromMinutes(t);
    const hours = String((t - start) / 60).replace(".", ",");
    endSelect.append(el("option", { value: hm }, `${formatTime(hm)} (${hours} h)`));
  }
  if ([...endSelect.options].some((o) => o.value === previous)) endSelect.value = previous;
}

async function init() {
  const res = await api("GET", "/api/public/facilities");
  if (!res.ok) {
    formError.textContent = res.data.error || "No se pudieron cargar las instalaciones.";
    formError.classList.remove("hidden");
    return;
  }
  ctx.facilities = res.data.facilities;
  ctx.rules = res.data.rules;
  ctx.now = res.data.now;
  for (const f of ctx.facilities) facilitySelect.append(el("option", { value: String(f.id) }, f.name));
  const today = ctx.now.slice(0, 10);
  dateInput.min = today;
  dateInput.max = addDays(today, ctx.rules.horizon_days);

  const q = new URLSearchParams(location.search);
  if (q.get("f") && ctx.facilities.some((f) => String(f.id) === q.get("f"))) facilitySelect.value = q.get("f");
  const d = q.get("d");
  if (d && /^\d{4}-\d{2}-\d{2}$/.test(d)) dateInput.value = d;
  fillStarts(q.get("s"));
  updateCapacity();
}

function updateCapacity() {
  const f = currentFacility();
  const input = document.getElementById("expected_attendees");
  if (f && f.capacity) input.max = String(f.capacity);
}

facilitySelect.addEventListener("change", () => { fillStarts(startSelect.value); updateCapacity(); });
dateInput.addEventListener("change", () => fillStarts(startSelect.value));
startSelect.addEventListener("change", fillEnds);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formError.classList.add("hidden");
  const date = dateInput.value;
  const payload = {
    facility_id: Number(facilitySelect.value),
    starts_at: date && startSelect.value ? `${date}T${startSelect.value}` : "",
    ends_at: date && endSelect.value ? `${date}T${endSelect.value}` : "",
    requester_name: form.requester_name.value,
    organization: form.organization.value,
    phone: form.phone.value,
    email: form.email.value,
    purpose: form.purpose.value,
    expected_attendees: form.expected_attendees.value,
    consent: form.consent.checked,
    website: form.website.value,
  };
  const button = document.getElementById("submit");
  button.disabled = true;
  button.textContent = "Enviando…";
  const res = await api("POST", "/api/public/requests", payload);
  button.disabled = false;
  button.textContent = "Enviar solicitud";
  if (!res.ok) {
    formError.textContent = res.data.error || "No se pudo enviar la solicitud.";
    formError.classList.remove("hidden");
    showFieldErrors(form, res.data.fields);
    if (!res.data.fields) formError.scrollIntoView({ block: "center" });
    return;
  }
  showFieldErrors(form, {});
  const url = `${location.origin}${res.data.status_path}`;
  document.getElementById("status-link").textContent = url;
  document.getElementById("open-link").href = res.data.status_path;
  document.getElementById("form-card").classList.add("hidden");
  document.getElementById("done-card").classList.remove("hidden");
  window.scrollTo(0, 0);
  document.getElementById("copy-link").addEventListener("click", async () => {
    const msg = document.getElementById("copy-msg");
    try {
      await navigator.clipboard.writeText(url);
      msg.textContent = "Enlace copiado. Péguelo en un lugar seguro (por ejemplo, un chat con usted mismo).";
    } catch (err) {
      msg.textContent = "No se pudo copiar automáticamente. Mantenga presionado el enlace para copiarlo.";
    }
  });
});

init();
