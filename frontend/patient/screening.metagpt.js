import { fetchWithAuth, debugLog, readJsonWithRequestId } from "./screening_auth.js";

const ui = window.screeningUI || {};
const META_TOTAL = 10;

let metagptSessionId = null;
let metagptStep = 0;
let metagptBusy = false;
let metagptCompleted = false;
let NEXT_IN_FLIGHT = false;
let LAST_NEXT_TS = 0;

function pickQuestion(data) {
  return (
    data?.question ||
    data?.data?.question ||
    data?.text ||
    data?.data?.text ||
    ""
  );
}

function pickTtsUrl(data) {
  return (
    data?.tts_url ||
    data?.data?.tts_url ||
    data?.audio_url ||
    data?.data?.audio_url ||
    null
  );
}

function isNeedsClarification(data) {
  return (
    data?.type === "needs_clarification" ||
    data?.invalid_answer === true ||
    data?.error === "invalid_answer"
  );
}

function isNeedsRestart(data) {
  return data?.type === "needs_restart";
}

function isRepeatQuestion(data) {
  return data?.type === "repeat_question";
}

function buildClarificationMessage(data) {
  const fallback = "Please provide a more specific answer.";
  const assistantText =
    data?.assistant?.text ||
    data?.message ||
    data?.hint ||
    (data?.error && data?.error !== "invalid_answer" ? data.error : "") ||
    "";
  const assistantQuestion = data?.assistant?.question || data?.question || "";
  let combined = assistantText;
  if (assistantQuestion && assistantQuestion !== assistantText) {
    combined = combined ? `${assistantText} ${assistantQuestion}` : assistantQuestion;
  }
  if (!combined) {
    combined = fallback;
  }
  return { text: assistantText || combined, question: assistantQuestion, combined };
}

function appendClarificationMessage(data) {
  const clarification = buildClarificationMessage(data);
  const ttsUrl = pickTtsUrl(data);
  if (clarification.text) {
    ui.appendAIMessage?.(clarification.text, { ttsUrl });
  }
  if (clarification.question && clarification.question !== clarification.text) {
    ui.appendAIMessage?.(clarification.question, { ttsUrl });
  }
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
  debugLog(`[MetaGPT] url=${path} status=${resp.status}`);
  if (resp.status === 401 || resp.status === 403) {
    const authError = new Error("auth_expired");
    authError.status = resp.status;
    authError.authExpired = true;
    throw authError;
  }
  const { data, requestId } = await readJsonWithRequestId(resp);
  if (!resp.ok || data?.ok === false) {
    const msg =
      data?.message || data?.error || `Request failed (HTTP ${resp.status})`;
    const error = new Error(msg);
    error.status = resp.status;
    error.data = data;
    error.requestId = requestId;
    error.url = path;
    throw error;
  }
  return data;
}

function markReady(message) {
  metagptCompleted = true;
  ui.setReadyToGenerate?.(true, message);
  ui.setMetaBusy?.(false);
  ui.setProgress?.(metagptStep || 1, META_TOTAL);
  updateUploadStatus("success", { session_id: metagptSessionId });
}

function updateUploadStatus(status, data = {}) {
  const card = document.getElementById("uploadStatusCard");
  const icon = document.getElementById("uploadStatusIcon");
  const text = document.getElementById("uploadStatusText");
  const detail = document.getElementById("uploadStatusDetail");
  const message = document.getElementById("uploadStatusMessage");

  if (!card || !icon || !text || !detail || !message) return;

  card.style.display = "block";

  const statusMap = {
    idle: { icon: "...", text: "Upload status: idle", color: "#64748b" },
    generating: { icon: "GEN", text: "Upload status: generating", color: "#f59e0b" },
    uploading: { icon: "UP", text: "Upload status: uploading", color: "#3b82f6" },
    success: { icon: "OK", text: "Upload status: success", color: "#10b981" },
    failed: { icon: "ERR", text: "Upload status: failed", color: "#ef4444" },
  };

  const statusInfo = statusMap[status] || statusMap.idle;
  icon.textContent = statusInfo.icon;
  text.textContent = statusInfo.text;
  text.style.color = statusInfo.color;

  if (status === "success") {
    const reportId = data?.report_id || data?.session_id || metagptSessionId || "N/A";
    const createdAt = data?.createdAt ? new Date(data.createdAt).toLocaleString() : new Date().toLocaleString();
    detail.textContent = `Report ID: ${reportId} | Created: ${createdAt}`;
    message.style.display = "block";
  } else if (status === "failed") {
    detail.textContent = data?.error || "Upload failed. Please check the Report page.";
    message.style.display = "none";
  } else if (status === "generating") {
    detail.textContent = "Generating report PDF...";
    message.style.display = "none";
  } else if (status === "uploading") {
    detail.textContent = "Uploading report to Firebase...";
    message.style.display = "none";
  } else {
    detail.textContent = "";
    message.style.display = "none";
  }
}

