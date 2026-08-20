const state = {
  currentPage: "overview",
  refreshTimers: new Map(),
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
  networkCommands: {
    openPanelId: "",
  },
  orchestration: {
    selectedId: "",
    preview: null,
  },
};

const elements = {
  rail: document.querySelector(".system-rail"),
  mobileNavToggle: document.querySelector("#admin-mobile-nav-toggle"),
  navButtons: Array.from(document.querySelectorAll("[data-admin-page]")),
  panels: Array.from(document.querySelectorAll("[data-admin-panel]")),
  title: document.querySelector("#admin-page-title"),
  feedback: document.querySelector("#system-feedback"),
  overviewRoot: document.querySelector("#overview-root"),
  overviewStatus: document.querySelector("#overview-status"),
  activityRoot: document.querySelector("#activity-root"),
  activityStatus: document.querySelector("#activity-status"),
  controlRoot: document.querySelector("#control-root"),
  controlStatus: document.querySelector("#control-status"),
  networkRoot: document.querySelector("#network-root"),
  networkStatus: document.querySelector("#network-status"),
  orchestrationRoot: document.querySelector("#orchestration-root"),
  orchestrationStatus: document.querySelector("#orchestration-status"),
  suggestionsRoot: document.querySelector("#suggestions-root"),
  suggestionsStatus: document.querySelector("#suggestions-status"),
  themeToggle: document.querySelector("#theme-toggle"),
  themeToggleIcon: document.querySelector("#theme-toggle-icon"),
  launcher: document.querySelector("#oracle-launcher"),
  launcherToggle: document.querySelector("#oracle-launcher-toggle"),
  launcherPanel: document.querySelector("#oracle-launcher-panel"),
  launcherClose: document.querySelector("#oracle-launcher-close"),
  launcherForm: document.querySelector("#oracle-launcher-form"),
  launcherInput: document.querySelector("#oracle-launcher-input"),
  launcherMic: document.querySelector("#oracle-launcher-mic"),
  launcherSend: document.querySelector("#oracle-launcher-send"),
  launcherStatus: document.querySelector("#oracle-launcher-status"),
  launcherReply: document.querySelector("#oracle-launcher-reply"),
};

const THEME_STORAGE_KEY = "oracle-ui-theme";
const LAUNCHER_SESSION_KEY = "oracle-admin-launcher-session";
const LAUNCHER_SOURCE = "browser-admin-ui";

const PAGE_TITLES = {
  overview: "Overview",
  activity: "Activity",
  control: "Control",
  network: "Network",
  orchestration: "Orchestration",
  suggestions: "Suggestions",
  trace: "Trace",
  logs: "Logs",
};

initialize();

function initialize() {
  initializeTheme();
  initializeLauncher();
  elements.mobileNavToggle?.addEventListener("click", toggleMobileNav);
  for (const button of elements.navButtons) {
    button.addEventListener("click", () => switchPage(button.dataset.adminPage || "overview"));
  }
  switchPage("overview");
}

function initializeTheme() {
  applyTheme(readStoredTheme());
  elements.themeToggle?.addEventListener("click", () => {
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
  if (elements.themeToggleIcon) {
    elements.themeToggleIcon.textContent = state.theme === "dark" ? "light_mode" : "dark_mode";
  }
  if (elements.themeToggle) {
    elements.themeToggle.setAttribute(
      "aria-label",
      state.theme === "dark" ? "Switch to light theme" : "Switch to dark theme",
    );
  }
}

function initializeLauncher() {
  if (!elements.launcher) {
    return;
  }
  state.launcher.sessionId = getOrCreateSessionId(LAUNCHER_SESSION_KEY);
  elements.launcherToggle?.addEventListener("click", () => setLauncherOpen(!state.launcher.open));
  elements.launcherClose?.addEventListener("click", () => setLauncherOpen(false));
  elements.launcherForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitLauncherPrompt();
  });
  elements.launcherMic?.addEventListener("click", async () => {
    await toggleLauncherMic();
  });
}

function getOrCreateSessionId(storageKey) {
  try {
    const existing = localStorage.getItem(storageKey);
    if (existing) {
      return existing;
    }
  } catch {
    // Ignore storage failures and generate an ephemeral id.
  }
  const created = globalThis.crypto?.randomUUID?.() || `oracle-${Date.now()}`;
  try {
    localStorage.setItem(storageKey, created);
  } catch {
    // Ignore storage failures and keep the in-memory id.
  }
  return created;
}

function setLauncherOpen(open) {
  state.launcher.open = open;
  elements.launcher?.classList.toggle("is-open", open);
  elements.launcherToggle?.setAttribute("aria-expanded", String(open));
}

function setLauncherStatus(message, tone = "info") {
  if (!elements.launcherStatus) {
    return;
  }
  elements.launcherStatus.textContent = message;
  elements.launcherStatus.classList.toggle("is-error", tone === "error");
}

async function submitLauncherPrompt(options = {}) {
  const text = String(elements.launcherInput?.value || "").trim();
  if (!text) {
    setLauncherStatus("Type something for Oracle first.", "error");
    return;
  }
  const playReplyAudio = Boolean(options.playReplyAudio);
  setLauncherOpen(true);
  setLauncherStatus("Oracle is thinking...");
  elements.launcherSend.disabled = true;
  try {
    const response = await fetch("/api/conversation/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        source: LAUNCHER_SOURCE,
        session_id: state.launcher.sessionId,
      }),
    });
    if (!response.ok) {
      throw new Error(await parseError(response));
    }
    const payload = await response.json();
    const replyText = String(payload.reply_text || "");
    elements.launcherReply.innerHTML = `
      <div><strong>You</strong><div>${escapeHtml(text)}</div></div>
      ${replyText ? `<div><strong>Oracle</strong><div>${escapeHtml(replyText)}</div></div>` : ""}
    `;
    elements.launcherReply.classList.remove("is-hidden");
    elements.launcherInput.value = "";
    if (playReplyAudio && replyText) {
      await playLauncherReply(replyText);
      setLauncherStatus("Oracle replied out loud.");
    } else {
      setLauncherStatus(replyText ? "Oracle replied." : "No reply required.");
    }
  } catch (error) {
    setLauncherStatus(error instanceof Error ? error.message : "Unable to reach Oracle.", "error");
  } finally {
    elements.launcherSend.disabled = false;
  }
}

