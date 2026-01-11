import { fetchWithAuth, debugLog } from "./screening_auth.js";

const ui = {
  form: document.getElementById("screeningForm"),
  input: document.getElementById("screeningInput"),
  sendBtn: document.getElementById("screeningSend"),
  micBtn: document.getElementById("screeningMic"),
  messagesEl: document.getElementById("screeningMessages"),
  hint: document.getElementById("screeningHint"),
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
  voiceBaseText: "",
  voiceTranscript: "",
};

let currentAudio = null;
let currentSpeech = null;
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const speechSupported = Boolean(SpeechRecognition);
const DEFAULT_PLACEHOLDER = "Type your answer...";
const LISTENING_PLACEHOLDER = "Listening... speak now";
let recognition = null;

function buildMessageId(prefix = "msg") {
  state.messageCounter += 1;
  return `${prefix}-${Date.now()}-${state.messageCounter}`;
}

function scrollToBottom() {
  if (!ui.messagesEl) return;
  ui.messagesEl.scrollTop = ui.messagesEl.scrollHeight;
}

function updateChatHint() {
  if (!ui.messagesEl || !ui.hint) return;
  const count = ui.messagesEl.querySelectorAll(".chat-row").length;
  ui.hint.hidden = count > 1;
}

function setInputValue(value) {
  if (!ui.input) return;
  ui.input.value = value;
  autoResize();
  const len = ui.input.value.length;
  if (typeof ui.input.setSelectionRange === "function") {
    try {
      ui.input.setSelectionRange(len, len);
    } catch {
      // ignore selection errors
    }
  }
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
  updateChatHint();
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
  setPlayButtonState(playBtn, false);
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
  updateChatHint();
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
  updateChatHint();
}

function setPlayButtonState(button, playing) {
  if (!button) return;
  button.classList.toggle("playing", playing);
  button.setAttribute("aria-pressed", playing ? "true" : "false");
  const icon = playing ? "&#9632;" : "&#9654;";
  const label = playing ? "Stop" : "Play";
  button.innerHTML = `<span class="play-icon" aria-hidden="true">${icon}</span><span>${label}</span>`;
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

function resolveSpeechError(event) {
  const code = String(event?.error || "");
  if (code === "not-allowed" || code === "service-not-allowed") {
    return "Microphone access was blocked. Please enable it and try again.";
  }
  if (code === "no-speech") {
    return "No speech detected. Please try again.";
  }
  if (code === "audio-capture") {
    return "No microphone was found. Please check your device.";
  }
  if (code === "network") {
    return "Network error. Please check your connection.";
  }
  return "Speech recognition failed. Please try again.";
}

function setListening(isListening) {
  state.recording = Boolean(isListening);
  updateActionState();
  if (ui.micBtn) {
    ui.micBtn.classList.toggle("mic--listening", state.recording);
    ui.micBtn.textContent = state.recording ? "Listening..." : "Mic";
    ui.micBtn.setAttribute("aria-pressed", state.recording ? "true" : "false");
  }
  if (ui.input) {
    ui.input.placeholder = state.recording ? LISTENING_PLACEHOLDER : DEFAULT_PLACEHOLDER;
  }
}

function initSpeechRecognition() {
  if (!speechSupported || recognition) return;
  recognition = new SpeechRecognition();
  recognition.lang = "en-US";
  recognition.interimResults = true;
  recognition.continuous = false;

  recognition.onstart = () => {
    setListening(true);
  };

  recognition.onresult = (event) => {
    let finalText = "";
    let interimText = "";
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const result = event.results[i];
      const transcript = result[0]?.transcript || "";
      if (result.isFinal) {
        finalText += transcript;
      } else {
        interimText += transcript;
      }
    }

    const base = state.voiceBaseText ? `${state.voiceBaseText} ` : "";
    const combined = `${base}${finalText}${interimText}`.trim();
    if (combined) {
      setInputValue(combined);
    }
    if (combined) {
      setHasAnswer(true);
    }
    if (finalText.trim()) {
      state.voiceTranscript = `${base}${finalText}`.trim();
    }
  };

  recognition.onerror = (event) => {
    setError(resolveSpeechError(event));
    setListening(false);
  };

  recognition.onend = () => {
    setListening(false);
  };
}

function startListening() {
  if (!speechSupported) return;
  initSpeechRecognition();
  if (!recognition) return;
  state.voiceBaseText = (ui.input?.value || "").trim();
  state.voiceTranscript = "";
  clearError();
  try {
    recognition.start();
  } catch (error) {
    setError("Unable to start speech recognition.");
    setListening(false);
  }
}

function stopListening() {
  if (!recognition) return;
  try {
    recognition.stop();
  } catch {
    setListening(false);
  }
}

async function handleMicToggle() {
  if (!speechSupported) {
    setError("Speech recognition is not supported in this browser.");
    return;
  }
  if (state.recording) {
    stopListening();
    return;
  }
  startListening();
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
  ui.input.style.height = `${Math.min(ui.input.scrollHeight, 120)}px`;
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
  updateChatHint();

  if (ui.input) {
    ui.input.placeholder = DEFAULT_PLACEHOLDER;
  }
  if (!speechSupported && ui.micBtn) {
    ui.micBtn.classList.add("mic--disabled");
    ui.micBtn.setAttribute("aria-disabled", "true");
    ui.micBtn.title = "Speech recognition is not supported in this browser.";
  } else {
    initSpeechRecognition();
  }

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
