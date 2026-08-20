const API = "/api";

let players = [];
let devices = [];
let settings = {};
let sinksData = { backend: "alsa", sinks: [], custom: [] };
let editingName = null; // set when the player modal is used for editing
let logViewTimer = null;
let logModalTimer = null; // kept for potential future single-player quick view
let wizardStep = 1;
const WIZARD_STEPS = 4;

let modalPlayer, modalSettings, modalSinks, modalWizard;

// ------------------------------------------------------------------- api --
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
  const container = document.getElementById("toast-container");
  const el = document.createElement("div");
  el.className = `toast align-items-center text-bg-${isError ? "danger" : "success"} border-0`;
  el.setAttribute("role", "alert");
  el.innerHTML = `
    <div class="d-flex">
      <div class="toast-body">${escapeHtml(message)}</div>
      <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
    </div>`;
  container.appendChild(el);
  const t = new bootstrap.Toast(el, { delay: 3500 });
  t.show();
  el.addEventListener("hidden.bs.toast", () => el.remove());
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

function formatUptime(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

// --------------------------------------------------------------- players --
async function loadPlayers() {
  players = await api("/players");
  renderPlayers();
  populateLogPlayerFilter();
}

const STATUS_META = {
  running:  { badge: "text-bg-success",   icon: "fa-play" },
  starting: { badge: "text-bg-warning",   icon: "fa-hourglass-half" },
  stopping: { badge: "text-bg-warning",   icon: "fa-hourglass-half" },
  stopped:  { badge: "text-bg-secondary", icon: "fa-stop" },
  crashed:  { badge: "text-bg-danger",    icon: "fa-triangle-exclamation" },
  error:    { badge: "text-bg-danger",    icon: "fa-triangle-exclamation" },
};

function renderPlayers() {
  const grid = document.getElementById("player-grid");
  const empty = document.getElementById("empty-state");
  grid.innerHTML = "";

  if (players.length === 0) {
    empty.classList.remove("d-none");
    return;
  }
  empty.classList.add("d-none");

  for (const p of players) {
    const meta = STATUS_META[p.status] || STATUS_META.stopped;
    const uptime = p.uptime_seconds ? formatUptime(p.uptime_seconds) : "-";

    const col = document.createElement("div");
    col.className = "col-sm-6 col-lg-4 col-xl-3";
    col.innerHTML = `
      <div class="card h-100 shadow-sm player-card">
        <div class="card-body">
          <div class="d-flex justify-content-between align-items-start mb-2">
            <h6 class="card-title mb-0 text-truncate" title="${escapeHtml(p.name)}">${escapeHtml(p.name)}</h6>
            <span class="badge ${meta.badge}"><i class="fa-solid ${meta.icon} me-1"></i>${escapeHtml(p.status)}</span>
          </div>
          <div class="small text-body-secondary text-truncate mb-2" title="${escapeHtml(p.config.device)}">
            <i class="fa-solid fa-speaker me-1"></i>${escapeHtml(p.config.device)}
          </div>
          <div class="small text-body-secondary">
            <div>Server: ${escapeHtml(p.config.snapserver_host || "(Standard)")}${p.config.snapserver_port ? ":" + p.config.snapserver_port : ""}</div>
            <div>Buffer: ${p.config.buffer_time_ms}ms · Latenz: ${p.config.latency_ms}ms</div>
            <div>Uptime: ${uptime} · PID: ${p.pid ?? "-"} · Neustarts: ${p.restart_count}</div>
          </div>
        </div>
        <div class="card-footer bg-transparent d-flex flex-wrap gap-1">
          <button class="btn btn-sm btn-outline-success" data-action="start" title="Start"><i class="fa-solid fa-play"></i></button>
          <button class="btn btn-sm btn-outline-secondary" data-action="stop" title="Stop"><i class="fa-solid fa-stop"></i></button>
          <button class="btn btn-sm btn-outline-secondary" data-action="restart" title="Neustart"><i class="fa-solid fa-rotate"></i></button>
          <button class="btn btn-sm btn-outline-secondary" data-action="logs" title="Logs"><i class="fa-solid fa-file-lines"></i></button>
          <button class="btn btn-sm btn-outline-secondary" data-action="edit" title="Bearbeiten"><i class="fa-solid fa-pen"></i></button>
          <button class="btn btn-sm btn-outline-danger ms-auto" data-action="delete" title="Löschen"><i class="fa-solid fa-trash"></i></button>
        </div>
      </div>
    `;

    col.querySelector('[data-action="start"]').onclick = () => runAction(p.name, "start");
    col.querySelector('[data-action="stop"]').onclick = () => runAction(p.name, "stop");
    col.querySelector('[data-action="restart"]').onclick = () => runAction(p.name, "restart");
    col.querySelector('[data-action="logs"]').onclick = () => openLogsForPlayer(p.name);
    col.querySelector('[data-action="edit"]').onclick = () => openEditModal(p);
    col.querySelector('[data-action="delete"]').onclick = () => deletePlayer(p.name);

    grid.appendChild(col);
  }
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
    toast(`Player „${name}“ gelöscht`);
  } catch (e) {
    toast(`Löschen fehlgeschlagen: ${e.message}`, true);
  }
}