async function playLauncherReply(text) {
  if (!text) {
    return;
  }
  const response = await fetch("/api/speech/tts", {
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
    elements.launcherMic.innerHTML = '<span class="material-symbols-outlined">stop</span><span>Stop</span>';
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
  elements.launcherMic.innerHTML = '<span class="material-symbols-outlined">mic</span><span>Mic</span>';
  try {
    const mimeType = state.launcher.chunks[0]?.type || "audio/webm";
    const blob = new Blob(state.launcher.chunks, { type: mimeType });
    const formData = new FormData();
    formData.append("audio", blob, mimeType.includes("mp4") ? "oracle-mic.m4a" : "oracle-mic.webm");
    const sttResponse = await fetch("/api/speech/stt", {
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
    elements.launcherInput.value = transcript;
    setLauncherStatus(`Heard: ${transcript}`);
    await submitLauncherPrompt({ playReplyAudio: true });
  } catch (error) {
    setLauncherStatus(error instanceof Error ? error.message : "Unable to transcribe microphone audio.", "error");
  } finally {
    state.launcher.chunks = [];
  }
}

function switchPage(page) {
  state.currentPage = page;
  elements.title.textContent = PAGE_TITLES[page] || "System";
  for (const button of elements.navButtons) {
    button.classList.toggle("is-active", button.dataset.adminPage === page);
  }
  for (const panel of elements.panels) {
    panel.classList.toggle("is-active", panel.dataset.adminPanel === page);
  }
  closeMobileNav();
  refreshCurrentPage();
}

function toggleMobileNav() {
  if (!elements.rail || !elements.mobileNavToggle) {
    return;
  }
  const isOpen = elements.rail.classList.toggle("is-open");
  elements.mobileNavToggle.setAttribute("aria-expanded", String(isOpen));
}

function closeMobileNav() {
  if (!elements.rail || !elements.mobileNavToggle) {
    return;
  }
  if (globalThis.innerWidth > 960) {
    return;
  }
  elements.rail.classList.remove("is-open");
  elements.mobileNavToggle.setAttribute("aria-expanded", "false");
}

function refreshCurrentPage() {
  if (state.currentPage === "overview") {
    void loadOverview();
    return;
  }
  if (state.currentPage === "activity") {
    void loadActivity();
    return;
  }
  if (state.currentPage === "control") {
    void loadControl();
    return;
  }
  if (state.currentPage === "network") {
    void loadNetwork();
    return;
  }
  if (state.currentPage === "orchestration") {
    void loadOrchestration();
    return;
  }
  if (state.currentPage === "suggestions") {
    void loadSuggestions();
    return;
  }
  if (state.currentPage === "trace" || state.currentPage === "logs") {
    showFeedback("");
  }
}

async function fetchJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  return response.json();
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

function scheduleRefresh(page, seconds) {
  const existing = state.refreshTimers.get(page);
  if (existing) {
    clearTimeout(existing);
  }
  if (!seconds || seconds <= 0) {
    return;
  }
  const timer = setTimeout(() => {
    if (state.currentPage === page) {
      refreshCurrentPage();
    }
  }, seconds * 1000);
  state.refreshTimers.set(page, timer);
}

function setStatus(element, text) {
  element.textContent = text;
}

function showFeedback(message, tone = "info") {
  if (!message) {
    elements.feedback.textContent = "";
    elements.feedback.classList.add("is-hidden");
    elements.feedback.classList.remove("is-error");
    return;
  }
  elements.feedback.textContent = message;
  elements.feedback.classList.remove("is-hidden");
  elements.feedback.classList.toggle("is-error", tone === "error");
}

function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function escapeAttribute(text) {
  return escapeHtml(text).replaceAll('"', "&quot;");
}

function formatTime(value) {
  if (!value) {
    return "";
  }
  try {
    return new Date(value).toLocaleString([], {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return String(value);
  }
}

function statusTone(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "ok" || normalized === "stable" || normalized === "active" || normalized === "available" || normalized === "healthy" || normalized === "completed" || normalized === "executed" || normalized === "skipped") {
    return "ok";
  }
  if (normalized === "failed" || normalized === "error" || normalized === "unavailable" || normalized === "down" || normalized === "interrupted" || normalized === "stopped" || normalized === "plan_changed") {
    return "danger";
  }
  if (normalized === "warning" || normalized === "degraded" || normalized === "partial" || normalized === "stale" || normalized === "completed_with_issues" || normalized === "running") {
    return "warn";
  }
  return "muted";
}

function renderEmpty(message, retryPage = "") {
  return `
    <div class="empty-state">
      <strong>${escapeHtml(message)}</strong>
      ${retryPage ? `<button class="retry-button" type="button" data-retry-page="${escapeHtml(retryPage)}">Try again</button>` : ""}
    </div>
  `;
}

function wireRetryButtons(root) {
  for (const button of root.querySelectorAll("[data-retry-page]")) {
    button.addEventListener("click", () => {
      const page = button.dataset.retryPage || state.currentPage;
      if (page === state.currentPage) {
        refreshCurrentPage();
        return;
      }
      switchPage(page);
    });
  }
}

async function loadOverview() {
  setStatus(elements.overviewStatus, "Loading overview.");
  try {
    const results = await Promise.allSettled([
      fetchJson("/api/admin/health"),
      fetchJson("/api/admin/health/home-assistant"),
      fetchJson("/api/admin/health/calendar"),
      fetchJson("/api/admin/health/music"),
      fetchJson("/api/admin/health/audiobook"),
      fetchJson("/api/admin/health/ollama"),
      fetchJson("/api/admin/health/news"),
      fetchJson("/api/admin/health/tts"),
      fetchJson("/api/admin/health/stt"),
      fetchJson("/api/admin/playback-authority"),
      fetchJson("/api/admin/sources"),
    ]);

    const [
      health,
      homeAssistant,
      calendar,
      music,
      audiobook,
      ollama,
      news,
      tts,
      stt,
      playback,
      sources,
    ] = results.map(resolveSettledValue);

    const serviceItems = [
      buildServiceItem("Home Assistant", homeAssistant),
      buildServiceItem("Calendar", calendar),
      buildServiceItem("Music", music),
      buildServiceItem("Audiobook", audiobook),
      buildServiceItem("Ollama", ollama),
      buildServiceItem("News", news),
      buildServiceItem("TTS", tts),
      buildServiceItem("STT", stt),
    ];
    const healthyCount = serviceItems.filter((item) => item.status === "ok").length;
    const degradedCount = serviceItems.length - healthyCount;
    const playbackSources = Array.isArray(playback?.sources) ? playback.sources : [];
    const sourceList = Array.isArray(sources?.sources) ? sources.sources : [];
    const playbackSummary = summarizePlaybackSources(playbackSources);
    const configuredPlaybackSources = sourceList.filter((item) => item.playback_capable).length;

    elements.overviewRoot.innerHTML = `
      <section class="hero-card">
        <div>
          <p class="system-kicker">System status</p>
          <p class="hero-temperature">${degradedCount === 0 ? "Stable" : "Degraded"}</p>
          <p class="small-copy">This is the System-mode front door. It summarizes Oracle-owned admin surfaces without exposing internal modules directly to the browser.</p>
        </div>
        <div class="hero-meta">
          <div class="row-card">
            <span class="metric-label">Service</span>
            <strong>${escapeHtml(health?.service || "oracle-brain")}</strong>
          </div>
          <div class="row-card">
            <span class="metric-label">Configured</span>
            <strong>${health?.home_assistant_configured ? "Home Assistant" : "No HA"} / ${health?.ollama_configured ? "Ollama" : "No Ollama"}</strong>
          </div>
          <div class="row-card">
            <span class="metric-label">Playback</span>
            <strong>${escapeHtml(playbackSummary)}</strong>
          </div>
        </div>
      </section>

      <section class="metric-grid">
        <article class="metric-block">
          <p class="metric-label">Healthy services</p>
          <p class="metric-value">${healthyCount}</p>
        </article>
        <article class="metric-block">
          <p class="metric-label">Degraded services</p>
          <p class="metric-value">${degradedCount}</p>
        </article>
        <article class="metric-block">
          <p class="metric-label">Playback sources</p>
          <p class="metric-value">${configuredPlaybackSources}</p>
        </article>
      </section>

      <section class="overview-grid">
        <article class="system-card">
          <div class="card-head">
            <div>
              <p class="system-kicker">Service health</p>
              <h4>Oracle-owned integrations</h4>
            </div>
          </div>
          <div class="service-list">
            ${serviceItems
              .map(
                (item) => `
                  <div class="service-row">
                    <div class="card-head">
                      <h5>${escapeHtml(item.label)}</h5>
                      <span class="status-pill status-pill--${statusTone(item.status)}">${escapeHtml(item.status)}</span>
                    </div>
                    <p class="small-copy">${escapeHtml(item.detail)}</p>
                  </div>
                `,
              )
              .join("")}
          </div>
        </article>

        <article class="system-card">
          <div class="card-head">
            <div>
              <p class="system-kicker">Media authority</p>
              <h4>Playback-capable sources</h4>
            </div>
          </div>
          <div class="service-list">
            ${
              sourceList.length > 0
                ? sourceList
                    .map((item) => {
                      const authority = playbackSources.find((entry) => entry.source === item.source) || null;
                      return `
                        <div class="source-card">
                          <div class="card-head">
                            <h5>${escapeHtml(item.source)}</h5>
                            <span class="status-pill status-pill--${statusTone(authority?.ok === false ? "failed" : item.playback_capable ? "ok" : "muted")}">${item.playback_capable ? "Playback" : "Read only"}</span>
                          </div>
                          <p class="small-copy">${escapeHtml(authority?.authority?.output_owner?.title || authority?.detail || "No active output owner.")}</p>
                        </div>
                      `;
                    })
                    .join("")
                : renderEmpty("No configured sources.")
            }
          </div>
        </article>
      </section>
    `;
    showFeedback("");
    setStatus(elements.overviewStatus, "Overview loaded.");
    scheduleRefresh("overview", 30);
  } catch (error) {
    elements.overviewRoot.innerHTML = renderEmpty(error instanceof Error ? error.message : "Overview unavailable.", "overview");
    wireRetryButtons(elements.overviewRoot);
    setStatus(elements.overviewStatus, "Overview unavailable.");
  }
}

async function loadActivity() {
  setStatus(elements.activityStatus, "Loading activity.");
  try {
    const params = new URLSearchParams({
      event_limit: "50",
      provider_limit: "50",
      source_limit: "50",
      satellite_limit: "50",
    });
    const payload = await fetchJson(`/api/admin/memory/diagnostics/summary?${params.toString()}`);
    const events = Array.isArray(payload?.events?.recent) ? payload.events.recent : [];
    const providers = Array.isArray(payload?.providers?.latest) ? payload.providers.latest : [];
    const sources = Array.isArray(payload?.sources?.items) ? payload.sources.items : [];
    const satellites = Array.isArray(payload?.satellites?.latest) ? payload.satellites.latest : [];
    elements.activityRoot.innerHTML = `
      ${renderActivitySnapshot(payload)}

      <section class="system-card">
        <div class="card-head">
          <div>
            <p class="system-kicker">Recent events</p>
            <h4>What happened</h4>
          </div>
          <span class="small-copy">${escapeHtml(formatActivityWindow(payload?.window))}</span>
        </div>
        ${renderActivityEvents(events)}
      </section>

      <section class="system-grid">
        ${renderActivityProviders(providers)}
        ${renderActivitySatellites(satellites)}
        ${renderActivitySources(sources)}
      </section>

      <div class="notice">Activity is built from structured operational records. Use <a href="./logs.html">Logs</a> when you need the raw journal tail.</div>
    `;
    showFeedback("");
    setStatus(elements.activityStatus, `Loaded ${events.length} events, ${providers.length} providers, ${satellites.length} satellites, and ${sources.length} sources.`);
    scheduleRefresh("activity", 20);
  } catch (error) {
    elements.activityRoot.innerHTML = renderEmpty(error instanceof Error ? error.message : "Activity unavailable.", "activity");
    wireRetryButtons(elements.activityRoot);
    setStatus(elements.activityStatus, "Activity unavailable.");
  }
}

function renderActivitySnapshot(payload) {
  const events = payload?.events || {};
  const providers = payload?.providers || {};
  const sources = payload?.sources || {};
  const satellites = payload?.satellites || {};
  const unavailable = Number(providers?.by_status?.unavailable || 0);
  const degraded = Number(providers?.by_status?.degraded || 0);
  const staleSatellites = Number(satellites?.stale_count || 0);
  return `
    <section class="system-card">
      <div class="card-head">
        <div>
          <p class="system-kicker">Activity snapshot</p>
          <h4>Current operational picture</h4>
        </div>
        <span class="small-copy">Generated ${escapeHtml(formatTime(payload?.generated_at) || "now")}</span>
      </div>
      <div class="metric-grid">
        <div class="metric-block">
          <p class="metric-label">Events</p>
          <p class="metric-value">${escapeHtml(events.total ?? 0)}</p>
          <p class="small-copy">Recent records</p>
        </div>
        <div class="metric-block">
          <p class="metric-label">Providers</p>
          <p class="metric-value">${escapeHtml(providers.total ?? 0)}</p>
          <p class="small-copy">${escapeHtml(unavailable + degraded)} need attention</p>
        </div>
        <div class="metric-block">
          <p class="metric-label">Sources</p>
          <p class="metric-value">${escapeHtml(sources.total ?? 0)}</p>
          <p class="small-copy">Registry records</p>
        </div>
        <div class="metric-block">
          <p class="metric-label">Satellites</p>
          <p class="metric-value">${escapeHtml(satellites.total ?? 0)}</p>
          <p class="small-copy">${escapeHtml(staleSatellites)} stale observations</p>
        </div>
      </div>
    </section>
  `;
}

function renderActivityEvents(events) {
  if (!events.length) {
    return renderEmpty("No structured activity records in the selected window.");
  }
  return `
    <div class="timeline-list">
      ${events.map(renderActivityEvent).join("")}
    </div>
  `;
}

function renderActivityEvent(event) {
  const title = activityEventTitle(event);
  const summary = activityEventSummary(event);
  const meta = [
    event.domain ? `Domain ${event.domain}` : "",
    event.provider ? `Provider ${event.provider}` : "",
    event.source_id ? `Source ${event.source_id}` : "",
    event.correlation_id ? `Correlation ${event.correlation_id}` : "",
  ].filter(Boolean);
  const tone = activityEventTone(event);
  return `
    <div class="timeline-item">
      <div class="card-head">
        <h4>${escapeHtml(title)}</h4>
        <span class="small-copy">${escapeHtml(formatTime(event.observed_at) || "Recent")}</span>
      </div>
      <p class="small-copy">${escapeHtml(meta.join(" / ") || event.category || "Operational record")}</p>
      ${summary ? `<p class="small-copy">${escapeHtml(summary)}</p>` : ""}
      <span class="status-pill status-pill--${statusTone(tone)}">${escapeHtml(event.status || event.severity || event.event_type || "event")}</span>
    </div>
  `;
}

function renderActivityProviders(providers) {
  return `
    <article class="system-card">
      <div class="card-head">
        <div>
          <p class="system-kicker">Provider status</p>
          <h4>Latest observations</h4>
        </div>
      </div>
      <div class="list-grid">
        ${
          providers.length
            ? providers.map(renderActivityProvider).join("")
            : renderEmpty("No provider status observations yet.")
        }
      </div>
    </article>
  `;
}

function renderActivityProvider(item) {
  const detail = item.payload?.detail_classification || item.payload?.model || item.domain || "Latest status";
  return `
    <div class="source-card">
      <div class="card-head">
        <h5>${escapeHtml(item.provider || item.domain || "Provider")}</h5>
        <span class="status-pill status-pill--${statusTone(item.status)}">${escapeHtml(item.status || "unknown")}</span>
      </div>
      <p class="small-copy">${escapeHtml(item.domain || "provider")} / ${escapeHtml(formatTime(item.observed_at) || "No timestamp")}</p>
      <p class="small-copy">${escapeHtml(detail)}</p>
    </div>
  `;
}

function renderActivitySatellites(satellites) {
  return `
    <article class="system-card">
      <div class="card-head">
        <div>
          <p class="system-kicker">Satellite status</p>
          <h4>Latest observations</h4>
        </div>
      </div>
      <div class="list-grid">
        ${
          satellites.length
            ? satellites.map(renderActivitySatellite).join("")
            : renderEmpty("No satellite status observations yet.")
        }
      </div>
    </article>
  `;
}

function renderActivitySatellite(item) {
  const payload = item.payload || {};
  const title = payload.display_name || item.display_name || item.source_id || item.provider || "Satellite";
  const lastSeen = payload.last_seen_at || item.observed_at;
  const lastWake = payload.last_wake_at || "";
  const lastError = payload.last_error || "";
  return `
    <div class="source-card">
      <div class="card-head">
        <h5>${escapeHtml(title)}</h5>
        <span class="status-pill status-pill--${statusTone(item.status)}">${escapeHtml(item.status || "unknown")}</span>
      </div>
      <p class="small-copy">${escapeHtml(item.source_id || item.provider || "unknown satellite")}</p>
      <p class="small-copy">Last seen ${escapeHtml(formatTime(lastSeen) || "No timestamp")}</p>
      ${lastWake ? `<p class="small-copy">Last wake ${escapeHtml(formatTime(lastWake) || lastWake)}</p>` : ""}
      ${lastError ? `<p class="small-copy">Last error ${escapeHtml(lastError)}</p>` : ""}
      ${item.is_stale ? `<span class="status-pill status-pill--warn">Stale observation</span>` : ""}
    </div>
  `;
}

function renderActivitySources(sources) {
  return `
    <article class="system-card">
      <div class="card-head">
        <div>
          <p class="system-kicker">Sources</p>
          <h4>Registry records</h4>
        </div>
      </div>
      <div class="list-grid">
        ${
          sources.length
            ? sources.map(renderActivitySource).join("")
            : renderEmpty("No source registry records yet.")
        }
      </div>
    </article>
  `;
}

function renderActivitySource(item) {
  return `
    <div class="source-card">
      <div class="card-head">
        <h5>${escapeHtml(item.display_name || item.source_id || "Source")}</h5>
        <span class="status-pill status-pill--${statusTone(item.status)}">Record: ${escapeHtml(item.status || "unknown")}</span>
      </div>
      <p class="small-copy">${escapeHtml(item.source_id || "unknown")} / ${escapeHtml(item.source_type || "source")}</p>
    </div>
  `;
}

function activityEventTitle(event) {
  const type = String(event?.event_type || "event");
  const payload = event?.payload || {};
  if (type === "network_control_dry_run") {
    return `Network command checked: ${formatActivityTarget(payload)}`;
  }
  if (type === "network_control_confirm") {
    return `Network command result: ${formatActivityTarget(payload)}`;
  }
  if (type === "network_control_started") {
    return `Network command started: ${formatActivityTarget(payload)}`;
  }
  if (type === "orchestration_recovery_started") {
    return `Recovery started: ${payload.orchestration_id || "runbook"}`;
  }
  if (type === "orchestration_recovery_completed") {
    return `Recovery result: ${payload.orchestration_id || "runbook"}`;
  }
  if (type === "orchestration_recovery_interrupted") {
    return `Recovery interrupted: ${payload.orchestration_id || "runbook"}`;
  }
  if (type === "orchestration_routine_started") {
    return `Routine started: ${payload.orchestration_id || "routine"}`;
  }
  if (type === "orchestration_routine_waiting") {
    return `Routine waiting: ${payload.step_id || payload.orchestration_id || "routine"}`;
  }
  if (type === "orchestration_routine_resumed") {
    return `Routine resumed: ${payload.orchestration_id || "routine"}`;
  }
  if (type === "orchestration_routine_completed") {
    return `Routine result: ${payload.orchestration_id || "routine"}`;
  }
  if (type === "orchestration_routine_canceled") {
    return `Routine canceled: ${payload.orchestration_id || "routine"}`;
  }
  if (type === "orchestration_routine_interrupted") {
    return `Routine interrupted: ${payload.orchestration_id || "routine"}`;
  }
  const known = {
    server_started: "Brain service started",
    server_stopped: "Brain service stopped",
    application_startup_complete: "Application startup complete",
    application_shutdown_complete: "Application shutdown complete",
    config_warning: "Configuration warning",
    deprecated_config_source: "Deprecated configuration source",
    missing_required_config: "Missing required configuration",
    provider_available: "Provider available",
    provider_unavailable: "Provider unavailable",
    provider_degraded: "Provider degraded",
    provider_recovered: "Provider recovered",
  };
  return known[type] || type.replaceAll("_", " ");
}

function activityEventSummary(event) {
  const payload = event?.payload || {};
  if (
    event?.event_type === "network_control_dry_run"
    || event?.event_type === "network_control_confirm"
    || event?.event_type === "network_control_started"
  ) {
    const parts = [
      payload.action_id ? `Action ${payload.action_id}` : "",
      payload.result_status ? `Result ${payload.result_status}` : "",
      payload.policy_status ? `Policy ${payload.policy_status}` : "",
      payload.confirmation_status ? `Confirmation ${payload.confirmation_status}` : "",
      payload?.execution?.verification_status ? `Verification ${payload.execution.verification_status}` : "",
      payload?.execution?.readiness_status ? `Readiness ${payload.execution.readiness_status}` : "",
      payload?.execution?.availability_status ? `Control ${payload.execution.availability_status}` : "",
      payload?.execution?.cooldown_seconds ? `Cooldown ${payload.execution.cooldown_seconds}s` : "",
    ].filter(Boolean);
    const summary = payload.summary ? String(payload.summary) : "";
    return [summary, parts.join(" / ")].filter(Boolean).join(" ");
  }
  if (String(event?.event_type || "").startsWith("orchestration_")) {
    const result = payload.result || {};
    return String(result.summary || payload.summary || `${payload.approved_step_count || 0} approved steps`);
  }
  return "";
}

function formatActivityTarget(payload) {
  const targetType = String(payload?.target_type || "").trim();
  const targetId = String(payload?.target_id || "").trim();
  if (targetType && targetId) {
    return `${targetType}:${targetId}`;
  }
  return "network target";
}

function activityEventTone(event) {
  const status = String(event?.status || "").toLowerCase();
  const severity = String(event?.severity || "").toLowerCase();
  if (status) {
    return status;
  }
  return severity || "muted";
}

function formatActivityWindow(window) {
  if (!window?.observed_after && !window?.observed_before) {
    return "Recent structured records";
  }
  const start = formatTime(window.observed_after) || "beginning";
  const end = formatTime(window.observed_before) || "now";
  return `${start} to ${end}`;
}

async function loadControl() {
  setStatus(elements.controlStatus, "Loading control surface.");
  try {
    const [sources, playback, homeAssistant, stt, tts, music, audiobook, calendar, ollama, news] = await Promise.all([
      fetchJson("/api/admin/sources"),
      fetchJson("/api/admin/playback-authority"),
      fetchJson("/api/admin/health/home-assistant"),
      fetchJson("/api/admin/health/stt"),
      fetchJson("/api/admin/health/tts"),
      fetchJson("/api/admin/health/music"),
      fetchJson("/api/admin/health/audiobook"),
      fetchJson("/api/admin/health/calendar"),
      fetchJson("/api/admin/health/ollama"),
      fetchJson("/api/admin/health/news"),
    ]);
    const sourceList = Array.isArray(sources.sources) ? sources.sources : [];
    const playbackSources = Array.isArray(playback.sources) ? playback.sources : [];
    const dependencyItems = [
      buildServiceItem("Brain / routing", { status: "ok", detail: "Core brain service is responding through the admin surface." }),
      buildServiceItem("Speech to text", stt),
      buildServiceItem("Text to speech", tts),
      buildServiceItem("Home Assistant", homeAssistant),
      buildServiceItem("Music routing", music),
      buildServiceItem("Audiobookshelf", audiobook),
      buildServiceItem("Calendar", calendar),
      buildServiceItem("Ollama", ollama),
      buildServiceItem("News", news),
      {
        label: "Weather",
        status: "unknown",
        detail: "Weather is available to Oracle, but a dedicated admin health contract is not exposed yet.",
      },
    ];
    elements.controlRoot.innerHTML = `
      <section class="control-grid">
        <article class="system-card">
          <div class="card-head">
            <div>
              <p class="system-kicker">Environment</p>
              <h4>Control and overrides</h4>
            </div>
            <span class="status-pill status-pill--${statusTone(homeAssistant.status)}">${escapeHtml(homeAssistant.status || "unknown")}</span>
          </div>
          <p class="small-copy">Admin write controls are intentionally not exposed yet. This prototype keeps the System page present without bypassing Oracle’s still-curated admin contract.</p>
          <div class="notice">TODO: add explicit /api/admin write endpoints before wiring real override actions here.</div>
        </article>

        <article class="system-card">
          <div class="card-head">
            <div>
              <p class="system-kicker">Playback sources</p>
              <h4>Available targets</h4>
            </div>
          </div>
          <div class="service-list">
            ${
              sourceList.length > 0
                ? sourceList
                    .map((item) => `
                      <div class="source-card">
                        <div class="card-head">
                          <h5>${escapeHtml(item.source)}</h5>
                          <span class="status-pill status-pill--${item.playback_capable ? "ok" : "muted"}">${item.playback_capable ? "Playback-capable" : "Not playback-capable"}</span>
                        </div>
                        <p class="small-copy">Native music: ${item.supports_oracle_native_music ? "yes" : "no"} / Plexamp: ${item.supports_plexamp ? "yes" : "no"}</p>
                      </div>
                    `)
                    .join("")
                : renderEmpty("No admin sources available.")
            }
          </div>
        </article>
      </section>

      <section class="system-card">
        <div class="card-head">
          <div>
            <p class="system-kicker">Dependency health</p>
            <h4>Available control surfaces</h4>
          </div>
        </div>
        <div class="service-list">
          ${dependencyItems
            .map(
              (item) => `
                <div class="service-row">
                  <div class="card-head">
                    <h5>${escapeHtml(item.label)}</h5>
                    <span class="status-pill status-pill--${statusTone(item.status)}">${escapeHtml(item.status)}</span>
                  </div>
                  <p class="small-copy">${escapeHtml(item.detail)}</p>
                </div>
              `,
            )
            .join("")}
        </div>
      </section>
    `;
    showFeedback("");
    setStatus(elements.controlStatus, "Control surface loaded.");
    scheduleRefresh("control", 30);
  } catch (error) {
    elements.controlRoot.innerHTML = renderEmpty(error instanceof Error ? error.message : "Control surface unavailable.", "control");
    wireRetryButtons(elements.controlRoot);
    setStatus(elements.controlStatus, "Control surface unavailable.");
  }
}

async function loadNetwork() {
  setStatus(elements.networkStatus, "Loading network status.");
  try {
    const [payload, controlPayload] = await Promise.all([
      fetchJson("/api/admin/network/status"),
      fetchJson("/api/admin/network/control/actions"),
    ]);
    const network = payload?.network || {};
    const controlDiagnostics = controlPayload?.diagnostics || {};
    const hosts = Array.isArray(network.hosts) ? network.hosts : [];
    const dependencies = Array.isArray(network.dependencies) ? network.dependencies : [];
    const monitors = Array.isArray(network.monitors) ? network.monitors : [];
    const evidence = Array.isArray(network.evidence) ? network.evidence : [];
    const ungroupedServices = Array.isArray(network.ungrouped_services) ? network.ungrouped_services : [];

    const hostById = new Map(hosts.map((host) => [String(host.id || ""), host]));
    const context = { monitors, evidence, dependencies, hostById };
    elements.networkRoot.innerHTML = `
      ${renderNetworkSnapshot(network)}
      ${renderNetworkCoverageCard(network.coverage)}
      ${renderNetworkControlCoverage(controlDiagnostics)}
      ${ungroupedServices.length ? `
        <section class="system-card">
          <div class="card-head">
            <div>
              <p class="system-kicker">Services without hosts</p>
              <h4>Unattached services</h4>
            </div>
          </div>
          <div class="service-list">
            ${ungroupedServices.map((service) => renderNetworkService(service, context)).join("")}
          </div>
        </section>
      ` : ""}

      <section class="system-grid">
        ${renderNetworkDependencyCard(dependencies, monitors)}
        ${renderNetworkHostCategoryCard("Infrastructure", filterNetworkHosts(hosts, "infrastructure"), context)}
      </section>

      <section class="system-grid">
        ${renderNetworkHostCategoryCard("Satellites", filterNetworkHosts(hosts, "satellite"), context)}
        ${renderNetworkHostCategoryCard("Servers", filterNetworkHosts(hosts, "server"), context)}
      </section>

      ${renderNetworkProviderDiagnostics(network.provider_diagnostics)}

      <div class="notice">Network is read-only in this stage. Oracle interprets inventory and status; providers only supply evidence.</div>
    `;
    wireNetworkCommands(elements.networkRoot);
    showFeedback("");
    setStatus(elements.networkStatus, `Loaded ${hosts.length} hosts, ${monitors.length} monitors, and ${evidence.length} evidence records.`);
    scheduleRefresh("network", 30);
  } catch (error) {
    elements.networkRoot.innerHTML = renderEmpty(error instanceof Error ? error.message : "Network status unavailable.", "network");
    wireRetryButtons(elements.networkRoot);
    setStatus(elements.networkStatus, "Network status unavailable.");
  }
}

function renderNetworkSnapshot(network) {
  return `
    <section class="hero-card network-hero">
      <div>
        <p class="system-kicker">Network status</p>
        <p class="hero-temperature">${escapeHtml(statusLabel(network.status || "unknown"))}</p>
        <p class="small-copy">${escapeHtml(network.summary || "Oracle has not generated a network summary yet.")}</p>
      </div>
      <div class="hero-meta">
        <div class="row-card">
          <span class="metric-label">Status</span>
          <strong>${escapeHtml(network.status || "unknown")}</strong>
        </div>
        <div class="row-card">
          <span class="metric-label">Freshness</span>
          <strong>${escapeHtml(network.freshness || "unknown")}</strong>
        </div>
        <div class="row-card">
          <span class="metric-label">Generated</span>
          <strong>${escapeHtml(formatTime(network.generated_at) || "No timestamp")}</strong>
        </div>
        <div class="row-card">
          <span class="metric-label">Cache</span>
          <strong>${escapeHtml(formatNetworkCache(network))}</strong>
        </div>
      </div>
    </section>
  `;
}

function renderNetworkCoverageCard(coverage) {
  const hosts = coverage?.hosts || {};
  const services = coverage?.services || {};
  const monitorCoverage = coverage?.monitors || {};
  return `
    <section class="system-card">
      <div class="card-head">
        <div>
          <p class="system-kicker">Inventory Coverage</p>
          <h4>Monitoring Coverage</h4>
        </div>
      </div>
      <section class="metric-grid">
        ${renderCoverageMetric("Hosts monitored", hosts.monitored, hosts.total)}
        ${renderCoverageMetric("Services monitored", services.monitored, services.total)}
        ${renderCoverageMetric("Monitors with evidence", monitorCoverage.with_evidence, monitorCoverage.total)}
      </section>
    </section>
  `;
}

function renderNetworkControlCoverage(diagnostics) {
  const summary = diagnostics?.summary || {};
  const actions = Array.isArray(diagnostics?.actions) ? diagnostics.actions : [];
  const remaining = actions.filter((action) => action?.status === "enabled_unverified");
  return `
    <section class="system-card">
      <div class="card-head">
        <div>
          <p class="system-kicker">Stage 4 coverage</p>
          <h4>Control Verification</h4>
        </div>
        <span class="status-pill status-pill--${statusTone(summary.all_verified ? "ok" : "warn")}">${summary.all_verified ? "Complete" : `${remaining.length} remaining`}</span>
      </div>
      <section class="metric-grid">
        ${renderCoverageMetric("Verified", summary.verified, summary.total)}
        ${renderCoverageMetric("Enabled, unverified", summary.enabled_unverified, summary.total)}
        ${renderCoverageMetric("Disabled", summary.disabled, summary.total)}
        ${renderCoverageMetric("Misconfigured", summary.misconfigured, summary.total)}
      </section>
      <details class="network-provider-diagnostics">
        <summary>All control actions</summary>
        <div class="service-list">
          ${actions.length ? actions.map(renderNetworkControlCoverageAction).join("") : renderEmpty("No control actions are configured.")}
        </div>
      </details>
    </section>
  `;
}

function renderNetworkControlCoverageAction(action) {
  const target = action?.target || {};
  const status = String(action?.status || "misconfigured");
  const verifiedAt = action?.verification?.verified_at ? formatTime(action.verification.verified_at) : "";
  const issue = Array.isArray(action?.issues) ? action.issues.find((item) => item?.severity === "error") : null;
  const detail = issue?.summary
    || (status === "verified"
      ? `Verified${verifiedAt ? ` ${verifiedAt}` : ""}.`
      : status === "enabled_unverified"
        ? "Enabled and configured, but no durable successful verification exists."
        : status === "disabled"
          ? "Configured but disabled."
          : "Control configuration requires attention.");
  return `
    <div class="service-row">
      <div class="card-head">
        <div>
          <h5>${escapeHtml(target.display_name || action.target_id || "Control target")}</h5>
          <p class="small-copy">${escapeHtml(action.action_id || "action")} / ${escapeHtml(action.target_type || "target")}</p>
        </div>
        <span class="status-pill status-pill--${statusTone(networkControlCoverageTone(status))}">${escapeHtml(formatNetworkControlResultLabel(status))}</span>
      </div>
      <p class="small-copy">${escapeHtml(detail)}</p>
    </div>
  `;
}

function networkControlCoverageTone(status) {
  if (status === "verified") {
    return "ok";
  }
  if (status === "misconfigured") {
    return "danger";
  }
  if (status === "enabled_unverified") {
    return "warn";
  }
  return "muted";
}

function renderNetworkProviderDiagnostics(diagnostics) {
  const services = diagnostics?.librenms_services || {};
  const unmatched = (services.items || []).filter((item) => !(item.matched_monitor_ids || []).length);
  const visibleUnmatched = unmatched.slice(0, 10);
  return `
    <details class="system-card network-provider-diagnostics">
      <summary>
        <div>
          <p class="system-kicker">Provider Observations</p>
          <h4>LibreNMS Services</h4>
        </div>
        <span class="status-pill status-pill--muted">${escapeHtml(`${services.matched || 0}/${services.total || 0} matched`)}</span>
      </summary>
      <div class="list-grid">
        ${
          visibleUnmatched.length
            ? visibleUnmatched.map(renderLibrenmsServiceDiagnosticRow).join("")
            : `<p class="small-copy">All visible LibreNMS services are matched to declared Oracle monitors.</p>`
        }
        ${unmatched.length > visibleUnmatched.length ? `<p class="small-copy">And ${escapeHtml(String(unmatched.length - visibleUnmatched.length))} more unmatched provider services.</p>` : ""}
      </div>
    </details>
  `;
}

function renderLibrenmsServiceDiagnosticRow(item) {
  const label = item.service_name || item.service_desc || item.service_id || "LibreNMS service";
  const detail = [item.service_ip, item.service_desc, item.service_type].filter(Boolean).join(" / ");
  return `
    <div class="source-card network-compact-row">
      <div class="card-head">
        <h5>${escapeHtml(label)}</h5>
        <span class="status-pill status-pill--${statusTone(item.status)}">${escapeHtml(statusLabel(item.status || "unknown"))}</span>
      </div>
      <p class="small-copy">${escapeHtml(detail || "No provider detail.")}</p>
    </div>
  `;
}

function renderCoverageMetric(label, value, total) {
  const safeValue = Number(value || 0);
  const safeTotal = Number(total || 0);
  return `
    <article class="metric-block">
      <p class="metric-label">${escapeHtml(label)}</p>
      <p class="metric-value">${escapeHtml(`${safeValue}/${safeTotal}`)}</p>
    </article>
  `;
}

function renderNetworkHost(host, context) {
  const services = networkHostServices(host);
  const details = renderNetworkTargetDetails("host", host, context);
  const commandPanel = renderNetworkCommandLauncher("host", host);
  return `
    <article class="system-card network-host-card">
      <div class="card-head">
        <div>
          <p class="system-kicker">${escapeHtml(formatNetworkLabel(host.role || host.id || "host"))}</p>
          <h4>${escapeHtml(host.display_name || host.id || "Host")}</h4>
        </div>
        <div class="network-row-actions">
          <span class="status-pill status-pill--${statusTone(host.status)}">${escapeHtml(host.status || "unknown")}</span>
          ${commandPanel}
        </div>
      </div>
      <p class="small-copy">${escapeHtml(host.summary || "Status is unknown.")}</p>
      ${host.address_label ? `<p class="small-copy">Address ${escapeHtml(host.address_label)}</p>` : ""}
      <div class="network-service-stack">
        ${services.length ? renderNetworkServiceTable(services, context) : ""}
      </div>
      ${details}
    </article>
  `;
}

function renderNetworkServiceTable(services, context) {
  return `
    <div class="network-service-table">
      <div class="network-service-table__head">
        <span>Service</span>
        <span>Status</span>
        <span>Commands</span>
      </div>
      ${services.map((service) => renderNetworkService(service, context)).join("")}
    </div>
  `;
}

function renderNetworkService(service, context) {
  const presentation = networkStatusPresentation("service", service, context);
  const detail = networkServiceDetail(service, context);
  const lastControl = renderNetworkLastControlResult(service.last_control_result);
  return `
    <details class="network-service-row">
      <summary>
        <span>
          <strong>${escapeHtml(service.display_name || service.id || "Service")}</strong>
          <small>${escapeHtml(service.kind || service.id || "service")}</small>
        </span>
        <span class="network-service-status">
          <span class="status-pill status-pill--${presentation.tone}">${escapeHtml(presentation.label)}</span>
        </span>
        <span class="network-service-command-cell">${renderNetworkCommandLauncher("service", service)}</span>
      </summary>
      <div class="network-service-row__body">
        <p class="small-copy">${escapeHtml(`${service.monitor_count || 0} monitors / ${service.evidence_count || 0} evidence records`)}</p>
        <p class="small-copy">${escapeHtml(detail)}</p>
        <p class="small-copy">${escapeHtml(presentation.summary)}</p>
        ${lastControl}
        ${renderNetworkTargetDetails("service", service, context, { open: true })}
      </div>
    </details>
  `;
}

function renderNetworkCommandLauncher(targetType, item) {
  const commands = networkCommandSpecs(targetType, item);
  if (!commands.length) {
    return "";
  }
  const targetId = String(item.id || "");
  const panelId = `network-command-${targetType}-${targetId}`.replace(/[^a-zA-Z0-9_-]/g, "-");
  const lastControl = renderNetworkLastControlResult(item.last_control_result, { compact: true });
  const readyCount = commands.filter((command) => command.availabilityStatus === "ready").length;
  const launcherLabel = readyCount ? "Run" : "Wait";
  return `
    <span class="network-command-shell" data-network-command-shell="${escapeAttribute(panelId)}">
      <button
        class="network-command-button"
        type="button"
        data-network-command-toggle="${escapeAttribute(panelId)}"
        data-target-type="${escapeAttribute(targetType)}"
        data-target-id="${escapeAttribute(targetId)}"
        aria-expanded="false"
      >
        ${escapeHtml(launcherLabel)}
      </button>
      ${lastControl}
      <span class="network-command-popover" id="${escapeAttribute(panelId)}" hidden>
        <span class="network-command-popover__head">
          <strong>${escapeHtml(item.display_name || item.id || "Target")}</strong>
          <span class="status-pill status-pill--muted" data-network-command-status="${escapeAttribute(panelId)}">${escapeHtml(readyCount ? "Ready" : "Unavailable")}</span>
          <button class="network-command-close" type="button" data-network-command-close="${escapeAttribute(panelId)}" aria-label="Close commands">
            <span class="material-symbols-outlined">close</span>
          </button>
        </span>
        <span class="network-command-list">
          ${commands.map((command) => renderNetworkCommandButton(command.targetType, command.targetId, command)).join("")}
        </span>
        <span class="network-command-log" data-network-command-log="${escapeAttribute(panelId)}">
          <span class="network-command-log__line">Select a command to preview Oracle's checks.</span>
        </span>
        <span class="network-command-confirm" data-network-command-confirm="${escapeAttribute(panelId)}" hidden>
          <button class="admin-button" type="button" data-network-command-confirm-action="true" disabled>Confirm</button>
          <button class="admin-button--secondary" type="button" data-network-command-close="${escapeAttribute(panelId)}">Cancel</button>
        </span>
      </span>
    </span>
  `;
}

function renderNetworkLastControlResult(result, options = {}) {
  if (!result || typeof result !== "object") {
    return "";
  }
  const status = String(result.result_status || result.policy_status || "unknown");
  const tone = networkControlResultTone(status);
  const label = formatNetworkControlResultLabel(status);
  const when = formatTime(result.recorded_at || result.requested_at);
  const summary = String(result.summary || "").trim();
  const text = [label, when].filter(Boolean).join(" · ");
  if (options.compact) {
    return `<span class="network-last-control network-last-control--compact"><span class="status-pill status-pill--${statusTone(tone)}">${escapeHtml(label)}</span>${when ? `<small>${escapeHtml(when)}</small>` : ""}</span>`;
  }
  return `
    <p class="network-last-control">
      <span class="status-pill status-pill--${statusTone(tone)}">${escapeHtml(label)}</span>
      <span>${escapeHtml(text || label)}</span>
      ${summary ? `<small>${escapeHtml(summary)}</small>` : ""}
    </p>
  `;
}

function networkControlResultTone(status) {
  if (status === "executed") {
    return "ok";
  }
  if (status === "failed") {
    return "danger";
  }
  if (
    status === "blocked"
    || status === "denied"
    || status === "interrupted"
    || status === "not_executed"
    || status === "not_implemented"
  ) {
    return "warn";
  }
  return "muted";
}

function formatNetworkControlResultLabel(status) {
  const normalized = String(status || "unknown").replaceAll("_", " ");
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function renderNetworkCommandButton(targetType, targetId, command) {
  const unavailable = command.availabilityStatus !== "ready";
  return `
    <button
      class="network-command-option"
      type="button"
      data-network-command-run="true"
      data-target-type="${escapeAttribute(targetType)}"
      data-target-id="${escapeAttribute(targetId)}"
      data-action-id="${escapeAttribute(command.actionId)}"
      data-command-label="${escapeAttribute(command.label)}"
      data-command-impact="${escapeAttribute(command.impact)}"
      ${unavailable ? "disabled" : ""}
    >
      <span class="material-symbols-outlined">${escapeHtml(command.icon)}</span>
      <span>
        <strong>${escapeHtml(command.label)}</strong>
        <small>${escapeHtml(unavailable ? command.availabilityLabel : command.description)}</small>
      </span>
    </button>
  `;
}

function networkCommandSpecs(targetType, item) {
  const actions = Array.isArray(item?.control_actions) ? item.control_actions : [];
  return actions
    .map((action) => networkCommandSpecFromAction(targetType, item, action))
    .filter(Boolean);
}

function networkCommandSpecFromAction(targetType, item, action) {
  const actionId = String(action?.action_id || "");
  const specs = {
    restart_service: {
      label: "Restart",
      description: "service",
    },
    restart_runtime: {
      label: "Restart runtime",
      description: "satellite runtime",
    },
    restart_ui: {
      label: "Restart UI",
      description: "satellite UI",
    },
    restart_router: {
      label: "Restart router",
      description: "router",
    },
    restart_host: {
      label: "Restart host",
      description: "host",
    },
    power_cycle: {
      label: "Power cycle",
      description: "device power",
    },
  };
  const spec = specs[actionId];
  if (!spec) {
    return null;
  }
  const targetName = String(item?.display_name || item?.id || "service");
  const enabled = action?.enabled === true;
  const availability = action?.availability && typeof action.availability === "object"
    ? action.availability
    : { status: "ready" };
  const availabilityStatus = enabled ? String(availability.status || "ready") : "disabled";
  return {
    targetType: String(action?.target_type || targetType),
    targetId: String(action?.target_id || item?.id || ""),
    actionId,
    label: spec.label,
    icon: actionId === "power_cycle" ? "power_settings_new" : "restart_alt",
    description: enabled
      ? `Preview ${spec.description} restart policy for ${targetName}.`
      : `${spec.label} policy for ${targetName} is configured but disabled.`,
    impact: networkCommandImpact(targetType, item, action),
    availabilityStatus,
    availabilityLabel: networkCommandAvailabilityLabel(availabilityStatus, availability),
  };
}

function networkCommandAvailabilityLabel(status, availability) {
  if (status === "in_progress") {
    return "This command is currently running.";
  }
  if (status === "blocked_by_active") {
    const target = availability?.active_target_id ? ` for ${availability.active_target_id}` : "";
    return `Another network command${target} is running.`;
  }
  if (status === "cooldown") {
    const remaining = Number(availability?.cooldown_remaining_seconds || 0);
    return `Available after the post-action cooldown, about ${remaining} second(s).`;
  }
  if (status === "disabled") {
    return "This command policy is disabled.";
  }
  return "Ready.";
}

function networkCommandImpact(targetType, item, action) {
  const actionId = String(action?.action_id || "");
  const targetName = String(item?.display_name || item?.id || "target");
  if (targetType === "service" && String(item?.id || "") === "plex") {
    return "This would temporarily stop access to Plex while the service restarts.";
  }
  if (targetType === "service") {
    return `This would temporarily stop access to ${targetName} while the service restarts.`;
  }
  if (targetType === "host" && actionId === "restart_runtime") {
    return `This would temporarily stop voice handling on ${targetName} while its satellite runtime restarts.`;
  }
  if (targetType === "host" && actionId === "restart_ui") {
    return `This would temporarily close the Oracle display on ${targetName} while its UI restarts.`;
  }
  if (targetType === "host" && actionId === "restart_router") {
    return `This will interrupt household network access while ${targetName} restarts, then wait for it to come back online.`;
  }
  if (targetType === "host" && actionId === "restart_host") {
    const preconditions = Array.isArray(action?.required_preconditions)
      ? action.required_preconditions.map((value) => String(value || ""))
      : [];
    if (preconditions.includes("host_storage_safe_for_restart")) {
      return `This will stop all services on ${targetName}, verify its configured storage is safe to interrupt, restart the machine, and wait for it to return online.`;
    }
    return `This will stop all services on ${targetName}, restart the machine, and wait for it to return online.`;
  }
  if (actionId === "power_cycle") {
    return `This will disconnect ${targetName} from power for about 10 seconds, restore power, and wait for the device to come back online.`;
  }
  return `This would temporarily affect ${targetName}.`;
}

function wireNetworkCommands(root) {
  for (const button of root.querySelectorAll("[data-network-command-toggle]")) {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      toggleNetworkCommandPanel(button.dataset.networkCommandToggle || "");
    });
  }
  for (const button of root.querySelectorAll("[data-network-command-close]")) {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      closeNetworkCommandPanel(button.dataset.networkCommandClose || "");
    });
  }
  for (const button of root.querySelectorAll("[data-network-command-run]")) {
    button.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      await runNetworkCommandDryRun(button);
    });
  }
  for (const button of root.querySelectorAll("[data-network-command-confirm-action]")) {
    button.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      await runNetworkCommandConfirm(button);
    });
  }
}

