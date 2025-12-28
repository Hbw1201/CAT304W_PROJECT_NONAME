import { fetchWithAuth, debugLog } from "./screening_auth.js";

const ui = {
  form: document.getElementById("screeningForm"),
  input: document.getElementById("screeningInput"),
  sendBtn: document.getElementById("screeningSend"),
  micBtn: document.getElementById("screeningMic"),
  messagesEl: document.getElementById("screeningMessages"),
  statusPill: document.getElementById("screenStatusPill"),
  statusDetail: document.getElementById("screenStatusDetail"),
  errorBanner: document.getElementById("screenError"),
};

const state = {
  voiceSessionId: null,
  mediaStream: null,
  mediaRecorder: null,
  chunks: [],
  recording: false,
  metaBusy: false,
  readyToGenerate: false,
  hasAnswer: false,
  mode: "metagpt",
  answers: [],
  metaSessionId: null,
  currentQuestion: "",
  messageCounter: 0,
  messageMeta: new Map(),
  ttsCache: new Map(),
  playingMessageId: null,
  playingButton: null,
};

let currentAudio = null;
let currentSpeech = null;

function buildMessageId(prefix = "msg") {
  state.messageCounter += 1;
  return `${prefix}-${Date.now()}-${state.messageCounter}`;
}

function scrollToBottom() {
  if (!ui.messagesEl) return;
  ui.messagesEl.scrollTop = ui.messagesEl.scrollHeight;
}

function setStatus(stateLabel, detail = "") {
  const label = String(stateLabel || "Idle");
  const normalized = label.toLowerCase();
  if (ui.statusPill) {
    ui.statusPill.textContent = label;
    ui.statusPill.dataset.state = normalized;
  }
  if (ui.statusDetail) {
    ui.statusDetail.textContent = detail || "";
  }
  if (normalized !== "error") {
    clearError();
  }
}

function setError(message) {
  const text = String(message || "").trim();
  if (ui.errorBanner) {
    ui.errorBanner.textContent = text;
    ui.errorBanner.classList.toggle("show", Boolean(text));
  }
  if (text) {
    setStatus("Error", text);
  }
}

function clearError() {
  if (!ui.errorBanner) return;
  ui.errorBanner.textContent = "";
  ui.errorBanner.classList.remove("show");
}

function setProgress(step, total) {
  const current = Number(step) || 1;
  const max = Number(total) || 10;
  setStatus("Active", `Question ${current} of ${max}`);
}

function setMetaBusy(isBusy) {
  state.metaBusy = Boolean(isBusy);
  updateActionState();
}

function setRecording(isRecording) {
  state.recording = Boolean(isRecording);
  updateActionState();
}

function setHasAnswer(hasAnswer) {
  state.hasAnswer = Boolean(hasAnswer);
  updateActionState();
}

function setReadyToGenerate(isReady, message = "") {
  state.readyToGenerate = Boolean(isReady);
  if (state.readyToGenerate) {
    setStatus("Ready", message || "All questions completed.");
  }
  updateActionState();
}

function updateActionState() {
  const disabled = state.recording || state.metaBusy;
  if (ui.sendBtn) {
    ui.sendBtn.disabled = disabled;
  }
  if (ui.micBtn) {
    ui.micBtn.disabled = state.metaBusy;
  }
}

function recordAnswer(question, answer, mode) {
  const q = String(question || "").trim();
  const a = String(answer || "").trim();
  if (!q || !a) return;
  const entry = {
    question: q,
    answer: a,
    mode: mode || state.mode,
    timestamp: new Date().toISOString(),
  };
  const last = state.answers[state.answers.length - 1];
  if (last && last.question === entry.question && last.answer === entry.answer && last.mode === entry.mode) {
    return;
  }
  state.answers.push(entry);
  setHasAnswer(true);
}

function getAnswers() {
  return state.answers.slice();
}

function setMetaSessionId(sessionId) {
  state.metaSessionId = sessionId || null;
}

function getMetaSessionId() {
  return state.metaSessionId;
}

function getMode() {
  return state.mode;
}

function setMode(mode) {
  state.mode = mode === "voice" ? "voice" : "metagpt";
}

function setCurrentQuestion(text) {
  state.currentQuestion = text || "";
}

