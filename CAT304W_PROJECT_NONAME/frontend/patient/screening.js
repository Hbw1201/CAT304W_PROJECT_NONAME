import { fetchWithAuth, debugLog } from "./screening.auth.js";

const ANSWER_PLACEHOLDER =
  "Tap the mic and speak your answer. We'll show the transcription here.";

const defaultQuestions = [
  "Please describe any symptoms or discomfort you felt in the last 24 hours.",
  "Have you experienced dizziness or shortness of breath recently?",
  "Rate your current pain level from zero to ten and describe where it is located.",
];

const ui = {
  playBtn: document.getElementById("play-question"),
  recordBtn: document.getElementById("record-toggle"),
  statusPill: document.getElementById("record-status"),
  statusDetail: document.getElementById("screenStatus"),
  errorBanner: document.getElementById("screenError"),
  answerEl: document.getElementById("qa-answer"),
  answerInput: document.getElementById("answerInput"),
  questionEl: document.getElementById("questionText"),
  nextBtn: document.getElementById("next-question"),
  progressPill: document.getElementById("question-progress"),
  voiceModeBtn: document.getElementById("voice-mode"),
  metagptModeBtn: document.getElementById("metagptBtn"),
  finishBtn: document.getElementById("finish-screening"),
};

const state = {
  voiceSessionId: null,
  mediaStream: null,
  mediaRecorder: null,
  chunks: [],
  recording: false,
  metaBusy: false,
  hasAnswer: false,
  readyToGenerate: false,
  mode: "voice",
  questionIndex: 0,
  answers: [],
  metaSessionId: null,
};