function toggleNetworkCommandPanel(panelId) {
  if (!panelId) {
    return;
  }
  const panel = document.getElementById(panelId);
  if (!panel) {
    return;
  }
  const shouldOpen = panel.hidden;
  closeNetworkCommandPanel(state.networkCommands.openPanelId);
  panel.hidden = !shouldOpen;
  state.networkCommands.openPanelId = shouldOpen ? panelId : "";
  const toggle = document.querySelector(`[data-network-command-toggle="${cssEscape(panelId)}"]`);
  toggle?.setAttribute("aria-expanded", String(shouldOpen));
}

function closeNetworkCommandPanel(panelId) {
  if (!panelId) {
    return;
  }
  const panel = document.getElementById(panelId);
  if (panel) {
    panel.hidden = true;
  }
  const toggle = document.querySelector(`[data-network-command-toggle="${cssEscape(panelId)}"]`);
  toggle?.setAttribute("aria-expanded", "false");
  if (state.networkCommands.openPanelId === panelId) {
    state.networkCommands.openPanelId = "";
  }
}

async function runNetworkCommandDryRun(button) {
  const shell = button.closest("[data-network-command-shell]");
  if (!shell || shell.dataset.networkCommandBusy === "true") {
    return;
  }
  const panelId = shell?.dataset.networkCommandShell || "";
  const log = shell?.querySelector("[data-network-command-log]");
  const confirm = shell?.querySelector("[data-network-command-confirm]");
  const status = shell?.querySelector("[data-network-command-status]");
  const targetType = button.dataset.targetType || "";
  const targetId = button.dataset.targetId || "";
  const actionId = button.dataset.actionId || "";
  const label = button.dataset.commandLabel || actionId;
  const impact = button.dataset.commandImpact || "";
  if (!log) {
    return;
  }
  if (confirm) {
    confirm.hidden = true;
  }
  setNetworkCommandStatus(status, "Checking", "muted");
  setNetworkCommandLog(log, [
    { text: `Checking if ${label.toLowerCase()} is allowed for ${targetType}:${targetId}.`, tone: "info" },
    { text: "Checking policy and required preconditions.", tone: "info" },
  ]);
  shell.dataset.networkCommandBusy = "true";
  button.disabled = true;
  try {
    const payload = await postJson("/api/admin/network/control/dry-run", {
      target_type: targetType,
      target_id: targetId,
      action_id: actionId,
      actor: "system_mode",
      source: "system_mode",
      reason: `System Mode preview for ${label}`,
    });
    const control = payload?.control || {};
    const lines = buildNetworkCommandDryRunLines(control, label, impact);
    setNetworkCommandLog(log, lines);
    setNetworkCommandStatusFromControl(status, control);
    if (confirm) {
      const canConfirmLater = control.allowed === true;
      confirm.hidden = false;
      const confirmButton = confirm.querySelector(".admin-button");
      if (confirmButton) {
        confirmButton.disabled = !canConfirmLater;
        confirmButton.textContent = canConfirmLater ? "Confirm" : "Confirm blocked";
        confirmButton.title = canConfirmLater ? "Confirm this command preview." : "Oracle blocked this command preview.";
        confirmButton.dataset.targetType = targetType;
        confirmButton.dataset.targetId = targetId;
        confirmButton.dataset.actionId = actionId;
        confirmButton.dataset.commandLabel = label;
        confirmButton.dataset.commandImpact = impact;
      }
    }
    if (panelId) {
      state.networkCommands.openPanelId = panelId;
    }
  } catch (error) {
    setNetworkCommandStatus(status, "Failed", "danger");
    setNetworkCommandLog(log, [
      { text: `Checking if ${label.toLowerCase()} is allowed for ${targetType}:${targetId}.`, tone: "info" },
      { text: error instanceof Error ? error.message : "Oracle could not complete the dry-run.", tone: "danger" },
    ]);
  } finally {
    button.disabled = false;
    shell.dataset.networkCommandBusy = "false";
  }
}