function getCurrentQuestion() {
  return state.currentQuestion;
}

function resetAnswer() {
  if (ui.input) {
    ui.input.value = "";
  }
  autoResize();
  setHasAnswer(false);
}

function getAnswerValue() {
  return (ui.input?.value || "").trim();
}

function appendSystemMessage(text) {
  if (!ui.messagesEl) return;
  const row = document.createElement("div");
  row.className = "chat-row system";
  const content = document.createElement("div");
  content.className = "chat-content";
  const bubble = document.createElement("div");
  bubble.className = "chat-bubble system";
  bubble.textContent = text;
  content.appendChild(bubble);
  row.appendChild(content);
  ui.messagesEl.appendChild(row);
  scrollToBottom();
}

function appendAIMessage(text, options = {}) {
  if (!ui.messagesEl) return null;
  const messageId = options.messageId || buildMessageId("ai");
  const ttsUrl = options.ttsUrl || null;

  state.messageMeta.set(messageId, { text, ttsUrl });
  if (ttsUrl) {
    state.ttsCache.set(messageId, ttsUrl);
  }

  const row = document.createElement("div");
  row.className = "chat-row ai";
  row.dataset.messageId = messageId;

  const avatar = document.createElement("div");
  avatar.className = "chat-avatar";
  avatar.textContent = "AI";

  const content = document.createElement("div");
  content.className = "chat-content";

  const bubble = document.createElement("div");
  bubble.className = "chat-bubble ai";
  bubble.textContent = text;

  const actions = document.createElement("div");
  actions.className = "chat-actions";

  const playBtn = document.createElement("button");
  playBtn.type = "button";
  playBtn.className = "play-btn";
  playBtn.textContent = "🔊 Play";
  playBtn.dataset.messageId = messageId;
  playBtn.addEventListener("click", () => {
    void speakMessage(messageId, text, playBtn);
  });

  actions.appendChild(playBtn);
  content.appendChild(bubble);
  content.appendChild(actions);
  row.appendChild(avatar);
  row.appendChild(content);
  ui.messagesEl.appendChild(row);
  scrollToBottom();
  setCurrentQuestion(text);
  return messageId;
}

function appendUserMessage(text) {
  if (!ui.messagesEl) return;
  const row = document.createElement("div");
  row.className = "chat-row user";

  const avatar = document.createElement("div");
  avatar.className = "chat-avatar";
  avatar.textContent = "You";

  const content = document.createElement("div");
  content.className = "chat-content";

  const bubble = document.createElement("div");
  bubble.className = "chat-bubble user";
  bubble.textContent = text;

  content.appendChild(bubble);
  row.appendChild(content);
  row.appendChild(avatar);
  ui.messagesEl.appendChild(row);
  scrollToBottom();
}

function setPlayButtonState(button, playing) {
  if (!button) return;
  button.classList.toggle("playing", playing);
  button.textContent = playing ? "⏸ Stop" : "🔊 Play";
}

function stopCurrentAudio() {
  if (currentAudio) {
    try {
      currentAudio.pause();
      currentAudio.currentTime = 0;
    } catch {
      // ignore stop errors
    }
    currentAudio = null;
  }
  if (currentSpeech && window.speechSynthesis) {
    window.speechSynthesis.cancel();
    currentSpeech = null;
  }
  if (state.playingButton) {
    setPlayButtonState(state.playingButton, false);
  }
  state.playingButton = null;
  state.playingMessageId = null;
}

function resolveTtsSource(messageId) {
  if (state.ttsCache.has(messageId)) {
    return state.ttsCache.get(messageId);
  }
  const meta = state.messageMeta.get(messageId);
  if (meta?.ttsUrl) {
    state.ttsCache.set(messageId, meta.ttsUrl);
    return meta.ttsUrl;
  }
  return null;
}

