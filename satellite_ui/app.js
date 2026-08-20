const state = {
  satelliteId: "",
  sourceId: "",
  clientId: "",
  config: null,
  currentPage: "home",
  clockTimer: 0,
  headerAudioTimer: 0,
  pageRefreshTimer: 0,
  headerAudio: null,
  headerAudioLoading: false,
  pageSnapshots: {},
  pageSnapshotLoadedAt: {},
  pageLoadSeq: 0,
  mediaSearch: {
    music: null,
    audiobooks: null,
  },
  mediaPlayStatus: {
    music: null,
    audiobooks: null,
  },
  currentAudiobookResult: null,
  selectedAudiobookUser: "",
  pendingAudiobookSleepTimerMinutes: 0,
  audiobookPlaybackActive: false,
  voice: {
    status: "ready",
    detail: "Tap to talk",
    recorder: null,
    stream: null,
    chunks: [],
    audioContext: null,
    audioSource: null,
    audioProcessor: null,
    audioSilence: null,
    pcmChunks: [],
    inputSampleRate: 0,
    timeoutId: 0,
    sessionId: "",
    lastInterimEventId: 0,
    audio: null,
    audioUrl: "",
    debug: {
      events: [],
      lastTranscript: "",
      lastReplyText: "",
      lastTtsBytes: 0,
      lastPlayback: null,
    },
  },
};

const elements = {
  title: document.querySelector("#sat-title"),
  room: document.querySelector("#sat-room"),
  pageRoot: document.querySelector("#page-root"),
  voiceChip: document.querySelector("#voice-chip"),
  pttButton: document.querySelector("#ptt-button"),
  pttLabel: document.querySelector("#ptt-label"),
  headerPlayer: document.querySelector("#header-player"),
  bottomNav: document.querySelector("#bottom-nav"),
  navButtons: [],
};

const CLIENT_TIMEOUT_MS = 8000;
const LIVE_CONTROL_PAGES = new Set(["home", "house"]);
const LIVE_CONTROL_PAGE_MAX_STALE_MS = 3000;
const DEFAULT_PAGE_REFRESH_SECONDS = {
  home: 30,
  house: 30,
  audio: 5,
  music: 5,
  audiobooks: 5,
  calendar: 120,
  weather: 300,
};

initialize().catch((error) => {
  renderFatal(error instanceof Error ? error.message : "Unable to load satellite UI.");
});

async function initialize() {
  bindPushToTalk();
  state.config = await apiGet(`/api/satellite/config${buildSatelliteQuery()}`);
  state.satelliteId = String(state.config.satellite_id || "");
  state.sourceId = String(state.config.source_id || state.satelliteId);
  state.clientId = `satellite-ui-${state.satelliteId.replaceAll("_", "-")}`;
  state.voice.sessionId = globalThis.crypto?.randomUUID?.() || `satellite-ui-${Date.now()}`;
  elements.title.textContent = state.config.display_name || "Oracle";
  elements.room.textContent = state.config.room || "Satellite";
  renderBottomNavigation();
  highlightNav();
  setVoiceState("ready", "Tap to talk");
  startClock();
  startHeaderAudioPolling();
  await loadCurrentPage();
}

function startClock() {
  clearInterval(state.clockTimer);
  state.clockTimer = globalThis.setInterval(() => {
    if (state.currentPage === "home") {
      updateClockText();
    }
    if (state.currentPage === "audiobooks") {
      updateSleepTimerCountdown();
    }
  }, 1000);
}

function updateClockText() {
  const timeElement = document.querySelector("[data-clock-time]");
  const dateElement = document.querySelector("[data-clock-date]");
  if (!timeElement || !dateElement) {
    return;
  }
  const now = new Date();
  timeElement.textContent = new Intl.DateTimeFormat([], { hour: "numeric", minute: "2-digit" }).format(now);
  dateElement.textContent = new Intl.DateTimeFormat([], { weekday: "long", month: "short", day: "numeric" }).format(now);
}

function startHeaderAudioPolling() {
  clearInterval(state.headerAudioTimer);
  void refreshHeaderAudio();
  state.headerAudioTimer = globalThis.setInterval(() => {
    void refreshHeaderAudio();
  }, 5000);
}

async function refreshHeaderAudio() {
  if (!state.sourceId || state.headerAudioLoading) {
    return;
  }
  state.headerAudioLoading = true;
  try {
    const payload = await apiGet(`/api/ui/audio?source=${encodeURIComponent(state.sourceId)}`);
    state.headerAudio = buildHeaderAudioState(payload);
    renderHeaderAudio();
  } catch (error) {
    noteVoiceEvent("header_audio_refresh_failed", {
      message: error instanceof Error ? error.message : String(error),
    });
  } finally {
    state.headerAudioLoading = false;
  }
}

function buildHeaderAudioState(payload) {
  const playback = payload?.playback || {};
  const outputOwner = playback.output_owner || payload?.now_playing || null;
  if (playback.active !== true || !outputOwner) {
    return null;
  }
  const mediaKind = String(outputOwner.media_kind || outputOwner.media_type || "").trim().toLowerCase();
  return {
    title: String(outputOwner.title || "Audio playing").trim(),
    subtitle: String(outputOwner.artist_or_author || outputOwner.album || outputOwner.backend_type || "").trim(),
    mediaKind: mediaKind || null,
    icon: mediaKind === "audiobook" ? "book" : mediaKind === "music" ? "music_note" : "graphic_eq",
    label: mediaKind === "audiobook" ? "Story" : mediaKind === "music" ? "Music" : "Audio",
  };
}

function renderHeaderAudio() {
  if (!elements.headerPlayer) {
    return;
  }
  const audio = state.headerAudio;
  if (!audio) {
    elements.headerPlayer.innerHTML = "";
    elements.headerPlayer.classList.remove("is-active");
    return;
  }
  elements.headerPlayer.classList.add("is-active");
  elements.headerPlayer.innerHTML = `
    <div class="header-player__meta">
      <span class="material-symbols-outlined header-player__icon">${escapeHtml(audio.icon)}</span>
      <span class="header-player__copy">
        <span class="header-player__label">${escapeHtml(audio.label)}</span>
        <span class="header-player__title">${escapeHtml(audio.title)}</span>
        ${audio.subtitle ? `<span class="header-player__subtitle">${escapeHtml(audio.subtitle)}</span>` : ""}
      </span>
    </div>
    <button class="header-player__stop" type="button" data-header-audio-stop="true" aria-label="Stop current audio">
      <span class="material-symbols-outlined">stop</span>
      <span>Stop</span>
    </button>
  `;
  const stopButton = elements.headerPlayer.querySelector("[data-header-audio-stop]");
  stopButton?.addEventListener("click", stopHeaderAudio);
}