async function runNetworkCommandConfirm(button) {
  const shell = button.closest("[data-network-command-shell]");
  if (!shell || shell.dataset.networkCommandBusy === "true") {
    return;
  }
  const log = shell?.querySelector("[data-network-command-log]");
  const status = shell?.querySelector("[data-network-command-status]");
  const targetType = button.dataset.targetType || "";
  const targetId = button.dataset.targetId || "";
  const actionId = button.dataset.actionId || "";
  const label = button.dataset.commandLabel || actionId;
  if (!log) {
    return;
  }
  setNetworkCommandStatus(status, "Executing", "warn");
  appendNetworkCommandLog(log, [
    { text: "Confirmation received.", tone: "info" },
    { text: "Rechecking policy and required preconditions.", tone: "info" },
  ]);
  shell.dataset.networkCommandBusy = "true";
  button.disabled = true;
  try {
    const payload = await postJson("/api/admin/network/control/confirm", {
      target_type: targetType,
      target_id: targetId,
      action_id: actionId,
      actor: "system_mode",
      source: "system_mode",
      reason: `System Mode confirmed preview for ${label}`,
      confirmed: true,
    });
    const control = payload?.control || {};
    appendNetworkCommandLog(log, buildNetworkCommandConfirmLines(control, label));
    setNetworkCommandStatusFromControl(status, control);
  } catch (error) {
    setNetworkCommandStatus(status, "Failed", "danger");
    appendNetworkCommandLog(log, [
      { text: error instanceof Error ? error.message : "Oracle could not confirm this command.", tone: "danger" },
      { text: "No command was sent.", tone: "muted" },
    ]);
    button.disabled = false;
  } finally {
    shell.dataset.networkCommandBusy = "false";
  }
}

