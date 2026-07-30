const STORAGE_KEY = "oracle-operator-ui-state";
const MAX_RECENT_COMMANDS = 20;

const state = {
  page: document.body.dataset.page || "oracle",
  defaults: loadStoredState().defaults || {},
  recentCommands: loadStoredState().recentCommands || [],
  selectedCommandId: loadStoredState().selectedCommandId || null,
  logTargets: [],
  theme: "dark",
  launcher: {
    open: false,
    sessionId: "",
    mediaRecorder: null,
    mediaStream: null,
    chunks: [],
    recording: false,
    audio: null,
    audioUrl: "",
  },
};

const chromeElements = {
  themeToggle: document.querySelector("#theme-toggle"),
  themeToggleIcon: document.querySelector("#theme-toggle-icon"),
  launcher: document.querySelector("#oracle-launcher"),
  launcherToggle: document.querySelector("#oracle-launcher-toggle"),
  launcherClose: document.querySelector("#oracle-launcher-close"),
  launcherForm: document.querySelector("#oracle-launcher-form"),
  launcherInput: document.querySelector("#oracle-launcher-input"),
  launcherMic: document.querySelector("#oracle-launcher-mic"),
  launcherSend: document.querySelector("#oracle-launcher-send"),
  launcherStatus: document.querySelector("#oracle-launcher-status"),
  launcherReply: document.querySelector("#oracle-launcher-reply"),
};

const THEME_STORAGE_KEY = "oracle-ui-theme";
const LAUNCHER_SESSION_KEY = "oracle-admin-tools-launcher-session";

initialize();

function initialize() {
  initializeTheme();
  initializeLauncher();
  if (state.page === "oracle") {
    initOraclePage();
    return;
  }
  if (state.page === "trace") {
    initTracePage();
    return;
  }
  if (state.page === "logs") {
    initLogsPage();
  }
}

function initializeTheme() {
  applyTheme(readStoredTheme());
  chromeElements.themeToggle?.addEventListener("click", () => {
    const nextTheme = state.theme === "dark" ? "light" : "dark";
    localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
    applyTheme(nextTheme);
  });
}

function readStoredTheme() {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "light" || stored === "dark") {
      return stored;
    }
  } catch {
    // Ignore storage failures and fall through to preference/default.
  }
  return globalThis.matchMedia?.("(prefers-color-scheme: light)")?.matches ? "light" : "dark";
}

function applyTheme(theme) {
  state.theme = theme === "light" ? "light" : "dark";
  document.body.dataset.theme = state.theme;
  document.documentElement.dataset.theme = state.theme;
  if (chromeElements.themeToggleIcon) {
    chromeElements.themeToggleIcon.textContent = state.theme === "dark" ? "light_mode" : "dark_mode";
  }
  if (chromeElements.themeToggle) {
    chromeElements.themeToggle.setAttribute(
      "aria-label",
      state.theme === "dark" ? "Switch to light theme" : "Switch to dark theme",
    );
  }
}

function initializeLauncher() {
  if (!chromeElements.launcher) {
    return;
  }
  state.launcher.sessionId = getOrCreateLauncherSessionId();
  chromeElements.launcherToggle?.addEventListener("click", () => setLauncherOpen(!state.launcher.open));
  chromeElements.launcherClose?.addEventListener("click", () => setLauncherOpen(false));
  chromeElements.launcherForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitLauncherPrompt();
  });
  chromeElements.launcherMic?.addEventListener("click", async () => {
    await toggleLauncherMic();
  });
}

function getOrCreateLauncherSessionId() {
  try {
    const existing = localStorage.getItem(LAUNCHER_SESSION_KEY);
    if (existing) {
      return existing;
    }
  } catch {
    // Ignore storage failures and generate an ephemeral id.
  }
  const created = buildLocalId();
  try {
    localStorage.setItem(LAUNCHER_SESSION_KEY, created);
  } catch {
    // Ignore storage failures and keep the in-memory id.
  }
  return created;
}

function launcherSource() {
  if (state.page === "trace" || state.page === "logs") {
    return "browser-admin-tools";
  }
  return "browser-admin-ui";
}

function setLauncherOpen(open) {
  state.launcher.open = open;
  chromeElements.launcher?.classList.toggle("is-open", open);
  chromeElements.launcherToggle?.setAttribute("aria-expanded", String(open));
}

function setLauncherStatus(message, tone = "info") {
  if (!chromeElements.launcherStatus) {
    return;
  }
  chromeElements.launcherStatus.textContent = message;
  chromeElements.launcherStatus.classList.toggle("is-error", tone === "error");
}