async function resolveNext(payload) {
  let data = await requestJson("/api/screen/metagpt/next", payload);
  if (isNeedsRestart(data)) {
    return { data, needsRestart: true };
  }
  if (isRepeatQuestion(data)) {
    return { data, repeatQuestion: true };
  }
  if (isNeedsClarification(data)) {
    return { data, needsClarification: true };
  }
  let question = pickQuestion(data);
  const doneFlag = Boolean(data?.done || data?.finished || data?.end);
  const isDone =
    Boolean(data?.done || data?.finished || data?.end) ||
    !question ||
    !String(question).trim();

  if (isDone) {
    updateUploadStatus("success", data);
  }

  return {
    data,
    question,
    isDone,
    needsClarification: false,
    needsRestart: false,
    repeatQuestion: false,
  };
}

async function startMetagptSession() {
  ui.clearError?.();
  metagptBusy = true;
  ui.setMetaBusy?.(true);
  ui.setStatus?.("Processing", "Starting screening session...");
  updateUploadStatus("idle");
  try {
    const data = await requestJson("/api/screen/metagpt/start", {});
    metagptSessionId = data.session_id;
    metagptStep = Number(data.step || 1);
    metagptCompleted = false;
    ui.setReadyToGenerate?.(false);
    ui.setMetaSessionId?.(metagptSessionId);

    const question = pickQuestion(data);
    if (!question) {
      throw new Error("Screening response missing question");
    }

    const ttsUrl = pickTtsUrl(data);
    ui.appendAIMessage?.(question, { ttsUrl });
    ui.setProgress?.(metagptStep, META_TOTAL);
    ui.setStatus?.("Idle", "Screening active");
  } catch (err) {
    if (err?.authExpired || err?.status === 401 || err?.status === 403) {
      return;
    }
    metagptSessionId = null;
    ui.setError?.(err.message || "Failed to start screening session.");
    ui.appendSystemMessage?.(err.message || "Failed to start screening session.");
    updateUploadStatus("failed", { error: err.message });
  } finally {
    metagptBusy = false;
    ui.setMetaBusy?.(false);
  }
}

