"use strict";
// Public weekly calendar: shows only "Disponible" / "Ocupado" (or a CCDR program name).

const state = { facilities: [], facilityId: null, weekStart: null, rules: null, now: null };

const facilitySelect = document.getElementById("facility");
const weekNode = document.getElementById("week");
const weekTitle = document.getElementById("week-title");
const errorNode = document.getElementById("calendar-error");

function mondayOf(dateStr) {
  return addDays(dateStr, -weekdayIndex(dateStr));
}

function renderFacilityInfo() {
  const info = clear(document.getElementById("facility-info"));
  const f = state.facilities.find((x) => x.id === state.facilityId);
  if (!f) return;
  const dl = el("dl", null,
    el("dt", null, "Ubicación"), el("dd", null, f.location || "—"),
    el("dt", null, "Deportes"), el("dd", null, f.sports || "—"),
    el("dt", null, "Capacidad"), el("dd", null, f.capacity ? `${f.capacity} personas` : "—"));
  info.append(dl);
  if (f.rules_text) info.append(el("p", { class: "rules muted" }, f.rules_text));
}

function busyAt(busy, startsAt, endsAt) {
  return busy.find((b) => b.starts_at < endsAt && startsAt < b.ends_at);
}

function renderWeek(data) {
  clear(weekNode);
  const rules = data.rules;
  const nowStr = data.now;
  const today = nowStr.slice(0, 10);
  const earliest = addMinutesToStamp(nowStr, rules.min_notice_hours * 60);
  const latest = addMinutesToStamp(nowStr, rules.horizon_days * 1440);
  const step = Math.max(rules.min_minutes, rules.slot_minutes);
  const lastDay = addDays(data.week_start, 6);
  weekTitle.textContent = formatWeekTitle(data.week_start, lastDay);

  for (let i = 0; i < 7; i++) {
    const date = addDays(data.week_start, i);
    const ranges = data.opening_hours.filter((h) => h.weekday === i);
    const day = el("div", { class: "day" + (date === today ? " today" : "") },
      el("h3", null, capitalize(formatDate(date))));
    if (!ranges.length) {
      day.append(el("p", { class: "closed" }, "Cerrado"));
      weekNode.append(day);
      continue;
    }
    const slots = el("div", { class: "slots" });
    for (const range of ranges) {
      const open = toMinutes(range.opens_at);
      const close = toMinutes(range.closes_at);
      for (let t = open; t + step <= close; t += step) {
        const startHm = fromMinutes(t);
        const startsAt = `${date}T${startHm}`;
        const endsAt = `${date}T${fromMinutes(t + step)}`;
        const timeText = formatTimeRange(startHm, fromMinutes(t + step));
        const busy = busyAt(data.busy, startsAt, endsAt);
        if (busy) {
          const program = busy.label !== "Ocupado";
          slots.append(el("div", { class: "slot " + (program ? "program" : "busy") },
            el("span", { class: "time" }, timeText), el("span", { class: "label" }, busy.label)));
        } else if (startsAt < earliest || startsAt > latest) {
          slots.append(el("div", { class: "slot na" },
            el("span", { class: "time" }, timeText), el("span", { class: "label" }, "No reservable")));
        } else {
          const params = new URLSearchParams({ f: String(state.facilityId), d: date, s: startHm });
          slots.append(el("a", { class: "slot free", href: `/solicitar?${params}` },
            el("span", { class: "time" }, timeText), el("span", { class: "label" }, "Disponible")));
        }
      }
    }
    day.append(slots);
    weekNode.append(day);
  }
}

function addMinutesToStamp(stamp, minutes) {
  const date = stamp.slice(0, 10);
  const total = toMinutes(stamp.slice(11)) + minutes;
  const days = Math.floor(total / 1440);
  return `${addDays(date, days)}T${fromMinutes(total - days * 1440)}`;
}

async function loadWeek() {
  errorNode.classList.add("hidden");
  const params = new URLSearchParams({ facility_id: String(state.facilityId), week_start: state.weekStart });
  const res = await api("GET", `/api/public/availability?${params}`);
  if (!res.ok) {
    errorNode.textContent = res.data.error || "No se pudo cargar el calendario.";
    errorNode.classList.remove("hidden");
    return;
  }
  state.weekStart = res.data.week_start;
  renderWeek(res.data);
}

async function init() {
  const res = await api("GET", "/api/public/facilities");
  if (!res.ok) {
    errorNode.textContent = res.data.error || "No se pudieron cargar las instalaciones.";
    errorNode.classList.remove("hidden");
    return;
  }
  state.facilities = res.data.facilities;
  state.now = res.data.now;
  for (const f of state.facilities) facilitySelect.append(el("option", { value: String(f.id) }, f.name));
  if (!state.facilities.length) {
    weekNode.append(el("p", { class: "muted" }, "No hay instalaciones disponibles."));
    return;
  }
  const saved = new URLSearchParams(location.search).get("f");
  const initial = state.facilities.find((f) => String(f.id) === saved) || state.facilities[0];
  state.facilityId = initial.id;
  facilitySelect.value = String(initial.id);
  state.weekStart = mondayOf(state.now.slice(0, 10));
  renderFacilityInfo();
  loadWeek();
}

facilitySelect.addEventListener("change", () => {
  state.facilityId = Number(facilitySelect.value);
  renderFacilityInfo();
  loadWeek();
});
document.getElementById("prev-week").addEventListener("click", () => {
  state.weekStart = addDays(state.weekStart, -7);
  loadWeek();
});
document.getElementById("next-week").addEventListener("click", () => {
  state.weekStart = addDays(state.weekStart, 7);
  loadWeek();
});

init();