async function stopHeaderAudio() {
  const mediaKind = state.headerAudio?.mediaKind || null;
  try {
    await stopSatelliteAudio(mediaKind);
    state.headerAudio = null;
    renderHeaderAudio();
    await refreshHeaderAudio();
    if (state.currentPage === "audio" || state.currentPage === "music" || state.currentPage === "audiobooks") {
      await reloadCurrentPage();
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : "Audio stop failed.";
    setVoiceState("error", friendlyVoiceError(message));
  }
}

function buildSatelliteQuery() {
  const params = new URLSearchParams(globalThis.location.search);
  const satelliteId = String(params.get("satellite_id") || "").trim();
  return satelliteId ? `?satellite_id=${encodeURIComponent(satelliteId)}` : "";
}

function bindNavigation() {
  elements.navButtons = Array.from(document.querySelectorAll("[data-page]"));
  for (const button of elements.navButtons) {
    button.addEventListener("click", async () => {
      const nextPage = button.dataset.page || "home";
      clearSearchResultsForNavigation(state.currentPage, nextPage);
      state.currentPage = nextPage;
      highlightNav();
      await loadCurrentPage();
    });
  }
}

function clearSearchResultsForNavigation(previousPage, nextPage) {
  if (previousPage === nextPage) {
    return;
  }
  if (previousPage === "music" || previousPage === "audiobooks") {
    state.mediaSearch[previousPage] = null;
    state.mediaPlayStatus[previousPage] = null;
  }
  if (previousPage === "audiobooks") {
    state.pendingAudiobookSleepTimerMinutes = 0;
  }
}

function renderBottomNavigation() {
  const navItems = configuredBottomNav();
  state.currentPage = navItems.some((item) => item.id === state.currentPage) ? state.currentPage : navItems[0]?.id || "home";
  if (!elements.bottomNav) {
    return;
  }
  elements.bottomNav.innerHTML = navItems.map((item) => `
    <button class="bottom-nav__button" data-page="${escapeHtml(item.id)}" type="button">
      <span class="material-symbols-outlined">${escapeHtml(item.icon)}</span>
      <span>${escapeHtml(item.label)}</span>
    </button>
  `).join("");
  bindNavigation();
}

function configuredBottomNav() {
  const configured = Array.isArray(state.config?.profile?.bottom_nav) ? state.config.profile.bottom_nav : [];
  const fallbackPages = Array.isArray(state.config?.profile?.pages) ? state.config.profile.pages : ["home", "weather", "calendar", "audio", "house"];
  const rawItems = configured.length
    ? configured
    : fallbackPages.map((page) => ({ id: page, label: pageLabel(page), icon: pageIcon(page) }));
  const output = [];
  const seen = new Set();
  for (const rawItem of rawItems) {
    const id = String(rawItem?.id || rawItem || "").trim().toLowerCase();
    if (!id || seen.has(id) || !pageRenderers[id]) {
      continue;
    }
    seen.add(id);
    output.push({
      id,
      label: String(rawItem?.label || pageLabel(id)),
      icon: String(rawItem?.icon || pageIcon(id)),
    });
  }
  return output.length ? output : [{ id: "home", label: "Home", icon: "home" }];
}

const pageLoaders = {
  home: async () => apiGet(`/api/ui/satellite/home?satellite_id=${encodeURIComponent(state.satelliteId)}`),
  weather: async () => apiGet("/api/ui/weather"),
  calendar: async () => apiGet("/api/ui/calendar"),
  audio: async () => apiGet(`/api/ui/audio?source=${encodeURIComponent(state.sourceId)}`),
  music: async () => apiGet(`/api/ui/audio?source=${encodeURIComponent(state.sourceId)}`),
  audiobooks: async () => apiGet(`/api/ui/audio?source=${encodeURIComponent(state.sourceId)}`),
  house: async () => apiGet("/api/ui/house"),
};

const pageRenderers = {
  home: renderHome,
  weather: renderWeather,
  calendar: renderCalendar,
  audio: renderAudio,
  music: renderMusic,
  audiobooks: renderAudiobooks,
  house: renderHouse,
};

function pageLabel(page) {
  return {
    home: "Home",
    weather: "Weather",
    calendar: "Calendar",
    audio: "Audio",
    music: "Music",
    audiobooks: "Audiobooks",
    house: "House",
  }[page] || page;
}

function pageIcon(page) {
  return {
    home: "home",
    weather: "cloud",
    calendar: "calendar_today",
    audio: "speaker",
    music: "music_note",
    audiobooks: "book",
    house: "house",
  }[page] || "circle";
}

function highlightNav() {
  for (const button of elements.navButtons) {
    button.classList.toggle("is-active", button.dataset.page === state.currentPage);
  }
  elements.pageRoot.dataset.page = state.currentPage;
}

function bindPushToTalk() {
  elements.pttButton?.addEventListener("click", async () => {
    if (state.voice.status === "listening") {
      await stopListening();
      return;
    }
    await startListening();
  });
}

function setVoiceState(status, detail) {
  state.voice.status = status;
  state.voice.detail = detail;
  const labels = {
    ready: "Ready",
    listening: "Listening",
    processing: "Processing",
    speaking: "Speaking",
    muted: "Muted",
    error: "Unavailable",
  };
  const chipLabel = labels[status] || "Ready";
  elements.voiceChip.textContent = chipLabel;
  elements.voiceChip.className = `voice-chip voice-chip--${status}`;
  elements.pttButton.className = `talk-card talk-card--${status}`;
  elements.pttLabel.textContent =
    status === "error" ? detail : status === "listening" ? "Tap again to send" : chipLabel === "Ready" ? "Tap to talk" : chipLabel;
  exposeVoiceDebug();
}

function noteVoiceEvent(event, detail = {}) {
  state.voice.debug.events.push({
    at: new Date().toISOString(),
    event,
    detail,
  });
  state.voice.debug.events = state.voice.debug.events.slice(-20);
  exposeVoiceDebug();
}

function exposeVoiceDebug() {
  globalThis.__oracleVoiceDebug = {
    status: state.voice.status,
    detail: state.voice.detail,
    recorderState: state.voice.recorder?.state || null,
    chunkCount: state.voice.chunks.length,
    pcmChunkCount: state.voice.pcmChunks.length,
    inputSampleRate: state.voice.inputSampleRate,
    lastTranscript: state.voice.debug.lastTranscript,
    lastReplyText: state.voice.debug.lastReplyText,
    lastTtsBytes: state.voice.debug.lastTtsBytes,
    lastPlayback: state.voice.debug.lastPlayback,
    events: [...state.voice.debug.events],
  };
}

async function startListening() {
  if (!globalThis.isSecureContext && !["localhost", "127.0.0.1"].includes(globalThis.location.hostname)) {
    setVoiceState("error", "Mic needs HTTPS or localhost.");
    return;
  }
  const AudioContextCtor = globalThis.AudioContext || globalThis.webkitAudioContext;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !AudioContextCtor) {
    setVoiceState("error", "Browser microphone capture is unavailable.");
    return;
  }
  try {
    noteVoiceEvent("listen_start_requested");
    state.voice.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    state.voice.pcmChunks = [];
    state.voice.audioContext = new AudioContextCtor();
    state.voice.inputSampleRate = state.voice.audioContext.sampleRate;
    state.voice.audioSource = state.voice.audioContext.createMediaStreamSource(state.voice.stream);
    state.voice.audioProcessor = state.voice.audioContext.createScriptProcessor(4096, 1, 1);
    state.voice.audioSilence = state.voice.audioContext.createGain();
    state.voice.audioSilence.gain.value = 0;
    state.voice.audioProcessor.onaudioprocess = (event) => {
      const input = event.inputBuffer.getChannelData(0);
      state.voice.pcmChunks.push(new Float32Array(input));
      if (state.voice.pcmChunks.length === 1 || state.voice.pcmChunks.length % 10 === 0) {
        noteVoiceEvent("pcm_chunk", {
          chunks: state.voice.pcmChunks.length,
          samples: input.length,
          sampleRate: state.voice.inputSampleRate,
        });
      }
    };
    state.voice.audioSource.connect(state.voice.audioProcessor);
    state.voice.audioProcessor.connect(state.voice.audioSilence);
    state.voice.audioSilence.connect(state.voice.audioContext.destination);
    setVoiceState("listening", "Listening...");
    noteVoiceEvent("listening");
    state.voice.timeoutId = globalThis.setTimeout(() => {
      void stopListening();
    }, CLIENT_TIMEOUT_MS);
  } catch (error) {
    noteVoiceEvent("listen_start_failed", { message: error instanceof Error ? error.message : String(error) });
    setVoiceState("error", error instanceof Error ? error.message : "Unable to start microphone.");
  }
}

async function stopListening() {
  if (!state.voice.stream && !state.voice.audioContext) {
    setVoiceState("ready", "Tap to talk");
    return;
  }
  clearTimeout(state.voice.timeoutId);
  setVoiceState("processing", "Processing...");
  noteVoiceEvent("listen_stop_requested", { pcmChunks: state.voice.pcmChunks.length });
  state.voice.audioProcessor?.disconnect();
  state.voice.audioSource?.disconnect();
  state.voice.audioSilence?.disconnect();
  for (const track of state.voice.stream?.getTracks?.() || []) {
    track.stop();
  }
  await state.voice.audioContext?.close?.();
  state.voice.recorder = null;
  state.voice.stream = null;
  state.voice.audioContext = null;
  state.voice.audioSource = null;
  state.voice.audioProcessor = null;
  state.voice.audioSilence = null;
  try {
    if (!state.voice.pcmChunks.length) {
      setVoiceState("ready", "Tap to talk");
      return;
    }
    const sourceSamples = mergeFloat32Chunks(state.voice.pcmChunks);
    const wavSamples = resampleLinear(sourceSamples, state.voice.inputSampleRate || 48000, 16000);
    const rms = calculateRms(wavSamples);
    const blob = encodeWavPcm16(wavSamples, 16000);
    noteVoiceEvent("wav_encoded", {
      sourceSamples: sourceSamples.length,
      wavSamples: wavSamples.length,
      bytes: blob.size,
      rms: Number(rms.toFixed(5)),
    });
    if (blob.size <= 44 || wavSamples.length < 1600) {
      setVoiceState("ready", "Tap to talk");
      return;
    }
    const formData = new FormData();
    formData.append("source", state.sourceId);
    formData.append("audio", blob, "satellite-ui.wav");
    const sttPayload = await apiForm("/api/speech/stt", formData);
    const transcript = String(sttPayload.text || "").trim();
    state.voice.debug.lastTranscript = transcript;
    noteVoiceEvent("stt_complete", { transcript });
    if (!transcript) {
      setVoiceState("ready", "Tap to talk");
      return;
    }
    const commandResponse = await postCommandWithInterimEvents({
      text: transcript,
      source: state.sourceId,
      playback_target_source_id: state.sourceId,
      session_id: state.voice.sessionId,
    });
    state.voice.debug.lastReplyText = String(commandResponse.reply_text || "");
    noteVoiceEvent("command_complete", { replyText: state.voice.debug.lastReplyText });
    const deferredResume = extractDeferredResume(commandResponse);
    await playReplyAudio(state.voice.debug.lastReplyText);
    if (deferredResume) {
      await resumeDeferredPlayback(deferredResume);
    }
    applyUiContextResult(commandResponse);
    setVoiceState("ready", "Tap to talk");
    await reloadCurrentPage();
  } catch (error) {
    const message = error instanceof Error ? error.message : "Voice request failed.";
    noteVoiceEvent("voice_failed", { message });
    if (isUnusableAudioError(message)) {
      setVoiceState("ready", "Tap to talk");
      return;
    }
    setVoiceState("error", friendlyVoiceError(message));
  } finally {
    state.voice.chunks = [];
    state.voice.pcmChunks = [];
    state.voice.inputSampleRate = 0;
  }
}

async function postCommandWithInterimEvents(payload) {
  let completed = false;
  let ackPlayed = false;
  const commandPromise = apiPost("/api/conversation/command", payload).finally(() => {
    completed = true;
  });

  const pollPromise = (async () => {
    while (!completed && !ackPlayed) {
      await delay(350);
      const params = new URLSearchParams({
        source: state.sourceId,
        session_id: state.voice.sessionId,
        after_event_id: String(state.voice.lastInterimEventId || 0),
      });
      let payload;
      try {
        payload = await apiGet(`/api/conversation/command-events?${params.toString()}`);
      } catch (error) {
        noteVoiceEvent("interim_event_poll_failed", {
          message: error instanceof Error ? error.message : String(error),
        });
        return;
      }
      const events = Array.isArray(payload.events) ? payload.events : [];
      for (const event of events) {
        state.voice.lastInterimEventId = Math.max(state.voice.lastInterimEventId || 0, Number(event.event_id || 0));
        if (event.event_type === "facts_summarizer_ack" && !ackPlayed) {
          ackPlayed = true;
          noteVoiceEvent("interim_facts_ack", { eventId: event.event_id });
          await playReplyAudio(event.message);
          return;
        }
      }
    }
  })();

  const response = await commandPromise;
  completed = true;
  await pollPromise;
  return response;
}