// ------------------------------------------------------------- add/edit --
function openAddModal() {
  editingName = null;
  document.getElementById("modal-title").innerHTML = '<i class="fa-solid fa-plus me-2"></i>Player hinzufügen';
  document.getElementById("form-player").reset();
  document.getElementById("f-name").disabled = false;
  document.getElementById("f-buffer").value = settings.default_buffer_time_ms ?? 80;
  document.getElementById("f-fragments").value = settings.default_fragments ?? 4;
  document.getElementById("f-latency").value = settings.default_latency_ms ?? 0;
  document.getElementById("f-enabled").checked = true;
  applyBackendFieldVisibility();
  modalPlayer.show();
}

function openEditModal(p) {
  editingName = p.name;
  document.getElementById("modal-title").innerHTML = `<i class="fa-solid fa-pen me-2"></i>Player "${escapeHtml(p.name)}" bearbeiten`;
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
  applyBackendFieldVisibility();
  modalPlayer.show();
}

function applyBackendFieldVisibility() {
  const isPulse = settings.backend === "pulse";
  document.getElementById("f-fragments-group").classList.toggle("d-none", isPulse);
  document.getElementById("f-device-hint").textContent = isPulse
    ? "PulseAudio-Sink (hardwarebasiert oder Custom Sink)."
    : "ALSA-Gerät auf diesem Host.";
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
    modalPlayer.hide();
    await loadPlayers();
    toast("Gespeichert");
  } catch (e) {
    toast(`Speichern fehlgeschlagen: ${e.message}`, true);
  }
}

// -------------------------------------------------------------- devices --
async function loadDevices() {
  devices = await api("/devices");
  const select = document.getElementById("f-device");
  const current = select.value;
  select.innerHTML = "";
  for (const d of devices) {
    const opt = document.createElement("option");
    opt.value = d.id;
    opt.textContent = d.label;
    select.appendChild(opt);
  }
  if (current) select.value = current;
}

// -------------------------------------------------------------- settings --
async function loadSettings() {
  settings = await api("/settings");
  updateBackendPill();
}

function updateBackendPill() {
  const pill = document.getElementById("backend-pill");
  pill.textContent = `Backend: ${settings.backend === "pulse" ? "PulseAudio" : "ALSA"}`;
  pill.className = `badge ${settings.backend === "pulse" ? "text-bg-info" : "text-bg-secondary"}`;
}

async function openSettingsModal() {
  await loadSettings();
  document.getElementById("s-backend").value = settings.backend || "alsa";
  document.getElementById("s-host").value = settings.default_snapserver_host || "";
  document.getElementById("s-port").value = settings.default_snapserver_port || 1704;
  document.getElementById("s-buffer").value = settings.default_buffer_time_ms ?? 80;
  document.getElementById("s-fragments").value = settings.default_fragments ?? 4;
  document.getElementById("s-latency").value = settings.default_latency_ms ?? 0;
  updateSettingsBackendHint();
  modalSettings.show();
}