function setStatus(stateLabel, detail = "") {
  const label = String(stateLabel || "Idle");
  const normalized = label.toLowerCase();
  const pillLabel = normalized === "recording" ? "Stop Recording" : label;
  if (ui.statusPill) {
    ui.statusPill.textContent = pillLabel;
    ui.statusPill.dataset.state = normalized;
    ui.statusPill.setAttribute(
      "aria-pressed",
      normalized === "recording" ? "true" : "false"
    );
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
  if (!ui.progressPill) return;
  const current = Number(step) || 1;
  const max = Number(total) || 10;
  ui.progressPill.textContent = `Q ${current}/${max}`;
}

function setVoiceProgress() {
  setProgress(state.questionIndex + 1, defaultQuestions.length);
}

function setMode(mode) {
  const nextMode = mode === "metagpt" ? "metagpt" : "voice";
  state.mode = nextMode;
  if (ui.voiceModeBtn) {
    ui.voiceModeBtn.classList.toggle("active", nextMode === "voice");
  }
  if (ui.metagptModeBtn) {
    ui.metagptModeBtn.classList.toggle("active", nextMode === "metagpt");
  }
}

function updateActionState() {
  const hasAnyAnswer = state.hasAnswer || state.answers.length > 0;
  if (ui.nextBtn) {
    ui.nextBtn.disabled =
      state.recording || state.metaBusy || state.readyToGenerate;
  }
  if (ui.finishBtn) {
    ui.finishBtn.disabled = !hasAnyAnswer || state.recording || state.metaBusy;
  }
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
  if (ui.nextBtn) {
    ui.nextBtn.textContent = state.readyToGenerate ? "Completed" : "Next Question";
  }
  if (state.readyToGenerate) {
    setStatus("Ready", message || "All questions completed. Ready to generate report.");
  }
  updateActionState();
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

function setQuestion(text) {
  if (ui.questionEl) {
    ui.questionEl.textContent = text || "";
  }
}

function resetAnswer() {
  if (ui.answerEl) {
    ui.answerEl.textContent = ANSWER_PLACEHOLDER;
  }
  if (ui.answerInput) {
    ui.answerInput.value = "";
  }
  setHasAnswer(state.answers.length > 0);
}

function getAnswerValue() {
  const typed = (ui.answerInput?.value || "").trim();
  if (typed) return typed;
  const transcript = (ui.answerEl?.textContent || "").trim();
  if (!transcript || transcript === ANSWER_PLACEHOLDER) return "";
  return transcript;
}

function syncAnswerState() {
  setHasAnswer(Boolean(getAnswerValue()) || state.answers.length > 0);
}

function speakQuestion() {
  const text = ui.questionEl?.textContent || "";
  if (!text || !window.speechSynthesis) return;
  const utter = new SpeechSynthesisUtterance(text);
  utter.lang = "en-US";
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utter);
}

async function requestJson(path, payload) {
  const resp = await fetchWithAuth(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  debugLog(`[screening voice] mode=voice url=${path} status=${resp.status}`);
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
  debugLog(`[screening voice] mode=voice url=${path} status=${resp.status}`);
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
  if (!ui.recordBtn) return;
  ui.recordBtn.disabled = true;
  clearError();
  setStatus("Processing", "Starting recording...");
  try {
    await ensureMediaStream();
    const startResp = await requestJson("/api/screen/voice/start", {
      session_id: state.voiceSessionId,
      language: "en",
    });
    state.voiceSessionId = startResp.session_id;

    state.chunks = [];
    state.mediaRecorder = new MediaRecorder(state.mediaStream, {
      mimeType: "audio/webm",
    });
    state.mediaRecorder.ondataavailable = (evt) => {
      if (evt.data && evt.data.size) state.chunks.push(evt.data);
    };
    state.mediaRecorder.onstop = handleRecordingStop;
    state.mediaRecorder.start();
    setRecording(true);
    ui.recordBtn.textContent = "Stop Recording";
    setStatus("Recording", "Recording...");
  } catch (err) {
    console.error("voice start error", err);
    setError(err.message || "Unable to start recording");
    setRecording(false);
  } finally {
    ui.recordBtn.disabled = false;
  }
}

async function handleRecordingStop() {
  if (!ui.recordBtn) return;
  setRecording(false);
  ui.recordBtn.textContent = "Start Recording";
  setStatus("Processing", "Transcribing...");
  if (!state.chunks.length) {
    setError("No audio captured");
    return;
  }
  const blob = new Blob(state.chunks, { type: "audio/webm" });
  const form = new FormData();
  if (state.voiceSessionId) form.append("session_id", state.voiceSessionId);
  form.append("audio", blob, "answer.webm");
  try {
    const data = await requestForm("/api/screen/voice/stop", form);
    const transcript = data.text?.trim() || "No speech detected.";
    if (ui.answerEl) {
      ui.answerEl.textContent = transcript;
    }
    if (ui.answerInput) {
      ui.answerInput.value = transcript;
    }
    setStatus("Idle", "Transcription complete");
    syncAnswerState();
  } catch (err) {
    console.error("voice stop error", err);
    setError(err.message || "Transcription failed");
  } finally {
    state.chunks = [];
  }
}

async function stopVoiceRecording() {
  if (!state.mediaRecorder || !ui.recordBtn) return;
  ui.recordBtn.disabled = true;
  try {
    state.mediaRecorder.stop();
  } catch (err) {
    setError("Unable to stop recorder");
  } finally {
    setRecording(false);
    ui.recordBtn.disabled = false;
    ui.recordBtn.textContent = "Start Recording";
  }
}

async function handleRecordToggle() {
  if (state.recording) {
    await stopVoiceRecording();
  } else {
    await beginVoiceRecording();
  }
}

async function handlePlayClick() {
  if (state.recording) {
    await stopVoiceRecording();
  }
  speakQuestion();
}

function cycleDefaultQuestion() {
  if (!ui.questionEl) return;
  recordAnswer(ui.questionEl.textContent, getAnswerValue(), "voice");
  state.questionIndex = (state.questionIndex + 1) % defaultQuestions.length;
  setQuestion(defaultQuestions[state.questionIndex]);
  setVoiceProgress();
  resetAnswer();
  setStatus("Idle", "");
  setReadyToGenerate(false);
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
  setMode("voice");
  setVoiceProgress();
  resetAnswer();
  setStatus("Idle", "");
  setReadyToGenerate(false);
  updateActionState();

  ui.playBtn?.addEventListener("click", handlePlayClick);
  ui.recordBtn?.addEventListener("click", handleRecordToggle);
  ui.statusPill?.addEventListener("click", async () => {
    if (!state.recording) return;
    await stopVoiceRecording();
  });
  ui.nextBtn?.addEventListener("click", cycleDefaultQuestion);
  ui.answerInput?.addEventListener("input", syncAnswerState);
  ui.voiceModeBtn?.addEventListener("click", () => {
    setMode("voice");
    setVoiceProgress();
    setReadyToGenerate(false);
  });
}

init();

window.screeningUI = {
  setStatus,
  setError,
  clearError,
  setMetaBusy,
  setMode,
  setProgress,
  setVoiceProgress,
  setReadyToGenerate,
  setHasAnswer,
  syncAnswerState,
  recordAnswer,
  getAnswers,
  setMetaSessionId,
  getMetaSessionId,
  getMode,
  resetAnswer,
  setQuestion,
  getAnswerValue,
};