function delay(milliseconds) {
  return new Promise((resolve) => globalThis.setTimeout(resolve, milliseconds));
}

function applyUiContextResult(commandResponse) {
  const presentation = commandResponse?.effects?.ui_presentation;
  const result = presentation?.kind === "dto" ? presentation.presentation : null;
  const action = String(result?.action || result?.ui_context_action || "").trim();
  const search = result?.search;
  if (!search || !["music_search", "audiobook_search"].includes(action)) {
    return;
  }
  const page = action === "music_search" ? "music" : "audiobooks";
  state.mediaSearch[page] = {
    kind: String(search.kind || ""),
    query: String(search.query || ""),
    selectedUser: String(search.selected_user || ""),
    results: Array.isArray(search.results) ? search.results : [],
    resultCount: Number(search.result_count || 0),
  };
  state.mediaPlayStatus[page] = null;
  state.currentPage = page;
  highlightNav();
}

function extractDeferredResume(commandResponse) {
  const token = String(commandResponse?.effects?.deferred_satellite_playback?.continuation_token || "").trim();
  if (!token) {
    return null;
  }
  return token;
}

async function resumeDeferredPlayback(continuationToken) {
  noteVoiceEvent("deferred_resume_requested", {});
  const payload = await apiPost("/api/satellite/deferred-resume", {
    source: state.sourceId,
    continuation_token: continuationToken,
  });
  noteVoiceEvent("deferred_resume_complete", {
    ok: payload.ok === true,
    state: String(payload.satellite?.state || payload.result?.satellite?.state || ""),
    detail: String(payload.result?.detail || ""),
  });
  if (payload.ok !== true) {
    throw new Error(payload.result?.detail || "Deferred playback resume failed.");
  }
}

function mergeFloat32Chunks(chunks) {
  const totalLength = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const merged = new Float32Array(totalLength);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.length;
  }
  return merged;
}

function resampleLinear(samples, sourceRate, targetRate) {
  if (!samples.length || sourceRate === targetRate) {
    return samples;
  }
  const ratio = sourceRate / targetRate;
  const outputLength = Math.max(1, Math.round(samples.length / ratio));
  const output = new Float32Array(outputLength);
  for (let index = 0; index < outputLength; index += 1) {
    const sourceIndex = index * ratio;
    const leftIndex = Math.floor(sourceIndex);
    const rightIndex = Math.min(leftIndex + 1, samples.length - 1);
    const weight = sourceIndex - leftIndex;
    output[index] = samples[leftIndex] * (1 - weight) + samples[rightIndex] * weight;
  }
  return output;
}

function calculateRms(samples) {
  if (!samples.length) {
    return 0;
  }
  let sum = 0;
  for (const sample of samples) {
    sum += sample * sample;
  }
  return Math.sqrt(sum / samples.length);
}

function encodeWavPcm16(samples, sampleRate) {
  const bytesPerSample = 2;
  const headerBytes = 44;
  const buffer = new ArrayBuffer(headerBytes + samples.length * bytesPerSample);
  const view = new DataView(buffer);
  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + samples.length * bytesPerSample, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * bytesPerSample, true);
  view.setUint16(32, bytesPerSample, true);
  view.setUint16(34, 8 * bytesPerSample, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, samples.length * bytesPerSample, true);
  let offset = headerBytes;
  for (const sample of samples) {
    const clipped = Math.max(-1, Math.min(1, sample));
    view.setInt16(offset, clipped < 0 ? clipped * 0x8000 : clipped * 0x7fff, true);
    offset += bytesPerSample;
  }
  return new Blob([view], { type: "audio/wav" });
}

function writeAscii(view, offset, text) {
  for (let index = 0; index < text.length; index += 1) {
    view.setUint8(offset + index, text.charCodeAt(index));
  }
}