async function submitLauncherPrompt(options = {}) {
  const text = String(chromeElements.launcherInput?.value || "").trim();
  if (!text) {
    setLauncherStatus("Type something for Oracle first.", "error");
    return;
  }
  const playReplyAudio = Boolean(options.playReplyAudio);
  setLauncherOpen(true);
  setLauncherStatus("Oracle is thinking...");
  chromeElements.launcherSend.disabled = true;
  try {
    const response = await postJson("/api/voice/command", {
      text,
      source: launcherSource(),
      session_id: state.launcher.sessionId,
    });
    const replyText = String(response.reply_text || "Oracle did not return a reply.");
    chromeElements.launcherReply.innerHTML = `
      <div><strong>You</strong><div>${escapeHtml(text)}</div></div>
      <div><strong>Oracle</strong><div>${escapeHtml(replyText)}</div></div>
    `;
    chromeElements.launcherReply.classList.remove("is-hidden");
    chromeElements.launcherInput.value = "";
    if (playReplyAudio) {
      await playLauncherReply(replyText);
      setLauncherStatus("Oracle replied out loud.");
    } else {
      setLauncherStatus("Oracle replied.");
    }
  } catch (error) {
    setLauncherStatus(error instanceof Error ? error.message : "Unable to reach Oracle.", "error");
  } finally {
    chromeElements.launcherSend.disabled = false;
  }
}

async function playLauncherReply(text) {
  if (!text) {
    return;
  }
  const response = await fetch("/api/voice/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  const blob = await response.blob();
  if (!state.launcher.audio) {
    state.launcher.audio = new Audio();
  }
  if (state.launcher.audioUrl) {
    URL.revokeObjectURL(state.launcher.audioUrl);
  }
  state.launcher.audioUrl = URL.createObjectURL(blob);
  state.launcher.audio.src = state.launcher.audioUrl;
  await state.launcher.audio.play();
}

async function toggleLauncherMic() {
  if (state.launcher.recording) {
    await stopLauncherMic();
    return;
  }
  await startLauncherMic();
}

async function startLauncherMic() {
  if (!globalThis.isSecureContext && globalThis.location?.hostname !== "localhost" && globalThis.location?.hostname !== "127.0.0.1") {
    setLauncherStatus("Browser mic usually needs HTTPS or localhost access.", "error");
    return;
  }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder === "undefined") {
    setLauncherStatus("Browser microphone capture is not available here.", "error");
    return;
  }
  try {
    setLauncherOpen(true);
    state.launcher.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    state.launcher.chunks = [];
    state.launcher.mediaRecorder = new MediaRecorder(state.launcher.mediaStream);
    state.launcher.mediaRecorder.addEventListener("dataavailable", (event) => {
      if (event.data && event.data.size > 0) {
        state.launcher.chunks.push(event.data);
      }
    });
    state.launcher.mediaRecorder.start();
    state.launcher.recording = true;
    chromeElements.launcherMic.innerHTML = '<span class="material-symbols-outlined">stop</span><span>Stop</span>';
    setLauncherStatus("Listening from this browser microphone.");
  } catch (error) {
    setLauncherStatus(error instanceof Error ? error.message : "Unable to start the microphone.", "error");
  }
}

async function stopLauncherMic() {
  const recorder = state.launcher.mediaRecorder;
  if (!recorder) {
    return;
  }
  setLauncherStatus("Transcribing microphone audio...");
  const stopped = new Promise((resolve) => {
    recorder.addEventListener("stop", resolve, { once: true });
  });
  recorder.stop();
  await stopped;
  for (const track of state.launcher.mediaStream?.getTracks?.() || []) {
    track.stop();
  }
  state.launcher.mediaRecorder = null;
  state.launcher.mediaStream = null;
  state.launcher.recording = false;
  chromeElements.launcherMic.innerHTML = '<span class="material-symbols-outlined">mic</span><span>Mic</span>';
  try {
    const mimeType = state.launcher.chunks[0]?.type || "audio/webm";
    const blob = new Blob(state.launcher.chunks, { type: mimeType });
    const formData = new FormData();
    formData.append("audio", blob, mimeType.includes("mp4") ? "oracle-mic.m4a" : "oracle-mic.webm");
    const sttResponse = await fetch("/api/voice/stt", {
      method: "POST",
      body: formData,
    });
    if (!sttResponse.ok) {
      throw new Error(await parseError(sttResponse));
    }
    const sttPayload = await sttResponse.json();
    const transcript = String(sttPayload.text || "").trim();
    if (!transcript) {
      throw new Error("Oracle returned an empty transcript.");
    }
    chromeElements.launcherInput.value = transcript;
    setLauncherStatus(`Heard: ${transcript}`);
    await submitLauncherPrompt({ playReplyAudio: true });
  } catch (error) {
    setLauncherStatus(error instanceof Error ? error.message : "Unable to transcribe microphone audio.", "error");
  } finally {
    state.launcher.chunks = [];
  }
}

