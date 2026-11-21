"use strict";
// Shared helpers. User data is always inserted with textContent (via el()), never innerHTML.

const WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"];
const MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
  "septiembre", "octubre", "noviembre", "diciembre"];
const STATUS_LABELS = {
  pending: "Pendiente",
  approved: "Aprobada",
  rejected: "Rechazada",
  cancelled: "Cancelada",
  expired: "Vencida",
};

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  if (props) {
    for (const [key, value] of Object.entries(props)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "innerHTML" || key === "outerHTML") throw new Error("innerHTML is not allowed");
      if (key === "class") node.className = value;
      else if (key === "dataset") Object.assign(node.dataset, value);
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
      else if (key === "text") node.textContent = value;
      else if (key in node && typeof value !== "string") node[key] = value;
      else node.setAttribute(key, value === true ? "" : String(value));
    }
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

async function api(method, url, body, csrf) {
  const headers = { Accept: "application/json" };
  const options = { method, headers, credentials: "same-origin" };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  if (csrf) headers["X-CSRF-Token"] = csrf;
  let response;
  try {
    response = await fetch(url, options);
  } catch (err) {
    return { ok: false, status: 0, data: { error: "No hay conexión con el servidor." } };
  }
  let data = {};
  try {
    data = await response.json();
  } catch (err) {
    data = { error: "Respuesta inesperada del servidor." };
  }
  return { ok: response.ok, status: response.status, data };
}

// ---- Dates as plain strings (local Costa Rica time, no timezone math) ----

function dateParts(dateStr) {
  const [y, m, d] = dateStr.split("-").map(Number);
  return { y, m, d };
}

function weekdayIndex(dateStr) {
  const { y, m, d } = dateParts(dateStr);
  return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7; // Monday = 0
}

function addDays(dateStr, days) {
  const { y, m, d } = dateParts(dateStr);
  const dt = new Date(Date.UTC(y, m - 1, d + days));
  return dt.toISOString().slice(0, 10);
}

function formatDate(dateStr, withYear) {
  const { y, m, d } = dateParts(dateStr);
  const text = `${WEEKDAYS[weekdayIndex(dateStr)]} ${d} de ${MONTHS[m - 1]}`;
  return withYear ? `${text} de ${y}` : text;
}

function capitalize(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function toMinutes(hm) {
  const [h, m] = hm.split(":").map(Number);
  return h * 60 + m;
}

function fromMinutes(total) {
  const h = String(Math.floor(total / 60)).padStart(2, "0");
  const m = String(total % 60).padStart(2, "0");
  return `${h}:${m}`;
}

function formatTime(hm) {
  const total = toMinutes(hm);
  let h = Math.floor(total / 60);
  const m = String(total % 60).padStart(2, "0");
  const suffix = h < 12 ? "a. m." : "p. m.";
  h = h % 12 || 12;
  return `${h}:${m} ${suffix}`;
}

function formatTimeRange(startHm, endHm) {
  const a = formatTime(startHm);
  const b = formatTime(endHm);
  const suffixA = a.slice(a.indexOf(" ") + 1);
  const suffixB = b.slice(b.indexOf(" ") + 1);
  return suffixA === suffixB ? `${a.slice(0, a.indexOf(" "))} – ${b}` : `${a} – ${b}`;
}

function formatWeekTitle(startDate, endDate) {
  const s = dateParts(startDate);
  const e = dateParts(endDate);
  const short = (m) => MONTHS[m - 1].slice(0, 3) + ".";
  return s.m === e.m
    ? `${s.d} – ${e.d} ${short(e.m)} ${e.y}`
    : `${s.d} ${short(s.m)} – ${e.d} ${short(e.m)} ${e.y}`;
}

function formatRange(startsAt, endsAt) {
  const date = startsAt.slice(0, 10);
  const range = `${formatTime(startsAt.slice(11))} a ${formatTime(endsAt.slice(11))}`;
  if (endsAt.slice(0, 10) === date) return `${formatDate(date, true)}, ${range}`;
  return `${formatDate(date, true)} ${formatTime(startsAt.slice(11))} al ` +
    `${formatDate(endsAt.slice(0, 10), true)} ${formatTime(endsAt.slice(11))}`;
}

function statusBadge(status) {
  return el("span", { class: `badge ${status}` }, STATUS_LABELS[status] || status);
}

function showFieldErrors(form, fields) {
  form.querySelectorAll(".field-error").forEach((n) => n.remove());
  form.querySelectorAll(".has-error").forEach((n) => n.classList.remove("has-error"));
  let first = null;
  for (const [name, message] of Object.entries(fields || {})) {
    const input = form.querySelector(`[name="${CSS.escape(name)}"]`);
    if (!input) continue;
    const field = input.closest(".field") || input.parentElement;
    field.classList.add("has-error");
    field.append(el("div", { class: "field-error", role: "alert" }, message));
    if (!first) first = input;
  }
  if (first) first.focus();
}