async function playReplyAudio(text) {
  const replyText = String(text || "").trim();
  if (!replyText) {
    noteVoiceEvent("reply_audio_skipped", { reason: "empty_reply" });
    return;
  }
  setVoiceState("speaking", "Speaking...");
  noteVoiceEvent("tts_requested", { chars: replyText.length });
  const response = await fetch("/api/speech/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: replyText }),
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  const blob = await response.blob();
  state.voice.debug.lastTtsBytes = blob.size;
  noteVoiceEvent("tts_received", { bytes: blob.size, type: blob.type });
  if (!state.voice.audio) {
    state.voice.audio = document.createElement("audio");
    state.voice.audio.dataset.oracleReplyAudio = "true";
    state.voice.audio.style.display = "none";
    document.body.appendChild(state.voice.audio);
  }
  if (state.voice.audioUrl) {
    URL.revokeObjectURL(state.voice.audioUrl);
  }
  state.voice.audioUrl = URL.createObjectURL(blob);
  state.voice.audio.volume = 1;
  state.voice.audio.muted = false;
  state.voice.audio.preload = "auto";
  state.voice.audio.src = state.voice.audioUrl;
  await new Promise((resolve, reject) => {
    const audio = state.voice.audio;
    const cleanup = () => {
      audio.removeEventListener("ended", onEnded);
      audio.removeEventListener("error", onError);
    };
    const onEnded = () => {
      cleanup();
      state.voice.debug.lastPlayback = {
        ok: true,
        ended: audio.ended,
        currentTime: audio.currentTime,
        duration: Number.isFinite(audio.duration) ? audio.duration : null,
      };
      noteVoiceEvent("reply_audio_ended", state.voice.debug.lastPlayback);
      resolve();
    };
    const onError = () => {
      cleanup();
      const error = audio.error;
      const message = error?.message || `Audio playback failed${error?.code ? ` (${error.code})` : ""}.`;
      state.voice.debug.lastPlayback = { ok: false, message };
      noteVoiceEvent("reply_audio_error", state.voice.debug.lastPlayback);
      reject(new Error(message));
    };
    audio.addEventListener("ended", onEnded, { once: true });
    audio.addEventListener("error", onError, { once: true });
    audio.play().then(() => {
      state.voice.debug.lastPlayback = {
        ok: true,
        started: true,
        paused: audio.paused,
        volume: audio.volume,
        muted: audio.muted,
      };
      noteVoiceEvent("reply_audio_started", state.voice.debug.lastPlayback);
    }, (error) => {
      cleanup();
      state.voice.debug.lastPlayback = {
        ok: false,
        message: error instanceof Error ? error.message : String(error),
      };
      noteVoiceEvent("reply_audio_play_rejected", state.voice.debug.lastPlayback);
      reject(error);
    });
  });
}

function isUnusableAudioError(message) {
  return /EBML|Error opening input|Invalid data found|End of file|input\.webm|matroska,webm/i.test(String(message || ""));
}

function friendlyVoiceError(message) {
  const text = String(message || "").trim();
  if (!text) {
    return "Voice unavailable.";
  }
  if (/Failed to fetch|NetworkError|Load failed/i.test(text)) {
    return "Voice unavailable.";
  }
  if (/permission|denied|notallowed/i.test(text)) {
    return "Mic permission needed.";
  }
  if (/notreadable|audio source/i.test(text)) {
    return "Mic unavailable.";
  }
  return text.length > 80 ? "Voice unavailable." : text;
}

async function loadCurrentPage() {
  return loadCurrentPageSnapshot({ force: false });
}

async function reloadCurrentPage() {
  return loadCurrentPageSnapshot({ force: true });
}

async function loadCurrentPageSnapshot({ force }) {
  const page = state.currentPage;
  const cached = state.pageSnapshots[page];
  const seq = ++state.pageLoadSeq;
  if (cached && !force && canRenderCachedPageSnapshot(page)) {
    renderPagePayload(page, cached);
    void refreshPageSnapshot(page, seq, { renderErrors: false });
    return;
  }
  await refreshPageSnapshot(page, seq, { renderErrors: true });
}

function canRenderCachedPageSnapshot(page) {
  if (!LIVE_CONTROL_PAGES.has(page)) {
    return true;
  }
  const loadedAt = Number(state.pageSnapshotLoadedAt[page] || 0);
  return loadedAt > 0 && Date.now() - loadedAt <= LIVE_CONTROL_PAGE_MAX_STALE_MS;
}

function invalidateLiveControlSnapshots() {
  for (const page of LIVE_CONTROL_PAGES) {
    delete state.pageSnapshots[page];
    delete state.pageSnapshotLoadedAt[page];
  }
}

async function refreshPageSnapshot(page, seq, { renderErrors }) {
  const loader = pageLoaders[page] || pageLoaders.home;
  try {
    const payload = await loader();
    state.pageSnapshots[page] = payload;
    state.pageSnapshotLoadedAt[page] = Date.now();
    if (seq === state.pageLoadSeq && state.currentPage === page) {
      renderPagePayload(page, payload);
      schedulePageRefresh(page, payload);
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unable to load page.";
    noteVoiceEvent("page_refresh_failed", { page, message });
    if (renderErrors && seq === state.pageLoadSeq && state.currentPage === page) {
      renderFatal(friendlyVoiceError(message));
    }
    if (seq === state.pageLoadSeq && state.currentPage === page) {
      schedulePageRefresh(page, null);
    }
  }
}

function schedulePageRefresh(page, payload) {
  clearTimeout(state.pageRefreshTimer);
  const refreshSeconds = normalizePageRefreshSeconds(page, payload);
  if (!refreshSeconds || state.currentPage !== page) {
    return;
  }
  state.pageRefreshTimer = globalThis.setTimeout(async () => {
    if (state.currentPage !== page) {
      return;
    }
    const seq = ++state.pageLoadSeq;
    await refreshPageSnapshot(page, seq, { renderErrors: false });
  }, refreshSeconds * 1000);
}

function normalizePageRefreshSeconds(page, payload) {
  const raw = Number(payload?.refresh_after_seconds);
  const fallback = DEFAULT_PAGE_REFRESH_SECONDS[page] || 0;
  const seconds = Number.isFinite(raw) && raw > 0 ? raw : fallback;
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return 0;
  }
  return Math.max(3, Math.min(600, seconds));
}

function renderPagePayload(page, payload) {
  const render = pageRenderers[page] || pageRenderers.home;
  render(payload);
}

function renderHome(payload) {
  const controls = Array.isArray(payload.room_controls?.items) ? payload.room_controls.items : [];
  const routineActions = Array.isArray(payload.routine_actions) ? payload.routine_actions : [];
  const event = Array.isArray(payload.calendar?.events) ? payload.calendar.events[0] : null;
  const weather = payload.weather || {};
  const hasControls = controls.length > 0;
  const hasRoomEnvironment = Array.isArray(payload.room_environment?.items)
    && payload.room_environment.items.length > 0;
  const secondaryCards = configuredHomeSecondaryCards(
    routineActions.length > 0,
    hasRoomEnvironment,
  );
  elements.pageRoot.innerHTML = `
    <section class="home-grid ${hasControls ? "" : "home-grid--visual"}">
      <article class="card card--hero room-card">
        <div class="hero-copy room-card__header">
          <div>
            <p class="card__eyebrow">${escapeHtml(state.config.room || "Room")}</p>
            <h2>${hasControls ? "Controls" : escapeHtml(state.config.display_name || "Oracle")}</h2>
          </div>
          <div class="room-clock" aria-label="Current time">
            <div class="room-clock__time" data-clock-time></div>
            <div class="room-clock__date" data-clock-date></div>
          </div>
        </div>
        ${
          hasControls
            ? `<div class="device-grid">${controls.map(renderRoomControlTile).join("")}</div>`
            : renderVisualRoomOverview(weather, event)
        }
      </article>
      ${secondaryCards.map((card, index) => renderHomeSecondaryCard(card, {
        weather,
        event,
        alarm: payload.alarm || {},
        roomEnvironment: payload.room_environment || {},
        routineActions,
      }, index)).join("")}
    </section>
  `;
  updateClockText();
  wireActionButtons();
}

function configuredHomeSecondaryCards(hasRoutineActions = false, hasRoomEnvironment = false) {
  const configured = Array.isArray(state.config?.profile?.home?.secondary_cards)
    ? state.config.profile.home.secondary_cards
    : [];
  const supported = new Set(["weather", "calendar", "alarm", "room_environment", "routine_actions"]);
  const cards = configured.map((item) => String(item || "").trim().toLowerCase()).filter((item) => supported.has(item));
  const selected = (cards.length ? cards : ["weather", "calendar"]).slice(0, 2);
  if (hasRoutineActions && !selected.includes("routine_actions")) {
    const calendarIndex = selected.indexOf("calendar");
    if (calendarIndex >= 0) {
      selected[calendarIndex] = "routine_actions";
    }
  } else if (hasRoomEnvironment && !selected.includes("room_environment")) {
    const calendarIndex = selected.indexOf("calendar");
    if (calendarIndex >= 0) {
      selected[calendarIndex] = "room_environment";
    }
  }
  return selected;
}

function renderHomeSecondaryCard(card, payload, index) {
  const slotClass = index === 0 ? "home-card-slot--a" : "home-card-slot--b";
  if (card === "alarm") {
    return renderHomeAlarmCard(slotClass, payload.alarm || {});
  }
  if (card === "room_environment") {
    return renderHomeRoomEnvironmentCard(slotClass, payload.roomEnvironment || {});
  }
  if (card === "routine_actions") {
    return renderHomeRoutineActionsCard(slotClass, payload.routineActions || []);
  }
  if (card === "calendar") {
    return `
      <article class="card card--secondary calendar-card ${slotClass}">
        <p class="card__eyebrow">Calendar</p>
        ${renderHomeCalendarCard(payload.event)}
      </article>
    `;
  }
  return `
    <article class="card card--secondary weather-card ${slotClass}">
      <p class="card__eyebrow">Weather</p>
      <div class="weather-card__temp">${payload.weather.temperature_f == null ? "--" : `${Math.round(Number(payload.weather.temperature_f))}°`}</div>
      <h3>${escapeHtml(payload.weather.condition || compactWeatherSummary(payload.weather.summary || "Weather unavailable."))}</h3>
      ${renderHomeWeatherMeta(payload.weather)}
    </article>
  `;
}

function renderHomeRoutineActionsCard(slotClass, actions) {
  const action = actions[0] || null;
  return `
    <article class="card card--secondary routine-action-card ${slotClass}">
      <p class="card__eyebrow">Routine</p>
      <h3>${escapeHtml(action?.label || "No routine")}</h3>
      <p class="mini-copy">${escapeHtml(action?.description || "No task routine is configured for this room.")}</p>
      ${action ? `
        <button class="nav-action nav-action--wide" type="button" data-routine-id="${escapeHtml(action.orchestration_id || "")}">
          <span class="material-symbols-outlined">${escapeHtml(action.icon || "bedtime")}</span>
          <span>Start</span>
        </button>
      ` : ""}
    </article>
  `;
}

function renderHomeRoomEnvironmentCard(slotClass, environment) {
  const items = Array.isArray(environment.items) ? environment.items : [];
  const temperatures = items
    .filter((item) => item && item.temperature_f != null)
    .map((item) => Number(item.temperature_f))
    .filter((value) => Number.isFinite(value));
  const humidities = items
    .filter((item) => item && item.humidity_pct != null)
    .map((item) => Number(item.humidity_pct))
    .filter((value) => Number.isFinite(value));
  const humiditySource = items.find((item) => item && item.humidity_source && Number.isFinite(Number(item.humidity_pct)));
  const humidityValue = humiditySource
    ? Number(humiditySource.humidity_pct)
    : humidities[0];
  const averageTemp = temperatures.length
    ? `${Math.round(temperatures.reduce((sum, value) => sum + value, 0) / temperatures.length)}°`
    : "--";
  const humidity = Number.isFinite(humidityValue)
    ? `${Math.round(humidityValue)}%`
    : "--";
  return `
    <article class="card card--secondary room-environment-card ${slotClass}">
      <p class="card__eyebrow">${escapeHtml(environment.title || "Room Climate")}</p>
      ${items.length ? `
        <div class="room-environment-card__summary">
          <div>
            <span class="material-symbols-outlined">device_thermostat</span>
            <span>Temperature</span>
            <strong>${escapeHtml(averageTemp)}</strong>
          </div>
          <div>
            <span class="material-symbols-outlined">humidity_percentage</span>
            <span>Humidity</span>
            <strong>${escapeHtml(humidity)}</strong>
          </div>
        </div>
      ` : '<p class="mini-copy">Room climate unavailable.</p>'}
    </article>
  `;
}

function renderRoomEnvironmentItem(item) {
  const available = item.available !== false;
  const temp = available && item.temperature_f != null ? `${Math.round(Number(item.temperature_f))}°` : "--";
  const humidity = available && item.humidity_pct != null ? `${Math.round(Number(item.humidity_pct))}%` : "--";
  return `
    <div class="room-environment-item">
      <h3>${escapeHtml(item.label || "Room")}</h3>
      <p class="mini-copy">${escapeHtml(temp)} / ${escapeHtml(humidity)}</p>
    </div>
  `;
}

function renderHomeAlarmCard(slotClass, alarm) {
  const nextAlarm = alarm?.next || null;
  const active = alarm?.active === true && nextAlarm;
  const alarmTime = active ? formatAlarmTime(nextAlarm.due_at) : "Set Alarm";
  const alarmDetail = active ? String(nextAlarm.message || "Alarm set") : "No alarm set";
  return `
    <article class="card card--secondary alarm-card ${slotClass}">
      <p class="card__eyebrow">Alarm</p>
      <h3>${escapeHtml(alarmTime)}</h3>
      <p class="mini-copy">${escapeHtml(alarmDetail)}</p>
      <button class="nav-action nav-action--wide" type="button" data-ui-context-action="set_alarm">
        <span class="material-symbols-outlined">alarm_add</span>
        <span>Set Alarm</span>
      </button>
      ${active ? `
        <button class="nav-action nav-action--wide nav-action--quiet" type="button" data-alarm-cancel>
          <span class="material-symbols-outlined">alarm_off</span>
          <span>Clear Alarm</span>
        </button>
      ` : ""}
    </article>
  `;
}

function formatAlarmTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Alarm Set";
  }
  return new Intl.DateTimeFormat([], { hour: "numeric", minute: "2-digit" }).format(date);
}

function renderVisualRoomOverview(weather, event) {
  const temp = weather.temperature_f == null ? "--" : `${Math.round(Number(weather.temperature_f))}°`;
  const condition = weather.condition || compactWeatherSummary(weather.summary || "Weather unavailable.");
  const eventTitle = event ? String(event.summary || "Upcoming event") : "No upcoming events";
  const eventTime = event ? formatCalendarTime(event, "upcoming") : "";
  return `
    <div class="room-overview">
      <div class="room-overview__primary">
        <div class="room-overview__label">Weather</div>
        <div class="room-overview__value">${escapeHtml(temp)}</div>
        <div class="room-overview__detail">${escapeHtml(condition)}</div>
      </div>
      <div class="room-overview__secondary">
        <div class="room-overview__label">Next</div>
        <div class="room-overview__event">${escapeHtml(eventTitle)}</div>
        ${eventTime ? `<div class="room-overview__detail">${escapeHtml(eventTime)}</div>` : ""}
      </div>
      <div class="room-overview__status">
        <span class="material-symbols-outlined">graphic_eq</span>
        <span>${escapeHtml(state.voice.status === "ready" ? "Ready" : state.voice.detail || "Oracle")}</span>
      </div>
    </div>
  `;
}

