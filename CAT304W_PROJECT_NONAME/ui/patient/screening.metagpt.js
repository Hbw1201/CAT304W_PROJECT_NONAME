const metagptBtn = document.getElementById("metagptBtn");
const nextBtn = document.getElementById("next-question");
const questionEl = document.getElementById("questionText");
const transcriptEl = document.getElementById("qa-answer");
const answerInput = document.getElementById("answerInput");
const statusEl = document.getElementById("record-status");
const errorEl = document.getElementById("screenError");

let metagptSessionId = null;
let metagptStep = 0;
let metagptActive = false;
let metagptBusy = false;

function setStatus(message, isError = false) {
  if (!statusEl) return;
  statusEl.textContent = message;
  statusEl.style.color = isError ? "#c0392b" : "";
}

function setError(message) {
  if (!errorEl) return;
  errorEl.textContent = message || "";
  errorEl.style.display = message ? "block" : "none";
}

function getAnswerValue() {
  const typed = (answerInput?.value || "").trim();
  if (typed) return typed;
  return (transcriptEl?.textContent || "").trim();
}

async function requestJson(path, payload) {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload ?? {}),
  });
  let data = {};
  try {
    data = await resp.json();
  } catch {
    throw new Error(`Request failed (${resp.status})`);
  }
  if (!resp.ok || data?.ok === false) {
    const msg = data?.error || data?.message || `Request failed (${resp.status})`;
    throw new Error(msg);
  }
  console.log(`[MetaGPT] ${path}`, data);
  return data;
}

async function startMetagptSession() {
  setError("");
  setStatus("MetaGPT: starting...");
  try {
    const data = await requestJson("/api/screen/metagpt/start", {});
    metagptSessionId = data.session_id;
    metagptStep = data.step || 1;
    metagptActive = true;
    if (questionEl) {
      questionEl.textContent = data.question || "MetaGPT questionnaire started.";
    }
    if (answerInput) {
      answerInput.value = "";
      answerInput.focus();
    }
    setStatus("MetaGPT: Active");
    metagptBtn?.classList.add("active");
  } catch (err) {
    metagptActive = false;
    setStatus("MetaGPT: unavailable", true);
    setError(err.message || "Failed to start MetaGPT session.");
    throw err;
  }
}

async function sendMetagptAnswer() {
  if (!metagptSessionId) {
    setError("Please start MetaGPT before answering.");
    return;
  }
  const answer = getAnswerValue();
  if (!answer) {
    setError("Please provide an answer before continuing.");
    return;
  }
  setError("");
  metagptBusy = true;
  setStatus("Submitting answer...");
  try {
    const data = await requestJson("/api/screen/metagpt/next", {
      session_id: metagptSessionId,
      answer,
      step: metagptStep,
    });
    if (data.done || !data.question) {
      setStatus("MetaGPT: Questionnaire complete");
      metagptActive = false;
      metagptSessionId = null;
      questionEl && (questionEl.textContent = data.question || "Questionnaire completed.");
      metagptBtn?.classList.remove("active");
      return;
    }
    metagptStep += 1;
    questionEl && (questionEl.textContent = data.question);
    if (answerInput) {
      answerInput.value = "";
      answerInput.focus();
    }
    setStatus(`MetaGPT: Step ${metagptStep}`);
  } catch (err) {
    setStatus("MetaGPT: error", true);
    setError(err.message || "Failed to submit answer.");
  } finally {
    metagptBusy = false;
  }
}

metagptBtn?.addEventListener("click", async () => {
  if (metagptBusy) return;
  if (!metagptActive) {
    try {
      await startMetagptSession();
    } catch {
      /* error handled in startMetagptSession */
    }
  } else {
    setStatus("MetaGPT: Active");
    setError("");
  }
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