async function speakMessage(messageId, text, button) {
  if (!text) return;
  if (state.playingMessageId === messageId) {
    stopCurrentAudio();
    return;
  }

  stopCurrentAudio();

  const audioSrc = resolveTtsSource(messageId);
  if (!audioSrc) {
    if (!window.speechSynthesis) {
      appendSystemMessage("Audio playback is unavailable in this browser.");
      return;
    }
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = "en-US";
    utter.onend = () => {
      if (state.playingMessageId === messageId) {
        setPlayButtonState(button, false);
        state.playingMessageId = null;
        state.playingButton = null;
      }
    };
    currentSpeech = utter;
    state.playingMessageId = messageId;
    state.playingButton = button;
    setPlayButtonState(button, true);
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utter);
    return;
  }

  const audio = new Audio(audioSrc);
  currentAudio = audio;
  state.playingMessageId = messageId;
  state.playingButton = button;
  setPlayButtonState(button, true);
  audio.onended = () => {
    if (state.playingMessageId === messageId) {
      setPlayButtonState(button, false);
      state.playingMessageId = null;
      state.playingButton = null;
      currentAudio = null;
    }
  };
  audio.onerror = () => {
    setPlayButtonState(button, false);
    state.playingMessageId = null;
    state.playingButton = null;
    currentAudio = null;
    appendSystemMessage("Audio playback failed. Please try again.");
  };
  try {
    await audio.play();
  } catch {
    setPlayButtonState(button, false);
    state.playingMessageId = null;
    state.playingButton = null;
    currentAudio = null;
    appendSystemMessage("Audio playback failed. Please try again.");
  }
}

async function requestJson(path, payload) {
  const resp = await fetchWithAuth(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  debugLog(`[screening voice] url=${path} status=${resp.status}`);
  if (resp.status === 401 || resp.status === 403) {
    const authError = new Error("auth_expired");
    authError.status = resp.status;
    authError.authExpired = true;
    throw authError;
  }
  let data = {};
  try {
    data = await resp.json();
  } catch {
    data = {};
  }
  if (!resp.ok || data?.ok === false) {
    const msg = data?.error || data?.message || `Request failed (${resp.status})`;
    const error = new Error(msg);
    error.status = resp.status;
    error.data = data;
    throw error;
  }
  return data;
}

async function requestForm(path, formData) {
  const resp = await fetchWithAuth(path, {
    method: "POST",
    body: formData,
  });
  debugLog(`[screening voice] url=${path} status=${resp.status}`);
  if (resp.status === 401 || resp.status === 403) {
    const authError = new Error("auth_expired");
    authError.status = resp.status;
    authError.authExpired = true;
    throw authError;
  }
  let data = {};
  try {
    data = await resp.json();
  } catch {
    data = {};
  }
  if (!resp.ok || data?.ok === false) {
    const msg = data?.error || data?.message || `Request failed (${resp.status})`;
    const error = new Error(msg);
    error.status = resp.status;
    error.data = data;
    throw error;
  }
  return data;
}

async function ensureMediaStream() {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("Microphone access is not supported in this browser.");
  }
  if (!state.mediaStream) {
    state.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  }
}

async function beginVoiceRecording() {
  if (state.recording || !ui.micBtn) return;
  ui.micBtn.disabled = true;
  clearError();
  setStatus("Processing", "Starting voice capture...");
  try {
    await ensureMediaStream();
    const startResp = await requestJson("/api/screen/voice/start", {
      session_id: state.voiceSessionId,
      language: "en",
    });
    state.voiceSessionId = startResp.session_id;

    state.chunks = [];
    state.mediaRecorder = new MediaRecorder(state.mediaStream, { mimeType: "audio/webm" });
    state.mediaRecorder.ondataavailable = (evt) => {
      if (evt.data && evt.data.size) state.chunks.push(evt.data);
    };
    state.mediaRecorder.onstop = handleRecordingStop;
    state.mediaRecorder.start();
    setRecording(true);
    ui.micBtn.classList.add("recording");
    ui.micBtn.textContent = "Stop";
    setStatus("Recording", "Recording...");
  } catch (err) {
    if (err?.authExpired || err?.status === 401 || err?.status === 403) {
      return;
    }
    console.warn("voice start error", err);
    appendSystemMessage(err.message || "Unable to start recording.");
    setError(err.message || "Unable to start recording");
    setRecording(false);
  } finally {
    ui.micBtn.disabled = false;
  }
}

