const state = {
  currentPage: "home",
  homeAudioSource: "",
  audioSource: "",
  audioUser: "",
  audioSelectedResultId: "",
  audioTimerMinutes: "0",
  audioCustomTimerMinutes: "",
  audioSearchQuery: "",
  audioSearchResults: [],
  audioResults: [],
  internetPreview: null,
  internetRun: null,
  escapeHatches: {},
  refreshTimers: new Map(),
  theme: "dark",
  calendarCreate: {
    open: false,
    submitting: false,
    fieldErrors: {},
    draftId: "",
    confirmationMessage: "",
    successMessage: "",
    form: null,
  },
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

const elements = {
  rail: document.querySelector(".rail"),
  mobileNavToggle: document.querySelector("#mobile-nav-toggle"),
  navButtons: Array.from(document.querySelectorAll("[data-page]")),
  panels: Array.from(document.querySelectorAll("[data-page-panel]")),
  pageTitle: document.querySelector("#page-title"),
  pageSubtitle: document.querySelector("#page-subtitle"),
  escapeHatches: document.querySelector("#escape-hatches"),
  feedback: document.querySelector("#app-feedback"),
  homeRoot: document.querySelector("#home-root"),
  homeStatus: document.querySelector("#home-status"),
  weatherRoot: document.querySelector("#weather-root"),
  weatherStatus: document.querySelector("#weather-status"),
  calendarRoot: document.querySelector("#calendar-root"),
  calendarStatus: document.querySelector("#calendar-status"),
  audioRoot: document.querySelector("#audio-root"),
  audioStatus: document.querySelector("#audio-status"),
  audioSource: document.querySelector("#audio-source"),
  houseRoot: document.querySelector("#house-root"),
  houseStatus: document.querySelector("#house-status"),
  internetRoot: document.querySelector("#internet-root"),
  internetStatus: document.querySelector("#internet-status"),
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
const LAUNCHER_SESSION_KEY = "oracle-house-launcher-session";
const LAUNCHER_SOURCE = "browser-house-ui";
const CALENDAR_CLIENT_ID = "browser-house-ui";

const PAGE_TITLES = {
  home: "Home",
  weather: "Weather",
  calendar: "Calendar",
  audio: "Audio",
  house: "House",
  internet: "Internet",
};

const PAGE_SUBTITLES = {
  home: "Calm Glance",
  weather: "Local Conditions",
  calendar: "Upcoming",
  audio: "Playback",
  house: "Daily Systems",
  internet: "Recovery",
};

const ESCAPE_HATCH_ICONS = new Set([
  "open_in_new",
  "cloud",
  "calendar_month",
  "music_note",
  "home",
]);

initialize();

function initialize() {
  state.calendarCreate.form = createDefaultCalendarForm();
  initializeTheme();
  initializeLauncher();
  elements.mobileNavToggle?.addEventListener("click", toggleMobileNav);
  for (const button of elements.navButtons) {
    button.addEventListener("click", () => switchPage(button.dataset.page || "home"));
  }
  elements.audioSource.addEventListener("change", () => {
    state.audioSource = elements.audioSource.value;
    if (state.currentPage === "audio") {
      void loadAudio();
    }
  });
  switchPage("home");
}

function todayInputValue() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function createDefaultCalendarForm() {
  return {
    title: "",
    date: todayInputValue(),
    allDay: false,
    startTime: "",
    endTime: "",
    durationMinutes: "",
  };
}

function resetCalendarCreateState({ keepSuccess = false } = {}) {
  state.calendarCreate.open = false;
  state.calendarCreate.submitting = false;
  state.calendarCreate.fieldErrors = {};
  state.calendarCreate.draftId = "";
  state.calendarCreate.confirmationMessage = "";
  state.calendarCreate.form = createDefaultCalendarForm();
  if (!keepSuccess) {
    state.calendarCreate.successMessage = "";
  }
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
    const response = await postJson("/api/conversation/command", {
      text,
      source: LAUNCHER_SOURCE,
      session_id: state.launcher.sessionId,
    });
    const replyText = String(response.reply_text || "");
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
  elements.pageTitle.textContent = PAGE_TITLES[page] || "Oracle";
  if (elements.pageSubtitle) {
    elements.pageSubtitle.textContent = PAGE_SUBTITLES[page] || "";
  }
  renderEscapeHatches(page);
  for (const button of elements.navButtons) {
    button.classList.toggle("is-active", button.dataset.page === page);
  }
  for (const panel of elements.panels) {
    panel.classList.toggle("is-active", panel.dataset.pagePanel === page);
  }
  closeMobileNav();
  refreshCurrentPage();
}

function renderEscapeHatches(page) {
  if (!elements.escapeHatches) {
    return;
  }
  const hatches = Array.isArray(state.escapeHatches[page]) ? state.escapeHatches[page] : [];
  elements.escapeHatches.innerHTML = hatches
    .map(
      (item) => `
        <a class="escape-hatch" href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">
          <span class="material-symbols-outlined">${escapeHtml(normalizeEscapeHatchIcon(item.icon))}</span>
          <span>${escapeHtml(item.label)}</span>
        </a>
      `,
    )
    .join("");
  elements.escapeHatches.classList.toggle("is-hidden", hatches.length === 0);
}

function normalizeEscapeHatchIcon(icon) {
  const normalized = String(icon || "").trim();
  return ESCAPE_HATCH_ICONS.has(normalized) ? normalized : "open_in_new";
}

function normalizeEscapeHatches(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  return Object.fromEntries(
    Object.entries(value).map(([page, links]) => [
      page,
      Array.isArray(links)
        ? links.filter((item) => item && typeof item.label === "string" && typeof item.url === "string")
        : [],
    ]),
  );
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
  if (state.currentPage === "home") {
    void loadHome();
    return;
  }
  if (state.currentPage === "weather") {
    void loadWeather();
    return;
  }
  if (state.currentPage === "calendar") {
    void loadCalendar();
    return;
  }
  if (state.currentPage === "audio") {
    void loadAudio();
    return;
  }
  if (state.currentPage === "house") {
    void loadHouse();
    return;
  }
  if (state.currentPage === "internet") {
    void loadInternet();
  }
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchJson(path, options = {}) {
  const retries = Number(options.retries ?? 2);
  let lastError = null;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      const response = await fetch(path, { cache: "no-store" });
      if (!response.ok) {
        throw new Error(await parseError(response));
      }
      return response.json();
    } catch (error) {
      lastError = error;
      if (attempt >= retries) {
        break;
      }
      await wait(350 * (attempt + 1));
    }
  }
  throw lastError instanceof Error ? lastError : new Error("Network request failed.");
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
      if (page === "audio" && document.activeElement?.id === "audio-search-input") {
        state.audioSearchQuery = document.activeElement.value;
        scheduleRefresh(page, 2);
        return;
      }
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
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
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

function formatTemperature(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return "--";
  }
  return String(Math.round(Number(value)));
}

function formatPercent(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return "--";
  }
  return String(Math.round(Number(value)));
}

function formatMph(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return "--";
  }
  return String(Math.round(Number(value)));
}

function formatRainRate(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return "--";
  }
  const numeric = Number(value);
  if (numeric === 0) {
    return "0.00";
  }
  if (numeric < 0.1) {
    return numeric.toFixed(2);
  }
  return numeric.toFixed(1);
}

function formatInHg(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return "--";
  }
  return Number(value).toFixed(2);
}

function formatMinutesFromSeconds(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return "--";
  }
  return String(Math.round(Number(value) / 60));
}

function formatPercentFromSeconds(current, total) {
  if (
    current == null ||
    total == null ||
    Number.isNaN(Number(current)) ||
    Number.isNaN(Number(total)) ||
    Number(total) <= 0
  ) {
    return 0;
  }
  return Math.max(0, Math.min(100, Math.round((Number(current) / Number(total)) * 100)));
}

function weatherIconName(summary) {
  const normalized = String(summary || "").trim().toLowerCase();
  if (normalized.includes("snow")) {
    return "weather_snowy";
  }
  if (normalized.includes("thunder")) {
    return "thunderstorm";
  }
  if (normalized.includes("rain") || normalized.includes("shower") || normalized.includes("drizzle")) {
    return "rainy";
  }
  if (normalized.includes("fog") || normalized.includes("mist") || normalized.includes("haze")) {
    return "foggy";
  }
  if (normalized.includes("partly") || normalized.includes("mostly cloudy")) {
    return "partly_cloudy_day";
  }
  if (normalized.includes("cloud")) {
    return "cloud";
  }
  if (normalized.includes("clear") || normalized.includes("sun") || normalized.includes("fair")) {
    return "sunny";
  }
  return "partly_cloudy_day";
}