function loadStoredState() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    return {
      defaults: parsed.defaults || {},
      recentCommands: Array.isArray(parsed.recentCommands) ? parsed.recentCommands : [],
      selectedCommandId: typeof parsed.selectedCommandId === "string" ? parsed.selectedCommandId : null,
    };
  } catch {
    return { defaults: {}, recentCommands: [], selectedCommandId: null };
  }
}

function persistState() {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      defaults: state.defaults,
      recentCommands: state.recentCommands,
      selectedCommandId: state.selectedCommandId,
    }),
  );
}

function rememberDefaults(prefix, source, session) {
  state.defaults[`${prefix}Source`] = source.trim();
  state.defaults[`${prefix}Session`] = session.trim();
  persistState();
}

function setButtonBusy(button, busy, busyLabel) {
  if (!button) {
    return;
  }
  if (!button.dataset.defaultLabel) {
    button.dataset.defaultLabel = button.textContent;
  }
  button.disabled = busy;
  button.textContent = busy ? busyLabel : button.dataset.defaultLabel;
}

function buildLocalId() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `cmd-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
}

function getSelectedOrLatestCommand() {
  return (
    state.recentCommands.find((entry) => entry.id === state.selectedCommandId) ||
    state.recentCommands[0] ||
    null
  );
}

function addRecentCommand(entry) {
  state.recentCommands.unshift(entry);
  state.recentCommands = state.recentCommands.slice(0, MAX_RECENT_COMMANDS);
  state.selectedCommandId = entry.id;
  persistState();
}

async function fetchJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  return response.json();
}

async function fetchText(path) {
  const response = await fetch(path, { headers: { Accept: "text/plain" }, cache: "no-store" });
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  return response.text();
}

async function postJson(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  return response.json();
}

async function parseError(response) {
  const text = await response.text();
  if (!text.trim()) {
    return `HTTP ${response.status}`;
  }
  try {
    const payload = JSON.parse(text);
    if (typeof payload?.detail === "string" && payload.detail.trim()) {
      return payload.detail.trim();
    }
    return JSON.stringify(payload, null, 2);
  } catch {
    return text.trim();
  }
}

function formatTimestamp(value) {
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function copyText(text) {
  if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
    return navigator.clipboard.writeText(text);
  }
  return Promise.reject(new Error("Clipboard write is not available."));
}

async function loadSourceOptions(selectElement, sourceInput, defaultsKey) {
  try {
    const payload = await fetchJson("/api/admin/sources");
    const sources = Array.isArray(payload.sources) ? payload.sources : [];
    const currentValue = sourceInput.value.trim() || state.defaults[defaultsKey] || "";
    selectElement.innerHTML = "";
    const customOption = document.createElement("option");
    customOption.value = "__custom__";
    customOption.textContent = "Custom source...";
    selectElement.append(customOption);
    for (const item of sources) {
      const option = document.createElement("option");
      option.value = item.source;
      const tags = [];
      if (item.playback_capable) {
        tags.push("playback");
      }
      if (item.supports_oracle_native_music) {
        tags.push("native-music");
      }
      option.textContent = tags.length > 0 ? `${item.source} (${tags.join(", ")})` : item.source;
      selectElement.append(option);
    }
    sourceInput.value = currentValue;
    syncSelectToText(selectElement, sourceInput);
  } catch {
    selectElement.innerHTML = '<option value="__custom__">Custom source...</option>';
    syncSelectToText(selectElement, sourceInput);
  }
}

function syncTextFromSelect(selectElement, inputElement) {
  const selected = selectElement.value;
  if (selected && selected !== "__custom__") {
    inputElement.value = selected;
  }
}

function syncSelectToText(selectElement, inputElement) {
  const value = inputElement.value.trim();
  const match = Array.from(selectElement.options).find((option) => option.value === value);
  selectElement.value = match ? value : "__custom__";
}

function buildCompactDetails(response) {
  if (!response) {
    return "";
  }
  const lines = [];
  if (response.route?.target) {
    lines.push(`Route: ${response.route.target}`);
  }
  if (response.dispatch?.hook) {
    lines.push(`Dispatch: ${response.dispatch.hook}`);
  }
  if (response.dispatch?.status) {
    lines.push(`Status: ${response.dispatch.status}`);
  }
  if (response.effective_session_id) {
    lines.push(`Effective Session: ${response.effective_session_id}`);
  }
  return lines.join(" | ");
}

function initOraclePage() {
  const elements = {
    form: document.querySelector("#oracle-form"),
    text: document.querySelector("#oracle-text"),
    sourceSelect: document.querySelector("#oracle-source-select"),
    source: document.querySelector("#oracle-source"),
    session: document.querySelector("#oracle-session"),
    send: document.querySelector("#oracle-send"),
    micStart: document.querySelector("#oracle-mic-start"),
    micStop: document.querySelector("#oracle-mic-stop"),
    playReply: document.querySelector("#oracle-play-reply"),
    micStatus: document.querySelector("#oracle-mic-status"),
    audio: document.querySelector("#oracle-audio"),
    reply: document.querySelector("#oracle-reply"),
    compactDetails: document.querySelector("#oracle-compact-details"),
    toggleDetails: document.querySelector("#oracle-toggle-details"),
    history: document.querySelector("#oracle-history"),
    clearHistory: document.querySelector("#oracle-clear-history"),
    healthPills: document.querySelector("#oracle-health-pills"),
  };

  const oracleRuntime = {
    mediaRecorder: null,
    mediaStream: null,
    chunks: [],
    lastReplyText: "",
  };

  elements.source.value = "oracle-webpage";
  elements.session.value = buildPageSessionId();
  void loadSourceOptions(elements.sourceSelect, elements.source, "oracleSource");
  elements.sourceSelect.addEventListener("change", () => {
    syncTextFromSelect(elements.sourceSelect, elements.source);
  });
  elements.source.addEventListener("input", () => syncSelectToText(elements.sourceSelect, elements.source));
  elements.toggleDetails.addEventListener("click", () => {
    elements.compactDetails.classList.toggle("is-hidden");
  });
  elements.playReply.addEventListener("click", async () => {
    await playOracleReply(elements, oracleRuntime.lastReplyText);
  });
  elements.audio.addEventListener("play", () => {
    elements.reply.classList.add("is-speaking");
  });
  elements.audio.addEventListener("ended", () => {
    elements.reply.classList.remove("is-speaking");
  });
  elements.audio.addEventListener("pause", () => {
    elements.reply.classList.remove("is-speaking");
  });
  elements.micStart.addEventListener("click", async () => {
    await startOracleMic(elements, oracleRuntime);
  });
  elements.micStop.addEventListener("click", async () => {
    await stopOracleMic(elements, oracleRuntime);
  });
  elements.clearHistory.addEventListener("click", () => {
    state.recentCommands = [];
    state.selectedCommandId = null;
    persistState();
    renderOracleHistory(elements.history);
  });
  elements.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      text: elements.text.value.trim(),
      source: elements.source.value.trim() || null,
      playback_target_source_id: elements.source.value.trim() || null,
      session_id: elements.session.value.trim() || null,
    };
    if (!payload.text) {
      elements.reply.textContent = "Command text is required.";
      return;
    }
    rememberDefaults("oracle", elements.source.value, elements.session.value);
    setButtonBusy(elements.send, true, "Sending...");
    try {
      const response = await postJson("/api/voice/command", payload);
      oracleRuntime.lastReplyText = response.reply_text || "";
      elements.reply.textContent = response.reply_text || "(empty reply)";
      elements.compactDetails.textContent = buildCompactDetails(response);
      elements.playReply.disabled = !oracleRuntime.lastReplyText;
      const entry = {
        id: buildLocalId(),
        at: new Date().toISOString(),
        request: payload,
        response,
        sessionSnapshot: null,
        playbackSnapshot: null,
      };
      addRecentCommand(entry);
      renderOracleHistory(elements.history);
      if (oracleRuntime.lastReplyText) {
        void playOracleReply(elements, oracleRuntime.lastReplyText, { auto: true });
      }
    } catch (error) {
      elements.reply.textContent = error instanceof Error ? error.message : "Unknown error";
      elements.playReply.disabled = true;
    } finally {
      setButtonBusy(elements.send, false, "Send");
    }
  });

  renderOracleHistory(elements.history);
  void loadOracleHealth(elements.healthPills);
}

async function playOracleReply(elements, text, options = {}) {
  if (!text) {
    return;
  }
  const auto = Boolean(options.auto);
  if (!auto) {
    setButtonBusy(elements.playReply, true, "Loading...");
  }
  try {
    const response = await fetch("/api/voice/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!response.ok) {
      throw new Error(await parseError(response));
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    elements.audio.src = url;
    await elements.audio.play();
  } catch (error) {
    if (!auto) {
      elements.reply.textContent = error instanceof Error ? error.message : "Unable to play reply audio.";
    }
  } finally {
    if (!auto) {
      setButtonBusy(elements.playReply, false, "Play Reply");
    }
  }
}

async function startOracleMic(elements, runtime) {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder === "undefined") {
    elements.micStatus.textContent = "Browser microphone capture is not available here.";
    return;
  }
  try {
    runtime.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    runtime.chunks = [];
    runtime.mediaRecorder = new MediaRecorder(runtime.mediaStream);
    runtime.mediaRecorder.addEventListener("dataavailable", (event) => {
      if (event.data && event.data.size > 0) {
        runtime.chunks.push(event.data);
      }
    });
    runtime.mediaRecorder.start();
    elements.micStart.disabled = true;
    elements.micStop.disabled = false;
    elements.micStatus.textContent = "Recording from this browser mic.";
  } catch (error) {
    elements.micStatus.textContent = error instanceof Error ? error.message : "Unable to start the microphone.";
  }
}

async function stopOracleMic(elements, runtime) {
  const recorder = runtime.mediaRecorder;
  if (!recorder) {
    return;
  }
  elements.micStop.disabled = true;
  elements.micStatus.textContent = "Transcribing microphone audio...";
  const stopped = new Promise((resolve) => {
    recorder.addEventListener("stop", resolve, { once: true });
  });
  recorder.stop();
  await stopped;
  for (const track of runtime.mediaStream?.getTracks?.() || []) {
    track.stop();
  }
  runtime.mediaRecorder = null;
  runtime.mediaStream = null;
  elements.micStart.disabled = false;
  try {
    const mimeType = runtime.chunks[0]?.type || "audio/webm";
    const blob = new Blob(runtime.chunks, { type: mimeType });
    const formData = new FormData();
    formData.append("audio", blob, mimeType.includes("mp4") ? "oracle-mic.m4a" : "oracle-mic.webm");
    const sttResponse = await fetch("/api/voice/stt", {
      method: "POST",
      body: formData,
    });
    if (!sttResponse.ok) {
      throw new Error(await parseError(sttResponse));
    }
    const sttPayload = await sttResponse.json();
    const transcript = String(sttPayload.text || "").trim();
    if (!transcript) {
      throw new Error("Oracle returned an empty transcript.");
    }
    elements.text.value = transcript;
    elements.micStatus.textContent = `Transcript ready: ${transcript}`;
    await elements.form.requestSubmit();
  } catch (error) {
    elements.micStatus.textContent = error instanceof Error ? error.message : "Unable to transcribe microphone audio.";
  } finally {
    runtime.chunks = [];
  }
}

function renderOracleHistory(container) {
  container.innerHTML = "";
  if (state.recentCommands.length === 0) {
    container.innerHTML = '<p class="status">No page-session history yet.</p>';
    return;
  }
  for (const entry of state.recentCommands.slice(0, 8)) {
    const item = document.createElement("div");
    item.className = "history-item";
    item.innerHTML = `
      <div class="history-main">
        <div class="history-line"><strong>You:</strong> ${escapeHtml(entry.request.text || "")}</div>
        <div class="history-line"><strong>Oracle:</strong> ${escapeHtml(entry.response?.reply_text || "(empty)")}</div>
      </div>
      <div class="history-meta">${escapeHtml(formatTimestamp(entry.at))}</div>
    `;
    container.append(item);
  }
}

async function loadOracleHealth(container) {
  try {
    const [brain, stt, tts] = await Promise.all([
      fetchJson("/api/admin/health"),
      fetchJson("/api/admin/health/stt"),
      fetchJson("/api/admin/health/tts"),
    ]);
    const pills = [
      { label: "Brain", ok: brain.status === "ok" },
      { label: "STT", ok: stt.status !== "failed" },
      { label: "TTS", ok: tts.status !== "failed" },
    ];
    container.innerHTML = "";
    for (const pill of pills) {
      const node = document.createElement("span");
      node.className = `pill ${pill.ok ? "pill-ok" : "pill-fail"}`;
      node.textContent = `${pill.label} ${pill.ok ? "ready" : "down"}`;
      container.append(node);
    }
  } catch {
    container.innerHTML = '<span class="pill pill-fail">Health unavailable</span>';
  }
}

function initTracePage() {
  const elements = {
    form: document.querySelector("#trace-form"),
    text: document.querySelector("#trace-text"),
    sourceSelect: document.querySelector("#trace-source-select"),
    source: document.querySelector("#trace-source"),
    session: document.querySelector("#trace-session"),
    routeOnly: document.querySelector("#trace-route-only"),
    send: document.querySelector("#trace-send"),
    latestRaw: document.querySelector("#trace-latest-raw"),
    routePreview: document.querySelector("#trace-route-preview"),
    history: document.querySelector("#trace-history"),
    clearHistory: document.querySelector("#trace-clear-history"),
    showCurrent: document.querySelector("#trace-show-current"),
    summaryStatus: document.querySelector("#trace-summary-status"),
    summaryStrip: document.querySelector("#trace-summary"),
    sections: {
      request: document.querySelector("#trace-section-request"),
      route: document.querySelector("#trace-section-route"),
      dispatch: document.querySelector("#trace-section-dispatch"),
      result: document.querySelector("#trace-section-result"),
      reply: document.querySelector("#trace-section-reply"),
      session: document.querySelector("#trace-section-session"),
      playback: document.querySelector("#trace-section-playback"),
    },
  };

  elements.source.value = state.defaults.traceSource || "";
  elements.session.value = state.defaults.traceSession || "";
  void loadSourceOptions(elements.sourceSelect, elements.source, "traceSource");
  elements.sourceSelect.addEventListener("change", () => {
    syncTextFromSelect(elements.sourceSelect, elements.source);
    rememberDefaults("trace", elements.source.value, elements.session.value);
  });
  elements.source.addEventListener("input", () => syncSelectToText(elements.sourceSelect, elements.source));
  elements.source.addEventListener("change", () => rememberDefaults("trace", elements.source.value, elements.session.value));
  elements.session.addEventListener("change", () => rememberDefaults("trace", elements.source.value, elements.session.value));
  elements.clearHistory.addEventListener("click", () => {
    state.recentCommands = [];
    state.selectedCommandId = null;
    persistState();
    renderTraceHistory(elements.history);
    renderEmptyTrace(elements);
  });
  elements.showCurrent.addEventListener("click", async () => {
    const entry = getSelectedOrLatestCommand();
    if (entry) {
      await showTraceEntry(elements, entry.id);
    }
  });
  elements.routeOnly.addEventListener("click", async () => {
    const payload = buildTracePayload(elements);
    if (!payload.text) {
      elements.routePreview.textContent = "Command text is required.";
      return;
    }
    rememberDefaults("trace", elements.source.value, elements.session.value);
    setButtonBusy(elements.routeOnly, true, "Routing...");
    try {
      const route = await postJson("/api/voice/route", payload);
      elements.routePreview.textContent = JSON.stringify(route, null, 2);
    } catch (error) {
      elements.routePreview.textContent = error instanceof Error ? error.message : "Unknown error";
    } finally {
      setButtonBusy(elements.routeOnly, false, "Route Only");
    }
  });
  elements.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = buildTracePayload(elements);
    if (!payload.text) {
      elements.latestRaw.textContent = "Command text is required.";
      return;
    }
    rememberDefaults("trace", elements.source.value, elements.session.value);
    setButtonBusy(elements.send, true, "Executing...");
    try {
      const response = await postJson("/api/voice/command", payload);
      elements.latestRaw.textContent = JSON.stringify(response, null, 2);
      const entry = {
        id: buildLocalId(),
        at: new Date().toISOString(),
        request: payload,
        response,
        sessionSnapshot: null,
        playbackSnapshot: null,
      };
      addRecentCommand(entry);
      renderTraceHistory(elements.history);
      await showTraceEntry(elements, entry.id);
    } catch (error) {
      elements.latestRaw.textContent = error instanceof Error ? error.message : "Unknown error";
    } finally {
      setButtonBusy(elements.send, false, "Execute");
    }
  });

  renderTraceHistory(elements.history);
  const selected = getSelectedOrLatestCommand();
  if (selected) {
    void showTraceEntry(elements, selected.id);
  } else {
    renderEmptyTrace(elements);
  }
}

function buildTracePayload(elements) {
  return {
    text: elements.text.value.trim(),
    source: elements.source.value.trim() || null,
    playback_target_source_id: elements.source.value.trim() || null,
    session_id: elements.session.value.trim() || null,
  };
}

function renderTraceHistory(container) {
  container.innerHTML = "";
  if (state.recentCommands.length === 0) {
    container.innerHTML = '<p class="status">No recent commands.</p>';
    return;
  }
  for (const entry of state.recentCommands) {
    const buttonClass = entry.id === state.selectedCommandId ? "trace-history-button active" : "trace-history-button";
    const source = entry.request.source || "no-source";
    const status = entry.response?.dispatch?.status || "unknown";
    const node = document.createElement("button");
    node.type = "button";
    node.className = buttonClass;
    node.innerHTML = `
      <span class="trace-history-main">${escapeHtml(entry.request.text || "(empty)")}</span>
      <span class="trace-history-meta">${escapeHtml(source)} | ${escapeHtml(status)} | ${escapeHtml(formatTimestamp(entry.at))}</span>
    `;
    node.addEventListener("click", async () => {
      await showTraceEntry(
        {
          history: container,
          summaryStatus: document.querySelector("#trace-summary-status"),
          summaryStrip: document.querySelector("#trace-summary"),
          showCurrent: document.querySelector("#trace-show-current"),
          sections: {
            request: document.querySelector("#trace-section-request"),
            route: document.querySelector("#trace-section-route"),
            dispatch: document.querySelector("#trace-section-dispatch"),
            result: document.querySelector("#trace-section-result"),
            reply: document.querySelector("#trace-section-reply"),
            session: document.querySelector("#trace-section-session"),
            playback: document.querySelector("#trace-section-playback"),
          },
        },
        entry.id,
      );
    });
    container.append(node);
  }
  document.querySelector("#trace-show-current").disabled = state.recentCommands.length === 0;
}

async function showTraceEntry(elements, commandId) {
  const entry = state.recentCommands.find((item) => item.id === commandId);
  if (!entry) {
    renderEmptyTrace(elements);
    return;
  }
  state.selectedCommandId = commandId;
  persistState();
  renderTraceHistory(elements.history);
  elements.showCurrent.disabled = false;

  const source = entry.request.source || "";
  const sessionId = entry.request.session_id || "";
  elements.summaryStatus.textContent = `${entry.request.text} | ${source || "no-source"} | ${entry.response?.route?.target || "no-route"}`;

  await hydrateTraceEntry(entry);
  renderTraceSummary(elements.summaryStrip, entry);
  renderTraceSection(elements.sections.request, "Request", entry.request);
  renderTraceSection(elements.sections.route, "Route", entry.response?.route || {});
  renderTraceSection(elements.sections.dispatch, "Dispatch", entry.response?.dispatch || {});
  renderTraceSection(elements.sections.result, "Result", entry.response?.dispatch?.result || {});
  renderTraceSection(elements.sections.reply, "Reply", {
    reply_text: entry.response?.reply_text || "",
    session_id: entry.response?.session_id || sessionId,
    effective_session_id: entry.response?.effective_session_id || "",
  });
  renderTraceSection(elements.sections.session, "Session", entry.sessionSnapshot || { detail: "No session snapshot." });
  renderTraceSection(elements.sections.playback, "Playback", entry.playbackSnapshot || { detail: "No playback snapshot." });
}

async function hydrateTraceEntry(entry) {
  const source = entry.request?.source || null;
  const effectiveSessionId = entry.response?.effective_session_id || entry.request?.session_id || null;
  if (source && effectiveSessionId) {
    try {
      entry.sessionSnapshot = await fetchJson(
        `/api/voice/session?${new URLSearchParams({ source, session_id: effectiveSessionId }).toString()}`,
      );
    } catch (error) {
      entry.sessionSnapshot = { ok: false, detail: error instanceof Error ? error.message : "Unknown error" };
    }
  } else {
    entry.sessionSnapshot = { ok: false, detail: "This command did not produce a source + effective session pair." };
  }

  if (source) {
    try {
      entry.playbackSnapshot = await fetchJson(
        `/api/admin/playback-authority?${new URLSearchParams({ source }).toString()}`,
      );
    } catch (error) {
      entry.playbackSnapshot = { ok: false, detail: error instanceof Error ? error.message : "Unknown error" };
    }
  } else {
    entry.playbackSnapshot = { ok: false, detail: "No source supplied for playback lookup." };
  }
  persistState();
}

function renderTraceSummary(container, entry) {
  const items = [
    ["Request", entry.request?.text || ""],
    ["Source", entry.request?.source || "none"],
    ["Session", entry.request?.session_id || "none"],
    ["Effective Session", entry.response?.effective_session_id || "none"],
    ["Route", entry.response?.route?.target || "none"],
    ["Dispatch", entry.response?.dispatch?.hook || "none"],
    ["Status", entry.response?.dispatch?.status || "none"],
    ["Reply", entry.response?.reply_text || "(empty)"],
  ];
  container.innerHTML = items
    .map(
      ([label, value]) => `
        <div class="summary-card">
          <div class="summary-label">${escapeHtml(label)}</div>
          <div class="summary-value">${escapeHtml(value)}</div>
        </div>
      `,
    )
    .join("");
}

function renderTraceSection(container, title, payload) {
  const summaryRows = summarizePayload(payload);
  container.innerHTML = `
    <div class="trace-card-head">
      <h3>${escapeHtml(title)}</h3>
      <button class="ghost small-copy" type="button">Copy JSON</button>
    </div>
    <div class="key-grid">
      ${summaryRows
        .map(
          ([label, value]) => `
            <div class="key-item">
              <div class="key-label">${escapeHtml(label)}</div>
              <div class="key-value">${escapeHtml(value)}</div>
            </div>
          `,
        )
        .join("")}
    </div>
    <details class="details-box">
      <summary>Raw JSON</summary>
      <pre class="code-block">${escapeHtml(JSON.stringify(payload, null, 2))}</pre>
    </details>
  `;
  const copyButton = container.querySelector(".small-copy");
  copyButton.addEventListener("click", async () => {
    try {
      await copyText(JSON.stringify(payload, null, 2));
      copyButton.textContent = "Copied";
      setTimeout(() => {
        copyButton.textContent = "Copy JSON";
      }, 900);
    } catch {
      copyButton.textContent = "Copy Failed";
      setTimeout(() => {
        copyButton.textContent = "Copy JSON";
      }, 1200);
    }
  });
}

function summarizePayload(payload) {
  if (!payload || typeof payload !== "object") {
    return [["Value", String(payload ?? "")]];
  }
  const rows = [];
  for (const [key, value] of Object.entries(payload).slice(0, 10)) {
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
      rows.push([key, String(value)]);
    } else if (Array.isArray(value)) {
      rows.push([key, `${value.length} item(s)`]);
    } else if (value && typeof value === "object") {
      rows.push([key, `${Object.keys(value).length} field(s)`]);
    } else {
      rows.push([key, String(value)]);
    }
  }
  return rows.length > 0 ? rows : [["State", "No fields"]];
}

function renderEmptyTrace(elements) {
  elements.summaryStatus.textContent = "No command selected.";
  elements.summaryStrip.innerHTML = "";
  for (const section of Object.values(elements.sections)) {
    section.innerHTML = "<p class='status'>No data yet.</p>";
  }
  elements.showCurrent.disabled = true;
}

async function initLogsPage() {
  const elements = {
    target: document.querySelector("#logs-target"),
    lines: document.querySelector("#logs-lines"),
    refresh: document.querySelector("#logs-refresh"),
    output: document.querySelector("#logs-output"),
    status: document.querySelector("#logs-status"),
    detail: document.querySelector("#logs-target-detail"),
  };
  await loadLogTargets(elements);
  elements.refresh.addEventListener("click", async () => {
    await refreshLogs(elements);
  });
  elements.target.addEventListener("change", async () => {
    renderLogTargetDetail(elements);
    await refreshLogs(elements);
  });
  await refreshLogs(elements);
}

async function loadLogTargets(elements) {
  try {
    const payload = await fetchJson("/api/admin/log-targets");
    state.logTargets = Array.isArray(payload.targets) ? payload.targets : [];
    elements.target.innerHTML = "";
    for (const item of state.logTargets) {
      const option = document.createElement("option");
      option.value = item.target;
      option.textContent = item.available ? item.label : `${item.label} (unavailable)`;
      elements.target.append(option);
    }
    elements.status.textContent = "Recent tail view. Brain logs are available first; remote logs remain deferred.";
    renderLogTargetDetail(elements);
  } catch (error) {
    elements.status.textContent = error instanceof Error ? error.message : "Unable to load log targets.";
  }
}

function renderLogTargetDetail(elements) {
  const selected = state.logTargets.find((item) => item.target === elements.target.value);
  if (!selected) {
    elements.detail.textContent = "No log target selected.";
    return;
  }
  elements.detail.textContent = selected.detail;
}

async function refreshLogs(elements) {
  const target = elements.target.value || "brain";
  const lines = Math.max(20, Math.min(Number(elements.lines.value || 120), 400));
  setButtonBusy(elements.refresh, true, "Refreshing...");
  try {
    const payload = await fetchJson(`/api/admin/logs?${new URLSearchParams({ target, lines: String(lines) }).toString()}`);
    elements.output.textContent = reverseLogLines(payload.content || payload.detail || "No log output.");
  } catch (error) {
    elements.output.textContent = error instanceof Error ? error.message : "Unknown error";
  } finally {
    setButtonBusy(elements.refresh, false, "Refresh");
  }
}

function reverseLogLines(text) {
  const lines = String(text || "").split("\n");
  while (lines.length > 0 && lines[lines.length - 1] === "") {
    lines.pop();
  }
  return lines.reverse().join("\n");
}

function buildPageSessionId() {
  return `web-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
}