function renderRoomControlTile(item) {
  const primaryAction = Array.isArray(item.actions) ? item.actions[0] : null;
  const icon = tileIcon(item);
  const stateLabel = normalizeStateLabel(item.status_label || item.state || "Unavailable");
  const content = `
    <span class="material-symbols-outlined device-tile__icon">${escapeHtml(icon)}</span>
    <span class="device-tile__name">${escapeHtml(item.label || "Control")}</span>
    <span class="device-tile__state">${escapeHtml(stateLabel)}</span>
  `;
  if (primaryAction?.action_id) {
    return `<button class="device-tile" type="button" data-action-id="${escapeHtml(primaryAction.action_id)}">${content}</button>`;
  }
  return `
    <div class="device-tile device-tile--static">
      ${content}
    </div>
  `;
}

function renderWeather(payload) {
  const current = payload.current || {};
  const periods = Array.isArray(payload.forecast?.periods) ? payload.forecast.periods : [];
  const [todayPeriod, nextPeriod, laterPeriod] = periods;
  elements.pageRoot.innerHTML = `
    <section class="page-stack">
      <article class="card section weather-section weather-section--current">
        <div class="weather-mast">
          <div>
            <p class="card__eyebrow">Current</p>
            <h2>${current.temperature_f == null ? "--" : `${Math.round(Number(current.temperature_f))}°`}</h2>
            <p class="section-copy section-copy--lead">${escapeHtml(compactWeatherSummary(current.summary || "Weather unavailable."))}</p>
            <p class="section-copy">${escapeHtml(payload.location || "")}</p>
          </div>
          <div class="weather-stat-block">
            <div class="weather-stat">${current.humidity_pct == null ? "--" : `${Math.round(Number(current.humidity_pct))}%`}</div>
            <div class="status-note">Humidity</div>
          </div>
        </div>
      </article>
      <article class="card section weather-section">
        <p class="card__eyebrow">Today</p>
        ${todayPeriod ? renderForecastFeature(todayPeriod) : '<div class="empty-state">Forecast unavailable.</div>'}
      </article>
      <article class="card section weather-section">
        <p class="card__eyebrow">Next</p>
        <div class="forecast-grid">
          ${nextPeriod ? renderForecastMini(nextPeriod) : ""}
          ${laterPeriod ? renderForecastMini(laterPeriod) : ""}
          ${!nextPeriod && !laterPeriod ? '<div class="empty-state">No additional forecast available.</div>' : ""}
        </div>
      </article>
    </section>
  `;
}

function renderCalendar(payload) {
  const today = Array.isArray(payload.today?.events) ? payload.today.events : [];
  const upcoming = Array.isArray(payload.upcoming?.events) ? payload.upcoming.events : [];
  const todayDate = String(payload.today?.date || "");
  const upcomingLater = upcoming.filter((item) => eventDateKey(item) !== todayDate).slice(0, 3);
  elements.pageRoot.innerHTML = `
    <section class="page-stack">
      <article class="card section">
        <p class="card__eyebrow">Today</p>
        ${today.length ? `<div class="list">${today.slice(0, 2).map((item) => renderCalendarItem(item, "today")).join("")}</div>` : '<div class="empty-state">No upcoming events.</div>'}
      </article>
      <article class="card section">
        <p class="card__eyebrow">Upcoming</p>
        ${upcomingLater.length ? `<div class="list">${upcomingLater.map((item) => renderCalendarItem(item, "upcoming")).join("")}</div>` : '<div class="empty-state">No upcoming events.</div>'}
      </article>
    </section>
  `;
}

function renderCalendarItem(item, mode) {
  return `
    <div class="list-item list-item--calendar">
      <div class="list-item__row">
        <div class="calendar-line">
          <div class="calendar-line__time">${escapeHtml(formatCalendarTime(item, mode))}</div>
          <div class="list-item__title">${escapeHtml(String(item.summary || "Untitled event"))}</div>
        </div>
      </div>
      ${renderCalendarMeta(item, mode)}
    </div>
  `;
}

function renderAudio(payload) {
  const playback = payload.playback || {};
  const outputOwner = playback.output_owner || {};
  const actions = buildAudioActions(outputOwner);
  elements.pageRoot.innerHTML = `
    <section class="page-stack">
      <article class="card section">
        <p class="card__eyebrow">Now Playing</p>
        <h2>${escapeHtml(outputOwner.title || "Nothing active")}</h2>
        <p class="section-copy">${escapeHtml(outputOwner.artist_or_author || outputOwner.backend_type || payload.selected_source || "This satellite")}</p>
        <p class="mini-copy">${playback.active ? "Playback is active on this satellite." : "Nothing active."}</p>
        <div class="action-row">${actions.map((action) => renderActionButton(action, "nav-action", "audio")).join("")}</div>
      </article>
    </section>
  `;
  wireActionButtons();
}

function renderMusic(payload) {
  const playback = payload.playback || {};
  const outputOwner = playback.output_owner || {};
  const isMusic = String(outputOwner.media_kind || "").toLowerCase() === "music";
  elements.pageRoot.innerHTML = `
    <section class="page-stack media-page media-page--music">
      <article class="card section media-hero">
        <div>
          <p class="card__eyebrow">Music</p>
          <h2>${escapeHtml(isMusic ? outputOwner.title || "Music playing" : "Music")}</h2>
          <p class="section-copy">${escapeHtml(isMusic ? outputOwner.artist_or_author || outputOwner.album || "This room" : "Use voice to search and play music in this room.")}</p>
        </div>
        <button class="media-search-button" type="button" data-voice-search="music">
          <span class="material-symbols-outlined">mic</span>
          <span>Search Music</span>
        </button>
      </article>
      ${renderMediaSearchResults("music")}
      ${renderMediaPlaybackCard(payload, "music")}
      ${renderMusicQueueCard(outputOwner)}
    </section>
  `;
  wireActionButtons();
  wireVoiceSearchButtons();
}

function renderAudiobooks(payload) {
  const playback = payload.playback || {};
  const outputOwner = playback.output_owner || {};
  const isAudiobook = String(outputOwner.media_kind || "").toLowerCase() === "audiobook";
  state.audiobookPlaybackActive = isAudiobook;
  state.selectedAudiobookUser = String(payload.selected_user || "");
  state.currentAudiobookResult = findCurrentAudiobookResult(payload);
  const current = payload.audiobook?.current || payload.current_audiobook || null;
  elements.pageRoot.innerHTML = `
    <section class="page-stack media-page media-page--audiobooks">
      <article class="card section media-hero">
        <div>
          <p class="card__eyebrow">Audiobooks</p>
          <h2>${escapeHtml(isAudiobook ? outputOwner.title || "Story playing" : current?.title || "Audiobooks")}</h2>
          <p class="section-copy">${escapeHtml(isAudiobook ? outputOwner.artist_or_author || "This room" : current?.author || "Use voice to find a story or resume the current audiobook.")}</p>
        </div>
        <button class="media-search-button" type="button" data-voice-search="audiobooks">
          <span class="material-symbols-outlined">mic</span>
          <span>Search Audiobooks</span>
        </button>
      </article>
      ${renderMediaSearchResults("audiobooks")}
      ${renderMediaPlaybackCard(payload, "audiobook")}
      ${renderAudiobookResumeCard(payload)}
      ${renderSleepTimerCard(payload)}
    </section>
  `;
  wireActionButtons();
  wireVoiceSearchButtons();
  updateSleepTimerCountdown();
}

function findCurrentAudiobookResult(payload) {
  const results = Array.isArray(payload.results) ? payload.results : [];
  for (const result of results) {
    if (String(result?.type || "").toLowerCase() === "audiobook" && String(result?.library_item_id || "").trim()) {
      return result;
    }
  }
  const current = payload.audiobook?.current || payload.current_audiobook || null;
  const libraryItemId = String(current?.library_item_id || "").trim();
  if (!libraryItemId) {
    return null;
  }
  return {
    result_id: `audiobook:${libraryItemId}`,
    type: "audiobook",
    title: current?.title || "Audiobook",
    subtitle: current?.author || "",
    source: "current_audiobook",
    library_item_id: libraryItemId,
    position_seconds: current?.current_time_seconds,
    duration_seconds: current?.duration_seconds,
  };
}

function renderMediaSearchResults(page) {
  const search = state.mediaSearch[page];
  if (!search) {
    return "";
  }
  const results = Array.isArray(search.results) ? search.results : [];
  const label = page === "music" ? "Music Results" : "Audiobook Results";
  return `
    <article class="card section media-results">
      <p class="card__eyebrow">${escapeHtml(label)}</p>
      <h2>${escapeHtml(search.query || "Search results")}</h2>
      ${renderMediaPlayStatus(page)}
      ${
        results.length
          ? `<div class="media-result-list">${results.map((result, index) => renderMediaResultCard(result, page, index)).join("")}</div>`
          : '<div class="empty-state">No results found.</div>'
      }
    </article>
  `;
}

function renderMediaPlayStatus(page) {
  const status = state.mediaPlayStatus[page];
  if (!status) {
    return "";
  }
  const label = {
    pending: "Starting",
    success: "Started",
    error: "Could not start",
  }[status.state] || "Playback";
  return `
    <div class="media-play-status media-play-status--${escapeHtml(status.state)}">
      <span class="material-symbols-outlined">${status.state === "error" ? "error" : status.state === "success" ? "check_circle" : "hourglass_top"}</span>
      <span>${escapeHtml(label)}${status.message ? `: ${escapeHtml(status.message)}` : ""}</span>
    </div>
  `;
}

function renderMediaResultCard(result, page, index) {
  const title = String(result?.title || "Untitled").trim();
  const subtitle = String(result?.subtitle || result?.artist || result?.author || "").trim();
  const artUrl = String(result?.art_url || "").trim();
  const status = state.mediaPlayStatus[page];
  const isPending = status?.state === "pending" && Number(status.index) === index;
  return `
    <div class="media-result-card">
      ${artUrl ? `<img class="media-result-card__art" src="${escapeHtml(artUrl)}" alt="">` : `<div class="media-result-card__art media-result-card__art--empty"><span class="material-symbols-outlined">${page === "music" ? "music_note" : "book"}</span></div>`}
      <div class="media-result-card__body">
        <div class="list-item__title">${escapeHtml(title)}</div>
        ${subtitle ? `<p class="mini-copy">${escapeHtml(subtitle)}</p>` : ""}
      </div>
      <button class="nav-action" type="button" data-audio-result-page="${escapeHtml(page)}" data-audio-result-index="${escapeHtml(String(index))}" ${isPending ? "disabled" : ""}>
        <span class="material-symbols-outlined">${isPending ? "hourglass_top" : "play_arrow"}</span>
        <span>${isPending ? "Starting" : "Play"}</span>
      </button>
    </div>
  `;
}