async function handleRecordingStop() {
  if (!ui.micBtn) return;
  setRecording(false);
  ui.micBtn.classList.remove("recording");
  ui.micBtn.textContent = "Mic";
  setStatus("Processing", "Transcribing...");
  if (!state.chunks.length) {
    appendSystemMessage("No audio captured. Please try again.");
    setError("No audio captured");
    return;
  }
  const blob = new Blob(state.chunks, { type: "audio/webm" });
  const form = new FormData();
  if (state.voiceSessionId) form.append("session_id", state.voiceSessionId);
  form.append("audio", blob, "answer.webm");
  try {
    const data = await requestForm("/api/screen/voice/stop", form);
    const transcript = data.text?.trim() || "";
    if (transcript) {
      if (ui.input) {
        ui.input.value = transcript;
        autoResize();
      }
      setStatus("Idle", "Transcription complete");
      setHasAnswer(true);
    } else {
      appendSystemMessage("No speech detected. Please try again.");
      setStatus("Idle", "No speech detected");
    }
  } catch (err) {
    if (err?.authExpired || err?.status === 401 || err?.status === 403) {
      return;
    }
    console.warn("voice stop error", err);
    appendSystemMessage(err.message || "Transcription failed.");
    setError(err.message || "Transcription failed");
  } finally {
    state.chunks = [];
  }
}

async function stopVoiceRecording() {
  if (!state.mediaRecorder || !ui.micBtn) return;
  ui.micBtn.disabled = true;
  try {
    state.mediaRecorder.stop();
  } catch {
    appendSystemMessage("Unable to stop recorder.");
    setError("Unable to stop recorder");
  } finally {
    setRecording(false);
    ui.micBtn.disabled = false;
    ui.micBtn.classList.remove("recording");
    ui.micBtn.textContent = "Mic";
  }
}

async function handleMicToggle() {
  if (state.recording) {
    await stopVoiceRecording();
  } else {
    await beginVoiceRecording();
  }
}

async function handleSubmit() {
  const trimmed = getAnswerValue();
  if (!trimmed || state.metaBusy || state.recording) return;
  appendUserMessage(trimmed);
  resetAnswer();
  setHasAnswer(false);
  try {
    const api = window.screeningMetagpt;
    if (api?.submitAnswer) {
      await api.submitAnswer(trimmed);
    } else {
      appendSystemMessage("Screening service is still initializing.");
    }
  } catch (err) {
    appendSystemMessage(err.message || "Failed to submit answer.");
  }
}

function autoResize() {
  if (!ui.input) return;
  ui.input.style.height = "auto";
  ui.input.style.height = `${Math.min(ui.input.scrollHeight, 160)}px`;
}

function hydrateBrand() {
  const profile = (() => {
    try {
      return JSON.parse(localStorage.getItem("userProfile") || "null");
    } catch {
      return null;
    }
  })();
  const name = (profile?.name || "osler").trim() || "osler";
  const initial = name.charAt(0).toUpperCase() || "O";
  const brandName = document.getElementById("brand-name");
  const brandAvatar = document.getElementById("brand-avatar");
  if (brandName) brandName.textContent = name;
  if (brandAvatar) brandAvatar.textContent = initial;
}

function attachLogout() {
  const logoutLink = document.getElementById("logout-link");
  logoutLink?.addEventListener("click", (e) => {
    e.preventDefault();
    localStorage.clear();
    window.location.href = "index.html";
  });
}

function init() {
  hydrateBrand();
  attachLogout();
  setMode("metagpt");
  setStatus("Idle", "Waiting for the first question...");
  updateActionState();
  autoResize();

  ui.micBtn?.addEventListener("click", () => {
    void handleMicToggle();
  });

  ui.form?.addEventListener("submit", (e) => {
    e.preventDefault();
    void handleSubmit();
  });

  ui.input?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSubmit();
    } else {
      setTimeout(autoResize, 0);
    }
  });

  ui.input?.addEventListener("input", () => {
    setHasAnswer(Boolean(getAnswerValue()));
    autoResize();
  });
}

init();

window.screeningUI = {
  appendAIMessage,
  appendUserMessage,
  appendSystemMessage,
  setStatus,
  setError,
  clearError,
  setMetaBusy,
  setMode,
  setProgress,
  setReadyToGenerate,
  setHasAnswer,
  recordAnswer,
  getAnswers,
  setMetaSessionId,
  getMetaSessionId,
  getMode,
  resetAnswer,
  getAnswerValue,
  setCurrentQuestion,
  getCurrentQuestion,
  stopCurrentAudio,
  speakMessage,
};
