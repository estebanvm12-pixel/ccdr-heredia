"use strict";
// Role tabs on the presentation page (accessible: arrow keys move between tabs).

const tabs = [...document.querySelectorAll('[role="tab"]')];

function selectTab(tab) {
  for (const t of tabs) {
    const selected = t === tab;
    t.classList.toggle("active", selected);
    t.setAttribute("aria-selected", String(selected));
    t.tabIndex = selected ? 0 : -1;
    document.getElementById(t.getAttribute("aria-controls")).hidden = !selected;
  }
}

tabs.forEach((tab, index) => {
  tab.addEventListener("click", () => selectTab(tab));
  tab.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    const next = tabs[(index + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
    selectTab(next);
    next.focus();
  });
});

if (location.hash === "#admin") selectTab(document.getElementById("tab-admin"));

// ---- Before / after switch ----

const journey = document.querySelector(".journey");
document.querySelectorAll(".switch-btn").forEach((button) => {
  button.addEventListener("click", () => {
    journey.dataset.mode = button.dataset.mode;
    document.querySelectorAll(".switch-btn").forEach((b) => {
      const on = b === button;
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", String(on));
    });
  });
});

// ---- Live availability from the demo API (fictitious data) ----

function stampPlusMinutes(stamp, minutes) {
  const total = toMinutes(stamp.slice(11)) + minutes;
  const days = Math.floor(total / 1440);
  return `${addDays(stamp.slice(0, 10), days)}T${fromMinutes(total - days * 1440)}`;
}

function freeSlots(facility, busy, rules, now) {
  const earliest = stampPlusMinutes(now, rules.min_notice_hours * 60);
  const latest = `${addDays(now.slice(0, 10), 7)}T23:59`;
  const step = Math.max(rules.min_minutes, rules.slot_minutes);
  const slots = [];
  for (let i = 0; i <= 8; i++) {
    const date = addDays(now.slice(0, 10), i);
    for (const range of facility.opening_hours.filter((h) => h.weekday === weekdayIndex(date))) {
      for (let t = toMinutes(range.opens_at); t + step <= toMinutes(range.closes_at); t += step) {
        const start = `${date}T${fromMinutes(t)}`;
        const end = `${date}T${fromMinutes(t + step)}`;
        if (start < earliest || start > latest) continue;
        if (busy.some((b) => b.starts_at < end && start < b.ends_at)) continue;
        slots.push({ date, start: fromMinutes(t), end: fromMinutes(t + step) });
      }
    }
  }
  return slots;
}

async function loadLive() {
  const grid = document.getElementById("live-grid");
  const res = await api("GET", "/api/public/facilities");
  if (!res.ok) {
    clear(grid).append(el("p", { class: "muted" }, "La demo está despertando. Recargue en un minuto."));
    return;
  }
  const { facilities, rules, now } = res.data;
  const weeks = [now.slice(0, 10), addDays(now.slice(0, 10), 7)];
  const cards = await Promise.all(facilities.map(async (f) => {
    const busy = [];
    for (const week of weeks) {
      const params = new URLSearchParams({ facility_id: String(f.id), week_start: week });
      const r = await api("GET", `/api/public/availability?${params}`);
      if (r.ok) busy.push(...r.data.busy);
    }
    const slots = freeSlots(f, busy, rules, now);
    // One suggestion per day, preferring the evening (when courts are most wanted).
    const byDay = new Map();
    for (const s of slots) {
      const best = byDay.get(s.date);
      if (!best || (best.start < "17:00" && s.start >= "17:00")) byDay.set(s.date, s);
    }
    const chips = [...byDay.values()].slice(0, 4).map((s) => el("a", {
      class: "chip",
      href: `/solicitar?${new URLSearchParams({ f: String(f.id), d: s.date, s: s.start })}`,
    }, el("span", { class: "chip-day" }, capitalize(formatDate(s.date)).split(" de ")[0]),
    el("span", { class: "chip-time" }, formatTimeRange(s.start, s.end))));
    return el("article", { class: "live-card" },
      el("h3", null, f.name),
      el("p", { class: "live-count" }, `${slots.length} espacios libres en los próximos 7 días`),
      el("div", { class: "chips" }, chips));
  }));
  clear(grid).append(...cards);
}

loadLive();