function renderMediaPlaybackCard(payload, mediaKind) {
  const playback = payload.playback || {};
  const outputOwner = playback.output_owner || {};
  const activeKind = String(outputOwner.media_kind || "").toLowerCase();
  const matching = activeKind === mediaKind;
  const actions = buildAudioActions(matching ? outputOwner : null);
  return `
    <article class="card section media-now">
      <p class="card__eyebrow">Now Playing</p>
      <h2>${escapeHtml(matching ? outputOwner.title || "Playing" : "Nothing active")}</h2>
      <p class="section-copy">${escapeHtml(matching ? outputOwner.artist_or_author || outputOwner.album || "This satellite" : "This room is not playing this type of audio.")}</p>
      ${matching ? renderProgressLine(outputOwner) : ""}
      <div class="action-row">${actions.map((action) => renderActionButton(action, "nav-action", "audio")).join("")}</div>
    </article>
  `;
}

function renderMusicQueueCard(outputOwner) {
  if (String(outputOwner?.media_kind || "").toLowerCase() !== "music") {
    return `
      <article class="card section section--compact media-secondary">
        <p class="card__eyebrow">Queue</p>
        <div class="empty-state">No music queue active.</div>
      </article>
    `;
  }
  const position = Number(outputOwner.queue_position || 0);
  const count = Number(outputOwner.queue_count || 0);
  return `
    <article class="card section section--compact media-secondary">
      <p class="card__eyebrow">Queue</p>
      <div class="info-tile">
        <div class="list-item__title">${escapeHtml(outputOwner.collection_title || outputOwner.album || outputOwner.title || "Current music")}</div>
        <div class="mini-copy">${position && count ? `Track ${position} of ${count}` : "Queue details unavailable."}</div>
      </div>
    </article>
  `;
}

function renderAudiobookResumeCard(payload) {
  const current = payload.audiobook?.current || payload.current_audiobook || null;
  if (!current) {
    return `
      <article class="card section section--compact media-secondary">
        <p class="card__eyebrow">Current Story</p>
        <div class="empty-state">No current audiobook.</div>
      </article>
    `;
  }
  return `
    <article class="card section section--compact media-secondary">
      <p class="card__eyebrow">Current Story</p>
      <div class="list-item">
        <div class="list-item__title">${escapeHtml(current.title || "Audiobook")}</div>
        <p class="mini-copy">${escapeHtml(current.author || "")}</p>
        ${renderProgressLine(current)}
      </div>
      ${renderMediaPlayStatus("audiobooks")}
      <div class="action-row">
        <button class="nav-action" type="button" data-current-audiobook-play="true">
          <span class="material-symbols-outlined">play_arrow</span>
          <span>Resume</span>
        </button>
      </div>
    </article>
  `;
}

function renderSleepTimerCard(payload) {
  const timer = payload.sleep_timer || {};
  const pendingMinutes = Number(state.pendingAudiobookSleepTimerMinutes || 0);
  const label = timer.active ? formatSleepTimerCountdown(timer) : pendingMinutes > 0 ? `${pendingMinutes}m selected` : "Off";
  const detail = timer.active
    ? "Audiobook playback will stop when the timer ends."
    : pendingMinutes > 0
      ? "The timer will start with the next audiobook play or resume."
      : "Choose a timer for story playback.";
  const dueAt = String(timer.due_at || "");
  const dueLabel = timer.active && dueAt ? formatSleepTimerDueLabel(dueAt) : "";
  const options = Array.isArray(timer.options_minutes) ? timer.options_minutes.filter((minutes) => Number(minutes) > 0) : [15, 20, 30, 60];
  return `
    <article class="card section section--compact media-tertiary">
      <p class="card__eyebrow">Sleep Timer</p>
      <div class="info-tile sleep-timer-tile ${timer.active ? "sleep-timer-tile--active" : ""}">
        ${
          timer.active
            ? `<div class="sleep-timer-countdown" aria-live="polite">
                <span class="material-symbols-outlined">timer</span>
                <span class="sleep-timer-countdown__copy">
                  <span class="sleep-timer-countdown__label">Stops in</span>
                  <span class="sleep-timer-countdown__value" data-sleep-timer-countdown="${escapeHtml(dueAt)}">${escapeHtml(label)}</span>
                </span>
              </div>`
            : `<div class="info-tile__value">${escapeHtml(label)}</div>`
        }
        ${dueLabel ? `<div class="mini-copy sleep-timer-due">${escapeHtml(dueLabel)}</div>` : ""}
        <div class="mini-copy">${escapeHtml(detail)}</div>
        <div class="action-row">
          ${options.map((minutes) => `
            <button class="nav-action ${pendingMinutes === Number(minutes) && !timer.active ? "nav-action--selected" : ""}" type="button" data-sleep-timer-minutes="${escapeHtml(String(minutes))}">
              <span class="material-symbols-outlined">timer</span>
              <span>${escapeHtml(String(minutes))}m</span>
            </button>
          `).join("")}
          ${
            timer.active
              ? `<button class="nav-action" type="button" data-sleep-timer-cancel="true">
                  <span class="material-symbols-outlined">timer_off</span>
                  <span>Cancel</span>
                </button>`
              : ""
          }
        </div>
      </div>
    </article>
  `;
}

function updateSleepTimerCountdown() {
  const elements = Array.from(document.querySelectorAll("[data-sleep-timer-countdown]"));
  if (!elements.length) {
    return;
  }
  for (const element of elements) {
    const dueAt = String(element.dataset.sleepTimerCountdown || "").trim();
    const remainingSeconds = remainingSecondsUntil(dueAt);
    if (remainingSeconds == null) {
      continue;
    }
    element.textContent = formatSleepTimerRemaining(remainingSeconds);
  }
}

function renderProgressLine(item) {
  const position = Number(item?.position_seconds ?? item?.current_time_seconds ?? 0);
  const duration = Number(item?.duration_seconds || 0);
  if (!duration) {
    return "";
  }
  const pct = Math.max(0, Math.min(100, (position / duration) * 100));
  return `
    <div class="progress-line" aria-label="Playback progress">
      <div class="progress-line__bar"><span style="width: ${pct.toFixed(1)}%"></span></div>
      <div class="mini-copy">${escapeHtml(formatDuration(position))} / ${escapeHtml(formatDuration(duration))}</div>
    </div>
  `;
}

function buildAudioActions(outputOwner) {
  const mediaKind = String(outputOwner?.media_kind || outputOwner?.media_type || "").trim().toLowerCase();
  if (!outputOwner || !outputOwner.backend_type) {
    return [];
  }
  return [
    { kind: "audio", operation: "pause", label: "Pause", icon: "pause", media_kind: mediaKind || null },
    { kind: "audio", operation: "resume", label: "Resume", icon: "play_arrow", media_kind: mediaKind || null },
    { kind: "audio", operation: "stop", label: "Stop", icon: "stop", media_kind: mediaKind || null },
    { kind: "audio", operation: "volume_down", label: "Down", icon: "volume_down", media_kind: mediaKind || null },
    { kind: "audio", operation: "volume_up", label: "Up", icon: "volume_up", media_kind: mediaKind || null },
  ];
}

function renderHouse(payload) {
  const lights = Array.isArray(payload.lights) ? payload.lights : [];
  const climate = Array.isArray(payload.climate) ? payload.climate : [];
  const temperatures = Array.isArray(payload.temperatures) ? payload.temperatures : [];
  elements.pageRoot.innerHTML = `
    <section class="page-stack house-layout">
      ${payload.front_door ? renderFrontDoorCard(payload.front_door) : ""}
      <article class="card section section--compact house-section house-section--lights">
        <p class="card__eyebrow">Lights</p>
        ${lights.length ? `<div class="tile-grid tile-grid--lights">${lights.map(renderHouseLightTile).join("")}</div>` : '<div class="empty-state">No light controls available.</div>'}
      </article>
      <article class="card section section--compact house-section house-section--temps">
        <p class="card__eyebrow">Temperatures</p>
        ${temperatures.length ? `<div class="tile-grid tile-grid--dense">${temperatures.map(renderTemperatureTile).join("")}</div>` : '<div class="empty-state">No room temperature data available.</div>'}
      </article>
      <article class="card section section--compact house-section house-section--climate">
        <p class="card__eyebrow">Climate</p>
        ${climate.length ? `<div class="tile-grid">${climate.map(renderClimateTile).join("")}</div>` : '<div class="empty-state">No climate controls available.</div>'}
      </article>
    </section>
  `;
  wireActionButtons();
}

function renderFrontDoorCard(item) {
  const action = item.action;
  const stateLabel = normalizeStateLabel(item.lock_state || item.state || "Unavailable");
  const tile = `
    <span class="material-symbols-outlined device-tile__icon">${escapeHtml(stateLabel === "Unlocked" ? "lock_open" : "lock")}</span>
    <span class="device-tile__name">${escapeHtml(item.label || "Entry")}</span>
    <span class="device-tile__state">${escapeHtml(stateLabel)}</span>
  `;
  return `
    <article class="card section section--compact house-section house-section--security">
      <p class="card__eyebrow">Entry / Security</p>
      <div class="tile-grid tile-grid--security">
        ${
          action?.action_id
            ? `<button class="device-tile house-tile house-tile--security" type="button" data-action-id="${escapeHtml(action.action_id)}">${tile}</button>`
            : `<div class="device-tile device-tile--static house-tile house-tile--security">${tile}</div>`
        }
      </div>
    </article>
  `;
}

