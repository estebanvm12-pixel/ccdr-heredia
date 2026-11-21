"use strict";
// The tracking token lives in the URL fragment (#...), which browsers never send to the server.

const resultNode = document.getElementById("result");
const lookupNode = document.getElementById("lookup");
const lookupError = document.getElementById("lookup-error");

const STATUS_HELP = {
  pending: "La administración todavía no ha revisado su solicitud. Le contactarán por teléfono.",
  approved: "Su reserva está confirmada. Preséntese a la hora indicada.",
  rejected: "Su solicitud no pudo aprobarse. Puede pedir otro horario desde el calendario.",
  cancelled: "Esta reserva fue cancelada.",
  expired: "La fecha pasó sin que la solicitud se aprobara.",
};

function extractToken(text) {
  const value = (text || "").trim();
  const hashIndex = value.indexOf("#");
  const token = hashIndex >= 0 ? value.slice(hashIndex + 1) : value;
  return /^[A-Za-z0-9_-]{20,100}$/.test(token) ? token : null;
}

function showError(message) {
  resultNode.classList.add("hidden");
  lookupNode.classList.remove("hidden");
  lookupError.textContent = message;
  lookupError.classList.remove("hidden");
}

async function lookup(token) {
  lookupError.classList.add("hidden");
  const res = await api("POST", "/api/public/requests/status", { token });
  if (!res.ok) {
    showError(res.data.error || "No se pudo consultar la solicitud.");
    return;
  }
  const d = res.data;
  clear(resultNode);
  resultNode.append(
    el("h1", null, "Estado de mi solicitud"),
    el("div", { class: "status-line" }, statusBadge(d.status)),
    el("p", null, STATUS_HELP[d.status] || ""),
    el("dl", null,
      el("dt", null, "Instalación"), el("dd", null, d.facility_name),
      el("dt", null, "Fecha y hora"), el("dd", null, formatRange(d.starts_at, d.ends_at)),
      el("dt", null, "Enviada"), el("dd", null, formatDate(d.created_at.slice(0, 10), true))),
  );
  if (d.admin_note) {
    resultNode.append(el("h2", null, "Nota de la administración"), el("p", { class: "note" }, d.admin_note));
  }
  resultNode.append(el("div", { class: "actions" },
    el("a", { class: "btn secondary", href: "/" }, "Ver calendario")));
  resultNode.classList.remove("hidden");
  lookupNode.classList.add("hidden");
}

document.getElementById("lookup-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const token = extractToken(document.getElementById("link").value);
  if (!token) {
    showError("Ese enlace no es válido. Cópielo completo, incluida la parte después del #.");
    return;
  }
  history.replaceState(null, "", `/estado#${token}`);
  lookup(token);
});

function lookupFromHash() {
  const token = extractToken(location.hash);
  if (token) lookup(token);
}

window.addEventListener("hashchange", lookupFromHash);
lookupFromHash();