function updateSettingsBackendHint() {
  const val = document.getElementById("s-backend").value;
  const hint = document.getElementById("s-backend-hint");
  if (val === "pulse") {
    hint.textContent = settings.pulse_available
      ? "PulseAudio läuft bereits."
      : "PulseAudio wird beim Speichern gestartet (kann einige Sekunden dauern).";
  } else {
    hint.textContent = "Jeder Player greift direkt auf sein ALSA-Gerät zu.";
  }
}

async function submitSettingsForm(ev) {
  ev.preventDefault();
  const payload = {
    backend: document.getElementById("s-backend").value,
    default_snapserver_host: document.getElementById("s-host").value.trim(),
    default_snapserver_port: Number(document.getElementById("s-port").value),
    default_buffer_time_ms: Number(document.getElementById("s-buffer").value),
    default_fragments: Number(document.getElementById("s-fragments").value),
    default_latency_ms: Number(document.getElementById("s-latency").value),
  };
  try {
    settings = await api("/settings", { method: "PUT", body: JSON.stringify(payload) });
    updateBackendPill();
    modalSettings.hide();
    toast("Einstellungen gespeichert");
    await loadDevices();
  } catch (e) {
    toast(`Speichern fehlgeschlagen: ${e.message}`, true);
  }
}

// ----------------------------------------------------------------- sinks --
async function loadSinks() {
  sinksData = await api("/sinks");
  renderSinks();
}