function renderHouseLightTile(item) {
  const primaryAction = Array.isArray(item.actions) ? item.actions[0] : null;
  const content = `
    <span class="material-symbols-outlined device-tile__icon">${escapeHtml(lightIconForState(item.state))}</span>
    <span class="device-tile__name">${escapeHtml(item.label || "Light")}</span>
    <span class="device-tile__state">${escapeHtml(normalizeStateLabel(compactLightState(item)))}</span>
  `;
  if (primaryAction?.action_id) {
    return `<button class="device-tile house-tile" type="button" data-action-id="${escapeHtml(primaryAction.action_id)}">${content}</button>`;
  }
  return `<div class="device-tile device-tile--static house-tile">${content}</div>`;
}

function renderClimateTile(item) {
  const actions = Array.isArray(item.actions) ? item.actions.slice(0, 2) : [];
  return `
    <div class="info-tile">
      <div class="list-item__title">${escapeHtml(item.label || "Climate")}</div>
      <div class="info-tile__value">${escapeHtml(compactClimateState(item))}</div>
      <div class="mini-copy">${escapeHtml(compactClimateDetail(item))}</div>
      <div class="action-row">${actions.map((action) => renderActionButton(action, "action-pill")).join("")}</div>
    </div>
  `;
}

function renderTemperatureTile(item) {
  return `
    <div class="info-tile info-tile--compact">
      <div class="list-item__title">${escapeHtml(item.label || "Temperature")}</div>
      <div class="info-tile__value">${escapeHtml(item.state || "--")}${escapeHtml(item.unit || "")}</div>
    </div>
  `;
}

function renderActionButton(action, className, mode = "ui") {
  if (mode === "audio") {
    return `<button class="${className}" type="button" data-audio-operation="${escapeHtml(action.operation)}" data-media-kind="${escapeHtml(action.media_kind || "")}">
      <span class="material-symbols-outlined">${escapeHtml(action.icon || "play_arrow")}</span>
      <span>${escapeHtml(action.label || action.operation)}</span>
    </button>`;
  }
  return `<button class="${className}" type="button" data-action-id="${escapeHtml(action.action_id || "")}">
    <span class="material-symbols-outlined">${escapeHtml(action.icon || "bolt")}</span>
    <span>${escapeHtml(action.label || "Act")}</span>
  </button>`;
}

async function startUiContext(action) {
  const contextAction = String(action || "").trim();
  if (!contextAction) {
    return;
  }
  setVoiceState("processing", "Starting...");
  noteVoiceEvent("ui_context_start_requested", { action: contextAction });
  const payload = await apiPost("/api/ui/context/start", {
    action: contextAction,
    client_id: state.clientId,
    ui_session_id: state.voice.sessionId,
    target_source_id: state.sourceId,
  });
  const prompt = String(payload.prompt || "").trim();
  noteVoiceEvent("ui_context_started", { action: contextAction, hasPrompt: Boolean(prompt) });
  await playReplyAudio(prompt);
  await startListening();
}

function wireActionButtons() {
  for (const button of document.querySelectorAll("[data-ui-context-action]")) {
    button.addEventListener("click", async () => {
      try {
        await startUiContext(button.dataset.uiContextAction);
      } catch (error) {
        const message = error instanceof Error ? error.message : "Unable to start screen action.";
        noteVoiceEvent("ui_context_start_failed", { message });
        setVoiceState("error", friendlyVoiceError(message));
      }
    });
  }
  for (const button of document.querySelectorAll("[data-alarm-cancel]")) {
    button.addEventListener("click", async () => {
      await apiPost("/api/ui/alarm/cancel", {
        client_id: state.clientId,
        source: state.sourceId,
      });
      await reloadCurrentPage();
    });
  }
  for (const button of document.querySelectorAll("[data-action-id]")) {
    button.addEventListener("click", async () => {
      await apiPost("/api/ui/action", {
        action_id: button.dataset.actionId,
        client_id: state.clientId,
      });
      invalidateLiveControlSnapshots();
      await reloadCurrentPage();
    });
  }
  for (const button of document.querySelectorAll("[data-routine-id]")) {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const payload = await apiPost(`/api/ui/orchestrations/${encodeURIComponent(button.dataset.routineId || "")}/run`, {
          client_id: state.clientId,
          source: state.sourceId,
          ui_session_id: state.voice.sessionId,
          inputs: {},
        });
        if (payload.pending_input && payload.prompt) {
          await playReplyAudio(String(payload.prompt));
          await startListening();
          return;
        }
        setVoiceState(payload.ok ? "ready" : "error", payload.run?.summary || "Routine started.");
        await reloadCurrentPage();
      } catch (error) {
        const message = error instanceof Error ? error.message : "Routine failed to start.";
        setVoiceState("error", friendlyVoiceError(message));
        button.disabled = false;
      }
    });
  }
  for (const button of document.querySelectorAll("[data-audio-operation]")) {
    button.addEventListener("click", async () => {
      try {
        const operation = button.dataset.audioOperation || "";
        const mediaKind = button.dataset.mediaKind || null;
        const payload = await controlSatelliteAudio(operation, mediaKind);
        if (operation === "resume" && mediaKind === "audiobook") {
          await applyPendingAudiobookSleepTimer();
        }
        setVoiceState("ready", "Tap to talk");
      } catch (error) {
        const message = error instanceof Error ? error.message : "Audio action failed.";
        setVoiceState("error", friendlyVoiceError(message));
      }
      await reloadCurrentPage();
      await refreshHeaderAudio();
    });
  }
  for (const button of document.querySelectorAll("[data-audio-result-page]")) {
    button.addEventListener("click", async () => {
      const page = button.dataset.audioResultPage || "";
      const index = Number(button.dataset.audioResultIndex || -1);
      const search = state.mediaSearch[page];
      const result = Array.isArray(search?.results) ? search.results[index] : null;
      if (!result) {
        return;
      }
      const title = String(result.title || "selection").trim();
      state.mediaPlayStatus[page] = { state: "pending", index, message: title };
      await loadCurrentPage();
      try {
        const payload = await apiPost("/api/ui/audio/play", {
          client_id: state.clientId,
          target: state.sourceId,
          result,
          user_id: search?.selectedUser || null,
          sleep_timer_minutes: page === "audiobooks" ? selectedPendingAudiobookSleepTimerMinutes() : null,
        });
        if (payload.ok !== true) {
          throw new Error(extractUiAudioFailureMessage(payload));
        }
        if (page === "audiobooks") {
          state.pendingAudiobookSleepTimerMinutes = 0;
        }
        state.mediaPlayStatus[page] = { state: "success", index, message: title };
        await refreshHeaderAudio();
      } catch (error) {
        const message = error instanceof Error ? error.message : "Playback failed.";
        state.mediaPlayStatus[page] = { state: "error", index, message };
      }
      await reloadCurrentPage();
    });
  }
  for (const button of document.querySelectorAll("[data-current-audiobook-play]")) {
    button.addEventListener("click", async () => {
      const result = state.currentAudiobookResult;
      if (!result) {
        setVoiceState("error", "No current audiobook is available.");
        return;
      }
      const title = String(result.title || "audiobook").trim();
      state.mediaPlayStatus.audiobooks = { state: "pending", index: -1, message: title };
      await loadCurrentPage();
      try {
        const payload = await apiPost("/api/ui/audio/play", {
          client_id: state.clientId,
          target: state.sourceId,
          result,
          user_id: state.selectedAudiobookUser || null,
          sleep_timer_minutes: selectedPendingAudiobookSleepTimerMinutes(),
        });
        if (payload.ok !== true) {
          throw new Error(extractUiAudioFailureMessage(payload));
        }
        state.pendingAudiobookSleepTimerMinutes = 0;
        state.mediaPlayStatus.audiobooks = { state: "success", index: -1, message: title };
        setVoiceState("ready", "Tap to talk");
        await refreshHeaderAudio();
      } catch (error) {
        const message = error instanceof Error ? error.message : "Playback failed.";
        state.mediaPlayStatus.audiobooks = { state: "error", index: -1, message };
        setVoiceState("error", friendlyVoiceError(message));
      }
      await reloadCurrentPage();
    });
  }
  for (const button of document.querySelectorAll("[data-sleep-timer-minutes]")) {
    button.addEventListener("click", async () => {
      const minutes = Number(button.dataset.sleepTimerMinutes || 0);
      try {
        if (state.audiobookPlaybackActive) {
          await setAudiobookSleepTimer(minutes);
          state.pendingAudiobookSleepTimerMinutes = 0;
        } else {
          state.pendingAudiobookSleepTimerMinutes = minutes;
        }
        setVoiceState("ready", "Tap to talk");
      } catch (error) {
        const message = error instanceof Error ? error.message : "Sleep timer failed.";
        setVoiceState("error", friendlyVoiceError(message));
      }
      await (state.audiobookPlaybackActive ? reloadCurrentPage() : loadCurrentPage());
    });
  }
  for (const button of document.querySelectorAll("[data-sleep-timer-cancel]")) {
    button.addEventListener("click", async () => {
      await apiPost("/api/ui/audio/sleep-timer", {
        client_id: state.clientId,
        target: state.sourceId,
        operation: "cancel",
        minutes: null,
      });
      state.pendingAudiobookSleepTimerMinutes = 0;
      await reloadCurrentPage();
    });
  }
}

function selectedPendingAudiobookSleepTimerMinutes() {
  const minutes = Number(state.pendingAudiobookSleepTimerMinutes || 0);
  return minutes > 0 ? minutes : null;
}

async function stopSatelliteAudio(mediaKind = null) {
  return controlSatelliteAudio("stop", mediaKind);
}