function formatCompactDateTime(value) {
  if (!value) {
    return "--";
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

function iconName(value) {
  const known = {
    lightbulb: "lightbulb",
    "lightbulb-off": "light_off",
    "lock-open": "lock_open",
    lock: "lock",
    bed: "bed",
    living: "living",
    "door-front": "door_front",
    "book-open": "menu_book",
    pause: "pause",
    square: "stop",
    play: "play_arrow",
    cloud: "cloud",
    audio: "headset",
    calendar: "calendar_today",
    house: "house",
    thermostat: "device_thermostat",
    camera: "videocam",
  };
  return known[String(value || "").trim()] || "bolt";
}

function statusTone(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (["on", "active", "playing", "heat", "cool", "heating", "cooling", "fresh", "ok", "healthy"].includes(normalized)) {
    return "ok";
  }
  if (["warning", "stale", "paused", "idle", "degraded"].includes(normalized)) {
    return "warn";
  }
  if (["off", "failed", "unavailable", "error", "down"].includes(normalized)) {
    return "danger";
  }
  if (["pending", "unknown"].includes(normalized)) {
    return "muted";
  }
  return "muted";
}

function renderEmpty(message, retryPage = "") {
  return `
    <div class="empty-state">
      <strong>${escapeHtml(message)}</strong>
      ${retryPage ? `<button class="ghost-button" type="button" data-retry-page="${escapeHtml(retryPage)}">Try again</button>` : ""}
    </div>
  `;
}

function renderActionButtons(actions, { source = "" } = {}) {
  if (!Array.isArray(actions) || actions.length === 0) {
    return "";
  }
  return `
    <div class="action-row">
      ${actions
        .map((action) => {
          const type = action.type === "secondary" ? "action-button--secondary" : "action-button";
          const icon = action.icon ? `<span class="material-symbols-outlined">${escapeHtml(iconName(action.icon))}</span>` : "";
          return `
            <button
              class="${type}"
              type="button"
              data-action-id="${escapeHtml(action.action_id)}"
              ${source ? `data-action-source="${escapeHtml(source)}"` : ""}
              ${action.requires_confirmation ? 'data-requires-confirmation="true"' : ""}
            >
              ${icon}
              <span>${escapeHtml(action.label || action.action_id)}</span>
            </button>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderHomeControlCards(controls) {
  if (!Array.isArray(controls) || controls.length === 0) {
    return renderEmpty("Quick controls are unavailable right now.");
  }
  return `
    <div class="home-control-grid">
      ${controls
        .map((control) => {
          const action = control.action || null;
          const tone = statusTone(control.state || control.lock_state || "");
          const icon = control.icon
            ? `<span class="material-symbols-outlined">${escapeHtml(iconName(control.icon))}</span>`
            : "";
          const detailMarkup =
            control.kind === "door"
              ? `
                <div class="home-control-card__meta">
                  <span>${escapeHtml(control.status_label || "Status unknown")}</span>
                  <span>${escapeHtml(control.detail || "Lock state unavailable")}</span>
                </div>
              `
              : `<p class="small-copy">${escapeHtml(control.detail || control.status_label || "Unavailable")}</p>`;
          return `
            <button
              class="home-control-card home-control-card--${tone}"
              type="button"
              ${action ? `data-action-id="${escapeHtml(action.action_id)}"` : ""}
              ${action?.requires_confirmation ? 'data-requires-confirmation="true"' : ""}
              ${!action ? "disabled" : ""}
            >
              <div class="home-control-card__head">
                <div class="home-control-card__icon">${icon}</div>
                <span class="home-control-card__badge">${escapeHtml(
                  control.kind === "door" ? control.detail || "Unavailable" : control.status_label || "",
                )}</span>
              </div>
              <div class="home-control-card__body">
                <strong>${escapeHtml(control.label || "Control")}</strong>
                ${detailMarkup}
              </div>
            </button>
          `;
        })
        .join("")}
    </div>
  `;
}

function wireActionButtons(root, afterAction) {
  for (const button of root.querySelectorAll("[data-action-id]")) {
    button.addEventListener("click", async () => {
      const actionId = button.dataset.actionId || "";
      const source = button.dataset.actionSource || "";
      const needsConfirmation = button.dataset.requiresConfirmation === "true";
      if (needsConfirmation && !window.confirm("Run this action?")) {
        return;
      }

      button.disabled = true;
      const original = button.innerHTML;
      button.innerHTML = "<span>Working...</span>";
      try {
        const payload = {
          action_id: actionId,
          client_id: "browser-house-ui",
        };
        if (source) {
          payload.source = source;
        }
        const result = await postJson("/api/ui/action", payload);
        showFeedback(result?.result?.message || "Action complete.");
        if (typeof afterAction === "function") {
          await afterAction(result);
        } else {
          refreshCurrentPage();
        }
      } catch (error) {
        showFeedback(error instanceof Error ? error.message : "Action failed.", "error");
      } finally {
        button.disabled = false;
        button.innerHTML = original;
      }
    });
  }
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

function renderEventRows(events, { emptyText, limit = events?.length || 0 } = {}) {
  if (!Array.isArray(events) || events.length === 0) {
    return renderEmpty(emptyText);
  }
  return events
    .slice(0, limit)
    .map(
      (event) => `
        <div class="event-row">
          <div>
            <strong>${escapeHtml(event.summary || "Untitled event")}</strong>
            <p class="small-copy">${escapeHtml(formatTime(event.start))}</p>
          </div>
        </div>
      `,
    )
    .join("");
}

function formatCalendarTimeRange(event) {
  if (event?.all_day) {
    return "All day";
  }
  const startLabel = formatTimeOnly(event?.start);
  const endLabel = event?.end ? formatTimeOnly(event.end) : "";
  if (!endLabel) {
    return startLabel;
  }
  return `${startLabel} — ${endLabel}`;
}

function formatClockValue(value) {
  if (!value) {
    return "--";
  }
  try {
    const [hoursRaw, minutesRaw] = String(value).split(":");
    const date = new Date();
    date.setHours(Number(hoursRaw), Number(minutesRaw), 0, 0);
    return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  } catch {
    return String(value);
  }
}

function formatCalendarDraftSummary(form) {
  if (!form) {
    return "";
  }
  if (form.allDay) {
    return `${formatCalendarHeadlineDate(form.date)} • All day`;
  }
  if (form.endTime) {
    return `${formatCalendarHeadlineDate(form.date)} • ${formatClockValue(form.startTime)} — ${formatClockValue(form.endTime)}`;
  }
  return `${formatCalendarHeadlineDate(form.date)} • ${formatClockValue(form.startTime)}`;
}

function renderCalendarCreateComposer(createEvent) {
  const createState = state.calendarCreate;
  const available = Boolean(createEvent?.available);
  const successBlock = createState.successMessage
    ? `
      <div class="calendar-create__notice calendar-create__notice--success">
        <span class="material-symbols-outlined">check_circle</span>
        <span>${escapeHtml(createState.successMessage)}</span>
      </div>
    `
    : "";
  if (!available) {
    return `
      ${successBlock}
      <div class="calendar-create-card calendar-create-card--disabled">
        <button class="action-button--secondary" type="button" disabled>Create Event</button>
        <p class="small-copy">${escapeHtml(createEvent?.detail || "Calendar write is not ready for House Mode right now.")}</p>
      </div>
    `;
  }
  if (!createState.open) {
    return `
      ${successBlock}
      <div class="calendar-create-card">
        <button class="action-button--secondary" type="button" data-calendar-create-open="true">Create Event</button>
        <p class="small-copy">${escapeHtml(createEvent?.detail || "Create an event inline, review it, and confirm before Oracle commits it.")}</p>
      </div>
    `;
  }
  if (createState.draftId) {
    return `
      ${successBlock}
      <div class="calendar-create-card calendar-create-card--confirmation">
        <div class="calendar-create-card__header">
          <div>
            <p class="card-kicker">Confirm</p>
            <h4>${escapeHtml(createState.form.title || "Untitled event")}</h4>
          </div>
          <span class="status-pill status-pill--ok">Ready</span>
        </div>
        <p class="calendar-create-card__summary">${escapeHtml(formatCalendarDraftSummary(createState.form))}</p>
        <p class="small-copy">${escapeHtml(createState.confirmationMessage || "Review the draft and confirm.")}</p>
        <div class="action-row">
          <button class="action-button" type="button" data-calendar-confirm="true">Confirm</button>
          <button class="action-button--secondary" type="button" data-calendar-edit="true">Edit</button>
          <button class="ghost-button" type="button" data-calendar-reset="true">Reset</button>
        </div>
      </div>
    `;
  }

  const fieldErrors = createState.fieldErrors || {};
  const form = createState.form || createDefaultCalendarForm();
  const allDayClass = form.allDay ? "is-all-day" : "";
  return `
    ${successBlock}
    <form class="calendar-create-card calendar-create-form ${allDayClass}" data-calendar-create-form="true">
      <div class="calendar-create-card__header">
        <div>
          <p class="card-kicker">Create Event</p>
          <h4>New calendar item</h4>
        </div>
      </div>
      <label class="calendar-field">
        <span>Title</span>
        <input name="title" type="text" value="${escapeHtml(form.title)}" placeholder="Dinner with friends">
        ${fieldErrors.title ? `<span class="calendar-field__error">${escapeHtml(fieldErrors.title)}</span>` : ""}
      </label>
      <div class="calendar-field-grid">
        <label class="calendar-field">
          <span>Date</span>
          <input name="date" type="date" value="${escapeHtml(form.date)}">
          ${fieldErrors.date ? `<span class="calendar-field__error">${escapeHtml(fieldErrors.date)}</span>` : ""}
        </label>
        <label class="calendar-toggle">
          <input name="all_day" type="checkbox" ${form.allDay ? "checked" : ""}>
          <span>All day</span>
        </label>
      </div>
      <div class="calendar-time-grid" data-calendar-time-grid="true">
        <label class="calendar-field">
          <span>Start</span>
          <input name="start_time" type="time" value="${escapeHtml(form.startTime)}" ${form.allDay ? "disabled" : ""}>
          ${fieldErrors.start_time ? `<span class="calendar-field__error">${escapeHtml(fieldErrors.start_time)}</span>` : ""}
        </label>
        <label class="calendar-field">
          <span>End</span>
          <input name="end_time" type="time" value="${escapeHtml(form.endTime)}" ${form.allDay ? "disabled" : ""}>
          ${fieldErrors.end_time ? `<span class="calendar-field__error">${escapeHtml(fieldErrors.end_time)}</span>` : ""}
        </label>
        <label class="calendar-field">
          <span>Duration</span>
          <input name="duration_minutes" type="number" min="1" max="1440" step="1" value="${escapeHtml(form.durationMinutes)}" ${form.allDay ? "disabled" : ""}>
          ${fieldErrors.duration_minutes ? `<span class="calendar-field__error">${escapeHtml(fieldErrors.duration_minutes)}</span>` : ""}
        </label>
      </div>
      <p class="small-copy">For timed events, set an end time or a duration. All-day events ignore time fields.</p>
      <div class="action-row">
        <button class="action-button" type="submit"${createState.submitting ? " disabled" : ""}>Create</button>
        <button class="action-button--secondary" type="button" data-calendar-cancel="true">Cancel</button>
      </div>
    </form>
  `;
}

function updateCalendarAllDayState(formElement) {
  const allDayInput = formElement.querySelector('input[name="all_day"]');
  const timeInputs = formElement.querySelectorAll('input[name="start_time"], input[name="end_time"], input[name="duration_minutes"]');
  const allDay = Boolean(allDayInput?.checked);
  formElement.classList.toggle("is-all-day", allDay);
  for (const input of timeInputs) {
    input.disabled = allDay;
  }
}

function readCalendarCreateForm(root) {
  const formElement = root.querySelector("[data-calendar-create-form]");
  if (!formElement) {
    return { ...(state.calendarCreate.form || createDefaultCalendarForm()) };
  }
  const data = new FormData(formElement);
  const allDay = data.get("all_day") === "on";
  return {
    title: String(data.get("title") || "").trim(),
    date: String(data.get("date") || "").trim(),
    allDay,
    startTime: allDay ? "" : String(data.get("start_time") || "").trim(),
    endTime: allDay ? "" : String(data.get("end_time") || "").trim(),
    durationMinutes: allDay ? "" : String(data.get("duration_minutes") || "").trim(),
  };
}

async function cancelCalendarDraftIfNeeded() {
  if (!state.calendarCreate.draftId) {
    return;
  }
  try {
    await postJson("/api/ui/calendar/cancel", {
      client_id: CALENDAR_CLIENT_ID,
      draft_id: state.calendarCreate.draftId,
    });
  } catch {
    // Ignore cancel failures during local reset; the next draft replaces stale state server-side.
  }
}

function wireCalendarComposer(root, createEvent) {
  root.querySelector("[data-calendar-create-open]")?.addEventListener("click", () => {
    state.calendarCreate.open = true;
    state.calendarCreate.fieldErrors = {};
    state.calendarCreate.successMessage = "";
    void loadCalendar();
  });

  root.querySelector("[data-calendar-cancel]")?.addEventListener("click", async () => {
    await cancelCalendarDraftIfNeeded();
    resetCalendarCreateState();
    await loadCalendar();
  });

  root.querySelector("[data-calendar-reset]")?.addEventListener("click", async () => {
    await cancelCalendarDraftIfNeeded();
    resetCalendarCreateState();
    state.calendarCreate.open = true;
    await loadCalendar();
  });

  root.querySelector("[data-calendar-edit]")?.addEventListener("click", async () => {
    await cancelCalendarDraftIfNeeded();
    state.calendarCreate.draftId = "";
    state.calendarCreate.confirmationMessage = "";
    state.calendarCreate.fieldErrors = {};
    state.calendarCreate.open = true;
    await loadCalendar();
  });

  root.querySelector("[data-calendar-confirm]")?.addEventListener("click", async () => {
    try {
      const response = await postJson("/api/ui/calendar/confirm", {
        client_id: CALENDAR_CLIENT_ID,
        draft_id: state.calendarCreate.draftId,
      });
      if (!response.ok) {
        showFeedback(response.detail || "Unable to create the event right now.", "error");
        return;
      }
      state.calendarCreate.successMessage = String(response.result?.message || "Event added.");
      showFeedback(state.calendarCreate.successMessage);
      resetCalendarCreateState({ keepSuccess: true });
      await Promise.all([loadCalendar(), loadHome()]);
    } catch (error) {
      showFeedback(error instanceof Error ? error.message : "Unable to create the event right now.", "error");
    }
  });

  const formElement = root.querySelector("[data-calendar-create-form]");
  formElement?.querySelector('input[name="all_day"]')?.addEventListener("change", () => {
    updateCalendarAllDayState(formElement);
  });
  if (formElement) {
    updateCalendarAllDayState(formElement);
    formElement.addEventListener("submit", async (event) => {
      event.preventDefault();
      state.calendarCreate.form = readCalendarCreateForm(root);
      state.calendarCreate.submitting = true;
      try {
        const response = await postJson("/api/ui/calendar/draft", {
          client_id: CALENDAR_CLIENT_ID,
          title: state.calendarCreate.form.title,
          date: state.calendarCreate.form.date,
          all_day: state.calendarCreate.form.allDay,
          start_time: state.calendarCreate.form.startTime || null,
          end_time: state.calendarCreate.form.endTime || null,
          duration_minutes: state.calendarCreate.form.durationMinutes ? Number(state.calendarCreate.form.durationMinutes) : null,
        });
        if (!response.ok) {
          state.calendarCreate.fieldErrors = response.validation?.field_errors || {};
          state.calendarCreate.open = true;
          state.calendarCreate.draftId = "";
          state.calendarCreate.confirmationMessage = "";
          await loadCalendar();
          return;
        }
        state.calendarCreate.fieldErrors = {};
        state.calendarCreate.open = true;
        state.calendarCreate.draftId = String(response.draft_id || "");
        state.calendarCreate.confirmationMessage = String(response.confirmation?.message || "");
        state.calendarCreate.form = {
          title: String(response.draft?.title || ""),
          date: String(response.draft?.date || ""),
          allDay: Boolean(response.draft?.all_day),
          startTime: String(response.draft?.start_time || ""),
          endTime: String(response.draft?.end_time || ""),
          durationMinutes:
            response.draft?.duration_minutes == null ? "" : String(response.draft.duration_minutes),
        };
        await loadCalendar();
      } catch (error) {
        showFeedback(error instanceof Error ? error.message : "Unable to prepare the event draft.", "error");
      } finally {
        state.calendarCreate.submitting = false;
      }
    });
  }
}

function formatCalendarHeadlineDate(value) {
  if (!value) {
    return "Today";
  }
  try {
    return new Date(`${value}T12:00:00`).toLocaleDateString([], {
      weekday: "long",
      month: "short",
      day: "numeric",
    });
  } catch {
    return String(value);
  }
}

function formatCalendarGroupDate(value) {
  if (!value) {
    return "Upcoming";
  }
  try {
    return new Date(`${value}T12:00:00`).toLocaleDateString([], {
      weekday: "long",
      month: "short",
      day: "numeric",
    });
  } catch {
    return String(value);
  }
}

function formatTimeOnly(value) {
  if (!value) {
    return "--";
  }
  try {
    return new Date(value).toLocaleTimeString([], {
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return String(value);
  }
}

function formatEventDuration(start, end) {
  if (!start || !end) {
    return "";
  }
  try {
    const startDate = new Date(start);
    const endDate = new Date(end);
    const minutes = Math.round((endDate.getTime() - startDate.getTime()) / 60000);
    if (!Number.isFinite(minutes) || minutes <= 0) {
      return "";
    }
    if (minutes < 60) {
      return `${minutes} min`;
    }
    const hours = Math.floor(minutes / 60);
    const remainder = minutes % 60;
    if (remainder === 0) {
      return `${hours} hr`;
    }
    return `${hours} hr ${remainder} min`;
  } catch {
    return "";
  }
}

function renderCalendarFocusEvents(events) {
  if (!Array.isArray(events) || events.length === 0) {
    return renderEmpty("Nothing on the calendar today.");
  }
  return events
    .slice(0, 10)
    .map(
      (event, index) => `
        <article class="calendar-focus-card">
          <div class="calendar-focus-card__accent ${index % 2 === 0 ? "" : "is-dim"}"></div>
          <div class="calendar-focus-card__head">
            <div>
              <h4>${escapeHtml(event.summary || "Untitled event")}</h4>
              <p class="small-copy">${escapeHtml(formatCalendarTimeRange(event))}</p>
            </div>
            <div class="calendar-focus-card__time">
              <strong>${escapeHtml(event?.all_day ? "All day" : formatTimeOnly(event.start))}</strong>
              <span>${escapeHtml(event?.all_day ? "Calendar" : formatEventDuration(event.start, event.end) || "Scheduled")}</span>
            </div>
          </div>
        </article>
      `,
    )
    .join("");
}

function groupCalendarEventsByDay(events, limit = 20) {
  if (!Array.isArray(events)) {
    return [];
  }
  const grouped = new Map();
  for (const event of events.slice(0, limit)) {
    const startValue = String(event?.start || "");
    const dayKey = startValue.includes("T") ? startValue.split("T", 1)[0] : startValue;
    if (!grouped.has(dayKey)) {
      grouped.set(dayKey, []);
    }
    grouped.get(dayKey).push(event);
  }
  return Array.from(grouped.entries()).map(([date, items]) => ({ date, items }));
}

function renderCalendarUpcomingGroups(events) {
  const groups = groupCalendarEventsByDay(events, 20);
  if (groups.length === 0) {
    return renderEmpty("No upcoming events.");
  }
  return groups
    .map(
      (group, index) => `
        <section class="calendar-group">
          <div class="calendar-group__marker ${index === 0 ? "is-active" : ""}"></div>
          <div class="calendar-group__content">
            <h4>${escapeHtml(formatCalendarGroupDate(group.date))}</h4>
            <div class="calendar-upcoming-list">
              ${group.items
                .map(
                  (event) => `
                    <article class="calendar-upcoming-item">
                      <span class="calendar-upcoming-item__time">${escapeHtml(event?.all_day ? "All day" : formatTimeOnly(event.start))}</span>
                      <div>
                        <p class="calendar-upcoming-item__title">${escapeHtml(event.summary || "Untitled event")}</p>
                        <p class="small-copy">${escapeHtml(event?.all_day ? "Calendar" : formatEventDuration(event.start, event.end) || "Scheduled")}</p>
                      </div>
                    </article>
                  `,
                )
                .join("")}
            </div>
          </div>
        </section>
      `,
    )
    .join("");
}

async function loadHome() {
  setStatus(elements.homeStatus, "Loading Home snapshot.");
  try {
    const audioQuery = state.homeAudioSource ? `?source=${encodeURIComponent(state.homeAudioSource)}` : "";
    const [homePayload, audioPayload] = await Promise.all([
      fetchJson("/api/ui/home"),
      fetchJson(`/api/ui/audio${audioQuery}`).catch(() => null),
    ]);
    state.escapeHatches = normalizeEscapeHatches(homePayload.escape_hatches);
    renderEscapeHatches(state.currentPage);
    const audioTargets = Array.isArray(audioPayload?.targets) ? audioPayload.targets : Array.isArray(audioPayload?.available_sources) ? audioPayload.available_sources : [];
    if (!state.homeAudioSource) {
      state.homeAudioSource = audioPayload?.selected_target || audioPayload?.source || audioTargets[0]?.source || "";
    }
    const audioSource = state.homeAudioSource || audioPayload?.selected_target || audioPayload?.source || "";
    const audioTargetLabel = (audioTargets.find((item) => item.source === audioSource) || {}).label || audioSource || "No target";
    const audioSessions = Array.isArray(audioPayload?.playback?.active_sessions) ? audioPayload.playback.active_sessions : [];
    const audioOwner =
      audioPayload?.now_playing ||
      audioPayload?.playback?.output_owner ||
      audioSessions.find((item) => {
        const itemState = String(item?.state || "").trim().toLowerCase();
        return Boolean(item?.resumable) || ["paused", "playing", "buffering", "starting"].includes(itemState);
      }) ||
      {};
    const audioMediaKind = String(audioOwner?.media_kind || "").trim().toLowerCase();
    const audioState = String(audioOwner?.state || "").trim().toLowerCase();
    const audioIsPlaying = audioMediaKind && ["playing", "starting", "buffering"].includes(audioState);
    const audioIsResumable = Boolean(audioMediaKind && (audioOwner?.resumable || audioState === "paused"));
    const audioHasAuthorityItem = Boolean(audioMediaKind || audioOwner?.title);
    const audioDisplay = audioHasAuthorityItem ? audioOwner : {};
    const audioDisplayKind = audioMediaKind || "standby";
    const audioDisplayTitle = audioDisplay?.title || (audioSource ? "Nothing to resume" : "Choose a target");
    const audioDisplayCreator = audioDisplay?.artist_or_author || audioDisplay?.author || (audioSource ? "No resumable playback on this target." : "Select a satellite to monitor.");
    const audioProgress =
      audioDisplay?.position_seconds != null || audioDisplay?.current_time_seconds != null
        ? `${formatMinutesFromSeconds(audioDisplay.position_seconds ?? audioDisplay.current_time_seconds)} / ${formatMinutesFromSeconds(audioDisplay.duration_seconds)} min`
        : audioSource
          ? `No resumable playback on ${audioTargetLabel}.`
          : "Select a target.";
    const audioProgressPercent = formatPercentFromSeconds(
      audioDisplay?.position_seconds ?? audioDisplay?.current_time_seconds,
      audioDisplay?.duration_seconds,
    );
    const audioIcon = audioDisplayKind === "audiobook" ? "menu_book" : audioDisplayKind === "music" ? "album" : "headset";
    const audioArtUrl = audioDisplay?.art_url || "";
    const audioPrimaryAction = audioIsPlaying
      ? { type: "control", operation: "pause", label: "Pause", media_kind: audioMediaKind }
      : audioIsResumable
        ? { type: "control", operation: "resume", label: "Resume", media_kind: audioMediaKind }
        : null;
    elements.homeRoot.innerHTML = `
      <section class="hero-card">
        <div>
          <p class="section-kicker">Current atmosphere</p>
          <p class="hero-card__temperature">${formatTemperature(homePayload.weather?.temperature_f)}${homePayload.weather?.temperature_f != null ? "°" : ""}</p>
          <p class="hero-card__summary">${escapeHtml(homePayload.weather?.summary || "Weather unavailable")}</p>
          <div class="hero-metrics">
            <div class="hero-metric">
              <span class="metric-label">Humidity</span>
              <strong>${escapeHtml(formatPercent(homePayload.weather?.humidity_pct))}${homePayload.weather?.humidity_pct != null ? "%" : ""}</strong>
            </div>
            <div class="hero-metric">
              <span class="metric-label">Wind</span>
              <strong>${escapeHtml(formatMph(homePayload.weather?.wind_speed_mph))}${homePayload.weather?.wind_speed_mph != null ? " mph" : ""}</strong>
            </div>
          </div>
        </div>
        <div class="hero-stack">
          <div class="stat-row">
            <span class="metric-label">Weather freshness</span>
            <span class="status-pill status-pill--${statusTone(homePayload.weather?.freshness_class)}">${escapeHtml(homePayload.weather?.freshness_class || "unknown")}</span>
          </div>
          <div class="stat-row">
            <span class="metric-label">Calendar</span>
            <span class="small-copy">${Array.isArray(homePayload.calendar?.events) ? homePayload.calendar.events.length : 0} upcoming items</span>
          </div>
          <div class="stat-row">
            <span class="metric-label">Audio</span>
            <span class="small-copy">${escapeHtml(audioDisplay?.title || "No target playback")}</span>
          </div>
          <div class="stat-row">
            <span class="metric-label">Network Health</span>
            <span class="status-pill status-pill--${statusTone(homePayload.network_health?.status)}">${escapeHtml(((homePayload.network_health?.status || "pending").charAt(0).toUpperCase() + (homePayload.network_health?.status || "pending").slice(1)))}</span>
          </div>
        </div>
      </section>

      <section class="home-grid">
        <article class="panel-card">
          <div class="card-head">
            <div>
              <p class="card-kicker">Agenda</p>
              <h4>Next up</h4>
            </div>
            <button class="ghost-button" type="button" data-switch-page="calendar">Open Calendar</button>
          </div>
          <div class="timeline-list">
            ${renderEventRows(homePayload.calendar?.events, { emptyText: "No upcoming events.", limit: 3 })}
          </div>
        </article>

        <article class="media-card home-audio-panel">
          <div class="card-head">
            <div>
              <p class="card-kicker">Audio Monitor</p>
              <h4>${escapeHtml(audioDisplayTitle)}</h4>
            </div>
            <div class="home-audio-head-tools">
              <label class="home-audio-target-select">
                <span>Target</span>
                <select id="home-audio-target">
                  ${
                    audioTargets.length
                      ? audioTargets
                          .map(
                            (target) =>
                              `<option value="${escapeHtml(target.source || "")}" ${target.source === audioSource ? "selected" : ""}>${escapeHtml(target.label || target.source || "Target")}</option>`,
                          )
                          .join("")
                      : `<option value="">No targets</option>`
                  }
                </select>
              </label>
              <span class="status-pill status-pill--${statusTone(audioOwner.state)}">${escapeHtml(audioOwner.state || "idle")}</span>
            </div>
          </div>
          <div class="home-audio-card">
            <div class="home-audio-card__cover">
              ${
                audioArtUrl
                  ? `<img src="${escapeHtml(audioArtUrl)}" alt="" loading="lazy">`
                  : `<span class="material-symbols-outlined">${audioIcon}</span>`
              }
            </div>
            <div class="home-audio-card__content">
              <div class="home-audio-card__meta">
                <span class="home-audio-pill">${escapeHtml(audioTargetLabel)}</span>
                <span class="home-audio-pill">${escapeHtml(audioDisplayKind)}</span>
              </div>
              <p class="home-audio-card__creator">${escapeHtml(audioDisplayCreator)}</p>
              <div class="home-audio-card__progress">
                <div class="home-audio-card__track">
                  <div class="home-audio-card__fill" style="width: ${audioProgressPercent}%"></div>
                </div>
                <span>${escapeHtml(audioProgress)}</span>
              </div>
            </div>
          </div>
          <div class="action-row">
            ${
              audioPrimaryAction
                ? `
                  <button
                    class="action-button"
                    type="button"
                    ${
                      audioPrimaryAction.type === "control"
                        ? `data-home-audio-control="${escapeHtml(audioPrimaryAction.operation)}" data-home-audio-kind="${escapeHtml(audioPrimaryAction.media_kind)}"`
                        : ""
                    }
                  >
                    <span>${escapeHtml(audioPrimaryAction.label)}</span>
                  </button>
                `
                : ""
            }
            <button class="action-button--secondary" type="button" data-switch-page="audio">Open Audio</button>
          </div>
        </article>
      </section>

      <section class="panel-card">
        <div class="card-head">
          <div>
            <p class="card-kicker">Quick controls</p>
            <h4>Common house toggles</h4>
          </div>
        </div>
        ${renderHomeControlCards(homePayload.controls)}
      </section>
    `;
    wireActionButtons(elements.homeRoot, async () => {
      await loadHome();
      await loadHouse();
    });
    wireHomeAudioControls(audioPayload);
    wirePageSwitchButtons(elements.homeRoot);
    showFeedback("");
    setStatus(elements.homeStatus, `Updated ${formatTime(homePayload.generated_at)}`);
    scheduleRefresh("home", Number(homePayload.refresh_after_seconds || 60));
  } catch (error) {
    elements.homeRoot.innerHTML = renderEmpty(error instanceof Error ? error.message : "Home unavailable.", "home");
    wireRetryButtons(elements.homeRoot);
    setStatus(elements.homeStatus, "Home unavailable.");
  }
}

function wireHomeAudioControls(audioPayload) {
  const audioSource = state.homeAudioSource || audioPayload?.selected_target || audioPayload?.source || "";

  elements.homeRoot.querySelector("#home-audio-target")?.addEventListener("change", async (event) => {
    state.homeAudioSource = event.currentTarget.value || "";
    await loadHome();
  });

  elements.homeRoot.querySelector("[data-home-audio-control]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const operation = button.getAttribute("data-home-audio-control") || "";
    const mediaKind = button.getAttribute("data-home-audio-kind") || "";
    if (!audioSource || !operation || !mediaKind) {
      showFeedback("No active audio target is available.", "warn");
      return;
    }
    try {
      const payload = await postJson("/api/ui/audio/control", {
        client_id: "browser-house",
        target: audioSource,
        operation,
        media_kind: mediaKind,
      });
      const successMessage = operation === "resume" ? "Audio resumed." : operation === "pause" ? "Audio paused." : "Audio stopped.";
      showFeedback(payload.ok ? successMessage : "Audio control failed.", payload.ok ? "success" : "error");
      await loadHome();
    } catch (error) {
      showFeedback(error instanceof Error ? error.message : "Audio control failed.", "error");
    }
  });
}

async function loadWeather() {
  setStatus(elements.weatherStatus, "Loading Weather snapshot.");
  try {
    const payload = await fetchJson("/api/ui/weather");
    if (payload.ok === false) {
      throw new Error(payload.detail || payload.error || "Weather unavailable.");
    }
    const periods = Array.isArray(payload.forecast?.periods) ? payload.forecast.periods : [];
    const currentSummary = String(payload.current?.summary || "Weather unavailable").trim();
    const weatherIcon = weatherIconName(currentSummary);
    const observedAt = payload.current?.observation_timestamp ? formatCompactDateTime(payload.current.observation_timestamp) : "--";
    const highTemp = periods
      .map((period) => Number(period?.temperature_f))
      .filter((value) => !Number.isNaN(value))
      .reduce((max, value) => (max == null ? value : Math.max(max, value)), null);
    const lowTemp = periods
      .map((period) => Number(period?.temperature_f))
      .filter((value) => !Number.isNaN(value))
      .reduce((min, value) => (min == null ? value : Math.min(min, value)), null);
    const featuredPeriods = periods.slice(0, 3);
    elements.weatherRoot.innerHTML = `
      <section class="weather-hero">
        <div class="weather-hero__copy">
          <p class="section-kicker">Current atmosphere</p>
          <p class="weather-hero__temperature">${formatTemperature(payload.current?.temperature_f)}${payload.current?.temperature_f != null ? "°" : ""}</p>
          <div class="weather-hero__summary-row">
            <span class="material-symbols-outlined weather-hero__summary-icon">${weatherIcon}</span>
            <div>
              <p class="weather-hero__summary">${escapeHtml(currentSummary)}</p>
              <p class="weather-hero__range">
                ${
                  highTemp != null || lowTemp != null
                    ? `High of ${highTemp != null ? `${formatTemperature(highTemp)}°` : "--"} • Low of ${lowTemp != null ? `${formatTemperature(lowTemp)}°` : "--"}`
                    : escapeHtml(payload.forecast?.summary || "Forecast details are limited right now.")
                }
              </p>
            </div>
          </div>
        </div>
        <div class="weather-visual">
          <div class="weather-visual__glow"></div>
          <div class="weather-visual__orb">
            <span class="material-symbols-outlined">${weatherIcon}</span>
          </div>
          <div class="weather-visual__stat weather-visual__stat--top">
            <p class="card-kicker">Freshness</p>
            <div class="weather-visual__value-row">
              <span class="status-pill status-pill--${statusTone(payload.current?.freshness_class)}">${escapeHtml(payload.current?.freshness_class || "unknown")}</span>
            </div>
          </div>
          <div class="weather-visual__stat weather-visual__stat--bottom">
            <p class="card-kicker">Observed</p>
            <strong>${escapeHtml(observedAt)}</strong>
          </div>
        </div>
      </section>

      <section class="weather-stat-band">
        <article class="weather-stat-card">
          <div class="weather-stat-card__head">
            <span class="material-symbols-outlined">humidity_low</span>
            <span class="card-kicker">Humidity</span>
          </div>
          <div class="weather-stat-card__body">
            <p class="value-display">${formatPercent(payload.current?.humidity_pct)}${payload.current?.humidity_pct != null ? "%" : ""}</p>
          </div>
        </article>
        <article class="weather-stat-card">
          <div class="weather-stat-card__head">
            <span class="material-symbols-outlined">air</span>
            <span class="card-kicker">Wind</span>
          </div>
          <div class="weather-stat-card__body weather-stat-card__body--split">
            <p class="value-display">${formatMph(payload.current?.wind_speed_mph)}${payload.current?.wind_speed_mph != null ? " mph" : ""}</p>
            <p class="small-copy">${escapeHtml(payload.current?.wind_direction_cardinal || "Calm")}</p>
          </div>
        </article>
        <article class="weather-stat-card">
          <div class="weather-stat-card__head">
            <span class="material-symbols-outlined">device_thermostat</span>
            <span class="card-kicker">Pressure</span>
          </div>
          <div class="weather-stat-card__body weather-stat-card__body--split">
            <p class="value-display">${formatInHg(payload.current?.barometer_inhg)}${payload.current?.barometer_inhg != null ? " inHg" : ""}</p>
            <p class="small-copy">${escapeHtml(payload.current?.wind_gust_mph != null ? `Gust ${formatMph(payload.current.wind_gust_mph)} mph` : "Stable")}</p>
          </div>
        </article>
        <article class="weather-stat-card">
          <div class="weather-stat-card__head">
            <span class="material-symbols-outlined">water_drop</span>
            <span class="card-kicker">Rain Rate</span>
          </div>
          <div class="weather-stat-card__body weather-stat-card__body--split">
            <p class="value-display">${formatRainRate(payload.current?.rain_rate_in_h)}${payload.current?.rain_rate_in_h != null ? " in/hr" : ""}</p>
            <p class="small-copy">${escapeHtml(payload.current?.age_seconds != null ? `${formatMinutesFromSeconds(payload.current.age_seconds)} min old` : "Live now")}</p>
          </div>
        </article>
      </section>

      <section class="weather-forecast-grid">
        ${
          featuredPeriods.length > 0
            ? featuredPeriods
                .map(
                  (period, index) => `
                    <article class="weather-forecast-card ${index === 0 ? "is-current" : ""}">
                      <div class="weather-forecast-card__head">
                        <span class="card-kicker">${escapeHtml(period.name || "Forecast period")}</span>
                        ${index === 0 ? '<span class="weather-forecast-badge">Current</span>' : ""}
                      </div>
                      <div class="weather-forecast-card__hero">
                        <span class="material-symbols-outlined">${weatherIconName(period.short_forecast || period.name)}</span>
                        <div>
                          <p class="weather-forecast-card__temp">${formatTemperature(period.temperature_f)}${period.temperature_f != null ? "°" : ""}</p>
                          <p class="weather-forecast-card__summary">${escapeHtml(period.short_forecast || "No summary")}</p>
                        </div>
                      </div>
                      <div class="weather-forecast-card__detail">
                        <div class="weather-forecast-card__row">
                          <span>Starts</span>
                          <strong>${escapeHtml(formatCompactDateTime(period.start_time))}</strong>
                        </div>
                        <div class="weather-forecast-card__row">
                          <span>Ends</span>
                          <strong>${escapeHtml(formatCompactDateTime(period.end_time))}</strong>
                        </div>
                      </div>
                    </article>
                  `,
                )
                .join("")
            : renderEmpty("Forecast is temporarily unavailable.")
        }
      </section>

      <section class="weather-detail-full">
        <article class="panel-card weather-detail-card weather-detail-card--full">
          <div class="card-head">
            <div>
              <p class="card-kicker">Conditions detail</p>
              <h4>${escapeHtml(payload.location || "Local weather")}</h4>
            </div>
            <span class="small-copy">${escapeHtml(payload.state || "")}</span>
          </div>
          <div class="weather-detail-list">
            <div class="row-card">
              <span class="metric-label">Update age</span>
              <strong>${formatMinutesFromSeconds(payload.current?.age_seconds)}${payload.current?.age_seconds != null ? " min" : ""}</strong>
            </div>
            <div class="row-card">
              <span class="metric-label">Observed</span>
              <strong>${escapeHtml(observedAt)}</strong>
            </div>
            <div class="row-card">
              <span class="metric-label">Temperature</span>
              <strong>${formatTemperature(payload.current?.temperature_f)}${payload.current?.temperature_f != null ? "°" : ""}</strong>
            </div>
            <div class="row-card">
              <span class="metric-label">Humidity</span>
              <strong>${formatPercent(payload.current?.humidity_pct)}${payload.current?.humidity_pct != null ? "%" : ""}</strong>
            </div>
            <div class="row-card">
              <span class="metric-label">Dew Point</span>
              <strong>${formatTemperature(payload.current?.dewpoint_f)}${payload.current?.dewpoint_f != null ? "°" : ""}</strong>
            </div>
            <div class="row-card">
              <span class="metric-label">Wind</span>
              <strong>${formatMph(payload.current?.wind_speed_mph)}${payload.current?.wind_speed_mph != null ? " mph" : ""}</strong>
            </div>
            <div class="row-card">
              <span class="metric-label">Pressure</span>
              <strong>${formatInHg(payload.current?.barometer_inhg)}${payload.current?.barometer_inhg != null ? " inHg" : ""}</strong>
            </div>
            <div class="row-card">
              <span class="metric-label">Wind gust</span>
              <strong>${formatMph(payload.current?.wind_gust_mph)}${payload.current?.wind_gust_mph != null ? " mph" : ""}</strong>
            </div>
            <div class="row-card">
              <span class="metric-label">Wind Chill</span>
              <strong>${formatTemperature(payload.current?.wind_chill_f)}${payload.current?.wind_chill_f != null ? "°" : ""}</strong>
            </div>
            <div class="row-card">
              <span class="metric-label">Direction</span>
              <strong>${escapeHtml(payload.current?.wind_direction_cardinal || "--")}</strong>
            </div>
            <div class="row-card">
              <span class="metric-label">Heat Index</span>
              <strong>${formatTemperature(payload.current?.heat_index_f)}${payload.current?.heat_index_f != null ? "°" : ""}</strong>
            </div>
            <div class="row-card">
              <span class="metric-label">Rain rate</span>
              <strong>${formatRainRate(payload.current?.rain_rate_in_h)}${payload.current?.rain_rate_in_h != null ? " in/hr" : ""}</strong>
            </div>
            <div class="row-card">
              <span class="metric-label">Rain Total</span>
              <strong>${formatRainRate(payload.current?.rain_total_in)}${payload.current?.rain_total_in != null ? " in" : ""}</strong>
            </div>
            <div class="row-card">
              <span class="metric-label">Inside Temp</span>
              <strong>${formatTemperature(payload.current?.inside_temperature_f)}${payload.current?.inside_temperature_f != null ? "°" : ""}</strong>
            </div>
            <div class="row-card">
              <span class="metric-label">Inside Humidity</span>
              <strong>${formatPercent(payload.current?.inside_humidity_pct)}${payload.current?.inside_humidity_pct != null ? "%" : ""}</strong>
            </div>
          </div>
        </article>
      </section>
    `;
    showFeedback("");
    setStatus(elements.weatherStatus, `Updated ${formatTime(payload.generated_at)}`);
    scheduleRefresh("weather", Number(payload.refresh_after_seconds || 300));
  } catch (error) {
    elements.weatherRoot.innerHTML = renderEmpty(error instanceof Error ? error.message : "Weather unavailable.", "weather");
    wireRetryButtons(elements.weatherRoot);
    setStatus(elements.weatherStatus, "Weather unavailable.");
  }
}

async function loadCalendar() {
  setStatus(elements.calendarStatus, "Loading Calendar snapshot.");
  try {
    const payload = await fetchJson("/api/ui/calendar");
    const todayEvents = Array.isArray(payload.today?.events) ? payload.today.events : [];
    const todayDate = String(payload.today?.date || "").trim();
    const upcomingEvents = (Array.isArray(payload.upcoming?.events) ? payload.upcoming.events : []).filter((event) => {
      if (!todayDate) {
        return true;
      }
      const startValue = String(event?.start || "");
      const eventDate = startValue.includes("T") ? startValue.split("T", 1)[0] : startValue;
      return eventDate !== todayDate;
    });
    elements.calendarRoot.innerHTML = `
      <section class="calendar-layout">
        <article class="calendar-focus">
          <div class="calendar-focus__header">
            <p class="card-kicker">Focus</p>
            <h3>${escapeHtml(formatCalendarHeadlineDate(payload.today?.date || ""))}</h3>
          </div>
          <div class="calendar-focus__list">
            ${renderCalendarFocusEvents(todayEvents)}
          </div>
          <div class="calendar-create">
            ${renderCalendarCreateComposer(payload.create_event)}
          </div>
        </article>

        <article class="calendar-horizon">
          <div class="calendar-horizon__header">
            <div>
              <p class="card-kicker">Horizon</p>
              <h3>Upcoming</h3>
            </div>
            <span class="small-copy">${upcomingEvents.length} items</span>
          </div>
          <div class="calendar-horizon__timeline">
            ${renderCalendarUpcomingGroups(upcomingEvents)}
          </div>
        </article>
      </section>
    `;
    wireCalendarComposer(elements.calendarRoot, payload.create_event);
    showFeedback("");
    setStatus(elements.calendarStatus, `Updated ${formatTime(payload.generated_at)}`);
    scheduleRefresh("calendar", Number(payload.refresh_after_seconds || 120));
  } catch (error) {
    elements.calendarRoot.innerHTML = renderEmpty(error instanceof Error ? error.message : "Calendar unavailable.", "calendar");
    wireRetryButtons(elements.calendarRoot);
    setStatus(elements.calendarStatus, "Calendar unavailable.");
  }
}

function renderSourceOptions(sources, selectedSource) {
  elements.audioSource.innerHTML = "";
  for (const item of sources) {
    const option = document.createElement("option");
    option.value = item.source;
    option.textContent = item.label || item.source;
    option.selected = item.source === selectedSource;
    elements.audioSource.append(option);
  }
}

async function loadAudio() {
  setStatus(elements.audioStatus, "Loading Audio snapshot.");
  try {
    const params = new URLSearchParams();
    if (state.audioSource) {
      params.set("source", state.audioSource);
    }
    if (state.audioUser) {
      params.set("user_id", state.audioUser);
    }
    const query = params.toString() ? `?${params.toString()}` : "";
    const payload = await fetchJson(`/api/ui/audio${query}`);
    const sources = Array.isArray(payload.targets) ? payload.targets : Array.isArray(payload.available_sources) ? payload.available_sources : [];
    if (sources.length > 0) {
      state.audioSource = payload.selected_target || payload.source || state.audioSource || sources[0].source;
      renderSourceOptions(sources, state.audioSource);
    } else {
      state.audioSource = "";
      elements.audioSource.innerHTML = "";
    }
    const users = Array.isArray(payload.users) ? payload.users : [];
    state.audioUser = payload.selected_user || state.audioUser || users[0]?.user_id || "";
    const snapshotResults = Array.isArray(payload.results) ? payload.results : [];
    const results = Array.isArray(state.audioSearchResults) && state.audioSearchResults.length > 0 ? state.audioSearchResults : snapshotResults;
    state.audioResults = results;
    if (!results.some((item) => item.result_id === state.audioSelectedResultId)) {
      state.audioSelectedResultId = results[0]?.result_id || "";
    }
    const playback = payload.playback || {};
    const owner = playback.output_owner || {};
    const capabilities = payload.capabilities || {};
    const selectedResult = results.find((item) => item.result_id === state.audioSelectedResultId) || null;
    const timerOptions = payload.sleep_timer?.options_minutes || [0, 15, 30, 45, 60];
    const customTimerValue = timerOptions.map(String).includes(String(state.audioTimerMinutes))
      ? state.audioCustomTimerMinutes
      : state.audioTimerMinutes;
    const timerReady = Boolean(payload.sleep_timer?.supported && selectedResult?.type === "audiobook");
    elements.audioRoot.innerHTML = `
      <section class="audio-lookup-panel">
        <div class="audio-lookup-panel__row">
          <label class="audio-search">
            <span class="material-symbols-outlined">search</span>
            <input id="audio-search-input" type="search" placeholder="Explore audiobooks or music" value="${escapeHtml(state.audioSearchQuery)}">
          </label>
          <label class="audio-user-select">
            <span>User</span>
            <select id="audio-user-select">
              ${users.map((user) => `
                <option value="${escapeHtml(user.user_id || "")}" ${user.user_id === state.audioUser ? "selected" : ""}>
                  ${escapeHtml(user.label || user.user_id || "User")}
                </option>
              `).join("")}
            </select>
          </label>
        </div>
        <div class="audio-mode-actions">
          <button class="audio-mode-button" type="button" data-audio-search="audiobook">
            <span class="material-symbols-outlined">menu_book</span>
            <span>Search Audiobooks</span>
          </button>
          <button class="audio-mode-button" type="button" data-audio-search="music">
            <span class="material-symbols-outlined">music_note</span>
            <span>Search Music</span>
          </button>
          <button class="audio-mode-button" type="button" data-audio-current>
            <span class="material-symbols-outlined">podcasts</span>
            <span>Get Current Audiobook</span>
          </button>
        </div>
      </section>

      <section class="audio-results-section">
        <div class="audio-section-head">
          <h4>Top Results</h4>
          <span>${results.length} item${results.length === 1 ? "" : "s"} displayed</span>
        </div>
        <div class="audio-result-list">
          ${renderAudioResultCards(results)}
        </div>
      </section>

      <section class="audio-routing-panel">
        <div class="audio-routing-grid">
          <div>
            <p class="card-kicker">Playback Target</p>
            <div class="audio-chip-row">
              ${sources.map((target) => `
                <button class="audio-chip ${target.source === state.audioSource ? "is-selected" : ""}" type="button" data-audio-target="${escapeHtml(target.source || "")}">
                  ${escapeHtml(target.label || target.source || "Target")}
                </button>
              `).join("") || `<span class="small-copy">No playback targets configured.</span>`}
            </div>
          </div>
          <div>
            <p class="card-kicker">Sleep Timer</p>
            <div class="audio-chip-row">
              ${timerOptions.map((minutes) => `
                <button
                  class="audio-chip ${String(minutes) === state.audioTimerMinutes ? "is-selected" : ""} ${timerReady ? "" : "is-disabled"}"
                  type="button"
                  data-audio-timer="${escapeHtml(String(minutes))}"
                  ${timerReady ? "" : "aria-disabled=\"true\""}
                >
                  ${Number(minutes) === 0 ? "None" : `${minutes}m`}
                </button>
              `).join("")}
            </div>
            <label class="audio-custom-timer ${timerReady ? "" : "is-disabled"}">
              <span>Custom</span>
              <input
                id="audio-custom-timer"
                type="number"
                min="1"
                max="240"
                step="1"
                inputmode="numeric"
                placeholder="minutes"
                value="${escapeHtml(customTimerValue)}"
                ${timerReady ? "" : "disabled"}
              >
              <small>1-240 min</small>
            </label>
          </div>
        </div>
        <div class="audio-execute-row">
          <div>
            <p class="card-kicker">Selected</p>
            <h4>${escapeHtml(selectedResult?.title || "Choose an item")}</h4>
            <p class="small-copy">${escapeHtml(selectedResult?.subtitle || "Select a result, then choose target and timer.")}</p>
          </div>
          <button
            class="audio-play-button"
            type="button"
            data-audio-play
            ${capabilities.structured_play && selectedResult && state.audioSource ? "" : "disabled"}
          >
            <span class="material-symbols-outlined">play_arrow</span>
            <span>Execute Playback</span>
          </button>
        </div>
        ${
          !capabilities.structured_play
            ? `<div class="notice"><strong>Playback launch pending.</strong> The structured play endpoint needs a contract for selected result, user, target, and sleep timer.</div>`
            : selectedResult?.type === "music" && Number(state.audioTimerMinutes) > 0
              ? `<div class="notice"><strong>Audiobook timers only.</strong> Sleep timer support is limited to audiobook playback in this pass.</div>`
            : ""
        }
      </section>

      <aside class="audio-route-float">
        <p>Current Routing</p>
        <strong><span></span>${escapeHtml((sources.find((item) => item.source === state.audioSource) || {}).label || state.audioSource || "No target")}</strong>
        <div></div>
        <span>${escapeHtml(owner.title || "Nothing playing")}</span>
      </aside>

      <section class="audio-monitor-panel">
        <article class="media-card">
          <div class="card-head">
            <div>
              <p class="card-kicker">Now playing</p>
              <h4>${escapeHtml(owner.title || "Nothing active")}</h4>
            </div>
            <span class="status-pill status-pill--${statusTone(owner.state)}">${escapeHtml(owner.state || "idle")}</span>
          </div>
          <p class="small-copy">${escapeHtml(owner.artist_or_author || owner.media_kind || "Oracle playback authority is quiet.")}</p>
          <div class="home-audio-card__track">
            <div class="home-audio-card__fill" style="width: ${formatPercentFromSeconds(owner.position_seconds, owner.duration_seconds)}%"></div>
          </div>
          <p class="small-copy">${formatDuration(owner.position_seconds)} / ${formatDuration(owner.duration_seconds)}</p>
          <div class="audio-control-row">
            <button
              class="audio-control-button"
              type="button"
              data-audio-control="pause"
              ${owner.media_kind ? "" : "disabled"}
            >
              <span class="material-symbols-outlined">pause</span>
              <span>Pause</span>
            </button>
            <button
              class="audio-control-button audio-control-button--stop"
              type="button"
              data-audio-control="stop"
              ${owner.media_kind ? "" : "disabled"}
            >
              <span class="material-symbols-outlined">stop</span>
              <span>Stop</span>
            </button>
          </div>
        </article>
      </section>
    `;
    wireAudioPageControls(payload);
    wireActionButtons(elements.audioRoot, async () => {
      await loadAudio();
      await loadHome();
    });
    showFeedback("");
    setStatus(elements.audioStatus, `Updated ${formatTime(payload.generated_at)}`);
    scheduleRefresh("audio", Number(payload.refresh_after_seconds || 5));
  } catch (error) {
    elements.audioRoot.innerHTML = renderEmpty(error instanceof Error ? error.message : "Audio unavailable.", "audio");
    wireRetryButtons(elements.audioRoot);
    setStatus(elements.audioStatus, "Audio unavailable.");
  }
}

function renderAudioResultCards(results) {
  if (!Array.isArray(results) || results.length === 0) {
    return renderEmpty("No audio result selected yet. Get the current audiobook or search when the structured search contract lands.");
  }
  return results.map((item) => {
    const resultId = String(item.result_id || "");
    const selected = resultId && resultId === state.audioSelectedResultId;
    const type = String(item.type || "audio");
    const progressPercent = formatPercentFromSeconds(item.position_seconds, item.duration_seconds);
    return `
      <button class="audio-result-card ${selected ? "is-selected" : ""}" type="button" data-audio-result="${escapeHtml(resultId)}">
        <span class="audio-result-card__art">
          ${
            item.art_url
              ? `<img src="${escapeHtml(item.art_url)}" alt="" loading="lazy">`
              : `<span class="material-symbols-outlined">${type === "music" ? "album" : "menu_book"}</span>`
          }
        </span>
        <span class="audio-result-card__main">
          <strong>${escapeHtml(item.title || "Untitled audio")}</strong>
          <span>${escapeHtml(item.subtitle || "Unknown creator")}</span>
          ${
            item.position_seconds != null && item.duration_seconds != null
              ? `<span class="audio-result-card__progress"><i style="width: ${progressPercent}%"></i></span>`
              : ""
          }
        </span>
        <span class="audio-result-card__meta">
          <span class="audio-kind-badge">${escapeHtml(type === "music" ? "Music" : "Audiobook")}</span>
          <span class="audio-select-dot"></span>
        </span>
      </button>
    `;
  }).join("");
}

function wireAudioPageControls(payload) {
  const searchInput = elements.audioRoot.querySelector("#audio-search-input");
  searchInput?.addEventListener("input", () => {
    state.audioSearchQuery = searchInput.value;
  });

  const userSelect = elements.audioRoot.querySelector("#audio-user-select");
  userSelect?.addEventListener("change", () => {
    state.audioUser = userSelect.value;
    state.audioSelectedResultId = "";
    state.audioSearchResults = [];
    void loadAudio();
  });

  for (const button of elements.audioRoot.querySelectorAll("[data-audio-result]")) {
    button.addEventListener("click", () => {
      state.audioSelectedResultId = button.getAttribute("data-audio-result") || "";
      void loadAudio();
    });
  }

  for (const button of elements.audioRoot.querySelectorAll("[data-audio-target]")) {
    button.addEventListener("click", () => {
      state.audioSource = button.getAttribute("data-audio-target") || "";
      void loadAudio();
    });
  }

  for (const button of elements.audioRoot.querySelectorAll("[data-audio-timer]")) {
    button.addEventListener("click", () => {
      if (payload.sleep_timer?.supported === false) {
        showFeedback("Sleep timer support needs the structured Audio play/timer contract.", "warn");
        return;
      }
      const selected = getSelectedAudioResult();
      if (selected?.type !== "audiobook") {
        showFeedback("Sleep timers are audiobook-only in this pass.", "warn");
        return;
      }
      state.audioTimerMinutes = button.getAttribute("data-audio-timer") || "0";
      if (state.audioTimerMinutes === "0") {
        state.audioCustomTimerMinutes = "";
      }
      void loadAudio();
    });
  }

  const customTimerInput = elements.audioRoot.querySelector("#audio-custom-timer");
  customTimerInput?.addEventListener("input", () => {
    const rawValue = String(customTimerInput.value || "").trim();
    state.audioCustomTimerMinutes = rawValue;
    if (!rawValue) {
      return;
    }
    const minutes = Math.max(1, Math.min(240, Math.round(Number(rawValue) || 0)));
    if (minutes > 0) {
      state.audioTimerMinutes = String(minutes);
    }
  });

  elements.audioRoot.querySelector("[data-audio-current]")?.addEventListener("click", () => {
    state.audioSelectedResultId = "";
    state.audioSearchResults = [];
    state.audioSearchQuery = "";
    showFeedback("Current audiobook loaded for the selected user.");
    void loadAudio();
  });

  for (const button of elements.audioRoot.querySelectorAll("[data-audio-search]")) {
    button.addEventListener("click", async () => {
      const kind = button.getAttribute("data-audio-search") || "";
      const query = String(searchInput?.value || state.audioSearchQuery || "").trim();
      state.audioSearchQuery = query;
      if (!query) {
        showFeedback("Enter search text first.", "warn");
        return;
      }
      try {
        const searchPayload = await postJson("/api/ui/audio/search", {
          client_id: "browser-house",
          kind,
          query,
          user_id: state.audioUser || undefined,
          limit: 12,
        });
        state.audioSearchResults = Array.isArray(searchPayload.results) ? searchPayload.results : [];
        state.audioSelectedResultId = state.audioSearchResults[0]?.result_id || "";
        showFeedback(`${state.audioSearchResults.length} ${kind} result${state.audioSearchResults.length === 1 ? "" : "s"} loaded.`);
        void loadAudio();
      } catch (error) {
        showFeedback(error instanceof Error ? error.message : "Audio search failed.", "error");
      }
    });
  }

  elements.audioRoot.querySelector("[data-audio-play]")?.addEventListener("click", async () => {
    const selected = getSelectedAudioResult();
    if (!selected) {
      showFeedback("Select an audio result first.", "warn");
      return;
    }
    try {
      const timerMinutes = Math.max(0, Math.min(240, Math.round(Number(state.audioTimerMinutes || 0) || 0)));
      const playPayload = await postJson("/api/ui/audio/play", {
        client_id: "browser-house",
        target: state.audioSource,
        user_id: state.audioUser || undefined,
        result: selected,
        sleep_timer_minutes: selected.type === "audiobook" && timerMinutes > 0 ? timerMinutes : undefined,
      });
      showFeedback(playPayload.ok ? "Playback sent to target." : "Playback request failed.", playPayload.ok ? "success" : "error");
      await loadAudio();
      await loadHome();
    } catch (error) {
      showFeedback(error instanceof Error ? error.message : "Audio playback failed.", "error");
    }
  });

  for (const button of elements.audioRoot.querySelectorAll("[data-audio-control]")) {
    button.addEventListener("click", async () => {
      const operation = button.getAttribute("data-audio-control") || "";
      const owner = payload?.playback?.output_owner || payload?.now_playing || {};
      const mediaKind = String(owner.media_kind || "").trim().toLowerCase();
      if (!operation || !mediaKind) {
        showFeedback("No active audio is available on this target.", "warn");
        return;
      }
      try {
        const controlPayload = await postJson("/api/ui/audio/control", {
          client_id: "browser-house",
          target: state.audioSource,
          operation,
          media_kind: mediaKind,
        });
        const label = operation === "pause" ? "paused" : "stopped";
        showFeedback(controlPayload.ok ? `Audio ${label}.` : `Audio ${operation} failed.`, controlPayload.ok ? "success" : "error");
        await loadAudio();
        await loadHome();
      } catch (error) {
        showFeedback(error instanceof Error ? error.message : `Audio ${operation} failed.`, "error");
      }
    });
  }
}

function getSelectedAudioResult() {
  const resultId = state.audioSelectedResultId;
  const candidates = Array.isArray(state.audioResults) ? state.audioResults : [];
  return candidates.find((item) => item.result_id === resultId) || null;
}

async function loadHouse() {
  setStatus(elements.houseStatus, elements.houseRoot.children.length > 0 ? "Refreshing House snapshot." : "Loading House snapshot.");
  try {
    const payload = await fetchJson("/api/ui/house", { retries: 3 });
    elements.houseRoot.innerHTML = `
      <section class="house-overview">
        ${renderHouseEntryPoint(payload.front_door)}
        <article class="house-overview__bento">
          ${renderHouseOverviewTiles(payload.temperatures, payload.climate)}
        </article>
      </section>

      <section class="house-section">
        <div class="house-section__head">
          <h4><span class="house-section__accent"></span>Light Orchestration</h4>
        </div>
        <div class="house-light-grid">
          ${renderHouseLightTiles(payload.lights)}
        </div>
      </section>

      <section class="house-section">
        <div class="house-section__head">
          <h4><span class="house-section__accent house-section__accent--danger"></span>Security Perimeter</h4>
          <span class="status-pill status-pill--warn">${Array.isArray(payload.cameras) ? payload.cameras.length : 0} nodes</span>
        </div>
        <div class="house-security-grid">
          ${renderHouseCameraTiles(payload.cameras)}
        </div>
      </section>

      <section class="house-section">
        <div class="house-section__head">
          <h4><span class="house-section__accent house-section__accent--soft"></span>Interactive Climate Controls</h4>
        </div>
        <div class="house-climate-grid">
          ${renderHouseClimateControls(payload.climate)}
        </div>
      </section>

      ${payload.notice ? `<div class="notice">${escapeHtml(payload.notice)}</div>` : ""}
    `;
    wireActionButtons(elements.houseRoot, async () => {
      await loadHouse();
      await loadHome();
    });
    showFeedback("");
    setStatus(elements.houseStatus, `Updated ${formatTime(payload.generated_at)}`);
    scheduleRefresh("house", Number(payload.refresh_after_seconds || 30));
  } catch (error) {
    const message = error instanceof Error ? error.message : "House unavailable.";
    if (elements.houseRoot.children.length > 0) {
      setStatus(elements.houseStatus, `Refresh failed: ${message}`);
      scheduleRefresh("house", 30);
      return;
    }
    elements.houseRoot.innerHTML = renderEmpty(message, "house");
    wireRetryButtons(elements.houseRoot);
    setStatus(elements.houseStatus, "House unavailable.");
  }
}

async function loadInternet() {
  setStatus(
    elements.internetStatus,
    elements.internetRoot.children.length > 0 ? "Refreshing Internet health." : "Checking Internet health.",
  );
  try {
    const payload = await fetchJson("/api/ui/internet", { retries: 3 });
    renderInternet(payload, state.internetPreview, state.internetRun);
    setStatus(elements.internetStatus, `Updated ${formatTime(payload.generated_at)}`);
    scheduleRefresh("internet", Number(payload.refresh_after_seconds || 30));
  } catch (error) {
    const message = error instanceof Error ? error.message : "Internet health unavailable.";
    elements.internetRoot.innerHTML = renderEmpty(message, "internet");
    wireRetryButtons(elements.internetRoot);
    setStatus(elements.internetStatus, "Internet health unavailable.");
  }
}

function renderInternet(payload, preview, run) {
  const recovery = payload.recovery || {};
  const categories = Array.isArray(payload.categories) ? payload.categories : [];
  elements.internetRoot.innerHTML = `
    <section class="internet-hero panel-card">
      <div>
        <p class="card-kicker">Household connection</p>
        <h4>${escapeHtml(payload.summary || "Internet health is unknown.")}</h4>
        <p class="small-copy">Oracle checks the connection, satellites, services, and network equipment. It only shows individual items here when something looks unhealthy.</p>
      </div>
      <div class="internet-hero__action">
        <span class="status-pill status-pill--${statusTone(payload.status)}">${escapeHtml(payload.status || "unknown")}</span>
        <button
          class="action-button"
          type="button"
          data-internet-preview="true"
          ${recovery.preview_available ? "" : "disabled"}
        >
          <span class="material-symbols-outlined">troubleshoot</span>
          <span>Find and fix internet problems</span>
        </button>
      </div>
    </section>

    <section class="internet-grid">
      ${categories.map(renderInternetCategory).join("")}
    </section>

    ${preview ? renderInternetPreview(preview) : ""}
    ${run ? renderInternetRun(run) : ""}
  `;
  wireInternetPreviewButton();
  wireInternetApprovalButton();
}

function renderInternetCategory(category) {
  const items = Array.isArray(category.items) ? category.items : [];
  return `
    <article class="internet-category panel-card">
      <div class="card-head">
        <div>
          <p class="card-kicker">${escapeHtml(category.total || 0)} checked / ${escapeHtml(category.unhealthy || 0)} problems</p>
          <h4>${escapeHtml(category.label || "Network")}</h4>
        </div>
        <span class="status-pill status-pill--${statusTone(category.status)}">${escapeHtml(category.status || "unknown")}</span>
      </div>
      <div class="internet-category__items">
        ${items.length > 0 ? items.map((item) => `
          <div class="internet-health-row">
            <div>
              <strong>${escapeHtml(item.display_name || item.id || "Unknown")}</strong>
              <p class="small-copy">${escapeHtml(item.summary || "Status unknown.")}</p>
            </div>
            <span class="status-pill status-pill--${statusTone(item.status)}">${escapeHtml(item.status || "unknown")}</span>
          </div>
        `).join("") : '<p class="small-copy">No unhealthy items in this group.</p>'}
      </div>
    </article>
  `;
}

function renderInternetPreview(preview) {
  const findings = Array.isArray(preview.findings) ? preview.findings : [];
  const steps = Array.isArray(preview.steps) ? preview.steps : [];
  return `
    <section class="internet-preview panel-card">
      <div class="card-head">
        <div>
          <p class="card-kicker">Frozen diagnostic preview</p>
          <h4>${escapeHtml(preview.display_name || "Fix the Internet")}</h4>
        </div>
        <span class="status-pill status-pill--${steps.length > 0 ? "warn" : "ok"}">${escapeHtml(preview.status || "ready")}</span>
      </div>
      <p class="small-copy">${escapeHtml(preview.approval_summary || preview.notice || "")}</p>
      <div class="internet-preview__meta">
        <span>Expires ${escapeHtml(formatTime(preview.expires_at))}</span>
        ${preview.estimated_total_duration ? `<span>Total ${escapeHtml(preview.estimated_total_duration)}</span>` : ""}
        <span>Plan ${escapeHtml(String(preview.digest || "").slice(0, 12))}</span>
      </div>
      <div class="internet-preview__columns">
        <div>
          <h5>Findings</h5>
          ${findings.length > 0 ? findings.map((finding) => `
            <div class="internet-preview__item">
              <strong>${escapeHtml(finding.display_name || finding.target_id || "Unknown")}</strong>
              <span>${escapeHtml(finding.status || "unknown")}</span>
            </div>
          `).join("") : '<p class="small-copy">No degraded findings.</p>'}
        </div>
        <div>
          <h5>Possible actions</h5>
          ${steps.length > 0 ? steps.map((step, index) => `
            <div class="internet-preview__step">
              <span>${index + 1}</span>
              <div>
                <strong>${escapeHtml(step.target_label || step.target_id || "Target")}</strong>
                <p>${escapeHtml(step.plain_language_summary || step.description || step.action_id || "Policy action")}</p>
                ${step.user_effect ? `<small>${escapeHtml(step.user_effect)}</small>` : ""}
                ${step.estimated_duration ? `<small>Estimated time: ${escapeHtml(step.estimated_duration)}</small>` : ""}
                <small>${escapeHtml(step.condition || "")}</small>
              </div>
            </div>
          `).join("") : '<p class="small-copy">Oracle found no enabled policy actions to propose.</p>'}
        </div>
      </div>
      ${
        preview.approval_available && steps.length > 0
          ? `
            <div class="internet-preview__approval">
              <p class="small-copy">One approval covers only the actions listed above. Oracle will re-check first, skip recovered items, and stop if a different fix is needed.</p>
              <button class="action-button" type="button" data-internet-approve="true">
                <span class="material-symbols-outlined">verified_user</span>
                <span>Approve these fixes</span>
              </button>
            </div>
          `
          : '<div class="notice">No repair action needs approval.</div>'
      }
    </section>
  `;
}

function renderInternetRun(run) {
  const steps = Array.isArray(run.steps) ? run.steps : [];
  const tone = run.status === "completed" ? "ok" : run.status === "completed_with_issues" ? "warn" : "danger";
  return `
    <section class="internet-run panel-card">
      <div class="card-head">
        <div>
          <p class="card-kicker">Recovery result</p>
          <h4>${escapeHtml(run.summary || "Recovery finished.")}</h4>
        </div>
        <span class="status-pill status-pill--${tone}">${escapeHtml(run.status || "unknown")}</span>
      </div>
      <div class="internet-run__steps">
        ${steps.length > 0 ? steps.map((step) => `
          <div class="internet-run__step">
            <div>
              <strong>${escapeHtml(step.target_label || "Network target")}</strong>
              <p class="small-copy">${escapeHtml(step.summary || step.action_id || "Action completed.")}</p>
            </div>
            <span class="status-pill status-pill--${step.status === "executed" || step.status === "skipped" ? "ok" : "danger"}">${escapeHtml(step.status || "unknown")}</span>
          </div>
        `).join("") : '<p class="small-copy">No actions were executed.</p>'}
      </div>
    </section>
  `;
}

function wireInternetPreviewButton() {
  const button = elements.internetRoot.querySelector("[data-internet-preview]");
  if (!button) {
    return;
  }
  button.addEventListener("click", async () => {
    button.disabled = true;
    const original = button.innerHTML;
    button.innerHTML = "<span>Checking...</span>";
    try {
      const payload = await postJson("/api/ui/orchestrations/fix_internet/preview", {
        client_id: "browser-house-ui",
      });
      state.internetPreview = payload.preview || null;
      state.internetRun = null;
      await loadInternet();
    } catch (error) {
      showFeedback(error instanceof Error ? error.message : "Unable to build a recovery preview.", "error");
      button.disabled = false;
      button.innerHTML = original;
    }
  });
}

function wireInternetApprovalButton() {
  const button = elements.internetRoot.querySelector("[data-internet-approve]");
  const preview = state.internetPreview;
  if (!button || !preview) {
    return;
  }
  button.addEventListener("click", async () => {
    const confirmed = globalThis.confirm(buildInternetApprovalPrompt(preview));
    if (!confirmed) {
      return;
    }
    button.disabled = true;
    button.innerHTML = "<span>Running approved fixes...</span>";
    try {
      const payload = await postJson("/api/ui/orchestrations/fix_internet/approve", {
        client_id: "browser-house-ui",
        preview_id: preview.preview_id,
        digest: preview.digest,
        approved: true,
      });
      state.internetRun = payload.run || null;
      state.internetPreview = null;
      await loadInternet();
      showFeedback(payload.run?.summary || "Recovery finished.", payload.ok ? "success" : "error");
    } catch (error) {
      showFeedback(error instanceof Error ? error.message : "Recovery approval failed.", "error");
      button.disabled = false;
      button.innerHTML = "<span>Approve these fixes</span>";
    }
  });
}

function buildInternetApprovalPrompt(preview) {
  const steps = Array.isArray(preview?.steps) ? preview.steps : [];
  const lines = [
    preview?.approval_summary || "Approve these possible fixes?",
    "",
    "Oracle will re-check first, skip anything already fixed, and stop if a different fix is needed.",
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

function renderHouseEntryPoint(frontDoor) {
  const item = frontDoor || {};
  const openLabel = item.open_state && item.open_state !== "unknown" ? item.open_state : "status unknown";
  const lockLabel = item.lock_state && item.lock_state !== "unknown" ? item.lock_state : "lock unknown";
  const tone = item.lock_state === "locked" ? "ok" : item.lock_state === "unlocked" ? "warn" : "muted";
  return `
    <article class="house-entry-card">
      <div class="house-entry-card__top">
        <div>
          <p class="card-kicker">Entry Point</p>
          <h4>${escapeHtml(item.label || "Entry")}</h4>
        </div>
        <div class="house-entry-card__icon house-entry-card__icon--${tone}">
          <span class="material-symbols-outlined">${item.lock_state === "locked" ? "lock" : "lock_open_right"}</span>
        </div>
      </div>
      <div class="house-entry-card__bottom">
        <div>
          <p class="house-entry-card__state">${escapeHtml(lockLabel.charAt(0).toUpperCase() + lockLabel.slice(1))}</p>
          <p class="small-copy">${escapeHtml(openLabel.charAt(0).toUpperCase() + openLabel.slice(1))}</p>
        </div>
        ${
          item.action
            ? `
              <button
                class="action-button--secondary house-entry-card__action"
                type="button"
                data-action-id="${escapeHtml(item.action.action_id)}"
              >
                ${escapeHtml(item.action.label || "Control")}
              </button>
            `
            : ""
        }
      </div>
    </article>
  `;
}

function houseOverviewTileIcon(label) {
  const normalized = String(label || "").toLowerCase();
  if (normalized.includes("upstairs")) return "roofing";
  if (normalized.includes("downstairs")) return "meeting_room";
  if (normalized.includes("air") || normalized.includes("ac")) return "air";
  if (normalized.includes("bed")) return "bed";
  return "device_thermostat";
}

function renderHouseOverviewTiles(temperatures, climateItems) {
  const tiles = Array.isArray(temperatures) ? temperatures.map((item) => ({
    label: item.label || item.entity_id || "Temperature",
    value: item.value_f,
    available: item.available,
  })) : [];
  if (tiles.length === 0) {
    return renderEmpty("Temperature helpers unavailable.");
  }
  return tiles.slice(0, 4).map((item) => `
    <div class="house-overview-tile">
      <div class="house-overview-tile__top">
        <div>
          <p class="card-kicker">Temperature Helper</p>
          <h4>${escapeHtml(item.label)}</h4>
        </div>
        <div class="house-overview-tile__icon">
          <span class="material-symbols-outlined">${houseOverviewTileIcon(item.label)}</span>
        </div>
      </div>
      <div class="house-overview-tile__bottom">
        <p class="house-overview-tile__value">${formatTemperature(item.value)}${item.value != null ? "°" : ""}</p>
        <p class="small-copy">${escapeHtml(item.available ? "Available now" : "Unavailable")}</p>
      </div>
    </div>
  `).join("");
}

function renderHouseLightTiles(items) {
  if (!Array.isArray(items) || items.length === 0) {
    return renderEmpty("Light status unavailable.");
  }
  return items.map((item) => {
    const active = String(item.state || "").toLowerCase() === "on";
    const available = Boolean(item.available);
    const tone = !available ? "unavailable" : active ? "ok" : "muted";
    const normalizedLabel = String(item.label || "").toLowerCase();
    const icon = normalizedLabel.includes("bed")
      ? "king_bed"
      : normalizedLabel.includes("living")
        ? "weekend"
        : normalizedLabel.includes("stairs")
          ? "stairs"
          : normalizedLabel.includes("bathroom")
            ? "bathtub"
            : normalizedLabel.includes("porch")
              ? "deck"
              : "lightbulb";
    const stateLabel = !available ? "Unavailable" : active ? "On" : "Off";
    return `
      <article class="house-light-tile house-light-tile--${tone}">
        <div class="house-light-tile__icon">
          <span class="material-symbols-outlined">${icon}</span>
        </div>
        <div>
          <p class="house-light-tile__label">${escapeHtml(item.label || item.entity_id || "Light")}</p>
          <span class="house-light-tile__status house-light-tile__status--${tone}">${escapeHtml(stateLabel)}</span>
        </div>
        ${
          available && Array.isArray(item.actions) && item.actions[0]
            ? `
              <button
                class="ghost-button house-light-tile__action"
                type="button"
                data-action-id="${escapeHtml(item.actions[0].action_id)}"
              >
                ${escapeHtml(item.actions[0].label || "Toggle")}
              </button>
            `
            : ""
        }
      </article>
    `;
  }).join("");
}

function renderHouseCameraTiles(items) {
  if (!Array.isArray(items) || items.length === 0) {
    return renderEmpty("Camera inventory unavailable.");
  }
  return items.map((item) => {
    const label = item.label || item.entity_id || "Camera";
    const snapshotUrl = item.snapshot_available && item.snapshot_url ? String(item.snapshot_url) : "";
    return `
      <article class="house-camera-tile">
        <div class="house-camera-tile__frame ${snapshotUrl ? "has-snapshot" : ""}">
          ${
            snapshotUrl
              ? `<img class="house-camera-tile__image" src="${escapeHtml(snapshotUrl)}" alt="${escapeHtml(`${label} camera snapshot`)}" loading="lazy">`
              : `<div class="house-camera-tile__placeholder"><span class="material-symbols-outlined">videocam_off</span><span>Snapshot unavailable</span></div>`
          }
          <div class="house-camera-tile__overlay">
            <div class="house-camera-tile__tag">
              <span class="house-camera-tile__dot"></span>
              <span>${escapeHtml(label)}</span>
            </div>
            <p class="small-copy">${escapeHtml((item.state || "unknown").toUpperCase())}</p>
          </div>
        </div>
        <div class="house-camera-tile__footer">
          <strong>${escapeHtml(label)}</strong>
          <span class="status-pill ${snapshotUrl ? "status-pill--ok" : "status-pill--warn"}">${snapshotUrl ? "Still snapshot" : "Inventory only"}</span>
        </div>
      </article>
    `;
  }).join("");
}

function renderHouseClimateControls(items) {
  if (!Array.isArray(items) || items.length === 0) {
    return renderEmpty("Climate status unavailable.");
  }
  return items.map((item) => {
    const normalizedMode = String(item.hvac_action || item.state || "").toLowerCase();
    const isCooling = normalizedMode.includes("cool");
    const isHeating = normalizedMode.includes("heat");
    const climateTone = isCooling ? "cool" : "warm";
    const climateIcon = isCooling ? "air" : isHeating ? "mode_heat" : "thermostat";
    const climateModeLabel = isCooling ? "Cooling" : isHeating ? "Heating" : "Idle";
    const cooler = Array.isArray(item.actions) ? item.actions.find((action) => /cooler$/.test(String(action.action_id || ""))) : null;
    const warmer = Array.isArray(item.actions) ? item.actions.find((action) => /warmer$/.test(String(action.action_id || ""))) : null;
    return `
      <article class="house-climate-card house-climate-card--${climateTone}">
        <div class="house-climate-card__head">
          <span class="card-kicker">${escapeHtml(item.label || item.entity_id || "Climate")}</span>
          <span class="material-symbols-outlined">${climateIcon}</span>
        </div>
        <div class="house-climate-card__dial">
          <div class="house-climate-card__dial-inner">
            <p class="house-climate-card__value">${formatTemperature(item.target_temperature_f)}${item.target_temperature_f != null ? "°" : ""}</p>
            <p class="small-copy">Target</p>
          </div>
        </div>
        <div class="house-climate-card__meta">
          <div>
            <span class="metric-label">System</span>
            <strong>${escapeHtml(climateModeLabel)}</strong>
          </div>
          <div>
            <span class="metric-label">Current</span>
            <strong>${formatTemperature(item.current_temperature_f)}${item.current_temperature_f != null ? "°" : ""}</strong>
          </div>
        </div>
        <div class="house-climate-card__actions">
          ${
            cooler
              ? `<button class="ghost-button house-climate-card__button" type="button" data-action-id="${escapeHtml(cooler.action_id)}" aria-label="${escapeHtml(cooler.label || "Cooler")}"><span class="material-symbols-outlined" aria-hidden="true">remove</span></button>`
              : ""
          }
          ${
            warmer
              ? `<button class="ghost-button house-climate-card__button" type="button" data-action-id="${escapeHtml(warmer.action_id)}" aria-label="${escapeHtml(warmer.label || "Warmer")}"><span class="material-symbols-outlined" aria-hidden="true">add</span></button>`
              : ""
          }
        </div>
      </article>
    `;
  }).join("");
}

function wirePageSwitchButtons(root) {
  for (const button of root.querySelectorAll("[data-switch-page]")) {
    button.addEventListener("click", () => {
      const page = button.dataset.switchPage || "home";
      switchPage(page);
    });
  }
}

function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds)) || Number(seconds) <= 0) {
    return "--";
  }
  const total = Math.round(Number(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remaining = total % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`;
  }
  return `${minutes}:${String(remaining).padStart(2, "0")}`;
}