function renderSinks() {
  const disabledAlert = document.getElementById("sinks-disabled-alert");
  const panel = document.getElementById("sinks-panel");
  const isPulse = sinksData.backend === "pulse";
  disabledAlert.classList.toggle("d-none", isPulse);
  panel.classList.toggle("d-none", !isPulse);
  if (!isPulse) return;

  const tbody = document.getElementById("sinks-table-body");
  tbody.innerHTML = "";
  for (const s of sinksData.sinks) {
    const tr = document.createElement("tr");
    const isCustom = s.kind === "combine" || s.kind === "remap";
    const custom = sinksData.custom.find((c) => c.name === s.name);
    tr.innerHTML = `
      <td><code>${escapeHtml(s.name)}</code></td>
      <td><span class="badge text-bg-light border">${escapeHtml(s.kind)}</span></td>
      <td>${escapeHtml((custom && custom.description) || s.description)}</td>
      <td class="text-end">${isCustom ? '<button class="btn btn-sm btn-outline-danger" data-del="' + escapeHtml(s.name) + '"><i class="fa-solid fa-trash"></i></button>' : ""}</td>
    `;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll("[data-del]").forEach((btn) => {
    btn.onclick = () => deleteSink(btn.dataset.del);
  });

  // populate slave/master selects with every currently loaded sink
  const slaveSelect = document.getElementById("combine-slaves");
  const masterSelect = document.getElementById("remap-master");
  slaveSelect.innerHTML = "";
  masterSelect.innerHTML = "";
  for (const s of sinksData.sinks) {
    const label = `${s.description} (${s.name}) [${s.kind}]`;
    const o1 = document.createElement("option");
    o1.value = s.name; o1.textContent = label;
    slaveSelect.appendChild(o1);
    const o2 = document.createElement("option");
    o2.value = s.name; o2.textContent = label;
    masterSelect.appendChild(o2);
  }
}

async function openSinksModal() {
  await loadSettings();
  await loadSinks();
  modalSinks.show();
}

async function createCombineSink(ev) {
  ev.preventDefault();
  const slaves = Array.from(document.getElementById("combine-slaves").selectedOptions).map((o) => o.value);
  if (slaves.length < 2) {
    toast("Bitte mindestens 2 Quell-Sinks auswählen", true);
    return;
  }
  const payload = {
    name: document.getElementById("combine-name").value.trim(),
    kind: "combine",
    description: document.getElementById("combine-desc").value.trim(),
    slaves,
  };
  try {
    await api("/sinks", { method: "POST", body: JSON.stringify(payload) });
    document.getElementById("form-sink-combine").reset();
    await loadSinks();
    await loadDevices();
    toast("Combine-Sink angelegt");
  } catch (e) {
    toast(`Anlegen fehlgeschlagen: ${e.message}`, true);
  }
}

// PulseAudio channel names with human-readable labels (matches Sendspin)
const PA_CHANNELS = [
  { value: "front-left",   label: "Front Left"   },
  { value: "front-right",  label: "Front Right"  },
  { value: "front-center", label: "Front Center" },
  { value: "lfe",          label: "LFE (Subwoofer)" },
  { value: "rear-left",    label: "Rear Left"    },
  { value: "rear-right",   label: "Rear Right"   },
  { value: "side-left",    label: "Side Left"    },
  { value: "side-right",   label: "Side Right"   },
  { value: "mono",         label: "Mono"         },
];

function channelSelect(selected, cls) {
  return `<select class="form-select form-select-sm ${cls}">
    ${PA_CHANNELS.map(ch =>
      `<option value="${ch.value}"${ch.value === selected ? " selected" : ""}>${ch.label}</option>`
    ).join("")}
  </select>`;
}

function renderRemapRows() {
  const isMono = document.getElementById("remap-mode-mono").checked;
  const rows = document.getElementById("remap-channel-rows");
  if (isMono) {
    rows.innerHTML = `
      <div class="d-flex align-items-center gap-2">
        <span class="text-body-secondary fw-semibold" style="min-width:100px">Mono Output</span>
        <span class="text-body-secondary">←</span>
        ${channelSelect("front-left", "remap-master-ch")}
      </div>`;
  } else {
    rows.innerHTML = `
      <div class="d-flex align-items-center gap-2">
        <span class="text-body-secondary fw-semibold" style="min-width:100px">Left Output</span>
        <span class="text-body-secondary">←</span>
        ${channelSelect("front-left", "remap-master-ch")}
      </div>
      <div class="d-flex align-items-center gap-2">
        <span class="text-body-secondary fw-semibold" style="min-width:100px">Right Output</span>
        <span class="text-body-secondary">←</span>
        ${channelSelect("front-right", "remap-master-ch")}
      </div>`;
  }
}

function initRemapRows() {
  document.getElementById("remap-mode-stereo").checked = true;
  renderRemapRows();
}

async function createRemapSink(ev) {
  ev.preventDefault();
  const isMono = document.getElementById("remap-mode-mono").checked;
  const masterSelects = [...document.querySelectorAll("#remap-channel-rows .remap-master-ch")];
  const masterChannels = masterSelects.map(s => s.value);
  const outputChannels = isMono ? ["mono"] : ["front-left", "front-right"];
  const payload = {
    name: document.getElementById("remap-name").value.trim(),
    kind: "remap",
    description: document.getElementById("remap-desc").value.trim(),
    master: document.getElementById("remap-master").value,
    channels: outputChannels.length,
    channel_map: outputChannels.join(","),
    master_channel_map: masterChannels.join(","),
  };
  try {
    await api("/sinks", { method: "POST", body: JSON.stringify(payload) });
    document.getElementById("form-sink-remap").reset();
    initRemapRows();
    await loadSinks();
    await loadDevices();
    toast("Remap-Sink angelegt");
  } catch (e) {
    toast(`Anlegen fehlgeschlagen: ${e.message}`, true);
  }
}

async function deleteSink(name) {
  if (!confirm(`Sink "${name}" wirklich löschen?`)) return;
  try {
    await api(`/sinks/${encodeURIComponent(name)}`, { method: "DELETE" });
    await loadSinks();
    await loadDevices();
    toast(`Sink „${name}“ gelöscht`);
  } catch (e) {
    toast(`Löschen fehlgeschlagen: ${e.message}`, true);
  }
}

// ---------------------------------------------------------------- wizard --
function openWizard() {
  wizardStep = 1;
  document.getElementById("wizard-backend-alsa").checked = (settings.backend || "alsa") === "alsa";
  document.getElementById("wizard-backend-pulse").checked = settings.backend === "pulse";
  document.getElementById("wizard-host").value = settings.default_snapserver_host || "";
  document.getElementById("wizard-port").value = settings.default_snapserver_port || 1704;
  renderWizardStep();
  modalWizard.show();
}

function renderWizardStep() {
  document.querySelectorAll(".wizard-step").forEach((el) => {
    el.classList.toggle("d-none", Number(el.dataset.step) !== wizardStep);
  });
  document.getElementById("wizard-progress").style.width = `${(wizardStep / WIZARD_STEPS) * 100}%`;
  document.getElementById("wizard-back").classList.toggle("d-none", wizardStep === 1);
  document.getElementById("wizard-next").classList.toggle("d-none", wizardStep === WIZARD_STEPS);
  document.getElementById("wizard-finish").classList.toggle("d-none", wizardStep !== WIZARD_STEPS);

  if (wizardStep === WIZARD_STEPS) {
    const backend = document.querySelector('input[name="wizard-backend"]:checked').value;
    const host = document.getElementById("wizard-host").value.trim() || "(unverändert)";
    const port = document.getElementById("wizard-port").value;
    document.getElementById("wizard-summary").innerHTML = `
      <div><strong>Backend:</strong> ${backend === "pulse" ? "PulseAudio" : "ALSA"}</div>
      <div><strong>Snapserver:</strong> ${escapeHtml(host)}${port ? ":" + escapeHtml(String(port)) : ""}</div>
    `;
  }
}

async function finishWizard() {
  const backend = document.querySelector('input[name="wizard-backend"]:checked').value;
  const host = document.getElementById("wizard-host").value.trim();
  const port = document.getElementById("wizard-port").value;
  const payload = {
    backend,
    default_snapserver_host: host || settings.default_snapserver_host || "",
    default_snapserver_port: port ? Number(port) : (settings.default_snapserver_port || 1704),
    default_buffer_time_ms: settings.default_buffer_time_ms ?? 80,
    default_fragments: settings.default_fragments ?? 4,
    default_latency_ms: settings.default_latency_ms ?? 0,
  };
  try {
    settings = await api("/settings", { method: "PUT", body: JSON.stringify(payload) });
    updateBackendPill();
    await loadDevices();
    modalWizard.hide();
    toast("Setup abgeschlossen");
  } catch (e) {
    toast(`Setup fehlgeschlagen: ${e.message}`, true);
  }
}

// ------------------------------------------------------------------ logs --
function switchView(view) {
  document.getElementById("view-players").classList.toggle("d-none", view !== "players");
  document.getElementById("view-logs").classList.toggle("d-none", view !== "logs");
  document.getElementById("tab-players").classList.toggle("active", view === "players");
  document.getElementById("tab-logs").classList.toggle("active", view === "logs");

  if (view === "logs") {
    refreshLogView();
    if (logViewTimer) clearInterval(logViewTimer);
    logViewTimer = setInterval(() => {
      if (document.getElementById("log-auto-refresh").checked) refreshLogView();
    }, 3000);
  } else if (logViewTimer) {
    clearInterval(logViewTimer);
    logViewTimer = null;
  }
}

function populateLogPlayerFilter() {
  const select = document.getElementById("log-filter-player");
  const current = select.value;
  select.innerHTML = '<option value="">Alle Player</option>';
  for (const p of players) {
    const opt = document.createElement("option");
    opt.value = p.name;
    opt.textContent = p.name;
    select.appendChild(opt);
  }
  select.value = current;
}

function openLogsForPlayer(name) {
  switchView("logs");
  document.getElementById("log-filter-player").value = name;
  refreshLogView();
}

async function refreshLogView() {
  const player = document.getElementById("log-filter-player").value;
  const search = document.getElementById("log-filter-search").value;
  const lines = document.getElementById("log-filter-lines").value;
  const params = new URLSearchParams({ lines });
  if (player) params.set("player", player);
  if (search) params.set("search", search);
  try {
    const entries = await api(`/logs?${params.toString()}`);
    const el = document.getElementById("log-view-content");
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
    el.textContent = entries.length
      ? entries.map((e) => `[${e.player}] ${e.line}`).join("\n")
      : "(keine passenden Log-Zeilen)";
    if (atBottom) el.scrollTop = el.scrollHeight;
  } catch (e) {
    // ignore transient errors during polling
  }
}

// ------------------------------------------------------------ diagnostics --
async function downloadDiagnostics() {
  try {
    const res = await fetch(`${API}/diagnostics`);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const text = await res.text();
    const blob = new Blob([text], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const ts = new Date().toISOString().replace(/[:.]/g, "-");
    a.href = url;
    a.download = `snapcast-multiroom-diagnostics-${ts}.txt`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (e) {
    toast(`Diagnose-Export fehlgeschlagen: ${e.message}`, true);
  }
}

// ------------------------------------------------------------------ init --
function wireUpEvents() {
  document.getElementById("btn-add").onclick = openAddModal;
  document.getElementById("form-player").onsubmit = submitPlayerForm;

  document.getElementById("tab-players").onclick = (e) => { e.preventDefault(); switchView("players"); };
  document.getElementById("tab-logs").onclick = (e) => { e.preventDefault(); switchView("logs"); };
  document.getElementById("empty-wizard-link").onclick = (e) => { e.preventDefault(); openWizard(); };

  document.getElementById("menu-system-settings").onclick = (e) => { e.preventDefault(); openSettingsModal(); };
  document.getElementById("form-settings").onsubmit = submitSettingsForm;
  document.getElementById("s-backend").onchange = updateSettingsBackendHint;

  document.getElementById("menu-sinks").onclick = (e) => { e.preventDefault(); openSinksModal(); };
  document.getElementById("sinks-open-settings").onclick = (e) => { e.preventDefault(); modalSinks.hide(); openSettingsModal(); };
  document.getElementById("form-sink-combine").onsubmit = createCombineSink;
  document.getElementById("form-sink-remap").onsubmit = createRemapSink;
  document.querySelectorAll("input[name='remap-mode']").forEach(r => r.onchange = renderRemapRows);
  initRemapRows();

  document.getElementById("menu-wizard").onclick = (e) => { e.preventDefault(); openWizard(); };
  document.getElementById("wizard-next").onclick = () => { wizardStep = Math.min(WIZARD_STEPS, wizardStep + 1); renderWizardStep(); };
  document.getElementById("wizard-back").onclick = () => { wizardStep = Math.max(1, wizardStep - 1); renderWizardStep(); };
  document.getElementById("wizard-finish").onclick = finishWizard;

  document.getElementById("menu-diagnostics").onclick = (e) => { e.preventDefault(); downloadDiagnostics(); };

  document.getElementById("btn-logs-refresh").onclick = refreshLogView;
  document.getElementById("log-filter-player").onchange = refreshLogView;
  document.getElementById("log-filter-lines").onchange = refreshLogView;
  let searchDebounce;
  document.getElementById("log-filter-search").oninput = () => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(refreshLogView, 350);
  };
}

async function init() {
  modalPlayer = new bootstrap.Modal(document.getElementById("modal-player"));
  modalSettings = new bootstrap.Modal(document.getElementById("modal-settings"));
  modalSinks = new bootstrap.Modal(document.getElementById("modal-sinks"));
  modalWizard = new bootstrap.Modal(document.getElementById("modal-wizard"));

  wireUpEvents();
  await loadSettings();
  await Promise.all([loadDevices(), loadPlayers()]);
  switchView("players");
  setInterval(loadPlayers, 3000);
}

init();
