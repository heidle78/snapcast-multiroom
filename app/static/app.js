const API = "/api";
let players = [];
let devices = [];
let editingName = null; // set when the player modal is used for editing
let logPollTimer = null;

async function api(path, options = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

function toast(message, isError = false) {
  const el = document.createElement("div");
  el.className = "toast" + (isError ? " err" : "");
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

// ---------------------------------------------------------------- players --
async function loadPlayers() {
  players = await api("/players");
  renderPlayers();
}

function renderPlayers() {
  const grid = document.getElementById("player-grid");
  const empty = document.getElementById("empty-state");
  grid.innerHTML = "";

  if (players.length === 0) {
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");

  for (const p of players) {
    const card = document.createElement("div");
    card.className = "player-card";

    const badgeClass = `badge-${p.status}`;
    const uptime = p.uptime_seconds ? formatUptime(p.uptime_seconds) : "-";

    card.innerHTML = `
      <div class="player-card-head">
        <h3>${escapeHtml(p.name)}</h3>
        <span class="badge ${badgeClass}">${p.status}</span>
      </div>
      <div class="player-device">${escapeHtml(p.config.device)}</div>
      <div class="player-meta">
        <div>Server: ${escapeHtml(p.config.snapserver_host || "(Standard)")}${p.config.snapserver_port ? ":" + p.config.snapserver_port : ""}</div>
        <div>Buffer: ${p.config.buffer_time_ms}ms · Latenz: ${p.config.latency_ms}ms</div>
        <div>Uptime: ${uptime} · PID: ${p.pid ?? "-"} · Neustarts: ${p.restart_count}</div>
      </div>
      <div class="player-actions">
        <button class="btn btn-secondary btn-small" data-action="start">Start</button>
        <button class="btn btn-secondary btn-small" data-action="stop">Stop</button>
        <button class="btn btn-secondary btn-small" data-action="restart">Neustart</button>
        <button class="btn btn-secondary btn-small" data-action="logs">Logs</button>
        <button class="btn btn-secondary btn-small" data-action="edit">Bearbeiten</button>
        <button class="btn btn-danger btn-small" data-action="delete">Löschen</button>
      </div>
    `;

    card.querySelector('[data-action="start"]').onclick = () => runAction(p.name, "start");
    card.querySelector('[data-action="stop"]').onclick = () => runAction(p.name, "stop");
    card.querySelector('[data-action="restart"]').onclick = () => runAction(p.name, "restart");
    card.querySelector('[data-action="logs"]').onclick = () => openLogs(p.name);
    card.querySelector('[data-action="edit"]').onclick = () => openEditModal(p);
    card.querySelector('[data-action="delete"]').onclick = () => deletePlayer(p.name);

    grid.appendChild(card);
  }
}

function formatUptime(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

async function runAction(name, action) {
  try {
    await api(`/players/${encodeURIComponent(name)}/${action}`, { method: "POST" });
    await loadPlayers();
  } catch (e) {
    toast(`${action} fehlgeschlagen: ${e.message}`, true);
  }
}

async function deletePlayer(name) {
  if (!confirm(`Player "${name}" wirklich löschen?`)) return;
  try {
    await api(`/players/${encodeURIComponent(name)}`, { method: "DELETE" });
    await loadPlayers();
  } catch (e) {
    toast(`Löschen fehlgeschlagen: ${e.message}`, true);
  }
}

// ------------------------------------------------------------- add/edit --
function openAddModal() {
  editingName = null;
  document.getElementById("modal-title").textContent = "Player hinzufügen";
  document.getElementById("form-player").reset();
  document.getElementById("f-name").disabled = false;
  document.getElementById("f-buffer").value = 80;
  document.getElementById("f-fragments").value = 4;
  document.getElementById("f-latency").value = 0;
  document.getElementById("f-enabled").checked = true;
  document.getElementById("modal-player").classList.remove("hidden");
}

function openEditModal(p) {
  editingName = p.name;
  document.getElementById("modal-title").textContent = `Player "${p.name}" bearbeiten`;
  document.getElementById("f-name").value = p.name;
  document.getElementById("f-name").disabled = true;
  document.getElementById("f-device").value = p.config.device;
  document.getElementById("f-host").value = p.config.snapserver_host || "";
  document.getElementById("f-port").value = p.config.snapserver_port || "";
  document.getElementById("f-buffer").value = p.config.buffer_time_ms;
  document.getElementById("f-fragments").value = p.config.fragments;
  document.getElementById("f-latency").value = p.config.latency_ms;
  document.getElementById("f-sampleformat").value = p.config.sampleformat || "";
  document.getElementById("f-extra").value = p.config.extra_args || "";
  document.getElementById("f-enabled").checked = p.config.enabled;
  document.getElementById("modal-player").classList.remove("hidden");
}

function closePlayerModal() {
  document.getElementById("modal-player").classList.add("hidden");
}

async function submitPlayerForm(ev) {
  ev.preventDefault();
  const payload = {
    device: document.getElementById("f-device").value.trim(),
    snapserver_host: document.getElementById("f-host").value.trim() || null,
    snapserver_port: document.getElementById("f-port").value ? Number(document.getElementById("f-port").value) : null,
    buffer_time_ms: Number(document.getElementById("f-buffer").value),
    fragments: Number(document.getElementById("f-fragments").value),
    latency_ms: Number(document.getElementById("f-latency").value),
    sampleformat: document.getElementById("f-sampleformat").value.trim() || null,
    extra_args: document.getElementById("f-extra").value.trim(),
    enabled: document.getElementById("f-enabled").checked,
  };

  try {
    if (editingName) {
      await api(`/players/${encodeURIComponent(editingName)}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      });
    } else {
      payload.name = document.getElementById("f-name").value.trim();
      await api("/players", { method: "POST", body: JSON.stringify(payload) });
    }
    closePlayerModal();
    await loadPlayers();
    toast("Gespeichert");
  } catch (e) {
    toast(`Speichern fehlgeschlagen: ${e.message}`, true);
  }
}

// ------------------------------------------------------------------ logs --
async function openLogs(name) {
  document.getElementById("log-title").textContent = `Logs: ${name}`;
  document.getElementById("modal-logs").classList.remove("hidden");
  await refreshLogs(name);
  logPollTimer = setInterval(() => refreshLogs(name), 2000);
}

async function refreshLogs(name) {
  try {
    const lines = await api(`/players/${encodeURIComponent(name)}/logs?lines=200`);
    const el = document.getElementById("log-content");
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
    el.textContent = lines.join("\n") || "(noch keine Log-Zeilen)";
    if (atBottom) el.scrollTop = el.scrollHeight;
  } catch (e) {
    // player might have been deleted while the log view was open
  }
}

function closeLogs() {
  document.getElementById("modal-logs").classList.add("hidden");
  if (logPollTimer) {
    clearInterval(logPollTimer);
    logPollTimer = null;
  }
}

// -------------------------------------------------------------- settings --
async function openSettings() {
  const s = await api("/settings");
  document.getElementById("s-host").value = s.default_snapserver_host || "";
  document.getElementById("s-port").value = s.default_snapserver_port || 1704;
  document.getElementById("modal-settings").classList.remove("hidden");
}

function closeSettings() {
  document.getElementById("modal-settings").classList.add("hidden");
}

async function submitSettingsForm(ev) {
  ev.preventDefault();
  const payload = {
    default_snapserver_host: document.getElementById("s-host").value.trim(),
    default_snapserver_port: Number(document.getElementById("s-port").value),
  };
  try {
    await api("/settings", { method: "PUT", body: JSON.stringify(payload) });
    closeSettings();
    toast("Einstellungen gespeichert");
  } catch (e) {
    toast(`Speichern fehlgeschlagen: ${e.message}`, true);
  }
}

// -------------------------------------------------------------- devices --
async function loadDevices() {
  devices = await api("/devices");
  const list = document.getElementById("device-list");
  list.innerHTML = "";
  for (const d of devices) {
    const opt = document.createElement("option");
    opt.value = d.id;
    opt.label = d.label;
    list.appendChild(opt);
  }
}

// ------------------------------------------------------------------ init --
function wireUpEvents() {
  document.getElementById("btn-add").onclick = openAddModal;
  document.getElementById("btn-cancel").onclick = closePlayerModal;
  document.getElementById("form-player").onsubmit = submitPlayerForm;

  document.getElementById("btn-settings").onclick = openSettings;
  document.getElementById("btn-settings-cancel").onclick = closeSettings;
  document.getElementById("form-settings").onsubmit = submitSettingsForm;

  document.getElementById("btn-logs-close").onclick = closeLogs;
}

async function init() {
  wireUpEvents();
  await Promise.all([loadDevices(), loadPlayers()]);
  setInterval(loadPlayers, 3000);
}

init();