async function controlSatelliteAudio(operation, mediaKind = null) {
  const payload = await apiPost("/api/ui/audio/control", {
    client_id: state.clientId,
    target: state.sourceId,
    operation,
    media_kind: mediaKind,
  });
  if (payload.ok !== true) {
    throw new Error(extractUiAudioFailureMessage(payload));
  }
  return payload;
}

async function applyPendingAudiobookSleepTimer() {
  const minutes = selectedPendingAudiobookSleepTimerMinutes();
  if (!minutes) {
    return null;
  }
  const payload = await setAudiobookSleepTimer(minutes);
  state.pendingAudiobookSleepTimerMinutes = 0;
  return payload;
}

async function setAudiobookSleepTimer(minutes) {
  const payload = await apiPost("/api/ui/audio/sleep-timer", {
    client_id: state.clientId,
    target: state.sourceId,
    operation: "set",
    minutes,
  });
  if (payload.ok !== true) {
    throw new Error(extractUiAudioFailureMessage(payload));
  }
  return payload;
}

function wireVoiceSearchButtons() {
  for (const button of document.querySelectorAll("[data-voice-search]")) {
    button.addEventListener("click", async () => {
      const kind = String(button.dataset.voiceSearch || "").trim();
      const action = kind === "audiobooks" ? "audiobook_search" : "music_search";
      noteVoiceEvent("contextual_search_requested", { kind, action });
      try {
        await startUiContext(action);
      } catch (error) {
        const message = error instanceof Error ? error.message : "Unable to start search.";
        noteVoiceEvent("contextual_search_failed", { message });
        setVoiceState("error", friendlyVoiceError(message));
      }
    });
  }
}

function extractUiAudioFailureMessage(payload) {
  const result = payload?.result || {};
  return String(
    payload?.detail ||
      result.detail ||
      result.failure_detail ||
      result.error ||
      "Playback failed."
  );
}

async function apiGet(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

async function apiPost(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

async function apiForm(path, body) {
  const response = await fetch(path, { method: "POST", body });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

async function readError(response) {
  const text = await response.text();
  if (!text.trim()) {
    return `HTTP ${response.status}`;
  }
  try {
    const payload = JSON.parse(text);
    return typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload);
  } catch {
    return text.trim();
  }
}

function renderFatal(message) {
  elements.pageRoot.innerHTML = `<div class="empty-state">${escapeHtml(message)}</div>`;
  setVoiceState("error", message);
}

function renderHomeWeatherMeta(weather) {
  const bits = [];
  if (weather.freshness_class) {
    bits.push(capitalize(String(weather.freshness_class)));
  }
  if (weather.humidity_pct != null) {
    bits.push(`Humidity ${Math.round(Number(weather.humidity_pct))}%`);
  }
  return bits.length ? `<p class="mini-copy">${escapeHtml(bits.join(" / "))}</p>` : "";
}

function renderHomeCalendarCard(event) {
  if (!event) {
    return `<h3>No upcoming events</h3>`;
  }
  return `
    <div class="calendar-preview">
      <div class="calendar-preview__time">${escapeHtml(formatCalendarTime(event, "upcoming"))}</div>
      <h3>${escapeHtml(String(event.summary || "Upcoming event"))}</h3>
      ${renderCalendarMeta(event, "upcoming")}
    </div>
  `;
}

function renderCalendarMeta(item, mode) {
  const label = formatCalendarMeta(item, mode);
  return label ? `<p class="mini-copy">${escapeHtml(label)}</p>` : "";
}

function formatCalendarTime(item, mode) {
  if (item?.all_day) {
    return mode === "today" ? "All day" : weekdayLabel(item.start);
  }
  const date = parseDate(item?.start);
  if (!date) {
    return mode === "today" ? "" : "Upcoming";
  }
  const time = new Intl.DateTimeFormat([], { hour: "numeric", minute: "2-digit" }).format(date);
  if (mode === "today") {
    return time;
  }
  return `${weekdayLabel(item.start)} ${time}`;
}

function formatCalendarMeta(item, mode) {
  if (mode === "today") {
    return "";
  }
  if (item?.all_day) {
    return "All day";
  }
  const end = parseDate(item?.end);
  if (!end) {
    return "";
  }
  return `Ends ${new Intl.DateTimeFormat([], { hour: "numeric", minute: "2-digit" }).format(end)}`;
}

function formatDuration(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainingSeconds = total % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`;
  }
  return `${minutes}:${String(remainingSeconds).padStart(2, "0")}`;
}

function remainingSecondsUntil(dueAt) {
  const text = String(dueAt || "").trim();
  if (!text) {
    return null;
  }
  const due = new Date(text);
  if (Number.isNaN(due.getTime())) {
    return null;
  }
  return Math.max(0, Math.ceil((due.getTime() - Date.now()) / 1000));
}

function formatSleepTimerCountdown(timer) {
  const remaining = remainingSecondsUntil(timer?.due_at);
  if (remaining == null) {
    return String(timer?.remaining_label || "Active");
  }
  return formatSleepTimerRemaining(remaining);
}

function formatSleepTimerRemaining(seconds) {
  const remaining = Math.max(0, Number(seconds) || 0);
  return remaining <= 0 ? "Stopping now" : formatDuration(remaining);
}

function formatSleepTimerDueLabel(dueAt) {
  const due = new Date(String(dueAt || ""));
  if (Number.isNaN(due.getTime())) {
    return "";
  }
  const time = new Intl.DateTimeFormat([], { hour: "numeric", minute: "2-digit" }).format(due);
  return `Ends at ${time}`;
}

function renderForecastFeature(item) {
  return `
    <div class="forecast-feature">
      <h2>${escapeHtml(item.name || "Forecast")}</h2>
      <div class="weather-card__temp">${item.temperature == null ? item.temperature_f == null ? "--" : `${Math.round(Number(item.temperature_f))}°` : `${Math.round(Number(item.temperature))}°`}</div>
      <p class="section-copy section-copy--lead">${escapeHtml(item.short_forecast || "Forecast unavailable.")}</p>
    </div>
  `;
}

function renderForecastMini(item) {
  return `
    <div class="info-tile">
      <div class="list-item__title">${escapeHtml(item.name || "Forecast")}</div>
      <div class="info-tile__value">${item.temperature == null ? item.temperature_f == null ? "--" : `${Math.round(Number(item.temperature_f))}°` : `${Math.round(Number(item.temperature))}°`}</div>
      <div class="mini-copy">${escapeHtml(item.short_forecast || "")}</div>
    </div>
  `;
}

function compactWeatherSummary(summary) {
  const text = String(summary || "").trim();
  if (!text) {
    return "Weather unavailable.";
  }
  const sentence = text
    .split(".")
    .map((part) => part.trim())
    .filter(Boolean)
    .find((part) => !/^it is currently\s+\d+/i.test(part)) || text;
  return capitalize(
    sentence
      .replace(/^it should stay\s+/i, "")
      .replace(/^it should be\s+/i, "")
      .replace(/^it will be\s+/i, "")
      .replace(/^it is\s+/i, ""),
  );
}

function compactDoorState(item) {
  const parts = [];
  if (item.lock_state) {
    parts.push(capitalize(String(item.lock_state)));
  }
  if (item.open_state) {
    parts.push(capitalize(String(item.open_state)));
  }
  return parts.join(" / ");
}

function compactLightState(item) {
  const state = String(item.state || item.status_label || "Unknown");
  if (state === "on" && item.brightness_pct != null) {
    return `On ${Math.round(Number(item.brightness_pct))}%`;
  }
  if (state === "off") {
    return "Off";
  }
  if (state === "unavailable") {
    return "Unavailable";
  }
  return normalizeStateLabel(state);
}

function lightIconForState(state) {
  return String(state || "").toLowerCase() === "on" ? "lightbulb" : "lightbulb";
}

function compactClimateState(item) {
  const target = item.target_temperature_f == null ? "--" : `${Math.round(Number(item.target_temperature_f))}°`;
  return `${capitalize(String(item.state || "Unknown"))} ${target}`;
}

function compactClimateDetail(item) {
  const bits = [];
  if (item.current_temperature_f != null) {
    bits.push(`Room ${Math.round(Number(item.current_temperature_f))}°`);
  }
  if (item.hvac_action) {
    bits.push(capitalize(String(item.hvac_action)));
  }
  return bits.join(" / ");
}

function eventDateKey(item) {
  const raw = String(item?.start || "");
  return raw.length >= 10 ? raw.slice(0, 10) : "";
}

function weekdayLabel(value) {
  const date = parseDate(value);
  return date ? new Intl.DateTimeFormat([], { weekday: "short" }).format(date) : "Soon";
}

function parseDate(value) {
  if (!value) {
    return null;
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function capitalize(text) {
  const value = String(text || "").trim();
  return value ? `${value.charAt(0).toUpperCase()}${value.slice(1)}` : "";
}

function normalizeStateLabel(value) {
  const raw = String(value || "").trim();
  if (!raw) {
    return "Unavailable";
  }
  const normalized = raw.replace(/^_+/, "").replaceAll("_", " ").trim().toLowerCase();
  if (!normalized) {
    return "Unavailable";
  }
  if (normalized === "on") {
    return "On";
  }
  if (normalized === "off") {
    return "Off";
  }
  if (normalized.startsWith("on ")) {
    return `On ${normalized.slice(3)}`;
  }
  if (normalized === "unavailable" || normalized === "unknown") {
    return "Unavailable";
  }
  if (normalized === "locked") {
    return "Locked";
  }
  if (normalized === "unlocked") {
    return "Unlocked";
  }
  if (normalized === "locking") {
    return "Locking";
  }
  if (normalized === "unlocking") {
    return "Unlocking";
  }
  return normalized
    .split(" ")
    .filter(Boolean)
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

function tileIcon(item) {
  const kind = String(item?.kind || "").toLowerCase();
  const status = normalizeStateLabel(item?.status_label || item?.state || "");
  if (kind === "lock") {
    return status === "Unlocked" ? "lock_open" : "lock";
  }
  return String(item?.icon || "tune");
}

function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}