function buildNetworkCommandDryRunLines(control, label, impact) {
  const lines = [
    { text: `Checking if ${label.toLowerCase()} is allowed.`, tone: "info" },
  ];
  const targetName = control?.target?.display_name || control?.target_id || "target";
  if (control?.target?.id) {
    lines.push({ text: `Target resolved: ${targetName}.`, tone: "ok" });
  }
  if (Array.isArray(control?.preconditions) && control.preconditions.length) {
    for (const precondition of control.preconditions) {
      lines.push({
        text: `${statusLabel(precondition.status || "unknown")}: ${precondition.summary || precondition.id || "Precondition checked."}`,
        tone: networkCommandPreconditionTone(precondition.status),
      });
    }
  }
  if (control?.allowed) {
    lines.push({ text: `${label} allowed.`, tone: "ok" });
    if (impact) {
      lines.push({ text: impact, tone: "warn" });
    }
    lines.push({ text: "Please confirm to send the configured restart request.", tone: "warn" });
  } else {
    lines.push({ text: `Blocked: ${control?.summary || "Oracle denied this dry-run."}`, tone: "warn" });
  }
  if (Array.isArray(control?.steps) && control.steps.length) {
    for (const step of control.steps) {
      lines.push({ text: step.summary || step.id || "Planned step.", tone: "info" });
    }
  }
  lines.push({ text: "No command was sent.", tone: "muted" });
  return lines;
}

function buildNetworkCommandConfirmLines(control, label) {
  const lines = [];
  if (Array.isArray(control?.preconditions) && control.preconditions.length) {
    for (const precondition of control.preconditions) {
      lines.push({
        text: `${statusLabel(precondition.status || "unknown")}: ${precondition.summary || precondition.id || "Precondition checked."}`,
        tone: networkCommandPreconditionTone(precondition.status),
      });
    }
  }
  if (control?.result_status === "not_implemented") {
    lines.push({ text: `${label} confirmed.`, tone: "ok" });
    lines.push({ text: "Execution adapter is not implemented yet.", tone: "warn" });
    lines.push({ text: "No command was sent.", tone: "muted" });
    return lines;
  }
  if (Array.isArray(control?.steps) && control.steps.length) {
    for (const step of control.steps) {
      lines.push({ text: step.summary || step.id || "Execution step completed.", tone: networkCommandStepTone(step.kind) });
    }
  }
  if (control?.result_status === "executed") {
    lines.push({ text: control?.summary || `${label} completed.`, tone: "ok" });
    return lines;
  }
  if (control?.result_status === "failed") {
    lines.push({ text: control?.summary || `${label} failed.`, tone: "danger" });
    return lines;
  }
  if (control?.allowed) {
    lines.push({ text: control?.summary || `${label} confirmed.`, tone: "ok" });
  } else {
    lines.push({ text: `Blocked: ${control?.summary || `${label} was not confirmed.`}`, tone: "warn" });
  }
  lines.push({ text: "No command was sent.", tone: "muted" });
  return lines;
}

function setNetworkCommandLog(log, lines) {
  log.innerHTML = lines.map(renderNetworkCommandLogLine).join("");
}

function appendNetworkCommandLog(log, lines) {
  log.insertAdjacentHTML(
    "beforeend",
    lines.map(renderNetworkCommandLogLine).join(""),
  );
  log.scrollTop = log.scrollHeight;
}

function renderNetworkCommandLogLine(line) {
  const entry = typeof line === "string" ? { text: line, tone: "info" } : line;
  return `<span class="network-command-log__line network-command-log__line--${escapeAttribute(entry.tone || "info")}"><time>${escapeHtml(new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" }))}</time>${escapeHtml(entry.text || "")}</span>`;
}

function setNetworkCommandStatus(element, label, tone) {
  if (!element) {
    return;
  }
  element.textContent = label;
  element.className = `status-pill status-pill--${statusTone(tone)}`;
}

function setNetworkCommandStatusFromControl(element, control) {
  if (control?.result_status === "executed") {
    setNetworkCommandStatus(element, "Executed", "ok");
    return;
  }
  if (control?.result_status === "failed") {
    setNetworkCommandStatus(element, "Failed", "danger");
    return;
  }
  if (control?.policy_status === "blocked" || control?.policy_status === "denied") {
    setNetworkCommandStatus(element, "Blocked", "warn");
    return;
  }
  if (control?.allowed) {
    setNetworkCommandStatus(element, "Allowed", "ok");
    return;
  }
  setNetworkCommandStatus(element, "Unknown", "muted");
}

