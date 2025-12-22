import { fetchWithAuth, debugLog } from "./screening.auth.js";

const metagptBtn = document.getElementById("metagptBtn");
const voiceModeBtn = document.getElementById("voice-mode");
const nextBtn = document.getElementById("next-question");
const questionEl = document.getElementById("questionText");
const answerInput = document.getElementById("answerInput");

const ui = window.screeningUI || {};
const META_TOTAL = 10;

let metagptSessionId = null;
let metagptStep = 0;
let metagptActive = false;
let metagptBusy = false;
let metagptCompleted = false;
let emptyQuestionRetries = 0;

function setQuestion(text) {
  if (ui.setQuestion) {
    ui.setQuestion(text);
    return;
  }
  if (questionEl) {
    questionEl.textContent = text || "";
  }
}

function getAnswerValue() {
  if (ui.getAnswerValue) {
    return ui.getAnswerValue();
  }
  return (answerInput?.value || "").trim();
}

function pickQuestion(data) {
  return (
    data?.question ||
    data?.data?.question ||
    data?.text ||
    data?.data?.text ||
    ""
  );
}

async function requestJson(path, payload) {
  let resp;
  try {
    resp = await fetchWithAuth(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload ?? {}),
    });
  } catch (err) {
    throw new Error(`Network error (URL: ${path}): ${err.message || err}`);
  }
  debugLog(`[MetaGPT] mode=metagpt url=${path} status=${resp.status}`);
  let data = {};
  try {
    data = await resp.json();
  } catch {
    data = {};
  }
  if (!resp.ok || data?.ok === false) {
    const msg =
      data?.message || data?.error || `Request failed (HTTP ${resp.status})`;
    const error = new Error(msg);
    error.status = resp.status;
    error.data = data;
    error.url = path;
    throw error;
  }
  return data;
}

function markReady(message) {
  metagptCompleted = true;
  ui.setReadyToGenerate?.(true, message);
  ui.syncAnswerState?.();
  ui.setMetaBusy?.(false);
  ui.setMode?.("metagpt");
  ui.setProgress?.(metagptStep || 1, META_TOTAL);
}

async function resolveNext(payload) {
  let data = await requestJson("/api/screen/metagpt/next", payload);
  let question = pickQuestion(data);
  const doneFlag = Boolean(data?.done || data?.finished || data?.end);
  if (!doneFlag && (!question || !String(question).trim()) && emptyQuestionRetries < 1) {
    emptyQuestionRetries += 1;
    ui.setStatus?.("Processing", "No question returned, retrying...");
    data = await requestJson("/api/screen/metagpt/next", payload);
    question = pickQuestion(data);
  }
  const isDone =
    Boolean(data?.done || data?.finished || data?.end) ||
    !question ||
    !String(question).trim();
  return { data, question, isDone };
}

async function startMetagptSession() {
  ui.clearError?.();
  metagptBusy = true;
  ui.setMetaBusy?.(true);
  ui.setStatus?.("Processing", "MetaGPT: starting...");
  try {
    const data = await requestJson("/api/screen/metagpt/start", {});
    metagptSessionId = data.session_id;
    metagptStep = Number(data.step || 1);
    metagptActive = true;
    metagptCompleted = false;
    emptyQuestionRetries = 0;
    ui.setReadyToGenerate?.(false);
    ui.setMetaSessionId?.(metagptSessionId);

    const question = pickQuestion(data);
    if (!question) {
      throw new Error("MetaGPT response missing question");
    }
    setQuestion(question);
    ui.resetAnswer?.();
    ui.setMode?.("metagpt");
    ui.setProgress?.(metagptStep, META_TOTAL);
    ui.setStatus?.("Idle", "MetaGPT: Active");
  } catch (err) {
    metagptActive = false;
    metagptSessionId = null;
    ui.setError?.(err.message || "Failed to start MetaGPT session.");
  } finally {
    metagptBusy = false;
    ui.setMetaBusy?.(false);
  }
}

async function sendMetagptAnswer() {
  if (!metagptSessionId) {
    ui.setError?.("Please start MetaGPT before answering.");
    return;
  }
  if (metagptCompleted) {
    markReady("All questions completed. Ready to generate report.");
    return;
  }
  const answer = getAnswerValue();
  if (!answer) {
    ui.setError?.("Please provide an answer before continuing.");
    return;
  }
  ui.clearError?.();
  metagptBusy = true;
  ui.setMetaBusy?.(true);
  ui.setStatus?.("Processing", "Submitting answer...");
  try {
    const payload = {
      session_id: metagptSessionId,
      answer,
      step: metagptStep,
    };
    ui.recordAnswer?.(questionEl?.textContent || "", answer, "metagpt");
    const { data, question, isDone } = await resolveNext(payload);
    if (isDone) {
      markReady("All questions completed. Ready to generate report.");
      return;
    }
    emptyQuestionRetries = 0;
    metagptStep += 1;
    setQuestion(question);
    ui.resetAnswer?.();
    ui.setReadyToGenerate?.(false);
    ui.setProgress?.(metagptStep, META_TOTAL);
    ui.setStatus?.("Idle", `MetaGPT: Step ${metagptStep}`);
  } catch (err) {
    const answers = ui.getAnswers?.() || [];
    const errorDetail = err?.data?.detail || err?.message || "";
    const isTimeout = /timed out/i.test(errorDetail);
    const isUnreachable = /screen backend unreachable/i.test(
      err?.data?.error || err?.message || ""
    );
    if ((isTimeout || isUnreachable) && answers.length > 0) {
      ui.clearError?.();
      markReady("All questions completed. Ready to generate report.");
      return;
    }
    const status = err?.status ? `HTTP ${err.status}` : "Network error";
    const url = err?.url || "/api/screen/metagpt/next";
    const detail = err?.data?.message || err?.data?.error || err.message || "Failed to submit answer.";
    const message = `${detail} (${status}, ${url})`;
    if (err?.data?.question) {
      setQuestion(err.data.question);
    }
    ui.setError?.(message);
  } finally {
    metagptBusy = false;
    ui.setMetaBusy?.(false);
  }
}

metagptBtn?.addEventListener("click", async () => {
  if (metagptBusy) return;
  if (!metagptActive || metagptCompleted) {
    await startMetagptSession();
  } else {
    ui.setStatus?.("Idle", "MetaGPT: Active");
    ui.clearError?.();
  }
});

voiceModeBtn?.addEventListener("click", () => {
  metagptActive = false;
  metagptCompleted = false;
  metagptSessionId = null;
  metagptStep = 0;
  emptyQuestionRetries = 0;
  ui.setMetaBusy?.(false);
  ui.setMode?.("voice");
  ui.setVoiceProgress?.();
  ui.setStatus?.("Idle", "Voice mode active");
  ui.setReadyToGenerate?.(false);
});

nextBtn?.addEventListener(
  "click",
  (event) => {
    if (!metagptActive) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (metagptBusy) return;
    void sendMetagptAnswer();
  },
  true
);