async function submitAnswer(answer) {
  if (!metagptSessionId) {
    await startMetagptSession();
    if (!metagptSessionId) return;
  }
  if (metagptCompleted) {
    markReady("All questions completed. Ready to generate report.");
    return;
  }
  const trimmed = String(answer || "").trim();
  if (!trimmed) {
    ui.appendSystemMessage?.("Please provide an answer before continuing.");
    return;
  }
  ui.clearError?.();
  const now = Date.now();
  if (NEXT_IN_FLIGHT) {
    console.warn("[metagpt] next blocked: in flight");
    return;
  }
  if (now - LAST_NEXT_TS < 800) {
    console.warn("[metagpt] next blocked: debounce");
    return;
  }
  LAST_NEXT_TS = now;
  NEXT_IN_FLIGHT = true;
  metagptBusy = true;
  ui.setMetaBusy?.(true);
  ui.setStatus?.("Processing", "Submitting answer...");
  if (ui.sendBtn) ui.sendBtn.disabled = true;
  if (ui.input) ui.input.disabled = true;
  let nextStatus = "ok";
  try {
    const payload = {
      session_id: metagptSessionId,
      answer: trimmed,
      step: metagptStep,
    };
    console.info("[metagpt] next start", {
      ts: now,
      sessionIdPresent: Boolean(metagptSessionId),
      answerLen: trimmed.length,
    });
    ui.recordAnswer?.(ui.getCurrentQuestion?.() || "", trimmed, "metagpt");
    const { data, question, isDone, needsClarification, needsRestart, repeatQuestion } =
      await resolveNext(payload);
    if (needsRestart) {
      const restartText =
        data?.assistant?.text ||
        "Session expired. Please start screening again.";
      ui.appendSystemMessage?.(restartText);
      await startMetagptSession();
      nextStatus = "needs_restart";
      return;
    }
    if (repeatQuestion) {
      const repeatText = data?.assistant?.text || data?.question || "Please answer the current question.";
      ui.appendAIMessage?.(repeatText, { ttsUrl: pickTtsUrl(data) });
      ui.resetAnswer?.();
      ui.setReadyToGenerate?.(false);
      ui.setStatus?.("Idle", "Repeating the previous question");
      nextStatus = "repeat_question";
      return;
    }
    if (needsClarification) {
      appendClarificationMessage(data);
      ui.resetAnswer?.();
      ui.setReadyToGenerate?.(false);
      ui.setStatus?.("Idle", "Awaiting a more specific answer");
      nextStatus = "needs_clarification";
      return;
    }
    if (isDone) {
      updateUploadStatus("generating");
      ui.appendSystemMessage?.("All questions completed. Preparing your report...");
      setTimeout(() => {
        updateUploadStatus("uploading");
        setTimeout(() => {
          markReady("All questions completed. Ready to generate report.");
        }, 1000);
      }, 500);
      nextStatus = "done";
      return;
    }
    metagptStep += 1;
    const ttsUrl = pickTtsUrl(data);
    ui.appendAIMessage?.(question, { ttsUrl });
    ui.resetAnswer?.();
    ui.setReadyToGenerate?.(false);
    ui.setProgress?.(metagptStep, META_TOTAL);
    ui.setStatus?.("Idle", `Question ${metagptStep} ready`);
  } catch (err) {
    nextStatus = "error";
    if (err?.authExpired || err?.status === 401 || err?.status === 403) {
      return;
    }
    if (err?.data?.error === "session_state_missing") {
      ui.appendSystemMessage?.("Session expired. Restarting screening.");
      await startMetagptSession();
      nextStatus = "session_missing";
      return;
    }
    if (isNeedsClarification(err?.data)) {
      appendClarificationMessage(err.data);
      ui.resetAnswer?.();
      ui.setReadyToGenerate?.(false);
      ui.setStatus?.("Idle", "Awaiting a more specific answer");
      return;
    }
    if (isRepeatQuestion(err?.data)) {
      const repeatText =
        err?.data?.assistant?.text ||
        err?.data?.question ||
        "Please answer the current question.";
      ui.appendAIMessage?.(repeatText, { ttsUrl: pickTtsUrl(err?.data) });
      ui.resetAnswer?.();
      ui.setReadyToGenerate?.(false);
      ui.setStatus?.("Idle", "Repeating the previous question");
      return;
    }
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
    const statusCode = err?.status || 0;
    const status = statusCode ? `HTTP ${statusCode}` : "Network error";
    const url = err?.url || "/api/screen/metagpt/next";
    const requestId = err?.data?.request_id || err?.requestId || "";
    const detail =
      err?.data?.message ||
      err?.data?.error ||
      err.message ||
      "Failed to submit answer.";
    const serverMessage =
      statusCode >= 500
        ? `Server error (${statusCode}). request_id=${requestId || "unknown"}. Check backend logs.`
        : detail;
    const message = `${serverMessage} (${status}, ${url})`;
    const hint = err?.data?.hint || err?.data?.question || "";
    if (hint) {
      ui.appendAIMessage?.(hint, { ttsUrl: pickTtsUrl(err?.data) });
    }
    ui.setError?.(message);
    ui.appendSystemMessage?.(message);
  } finally {
    console.info("[metagpt] next end", { ts: Date.now(), status: nextStatus });
    NEXT_IN_FLIGHT = false;
    if (ui.input) ui.input.disabled = false;
    if (ui.sendBtn) ui.sendBtn.disabled = false;
    metagptBusy = false;
    ui.setMetaBusy?.(false);
  }
}

export async function startScreening() {
  if (metagptBusy) return;
  await startMetagptSession();
}

window.screeningMetagpt = {
  startScreening,
  startSession: startMetagptSession,
  submitAnswer,
  isBusy: () => metagptBusy,
};