function networkCommandPreconditionTone(status) {
  const normalized = String(status || "").toLowerCase();
  if (normalized === "passed") {
    return "ok";
  }
  if (normalized === "failed") {
    return "warn";
  }
  return "muted";
}

function networkCommandStepTone(kind) {
  const normalized = String(kind || "").toLowerCase();
  if (normalized === "execution" || normalized === "verification") {
    return "ok";
  }
  if (normalized === "wait") {
    return "warn";
  }
  return "info";
}

function cssEscape(value) {
  if (globalThis.CSS?.escape) {
    return globalThis.CSS.escape(value);
  }
  return String(value || "").replace(/["\\]/g, "\\$&");
}

function networkServiceDetail(service, context) {
  const evidenceById = new Map(context.evidence.map((entry) => [String(entry.id || ""), entry]));
  const evidence = (service.evidence_ids || []).map((evidenceId) => evidenceById.get(String(evidenceId))).filter(Boolean);
  const latest = evidence[0];
  if (latest) {
    const reference = latest.provider_reference || {};
    const providerName = latest.provider || "provider";
    const serviceName = reference.service_name || reference.service_desc || "";
    const summary = latest.summary || latest.detail || service.summary || "";
    return [providerName, serviceName, summary].filter(Boolean).join(" / ");
  }
  return service.summary || "No provider evidence.";
}

function networkHostServices(host) {
  const byId = new Map();
  for (const service of host.services || []) {
    if (service && service.id) {
      byId.set(String(service.id), service);
    }
  }
  for (const group of host.service_groups || []) {
    for (const service of group.services || []) {
      if (service && service.id) {
        byId.set(String(service.id), service);
      }
    }
  }
  return Array.from(byId.values()).sort((left, right) => String(left.display_name || left.id || "").localeCompare(String(right.display_name || right.id || "")));
}

function networkStatusPresentation(targetType, item, context) {
  const status = String(item.status || "unknown").trim().toLowerCase() || "unknown";
  const monitorCount = networkTargetMonitorCount(targetType, item, context);
  const evidenceCount = Array.isArray(item.evidence_ids) ? item.evidence_ids.length : 0;
  if (status === "unknown" && monitorCount === 0 && evidenceCount === 0) {
    return {
      label: "Not monitored",
      tone: "muted",
      summary: networkUnmonitoredSummary(targetType, item, context),
    };
  }
  return {
    label: statusLabel(status),
    tone: statusTone(status),
    summary: String(item.summary || "Status is unknown."),
  };
}

function networkTargetMonitorCount(targetType, item, context) {
  const itemId = String(item.id || "");
  if (!itemId) {
    return 0;
  }
  if (targetType === "service_group") {
    const serviceIds = new Set((item.service_ids || []).map((serviceId) => String(serviceId || "")));
    return context.monitors.filter((monitor) => monitor.target_type === "service" && serviceIds.has(String(monitor.target_id || ""))).length;
  }
  return context.monitors.filter((monitor) => monitor.target_type === targetType && monitor.target_id === itemId).length;
}

function networkUnmonitoredSummary(targetType, item, context) {
  if (targetType === "service_group") {
    return "No service-specific monitors are attached to this group yet.";
  }
  const host = context.hostById?.get(String(item.host_id || ""));
  if (host && String(host.status || "").toLowerCase() === "healthy") {
    return `Host ${host.display_name || host.id || "health"} is healthy; no service-specific monitor is attached yet.`;
  }
  return "No service-specific monitor is attached yet.";
}

function renderNetworkTargetDetails(targetType, item, context, options = {}) {
  const itemId = String(item.id || "");
  const monitors = context.monitors.filter((monitor) => monitor.target_type === targetType && monitor.target_id === itemId);
  const evidenceById = new Map(context.evidence.map((entry) => [String(entry.id || ""), entry]));
  const evidence = (item.evidence_ids || []).map((evidenceId) => evidenceById.get(String(evidenceId))).filter(Boolean);
  const dependencies = context.dependencies.filter((dependency) => {
    if (dependency.from_type === targetType && dependency.from_id === itemId) {
      return true;
    }
    return dependency.to_type === targetType && dependency.to_id === itemId;
  });
  if (!monitors.length && !evidence.length && !dependencies.length) {
    return "";
  }
  return `
    <details class="network-details" ${options.open ? "open" : ""}>
      <summary>Evidence and relationships</summary>
      <div class="network-details__grid">
        ${monitors.length ? `<div><h5>Monitors</h5>${monitors.map(renderNetworkMonitorRow).join("")}</div>` : ""}
        ${dependencies.length ? `<div><h5>Dependencies</h5>${dependencies.map(renderNetworkDependencyRow).join("")}</div>` : ""}
        ${evidence.length ? `<div><h5>Evidence</h5>${evidence.map(renderNetworkEvidenceRow).join("")}</div>` : ""}
      </div>
    </details>
  `;
}

function renderNetworkDependencyCard(dependencies, monitors) {
  void dependencies;
  const stackRows = renderNetworkStackRows(monitors);
  const rows = [
    ...stackRows,
  ];
  return `
    <article class="system-card">
      <div class="card-head">
        <div>
          <p class="system-kicker">Internet</p>
          <h4>Networking Stack</h4>
        </div>
      </div>
      <div class="list-grid">
        ${rows.length ? rows.join("") : renderEmpty("No internet or networking stack monitors are declared yet.")}
      </div>
    </article>
  `;
}

function renderNetworkHostCategoryCard(title, hosts, context) {
  return `
    <article class="system-card">
      <div class="card-head">
        <div>
          <p class="system-kicker">Inventory</p>
          <h4>${escapeHtml(title)}</h4>
        </div>
        <span class="small-copy">${escapeHtml(String(hosts.length))} hosts</span>
      </div>
      <div class="network-host-list">
        ${hosts.length ? hosts.map((host) => renderNetworkHost(host, context)).join("") : renderEmpty(`No ${title.toLowerCase()} hosts are configured yet.`)}
      </div>
    </article>
  `;
}

function filterNetworkHosts(hosts, category) {
  return hosts.filter((host) => networkHostCategory(host) === category);
}

function networkHostCategory(host) {
  const text = `${host.id || ""} ${host.display_name || ""} ${host.role || ""} ${host.kind || ""}`.toLowerCase();
  if (text.includes("satellite")) {
    return "satellite";
  }
  if (text.includes("server") || text.includes("oracle_brain") || text.includes("edge_gateway") || text.includes("dns_server")) {
    return "server";
  }
  return "infrastructure";
}

function renderNetworkStackRows(monitors) {
  return monitors.map(renderNetworkMonitorRow);
}

function renderNetworkDependencyRow(item) {
  const subject = item.from_id && item.to_id ? `${item.from_type}:${item.from_id} -> ${item.to_type}:${item.to_id}` : item.id;
  return `
    <div class="source-card network-compact-row">
      <div class="card-head">
        <h5>${escapeHtml(item.display_name || item.id || "Dependency")}</h5>
        <span class="status-pill status-pill--${statusTone(item.status)}">${escapeHtml(item.status || "unknown")}</span>
      </div>
      <p class="small-copy">${escapeHtml(item.relationship || "depends_on")} / ${escapeHtml(subject || "dependency")}</p>
      <p class="small-copy">${escapeHtml(item.summary || "Status is unknown.")}</p>
    </div>
  `;
}

function renderNetworkMonitorRow(item) {
  const target = item.target_type && item.target_id ? `${item.target_type}:${item.target_id}` : "global";
  return `
    <div class="source-card network-compact-row">
      <div class="card-head">
        <h5>${escapeHtml(item.display_name || item.id || "Monitor")}</h5>
        <span class="status-pill status-pill--${statusTone(item.status)}">${escapeHtml(item.status || "unknown")}</span>
      </div>
      <p class="small-copy">${escapeHtml(item.provider || "provider")} / ${escapeHtml(target)}</p>
      <p class="small-copy">${escapeHtml(item.summary || "Status is unknown.")}</p>
    </div>
  `;
}

function renderNetworkEvidenceList(evidence) {
  if (!evidence.length) {
    return renderEmpty("No provider evidence has been recorded yet.");
  }
  return `
    <div class="network-evidence-list">
      ${evidence.map(renderNetworkEvidenceRow).join("")}
    </div>
  `;
}

function renderNetworkEvidenceRow(item) {
  const subject = item.subject_type && item.subject_id ? `${item.subject_type}:${item.subject_id}` : "network";
  return `
    <div class="source-card network-compact-row">
      <div class="card-head">
        <h5>${escapeHtml(item.provider || "provider")} / ${escapeHtml(item.id || "evidence")}</h5>
        <span class="status-pill status-pill--${statusTone(item.status)}">${escapeHtml(item.status || "unknown")}</span>
      </div>
      <p class="small-copy">${escapeHtml(subject)} / ${escapeHtml(formatTime(item.observed_at) || "No timestamp")}</p>
      <p class="small-copy">${escapeHtml(item.summary || item.detail || "No detail.")}</p>
    </div>
  `;
}

function formatNetworkCache(network) {
  const age = Number(network.cache_age_seconds || 0);
  const ttl = Number(network.cache_ttl_seconds || 0);
  if (!ttl) {
    return "Live";
  }
  return `${network.cache_hit ? "Cached" : "Fresh"} / ${Math.round(age)}s of ${ttl}s`;
}

function statusLabel(status) {
  const normalized = String(status || "unknown").trim();
  if (!normalized) {
    return "Unknown";
  }
  return normalized[0].toUpperCase() + normalized.slice(1);
}

function formatNetworkLabel(value) {
  return String(value || "").replaceAll("_", " ");
}

async function loadOrchestration() {
  setStatus(elements.orchestrationStatus, "Loading orchestration view.");
  try {
    const inventory = await fetchJson("/api/admin/orchestrations");
    const definitions = Array.isArray(inventory.definitions) ? inventory.definitions : [];
    if (!state.orchestration.selectedId && definitions.length > 0) {
      state.orchestration.selectedId = definitions[0].id || "";
    }
    const detail = state.orchestration.selectedId
      ? await fetchJson(`/api/admin/orchestrations/${encodeURIComponent(state.orchestration.selectedId)}`)
      : null;
    elements.orchestrationRoot.innerHTML = `
      <section class="routine-grid">
        ${definitions.map(renderOrchestrationCard).join("")}
      </section>
      ${detail ? renderOrchestrationDetail(detail) : renderEmpty("No orchestration definitions are configured.")}
    `;
    wireOrchestrationControls();
    showFeedback("");
    setStatus(elements.orchestrationStatus, `Loaded ${definitions.length} definitions and ${Number(inventory.summary?.run_count || 0)} recent runs.`);
    scheduleRefresh("orchestration", 30);
  } catch (error) {
    elements.orchestrationRoot.innerHTML = renderEmpty(error instanceof Error ? error.message : "Orchestration unavailable.", "orchestration");
    wireRetryButtons(elements.orchestrationRoot);
    setStatus(elements.orchestrationStatus, "Orchestration unavailable.");
  }
}

function renderOrchestrationCard(definition) {
  const selected = definition.id === state.orchestration.selectedId;
  const latest = definition.latest_run || null;
  const active = definition.active_run || null;
  return `
    <button class="routine-card orchestration-card${selected ? " is-selected" : ""}" type="button" data-orchestration-select="${escapeHtml(definition.id || "")}">
      <div class="card-head">
        <div>
          <p class="system-kicker">${escapeHtml(definition.kind === "recovery" ? "Recovery runbook" : "Task routine")}</p>
          <h4>${escapeHtml(definition.display_name || definition.id || "Orchestration")}</h4>
        </div>
        <span class="status-pill status-pill--${active ? statusTone(active.status) : definition.enabled ? "ok" : "muted"}">${active ? statusLabel(active.status) : definition.enabled ? "Enabled" : "Disabled"}</span>
      </div>
      <p class="small-copy">${escapeHtml(definition.description || "No description.")}</p>
      <div class="orchestration-card__meta">
        <span>${Number(definition.run_count || 0)} runs</span>
        <span>${active ? `Active: ${escapeHtml(statusLabel(active.status))}` : latest ? `Last: ${escapeHtml(statusLabel(latest.status))}` : "Never run"}</span>
      </div>
    </button>
  `;
}

function renderOrchestrationDetail(payload) {
  const definition = payload.definition || {};
  const runs = Array.isArray(payload.runs) ? payload.runs : [];
  const activeRun = runs.find((run) => ["running", "waiting"].includes(String(run.status || ""))) || null;
  const activeRoutine = definition.kind === "routine" ? activeRun : null;
  const preview = state.orchestration.preview;
  return `
    <section class="system-card orchestration-detail">
      <div class="card-head">
        <div>
          <p class="system-kicker">${escapeHtml(definition.kind === "recovery" ? "Recovery runbook" : "Task routine")}</p>
          <h4>${escapeHtml(definition.display_name || definition.id || "Orchestration")}</h4>
        </div>
        <span class="status-pill status-pill--${definition.enabled ? "ok" : "muted"}">${definition.enabled ? "Enabled" : "Disabled"}</span>
      </div>
      <p class="small-copy">${escapeHtml(definition.description || "No description.")}</p>
      ${activeRoutine ? `<div class="notice notice--warning">This routine already has an active ${escapeHtml(statusLabel(activeRoutine.status).toLowerCase())} run. Cancel it from Recent runs before starting another.</div>` : ""}
      ${definition.kind === "routine" ? renderOrchestrationInputs(definition.inputs || {}) : ""}
      <div class="orchestration-detail__actions">
        <button class="admin-button" type="button" ${definition.kind === "recovery" ? `data-orchestration-prepare="${escapeHtml(definition.id || "")}"` : `data-orchestration-run="${escapeHtml(definition.id || "")}"`} ${definition.execution_available && !activeRoutine ? "" : "disabled"}>
          <span class="material-symbols-outlined">play_arrow</span>
          <span>${definition.kind === "recovery" ? "Prepare run" : activeRoutine ? "Routine active" : "Run routine"}</span>
        </button>
        <button class="admin-button--secondary" type="button" disabled>
          <span class="material-symbols-outlined">tune</span>
          <span>Configure later</span>
        </button>
      </div>
      ${renderOrchestrationDefinition(definition)}
      ${preview && preview.orchestration_id === definition.id ? renderAdminOrchestrationPreview(preview) : ""}
    </section>
    <section class="system-card">
      <div class="card-head">
        <div>
          <p class="system-kicker">Durable history</p>
          <h4>Recent runs</h4>
        </div>
        <span class="status-pill status-pill--muted">${runs.length}</span>
      </div>
      <div class="orchestration-runs">
        ${runs.length > 0 ? runs.map(renderOrchestrationRun).join("") : '<div class="notice">This orchestration has not run yet.</div>'}
      </div>
    </section>
  `;
}

function renderOrchestrationInputs(inputs) {
  const entries = Object.entries(inputs || {});
  if (!entries.length) {
    return "";
  }
  return `
    <div class="orchestration-inputs">
      <h5>Run settings</h5>
      ${entries.map(([inputId, definition]) => {
        const input = definition || {};
        if (input.type !== "integer") {
          return "";
        }
        const label = inputId.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
        return `
          <label class="orchestration-input">
            <span>${escapeHtml(label)}</span>
            <input
              type="number"
              data-orchestration-input="${escapeHtml(inputId)}"
              value="${escapeHtml(input.default ?? "")}"
              min="${escapeHtml(input.minimum ?? "")}"
              max="${escapeHtml(input.maximum ?? "")}"
              step="1"
              ${input.required ? "required" : ""}
            >
          </label>
        `;
      }).join("")}
      <p class="small-copy">These values apply only to this run. Routine definitions remain file-based.</p>
    </div>
  `;
}

function renderOrchestrationDefinition(definition) {
  if (definition.kind === "routine") {
    const steps = Array.isArray(definition.steps) ? definition.steps : [];
    return `
      <div class="orchestration-definition">
        <h5>Declared steps</h5>
        ${steps.map((step, index) => `
          <div class="orchestration-definition__row">
            <span>${index + 1}</span>
            <div>
              <strong>${escapeHtml(step.label || step.id || "Step")}</strong>
              <p class="small-copy">${escapeHtml(step.type || "typed step")} / ${step.required === false ? "Best effort" : "Required"}</p>
            </div>
          </div>
        `).join("")}
      </div>
    `;
  }
  return `
    <div class="metric-grid orchestration-definition">
      <div class="metric-block"><p class="metric-label">Approval</p><strong>Frozen plan</strong></div>
      <div class="metric-block"><p class="metric-label">Diagnostic profile</p><strong>${escapeHtml(definition.diagnostic_profile || "Unknown")}</strong></div>
      <div class="metric-block"><p class="metric-label">Remediation profile</p><strong>${escapeHtml(definition.remediation_profile || "Unknown")}</strong></div>
    </div>
  `;
}

function renderAdminOrchestrationPreview(preview) {
  const steps = Array.isArray(preview.steps) ? preview.steps : [];
  return `
    <div class="orchestration-preview">
      <div class="card-head">
        <div><p class="system-kicker">Approval preview</p><h4>${steps.length > 0 ? `${steps.length} conditional actions` : "No actions needed"}</h4></div>
        <span class="status-pill status-pill--${steps.length > 0 ? "warn" : "ok"}">${escapeHtml(preview.status || "unknown")}</span>
      </div>
      <p class="small-copy">${escapeHtml(preview.approval_summary || preview.notice || "")}</p>
      ${steps.map((step, index) => `
        <div class="orchestration-preview__step">
          <span>${index + 1}</span>
          <div>
            <strong>${escapeHtml(step.target_label || step.target_id || "Target")}</strong>
            <p class="small-copy">${escapeHtml(step.plain_language_summary || step.description || step.action_id || "Policy action")}</p>
            ${step.estimated_duration ? `<p class="small-copy">Estimated time: ${escapeHtml(step.estimated_duration)}</p>` : ""}
          </div>
        </div>
      `).join("")}
      ${preview.approval_available ? '<button class="admin-button" type="button" data-orchestration-approve="true"><span class="material-symbols-outlined">verified_user</span><span>Approve these fixes</span></button>' : '<div class="notice">Current health does not require an approved action.</div>'}
    </div>
  `;
}

function renderOrchestrationRun(run) {
  const steps = Array.isArray(run.steps) ? run.steps : [];
  return `
    <details class="orchestration-run">
      <summary>
        <div><strong>${escapeHtml(formatTime(run.started_at) || run.run_id || "Run")}</strong><p class="small-copy">${escapeHtml(run.summary || "No summary.")}</p></div>
        <span class="status-pill status-pill--${statusTone(run.status)}">${escapeHtml(statusLabel(run.status))}</span>
      </summary>
      <div class="orchestration-run__steps">
        ${steps.length > 0 ? steps.map((step) => `
          <div class="orchestration-run__step">
            <div><strong>${escapeHtml(step.target_label || step.step_id || "Step")}</strong><p class="small-copy">${escapeHtml(step.summary || step.action_id || "No summary.")}</p></div>
            <span class="status-pill status-pill--${statusTone(step.status)}">${escapeHtml(statusLabel(step.status))}</span>
          </div>
        `).join("") : '<p class="small-copy">No step records.</p>'}
        ${run.kind === "routine" && run.status === "waiting" ? `<button class="admin-button--secondary" type="button" data-orchestration-cancel="${escapeHtml(run.run_id || "")}"><span class="material-symbols-outlined">cancel</span><span>Cancel routine</span></button>` : ""}
      </div>
    </details>
  `;
}

function wireOrchestrationControls() {
  for (const button of elements.orchestrationRoot.querySelectorAll("[data-orchestration-select]")) {
    button.addEventListener("click", () => {
      state.orchestration.selectedId = button.dataset.orchestrationSelect || "";
      state.orchestration.preview = null;
      void loadOrchestration();
    });
  }
  const prepare = elements.orchestrationRoot.querySelector("[data-orchestration-prepare]");
  prepare?.addEventListener("click", async () => {
    prepare.disabled = true;
    try {
      const orchestrationId = prepare.dataset.orchestrationPrepare || "";
      const payload = await postJson(`/api/ui/orchestrations/${encodeURIComponent(orchestrationId)}/preview`, { client_id: "browser-admin-ui" });
      state.orchestration.preview = payload.preview || null;
      await loadOrchestration();
    } catch (error) {
      showFeedback(error instanceof Error ? error.message : "Unable to prepare orchestration.", "error");
      prepare.disabled = false;
    }
  });
  const approve = elements.orchestrationRoot.querySelector("[data-orchestration-approve]");
  approve?.addEventListener("click", async () => {
    const preview = state.orchestration.preview;
    if (!preview || !globalThis.confirm(buildOrchestrationApprovalPrompt(preview))) {
      return;
    }
    approve.disabled = true;
    try {
      const payload = await postJson(`/api/ui/orchestrations/${encodeURIComponent(preview.orchestration_id)}/approve`, {
        client_id: "browser-admin-ui",
        preview_id: preview.preview_id,
        digest: preview.digest,
        approved: true,
      });
      state.orchestration.preview = null;
      showFeedback(payload.run?.summary || "Orchestration completed.", payload.ok ? "success" : "error");
      await loadOrchestration();
    } catch (error) {
      showFeedback(error instanceof Error ? error.message : "Orchestration failed.", "error");
      approve.disabled = false;
    }
  });
  const runRoutine = elements.orchestrationRoot.querySelector("[data-orchestration-run]");
  runRoutine?.addEventListener("click", async () => {
    if (!globalThis.confirm("Start this task routine now?")) {
      return;
    }
    runRoutine.disabled = true;
    try {
      const orchestrationId = runRoutine.dataset.orchestrationRun || "";
      const inputs = {};
      for (const input of elements.orchestrationRoot.querySelectorAll("[data-orchestration-input]")) {
        if (!input.checkValidity()) {
          input.reportValidity();
          runRoutine.disabled = false;
          return;
        }
        const inputId = input.dataset.orchestrationInput || "";
        const value = Number(input.value);
        if (inputId && Number.isInteger(value)) {
          inputs[inputId] = value;
        }
      }
      const payload = await postJson(`/api/ui/orchestrations/${encodeURIComponent(orchestrationId)}/run`, {
        client_id: "browser-admin-ui",
        inputs,
      });
      showFeedback(payload.run?.summary || "Routine started.", payload.ok ? "success" : "error");
      await loadOrchestration();
    } catch (error) {
      showFeedback(error instanceof Error ? error.message : "Routine failed to start.", "error");
      runRoutine.disabled = false;
    }
  });
  for (const cancelButton of elements.orchestrationRoot.querySelectorAll("[data-orchestration-cancel]")) {
    cancelButton.addEventListener("click", async () => {
      if (!globalThis.confirm("Cancel this waiting routine?")) {
        return;
      }
      cancelButton.disabled = true;
      try {
        const runId = cancelButton.dataset.orchestrationCancel || "";
        const payload = await postJson(`/api/ui/orchestration-runs/${encodeURIComponent(runId)}/cancel`, {
          client_id: "browser-admin-ui",
        });
        showFeedback(payload.run?.summary || "Routine canceled.", payload.ok ? "success" : "error");
        await loadOrchestration();
      } catch (error) {
        showFeedback(error instanceof Error ? error.message : "Routine could not be canceled.", "error");
        cancelButton.disabled = false;
      }
    });
  }
}

function buildOrchestrationApprovalPrompt(preview) {
  const steps = Array.isArray(preview?.steps) ? preview.steps : [];
  const lines = [
    preview?.approval_summary || "Approve these conditional actions?",
    "",
    "Oracle will re-check first, skip anything already fixed, and stop if a different action is needed.",
  ];
  if (steps.length > 0) {
    lines.push("", "Possible visible effects:");
    for (const step of steps) {
      const target = step.target_label || step.target_id || "Network item";
      const effect = step.user_effect || step.plain_language_summary || step.action_id || "May be unavailable during the fix.";
      const duration = step.estimated_duration ? ` (${step.estimated_duration})` : "";
      lines.push(`- ${target}: ${effect}${duration}`);
    }
  }
  return lines.join("\n");
}

async function loadSuggestions() {
  setStatus(elements.suggestionsStatus, "Loading suggestions.");
  try {
    const [status, inbox, runs, packet, response] = await Promise.all([
      fetchJson("/api/admin/suggestions/openclaw/status"),
      fetchJson("/api/admin/suggestions"),
      fetchJson("/api/admin/suggestions/runs"),
      fetchJson("/api/admin/suggestions/last-packet"),
      fetchJson("/api/admin/suggestions/last-response"),
    ]);
    const suggestions = Array.isArray(inbox.suggestions) ? inbox.suggestions : [];
    const latestRun = Array.isArray(runs.runs) && runs.runs.length ? runs.runs[0] : null;
    elements.suggestionsRoot.innerHTML = `
      <section class="suggestions-grid">
        <article class="system-card">
          <div class="card-head">
            <div>
              <p class="system-kicker">Manual analysis</p>
              <h4>Generate Suggestions</h4>
            </div>
            <span class="status-pill status-pill--${status.configured ? "ok" : "warn"}">${escapeHtml(status.configured ? "Configured" : "Not configured")}</span>
          </div>
          <p class="small-copy">${escapeHtml(status.detail || "OpenClaw status unavailable.")}</p>
          <form id="suggestions-generate-form" class="suggestion-form">
            <label>
              <span>Run type</span>
              <select name="run_type">
                <option value="all_sources">All sources review</option>
                <option value="oracle">Oracle review</option>
                <option value="home_assistant">Home Assistant review</option>
                <option value="librenms">LibreNMS review</option>
                <option value="custom">Custom prompt review</option>
              </select>
            </label>
            <div class="suggestion-form__row">
              <label>
                <span>Window start</span>
                <input name="window_start" type="datetime-local">
              </label>
              <label>
                <span>Window end</span>
                <input name="window_end" type="datetime-local">
              </label>
            </div>
            <label>
              <span>Reason</span>
              <input name="reason" type="text" placeholder="Manual system review">
            </label>
            <label>
              <span>Custom prompt</span>
              <textarea name="custom_prompt" rows="3"></textarea>
            </label>
            <div class="suggestion-form__row suggestion-form__compact">
              <label>
                <span>Limit</span>
                <input name="max_suggestions" type="number" min="1" max="100" value="10">
              </label>
              <label class="suggestion-checkbox">
                <input name="use_mock" type="checkbox">
                <span>Use explicitly labeled mock output</span>
              </label>
            </div>
            <button class="admin-button" type="submit">
              <span class="material-symbols-outlined">psychology</span>
              <span>Generate Suggestions</span>
            </button>
          </form>
        </article>

        <article class="system-card">
          <div class="card-head">
            <div>
              <p class="system-kicker">Latest run</p>
              <h4>${latestRun ? escapeHtml(latestRun.status) : "No runs yet"}</h4>
            </div>
            ${latestRun?.mock ? '<span class="status-pill status-pill--warn">Mock</span>' : ""}
          </div>
          <div class="service-list">
            <div class="row-card"><span class="metric-label">Adapter</span><strong>${escapeHtml(status.adapter || "unknown")}</strong></div>
            <div class="row-card"><span class="metric-label">Run</span><strong>${escapeHtml(latestRun?.run_id || "none")}</strong></div>
            <div class="row-card"><span class="metric-label">Suggestions</span><strong>${escapeHtml(String(latestRun?.suggestion_count ?? suggestions.length))}</strong></div>
          </div>
          ${latestRun?.error ? `<div class="notice">${escapeHtml(latestRun.error)}</div>` : ""}
        </article>
      </section>

      <section class="system-card">
        <div class="card-head">
          <div>
            <p class="system-kicker">Review inbox</p>
            <h4>Suggestions</h4>
          </div>
          <div class="suggestion-filters">
            <select id="suggestion-status-filter">
              <option value="">All statuses</option>
              ${["new", "accepted", "rejected", "corrected", "ignored", "archived", "needs_more_data", "false_positive"].map((item) => `<option value="${item}">${item}</option>`).join("")}
            </select>
            <select id="suggestion-severity-filter">
              <option value="">All severities</option>
              ${["info", "low", "medium", "high", "critical"].map((item) => `<option value="${item}">${item}</option>`).join("")}
            </select>
          </div>
        </div>
        <div id="suggestion-list" class="suggestion-list">
          ${renderSuggestionList(suggestions)}
        </div>
      </section>

      <section id="suggestion-detail-root" class="system-card">
        ${renderEmpty("Select a suggestion to review it.")}
      </section>

      <section class="suggestions-grid">
        <article class="system-card">
          <div class="card-head">
            <div>
              <p class="system-kicker">Last packet</p>
              <h4>Redacted JSON</h4>
            </div>
            <button class="admin-button--secondary" type="button" data-copy-json="packet">Copy</button>
          </div>
          <pre id="last-packet-json" class="json-viewer">${escapeHtml(JSON.stringify(packet.payload || packet, null, 2))}</pre>
        </article>
        <article class="system-card">
          <div class="card-head">
            <div>
              <p class="system-kicker">Last response</p>
              <h4>Redacted JSON</h4>
            </div>
            <button class="admin-button--secondary" type="button" data-copy-json="response">Copy</button>
          </div>
          <pre id="last-response-json" class="json-viewer">${escapeHtml(JSON.stringify(response.payload || response, null, 2))}</pre>
        </article>
      </section>
    `;
    wireSuggestions();
    showFeedback("");
    setStatus(elements.suggestionsStatus, `Loaded ${suggestions.length} suggestions.`);
    scheduleRefresh("suggestions", 0);
  } catch (error) {
    elements.suggestionsRoot.innerHTML = renderEmpty(error instanceof Error ? error.message : "Suggestions unavailable.", "suggestions");
    wireRetryButtons(elements.suggestionsRoot);
    setStatus(elements.suggestionsStatus, "Suggestions unavailable.");
  }
}

function renderSuggestionList(suggestions) {
  if (!suggestions.length) {
    return renderEmpty("No suggestions are in the inbox yet.");
  }
  return suggestions
    .map((item) => {
      const danger = item.severity === "critical" || item.severity === "high";
      return `
        <button class="suggestion-item ${danger ? "suggestion-item--hot" : ""}" type="button" data-suggestion-id="${escapeAttribute(item.id)}">
          <span>
            <strong>${escapeHtml(item.title)}</strong>
            <small>${escapeHtml(item.source)} / ${escapeHtml(item.category)} ${item.similar_to_id ? "/ similar to prior reviewed item" : ""}</small>
          </span>
          <span class="suggestion-item__badges">
            ${item.mock ? '<span class="status-pill status-pill--warn">Mock</span>' : ""}
            <span class="status-pill status-pill--${statusTone(item.severity === "critical" || item.severity === "high" ? "failed" : item.severity === "medium" ? "warning" : "ok")}">${escapeHtml(item.severity)}</span>
            <span class="status-pill status-pill--muted">${escapeHtml(item.status)}</span>
          </span>
        </button>
      `;
    })
    .join("");
}

function wireSuggestions() {
  document.querySelector("#suggestions-generate-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const payload = {
      run_type: String(form.get("run_type") || "all_sources"),
      reason: String(form.get("reason") || "").trim() || null,
      custom_prompt: String(form.get("custom_prompt") || "").trim() || null,
      window_start: datetimeLocalToIso(String(form.get("window_start") || "")),
      window_end: datetimeLocalToIso(String(form.get("window_end") || "")),
      max_suggestions: Number(form.get("max_suggestions") || 10),
      use_mock: form.get("use_mock") === "on",
    };
    setStatus(elements.suggestionsStatus, "Generating suggestions.");
    showFeedback("Generating suggestions. This is manual and advisory only.");
    try {
      const result = await postJson("/api/admin/suggestions/generate", payload);
      showFeedback(result.queued ? "Suggestion run started. OpenClaw may take a long time." : result.ok ? "Suggestion run completed." : (result.errors || []).join("; "), result.ok ? "info" : "error");
      await loadSuggestions();
    } catch (error) {
      showFeedback(error instanceof Error ? error.message : "Suggestion generation failed.", "error");
      setStatus(elements.suggestionsStatus, "Suggestion generation failed.");
    }
  });

  for (const button of document.querySelectorAll("[data-suggestion-id]")) {
    button.addEventListener("click", async () => {
      await loadSuggestionDetail(button.dataset.suggestionId || "");
    });
  }
  document.querySelector("#suggestion-status-filter")?.addEventListener("change", reloadFilteredSuggestions);
  document.querySelector("#suggestion-severity-filter")?.addEventListener("change", reloadFilteredSuggestions);
  for (const button of document.querySelectorAll("[data-copy-json]")) {
    button.addEventListener("click", async () => {
      const target = button.dataset.copyJson === "packet" ? "#last-packet-json" : "#last-response-json";
      await navigator.clipboard?.writeText(document.querySelector(target)?.textContent || "");
      showFeedback("Copied redacted JSON.");
    });
  }
}

async function reloadFilteredSuggestions() {
  const status = document.querySelector("#suggestion-status-filter")?.value || "";
  const severity = document.querySelector("#suggestion-severity-filter")?.value || "";
  const query = new URLSearchParams();
  if (status) query.set("status", status);
  if (severity) query.set("severity", severity);
  const payload = await fetchJson(`/api/admin/suggestions${query.toString() ? `?${query}` : ""}`);
  document.querySelector("#suggestion-list").innerHTML = renderSuggestionList(payload.suggestions || []);
  for (const button of document.querySelectorAll("[data-suggestion-id]")) {
    button.addEventListener("click", async () => loadSuggestionDetail(button.dataset.suggestionId || ""));
  }
}

async function loadSuggestionDetail(id) {
  if (!id) return;
  const payload = await fetchJson(`/api/admin/suggestions/${encodeURIComponent(id)}`);
  const item = payload.suggestion;
  const root = document.querySelector("#suggestion-detail-root");
  root.innerHTML = `
    <div class="card-head">
      <div>
        <p class="system-kicker">Suggestion detail</p>
        <h4>${escapeHtml(item.title)}</h4>
      </div>
      <div class="suggestion-item__badges">
        ${item.mock ? '<span class="status-pill status-pill--warn">Mock</span>' : ""}
        <span class="status-pill status-pill--${statusTone(item.severity === "critical" || item.severity === "high" ? "failed" : item.severity === "medium" ? "warning" : "ok")}">${escapeHtml(item.severity)}</span>
        <span class="status-pill status-pill--muted">${escapeHtml(item.status)}</span>
      </div>
    </div>
    ${item.similar_to_id ? `<div class="notice">Similar to previously reviewed suggestion ${escapeHtml(item.similar_to_id)}.</div>` : ""}
    <div class="suggestion-detail-grid">
      <div>
        <p class="metric-label">Summary</p>
        <p>${escapeHtml(item.summary)}</p>
        <p class="metric-label">Suggested action</p>
        <p>${escapeHtml(item.suggested_action)}</p>
        <p class="metric-label">Evidence</p>
        <ul>${(item.evidence || []).map((entry) => `<li>${escapeHtml(entry)}</li>`).join("") || "<li>No evidence provided.</li>"}</ul>
      </div>
      <div>
        <p class="metric-label">Raw OpenClaw JSON</p>
        <pre class="json-viewer">${escapeHtml(JSON.stringify(item.raw_openclaw_item || {}, null, 2))}</pre>
      </div>
    </div>
    <form id="suggestion-review-form" class="suggestion-form">
      <input type="hidden" name="id" value="${escapeAttribute(item.id)}">
      <div class="suggestion-form__row">
        <label>
          <span>Decision</span>
          <select name="status">
            ${["accepted", "rejected", "corrected", "ignored", "archived", "needs_more_data", "false_positive"].map((status) => `<option value="${status}">${status}</option>`).join("")}
          </select>
        </label>
        <label class="suggestion-checkbox">
          <input name="future_automation_candidate" type="checkbox" ${item.future_automation_candidate ? "checked" : ""}>
          <span>Future automation candidate</span>
        </label>
        <label class="suggestion-checkbox">
          <input name="suppress_if_repeated" type="checkbox" ${item.suppress_if_repeated ? "checked" : ""}>
          <span>Suppress if repeated</span>
        </label>
      </div>
      <label><span>Notes</span><textarea name="notes" rows="3">${escapeHtml(item.review_notes || "")}</textarea></label>
      <label><span>Correction</span><textarea name="correction_text" rows="3">${escapeHtml(item.correction_text || "")}</textarea></label>
      <label><span>Rejection reason</span><textarea name="rejection_reason" rows="2">${escapeHtml(item.rejection_reason || "")}</textarea></label>
      <button class="admin-button" type="submit">Save Review</button>
    </form>
  `;
  document.querySelector("#suggestion-review-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const suggestionId = String(form.get("id") || "");
    await postJson(`/api/admin/suggestions/${encodeURIComponent(suggestionId)}/review`, {
      status: String(form.get("status") || "ignored"),
      notes: String(form.get("notes") || ""),
      correction_text: String(form.get("correction_text") || ""),
      rejection_reason: String(form.get("rejection_reason") || ""),
      future_automation_candidate: form.get("future_automation_candidate") === "on",
      suppress_if_repeated: form.get("suppress_if_repeated") === "on",
    });
    showFeedback("Review saved.");
    await loadSuggestions();
  });
}

function datetimeLocalToIso(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toISOString();
}

function resolveSettledValue(result) {
  if (result.status === "fulfilled") {
    return result.value;
  }
  return { status: "failed", detail: result.reason instanceof Error ? result.reason.message : String(result.reason) };
}

function buildServiceItem(label, payload) {
  return {
    label,
    status: String(payload?.status || (payload?.ok === false ? "failed" : payload?.ok === true ? "ok" : "unknown")),
    detail: String(payload?.detail || payload?.service || "No detail available."),
  };
}

function summarizePlaybackSources(sources) {
  if (!Array.isArray(sources) || sources.length === 0) {
    return "No playback sources";
  }
  const healthy = sources.filter((item) => item.ok !== false).length;
  return `${healthy}/${sources.length} reachable sources`;
}
