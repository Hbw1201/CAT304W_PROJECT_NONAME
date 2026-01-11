// A) Firebase setup: reuse existing config exports (no re-init).
import { auth, db, storage } from "./firebase-config.js";
import { onAuthStateChanged } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";
import {
  collection,
  doc,
  getDoc,
  onSnapshot,
  orderBy,
  query,
  where
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js";
import {
  getDownloadURL,
  listAll,
  ref
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-storage.js";

const ui = {
  studyList: document.getElementById("studyList"),
  studyListStatus: document.getElementById("studyListStatus"),
  studySearch: document.getElementById("studySearch"),
  studyCount: document.getElementById("ctStudyCount"),
  viewer: document.getElementById("dicomViewer"),
  viewerStatus: document.getElementById("viewerStatus"),
  frameIndicator: document.getElementById("frameIndicator"),
  btnPrev: document.getElementById("btnPrev"),
  btnNext: document.getElementById("btnNext"),
  btnZoomIn: document.getElementById("btnZoomIn"),
  btnZoomOut: document.getElementById("btnZoomOut"),
  btnRunAI: document.getElementById("btnRunAI"),
  studyStatusBadge: document.getElementById("studyStatusBadge"),
  infoStudyId: document.getElementById("infoStudyId"),
  infoPatientId: document.getElementById("infoPatientId"),
  infoCreatedAt: document.getElementById("infoCreatedAt"),
  infoStatus: document.getElementById("infoStatus"),
  infoAnalysisUpdated: document.getElementById("infoAnalysisUpdated"),
  analysisStatusBadge: document.getElementById("analysisStatusBadge"),
  analysisMeta: document.getElementById("analysisMeta"),
  analysisResult: document.getElementById("analysisResult"),
  riskSummary: document.getElementById("ctRiskSummary"),
  riskInterpretation: document.getElementById("ctRiskInterpretation"),
  viewerOverlay: document.getElementById("viewerOverlay")
};

const EMPTY_PLACEHOLDER = "\u2014";

const state = {
  uid: "",
  studies: [],
  filtered: [],
  selectedStudyId: "",
  stack: [],
  currentIndex: 0,
  loadToken: 0,
  viewerReady: false,
  runAiBusy: false,
  unsubSelected: null,
  unsubStudies: null,
  workerInitialized: false
};

const patientCache = new Map();

function formatTimestamp(value) {
  if (!value) return EMPTY_PLACEHOLDER;
  if (typeof value === "string") {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
  }
  if (typeof value.toDate === "function") {
    return value.toDate().toLocaleString();
  }
  if (typeof value.seconds === "number") {
    return new Date(value.seconds * 1000).toLocaleString();
  }
  return EMPTY_PLACEHOLDER;
}

function formatStatus(value, fallback = "Unknown") {
  const text = String(value || "").trim();
  if (!text) return fallback;
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function statusToVariant(value) {
  const status = String(value || "").toLowerCase();
  if (["pending", "analyzing", "queued", "uploaded", "running"].includes(status)) return "pending";
  if (["done", "completed", "ready", "analyzed"].includes(status)) return "done";
  if (["error", "failed"].includes(status)) return "error";
  return "";
}

function setViewerStatus(message) {
  if (!ui.viewerStatus) return;
  ui.viewerStatus.textContent = message;
  ui.viewerStatus.classList.toggle("hidden", !message);
}

function setText(el, value) {
  if (!el) return;
  el.textContent = value ?? "-";
}

function updateFrameIndicator() {
  if (!ui.frameIndicator) return;
  const total = state.stack.length;
  const current = total ? state.currentIndex + 1 : 0;
  ui.frameIndicator.textContent = `${current} / ${total}`;
  updateViewerOverlay();
}

function updateViewerControls() {
  const hasStack = state.stack.length > 0;
  if (ui.btnPrev) ui.btnPrev.disabled = !hasStack || state.currentIndex <= 0;
  if (ui.btnNext) ui.btnNext.disabled = !hasStack || state.currentIndex >= state.stack.length - 1;
  if (ui.btnZoomIn) ui.btnZoomIn.disabled = !hasStack;
  if (ui.btnZoomOut) ui.btnZoomOut.disabled = !hasStack;
}

function getSelectedStudy() {
  return state.studies.find((item) => item.id === state.selectedStudyId) || null;
}

function resolveSliceRisk(slices, currentIndex) {
  if (!slices?.length) return null;
  const direct = slices.find((item) => item?.index === currentIndex);
  if (direct && Number.isFinite(direct.prob_malignant)) return direct.prob_malignant;
  const fallback = slices[currentIndex];
  if (fallback && Number.isFinite(fallback.prob_malignant)) return fallback.prob_malignant;
  return null;
}

function updateViewerOverlay() {
  if (!ui.viewerOverlay) return;
  const total = state.stack.length;
  const current = total ? state.currentIndex + 1 : 0;
  const study = getSelectedStudy();
  const slices = resolveSlices(study);
  const risk = resolveSliceRisk(slices, state.currentIndex);
  ui.viewerOverlay.textContent = `Slice ${current} / ${total || 0} \u00b7 Risk ${formatPct(risk)}`;
}

function updateRunAiLabel(study) {
  if (!ui.btnRunAI) return;
  const status = String(study?.analysisStatus || "").toLowerCase();
  ui.btnRunAI.textContent = status === "analyzed" ? "Re-run AI analysis" : "Run AI analysis";
}

function setRunAiBusy(busy) {
  state.runAiBusy = Boolean(busy);
  if (!ui.btnRunAI) return;
  ui.btnRunAI.disabled = Boolean(busy);
  if (busy) {
    ui.btnRunAI.textContent = "Running AI analysis...";
    return;
  }
  const current = state.studies.find((item) => item.id === state.selectedStudyId);
  updateRunAiLabel(current);
}

function updateStudyCount() {
  if (!ui.studyCount) return;
  const count = state.filtered.length;
  ui.studyCount.textContent = `${count} studies`;
}

function updateStudyStatusBadge(status) {
  if (!ui.studyStatusBadge) return;
  const label = formatStatus(status, "No study");
  ui.studyStatusBadge.textContent = label;
  ui.studyStatusBadge.classList.add("status-pill");
  ui.studyStatusBadge.classList.remove("pending", "done", "error");
  const variant = statusToVariant(status);
  if (variant) ui.studyStatusBadge.classList.add(variant);
}

function resolveSlices(study) {
  if (Array.isArray(study?.slices)) return study.slices;
  const legacy = [];
  if (study && typeof study === "object") {
    for (const [key, value] of Object.entries(study)) {
      if (!/^\d+$/.test(key)) continue;
      if (typeof value !== "number") continue;
      legacy.push({ index: Number(key), file: "", prob_malignant: value });
    }
  }
  if (legacy.length) {
    legacy.sort((a, b) => a.index - b.index);
    return legacy;
  }
  const rawSlices =
    study?.analysis?.result?.slice_scores ||
    study?.analysisResult?.slice_scores ||
    study?.analysisResult?.slices;
  if (Array.isArray(rawSlices)) {
    return rawSlices.map((item) => ({
      index: typeof item?.index === "number" ? item.index : null,
      file: item?.file || "",
      prob_malignant: item?.prob_malignant
    }));
  }
  return [];
}

function formatPct(prob) {
  if (!Number.isFinite(prob)) return "\u2014";
  const pct = prob > 1 ? prob : prob * 100;
  return `${pct.toFixed(1)}%`;
}

function riskLevel(maxProb) {
  if (!Number.isFinite(maxProb)) {
    return { label: "Unknown", tone: "neutral", className: "risk-neutral" };
  }
  const normalized = maxProb > 1 ? maxProb / 100 : maxProb;
  if (normalized >= 0.7) {
    return { label: "High", tone: "high", className: "risk-high" };
  }
  if (normalized >= 0.4) {
    return { label: "Moderate", tone: "moderate", className: "risk-moderate" };
  }
  return { label: "Low", tone: "low", className: "risk-low" };
}

function interpretation(level) {
  if (level === "high") {
    return (
      "High-risk CT slices detected with elevated malignant probability. " +
      "Prompt clinical correlation and follow-up imaging recommended."
    );
  }
  if (level === "moderate") {
    return (
      "No high-risk CT slices detected. Mild to moderate abnormalities observed in isolated slices. " +
      "Clinical correlation recommended."
    );
  }
  if (level === "low") {
    return (
      "No high-risk CT slices detected. Findings suggest low malignant risk across analyzed slices. " +
      "Continue routine monitoring as appropriate."
    );
  }
  return "Run AI analysis to view clinical summary.";
}

function pickNumber(...values) {
  for (const value of values) {
    if (Number.isFinite(value)) return value;
  }
  return null;
}

function buildRiskSummary(study) {
  if (!study) return null;
  const summary = study.resultSummary || study.analysisResult?.resultSummary || study.analysis?.resultSummary || {};
  const slices = resolveSlices(study);
  const sliceProbs = slices
    .map((item) => item?.prob_malignant)
    .filter((value) => Number.isFinite(value));
  const maxFromSlices = sliceProbs.length ? Math.max(...sliceProbs) : null;
  const avgFromSlices = sliceProbs.length
    ? sliceProbs.reduce((acc, value) => acc + value, 0) / sliceProbs.length
    : null;
  const highRiskFromSlices = sliceProbs.filter((value) => value >= 0.7).length;

  const maxProb = pickNumber(
    summary.max_prob,
    summary.maxProb,
    summary.max_prob_malignant,
    summary.prob_malignant,
    study.max_prob,
    study.maxProb,
    study.max_prob_malignant,
    study.prob_malignant,
    maxFromSlices
  );
  const avgProb = pickNumber(
    summary.avg_prob,
    summary.avgProb,
    summary.mean_prob,
    study.avg_prob,
    study.avgProb,
    study.mean_prob,
    avgFromSlices
  );
  const highRisk = Number.isFinite(summary.high_risk_slices)
    ? summary.high_risk_slices
    : Number.isFinite(study.high_risk_slices)
      ? study.high_risk_slices
      : highRiskFromSlices || null;
  const totalSlices = Number.isFinite(summary.slices_analyzed)
    ? summary.slices_analyzed
    : Number.isFinite(summary.total_slices)
      ? summary.total_slices
      : Number.isFinite(study.total_slices)
        ? study.total_slices
        : slices.length || study.resultSlicesCount || null;

  const level = riskLevel(maxProb);
  return {
    maxProb,
    avgProb,
    highRisk,
    totalSlices,
    level
  };
}

function renderRiskSummary(data) {
  if (!ui.riskSummary) return;
  const safeData = data || {
    maxProb: null,
    avgProb: null,
    highRisk: null,
    totalSlices: null,
    level: { label: "Unknown", tone: "neutral", className: "risk-neutral" }
  };
  const { maxProb, avgProb, highRisk, totalSlices, level } = safeData;
  const totalLabel = totalSlices ?? "\u2014";
  const highRiskLabel = highRisk ?? "\u2014";
  const toneClasses = ["risk-low", "risk-moderate", "risk-high", "risk-neutral"];
  ui.riskSummary.classList.add("risk-card");
  ui.riskSummary.classList.remove(...toneClasses);
  ui.riskSummary.classList.add(level.className);
  ui.riskSummary.innerHTML = `
    <div class="risk-card-head">
      <div>
        <p class="risk-title">Risk Summary</p>
        <p class="risk-sub">Auto-generated from CT slice scores.</p>
      </div>
      <span class="risk-badge ${level.className}">${level.label}</span>
    </div>
    <div class="risk-grid">
      <div class="risk-metric">
        <span class="risk-label">Overall Risk</span>
        <span class="risk-value">${level.label}</span>
      </div>
      <div class="risk-metric">
        <span class="risk-label">Highest Slice Risk</span>
        <span class="risk-value">${formatPct(maxProb)}</span>
      </div>
      <div class="risk-metric">
        <span class="risk-label">Average Slice Risk</span>
        <span class="risk-value">${formatPct(avgProb)}</span>
      </div>
      <div class="risk-metric">
        <span class="risk-label">High-risk Slices</span>
        <span class="risk-value">${highRiskLabel} / ${totalLabel}</span>
      </div>
      <div class="risk-metric">
        <span class="risk-label">Slices analyzed</span>
        <span class="risk-value">${totalLabel}</span>
      </div>
    </div>
  `;
}

function formatResultSummary(summary, slices) {
  if (!summary || typeof summary !== "object") return "";
  const lines = [];
  if ("prob_malignant" in summary) lines.push(`prob_malignant: ${formatSummaryValue(summary.prob_malignant)}`);
  if ("max_prob" in summary) lines.push(`max_prob: ${formatSummaryValue(summary.max_prob)}`);
  if ("avg_prob" in summary) lines.push(`avg_prob: ${formatSummaryValue(summary.avg_prob)}`);
  if ("high_risk_slices" in summary) lines.push(`high_risk_slices: ${summary.high_risk_slices ?? "-"}`);
  if (slices?.length) lines.push(`slices: ${slices.length}`);
  return lines.join("\n");
}

function updateAnalysisPanel(study) {
  if (!ui.analysisStatusBadge || !ui.analysisResult || !ui.analysisMeta) return;
  const status = String(study?.analysisStatus || "").toLowerCase();
  const label = status ? formatStatus(status, "Not run") : "Not run";
  const isError = status === "error" || status === "failed";
  const analysisError = study?.analysisError;
  let errorMessage = "";
  let errorDetail = "";
  let errorHint = "";
  if (analysisError && typeof analysisError === "object") {
    errorMessage = analysisError.message || analysisError.error || analysisError.code || "";
    errorDetail = analysisError.detail || analysisError.stack || "";
    errorHint = analysisError.hint || "";
    if (!errorMessage) {
      try {
        errorMessage = JSON.stringify(analysisError);
      } catch (error) {
        errorMessage = String(analysisError);
      }
    }
  } else if (analysisError) {
    errorMessage = String(analysisError);
  }
  if (!errorDetail && study?.analysisErrorDetail) {
    errorDetail = String(study.analysisErrorDetail);
  }
  ui.analysisStatusBadge.textContent = label;
  ui.analysisStatusBadge.classList.remove("pending", "done", "error");
  const variant = statusToVariant(status);
  if (variant) ui.analysisStatusBadge.classList.add(variant);

  const updatedAt = formatTimestamp(study?.analysisUpdatedAt);
  if (isError) {
    ui.analysisMeta.textContent = errorMessage ? `Error: ${errorMessage}` : "Analysis error.";
  } else if (status === "running") {
    ui.analysisMeta.textContent = "Analysis running...";
  } else if (status === "analyzed") {
    ui.analysisMeta.textContent =
      updatedAt !== "-" ? `Analysis completed · ${updatedAt}` : "Analysis completed.";
  } else if (updatedAt !== "-") {
    ui.analysisMeta.textContent = `Last updated: ${updatedAt}`;
  } else {
    ui.analysisMeta.textContent = "Awaiting analysis.";
  }

  const summaryData = buildRiskSummary(study);
  const result = study?.analysisResult;
  ui.analysisResult.classList.toggle("error", isError);
  if (isError) {
    const parts = [];
    if (errorMessage) parts.push(errorMessage);
    if (errorDetail && errorDetail !== errorMessage) parts.push(errorDetail);
    if (errorHint) parts.push(errorHint);
    ui.analysisResult.textContent = parts.length ? parts.join("\n") : "Analysis failed.";
    ui.analysisResult.classList.remove("ct-hidden");
    if (ui.riskSummary) ui.riskSummary.classList.add("ct-hidden");
    if (ui.riskInterpretation) ui.riskInterpretation.classList.add("ct-hidden");
    return;
  }
  renderRiskSummary(summaryData);
  if (ui.riskSummary) ui.riskSummary.classList.remove("ct-hidden");
  if (ui.riskInterpretation) {
    ui.riskInterpretation.classList.remove("ct-hidden");
    ui.riskInterpretation.textContent = interpretation(summaryData?.level?.tone);
  }
  ui.analysisResult.classList.add("ct-hidden");
  if (result === null || result === undefined || result === "") {
    ui.analysisResult.textContent = "No analysis result yet.";
    return;
  }
}

function updateStudyDetails(study) {
  if (!study) {
    updateStudyStatusBadge("");
    setText(ui.infoStudyId, EMPTY_PLACEHOLDER);
    setText(ui.infoPatientId, EMPTY_PLACEHOLDER);
    setText(ui.infoCreatedAt, EMPTY_PLACEHOLDER);
    setText(ui.infoStatus, EMPTY_PLACEHOLDER);
    setText(ui.infoAnalysisUpdated, EMPTY_PLACEHOLDER);
    updateAnalysisPanel(null);
    updateRunAiLabel(null);
    setRunAiBusy(false);
    return;
  }

  updateStudyStatusBadge(study.status || "uploaded");
  setText(ui.infoStudyId, study.id || "-");
  setText(ui.infoPatientId, study.patientId || "-");
  setText(ui.infoCreatedAt, formatTimestamp(study.createdAt));
  setText(ui.infoStatus, formatStatus(study.status, "-"));
  setText(ui.infoAnalysisUpdated, formatTimestamp(study.analysisUpdatedAt));
  updateAnalysisPanel(study);
  updateRunAiLabel(study);
  setRunAiBusy(state.runAiBusy);
  updateViewerOverlay();
}

function pickPatientName(data) {
  if (!data) return "";
  return (
    String(data.name || "").trim() ||
    String(data.displayName || "").trim() ||
    String(data.fullName || "").trim() ||
    String(data.username || "").trim()
  );
}

function resolvePatientName(patientId) {
  if (!patientId) return "Unknown patient";
  const cached = patientCache.get(patientId);
  return cached ? cached : "Unknown patient";
}

function formatProb(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return "-";
  return num.toFixed(3);
}

function formatNoduleEntry(nodule, idx) {
  if (!nodule || typeof nodule !== "object") {
    return `${idx + 1}. ${String(nodule)}`;
  }
  const location = nodule.location ?? nodule.file ?? nodule.slice ?? nodule.index ?? "-";
  const size = nodule.size ?? nodule.diameter ?? nodule.mm ?? nodule.volume;
  const confidence =
    nodule.confidence ??
    nodule.probability ??
    nodule.score ??
    nodule.prob_malignant ??
    nodule.prob;
  const hasFields = location !== "-" || size !== undefined || confidence !== undefined;
  if (hasFields) {
    const sizeText = size !== undefined ? String(size) : "-";
    const confidenceText = confidence !== undefined ? formatProb(confidence) : "-";
    return `${idx + 1}. location=${location} size=${sizeText} score=${confidenceText}`;
  }
  try {
    return `${idx + 1}. ${JSON.stringify(nodule)}`;
  } catch (error) {
    return `${idx + 1}. ${String(nodule)}`;
  }
}

function formatAnalysisResult(result, limit = 10) {
  if (!result || typeof result !== "object") return "";
  const nodules = Array.isArray(result.nodules) ? result.nodules : [];
  const hasNewFields =
    result.summary ||
    result.riskLevel ||
    result.noduleCount !== undefined ||
    result.elapsedMs !== undefined ||
    result.modelDevice ||
    nodules.length;

  if (hasNewFields) {
    const lines = [];
    if (result.riskLevel) lines.push(`Risk level: ${result.riskLevel}`);
    if (result.summary) lines.push(`Summary: ${result.summary}`);
    const count =
      Number.isFinite(Number(result.noduleCount)) ? Number(result.noduleCount) : nodules.length;
    if (count || count === 0) lines.push(`Nodule count: ${count}`);
    if (result.elapsedMs !== undefined) lines.push(`Elapsed: ${result.elapsedMs} ms`);
    if (result.modelDevice) lines.push(`Model device: ${result.modelDevice}`);
    if (nodules.length) {
      lines.push("Nodules:");
      nodules.slice(0, limit).forEach((nodule, idx) => {
        lines.push(formatNoduleEntry(nodule, idx));
      });
      if (nodules.length > limit) {
        lines.push(`...and ${nodules.length - limit} more`);
      }
    }
    return lines.filter(Boolean).join("\n");
  }

  const summary = result.summary ? `Summary: ${result.summary}` : "";
  const status = result.status ? `Status: ${result.status}` : "";
  const message = result.message ? `Message: ${result.message}` : "";
  const predictions = Array.isArray(result.predictions) ? result.predictions : [];
  if (!predictions.length) {
    return [summary, status, message].filter(Boolean).join("\n");
  }

  const lines = predictions.slice(0, limit).map((pred, idx) => {
    const patientId = pred.patient_id || pred.patientId || "-";
    const noduleId = pred.nodule_id || pred.noduleId || "-";
    const maxProb = formatProb(pred.max_prob_malignant ?? pred.maxProbMalignant);
    const avgProb = formatProb(pred.avg_prob_malignant ?? pred.avgProbMalignant);
    const label = pred.predicted_label ?? pred.predictedLabel ?? "-";
    const trueLabel =
      pred.true_label !== undefined && pred.true_label !== null
        ? pred.true_label
        : pred.trueLabel ?? "-";
    return `${idx + 1}. patient=${patientId} nodule=${noduleId} max=${maxProb} avg=${avgProb} label=${label} true=${trueLabel}`;
  });

  return [summary, status, message, "Top predictions:", ...lines].filter(Boolean).join("\n");
}

async function preloadPatientNames(studies) {
  if (!db || !Array.isArray(studies)) return;
  const ids = [
    ...new Set(
      studies
        .map((study) => String(study.patientId || "").trim())
        .filter(Boolean)
    )
  ];

  const toFetch = ids.filter((id) => !patientCache.has(id));
  if (!toFetch.length) return;

  await Promise.all(
    toFetch.map(async (patientId) => {
      try {
        const snap = await getDoc(doc(db, "users", patientId));
        const data = snap.exists() ? snap.data() : null;
        const name = pickPatientName(data);
        patientCache.set(patientId, name || "");
      } catch (error) {
        console.error("[CT] load patient name failed:", error);
        patientCache.set(patientId, "");
      }
    })
  );
}

function renderStudyList() {
  // C) Render study list cards (patientId, createdAt, status).
  if (!ui.studyList || !ui.studyListStatus) return;
  ui.studyList.innerHTML = "";

  if (!state.filtered.length) {
    setStudyListStatus(
      state.studies.length
        ? "No studies match your search."
        : "No CT studies found. Check doctorId field and Firestore rules."
    );
    if (!state.studies.length && state.uid) {
      console.log("[CT] No CT studies found.");
      console.log("[CT] current uid:", state.uid);
      console.log("[CT] query condition: doctorId ==", state.uid);
      console.log("[CT] Check ctStudies documents for matching doctorId.");
    }
    updateStudyCount();
    return;
  }

  setStudyListStatus("");
  state.filtered.forEach((study) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "ct-study-item";
    if (study.id === state.selectedStudyId) {
      item.classList.add("active");
    }

    const title = document.createElement("p");
    title.className = "ct-study-title";
    title.textContent = resolvePatientName(study.patientId);

    const meta = document.createElement("p");
    meta.className = "ct-study-meta";
    const patientId = study.patientId ? String(study.patientId) : EMPTY_PLACEHOLDER;
    meta.textContent = `Patient ID: ${patientId} · Created ${formatTimestamp(study.createdAt)}`;

    const status = document.createElement("span");
    status.className = "ct-study-status";
    status.textContent = String(study.status || "uploaded");

    item.append(title, meta, status);
    item.addEventListener("click", () => selectStudy(study));
    ui.studyList.appendChild(item);
  });

  updateStudyCount();
}

function applySearch(term) {
  const needle = String(term || "").trim().toLowerCase();
  if (!needle) {
    state.filtered = [...state.studies];
  } else {
    state.filtered = state.studies.filter((study) => {
      const patientId = String(study.patientId || "").toLowerCase();
      return patientId.includes(needle);
    });
  }
  renderStudyList();
}

async function displayImage(index) {
  if (!state.viewerReady || !state.stack.length || !ui.viewer) {
    updateViewerControls();
    return;
  }

  const entry = state.stack[index];
  const imageId = typeof entry === "string" ? entry : entry?.imageId;
  if (!imageId) return;

  try {
    const image = await window.cornerstone.loadAndCacheImage(imageId);
    if (!image) throw new Error("Image not available");
    window.cornerstone.displayImage(ui.viewer, image);
    setViewerStatus("");
    updateFrameIndicator();
    updateViewerControls();
  } catch (error) {
    console.error("[ct] display image error", error);
    setViewerStatus("Failed to load image.");
  }
}

function normalizeFilename(name) {
  return String(name || "");
}

function naturalCompare(aName, bName) {
  try {
    const aParts = normalizeFilename(aName).match(/\d+|\D+/g) || [];
    const bParts = normalizeFilename(bName).match(/\d+|\D+/g) || [];
    const max = Math.max(aParts.length, bParts.length);
    for (let i = 0; i < max; i += 1) {
      const aPart = aParts[i];
      const bPart = bParts[i];
      if (aPart === undefined) return -1;
      if (bPart === undefined) return 1;

      const aIsNum = /^\d+$/.test(aPart);
      const bIsNum = /^\d+$/.test(bPart);
      if (aIsNum && bIsNum) {
        const aNum = Number(aPart);
        const bNum = Number(bPart);
        if (aNum !== bNum) return aNum - bNum;
      } else if (aIsNum !== bIsNum) {
        return aIsNum ? -1 : 1;
      } else {
        const cmp = aPart.localeCompare(bPart, undefined, { sensitivity: "base" });
        if (cmp !== 0) return cmp;
      }
    }
    return 0;
  } catch (error) {
    return normalizeFilename(aName).localeCompare(normalizeFilename(bName));
  }
}

function isDicomFile(name) {
  return normalizeFilename(name).toLowerCase().endsWith(".dcm");
}

function assertGlobal(name, value) {
  if (value) return true;
  console.error(`[CT] Missing global: ${name}`);
  setViewerStatus(`Missing ${name} library (check script loading).`);
  return false;
}

function ensureCornerstoneReady() {
  if (!ui.viewer) return false;
  const cornerstone = window.cornerstone;
  const loader = window.cornerstoneWADOImageLoader;
  const dicomParser = window.dicomParser;

  if (!assertGlobal("cornerstone", cornerstone)) return false;
  if (!assertGlobal("cornerstoneWADOImageLoader", loader)) return false;
  if (!assertGlobal("dicomParser", dicomParser)) return false;

  try {
    loader.external.cornerstone = cornerstone;
    loader.external.dicomParser = dicomParser;
    if (window.pako) {
      loader.external.pako = window.pako;
    }

    if (!state.workerInitialized) {
      if (!loader.webWorkerManager || !loader.webWorkerManager.initialize) {
        console.error("[CT] Missing webWorkerManager on cornerstoneWADOImageLoader");
        setViewerStatus("Cornerstone loader missing webWorkerManager.");
        return false;
      }
      loader.webWorkerManager.initialize({
        maxWebWorkers: Math.min(4, navigator.hardwareConcurrency || 2),
        startWebWorkers: true,
        webWorkerPath:
          "https://unpkg.com/cornerstone-wado-image-loader@4.13.2/dist/cornerstoneWADOImageLoaderWebWorker.min.js",
        taskConfiguration: {
          decodeTask: {
            codecsPath:
              "https://unpkg.com/cornerstone-wado-image-loader@4.13.2/dist/cornerstoneWADOImageLoaderCodecs.min.js"
          }
        }
      });
      loader.configure({ useWebWorkers: true });
      state.workerInitialized = true;
      console.log("[CT] init worker paths ok");
    }

    if (!state.viewerReady) {
      cornerstone.enable(ui.viewer);
      state.viewerReady = true;
    }
    return true;
  } catch (error) {
    console.error("[CT] viewer init error:", error);
    setViewerStatus("Failed to initialize viewer.");
    return false;
  }
}

function loadImageWithTimeout(imageId, timeoutMs) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      const err = new Error("timeout");
      err.code = "timeout";
      reject(err);
    }, timeoutMs);

    window.cornerstone
      .loadAndCacheImage(imageId)
      .then((image) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(image);
      })
      .catch((error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(error);
      });
  });
}

async function loadFirstImageWithFallback(imageId) {
  if (!ensureCornerstoneReady()) return null;

  try {
    const image = await loadImageWithTimeout(imageId, 12000);
    console.log("[CT] first image loaded");
    return image;
  } catch (error) {
    console.error("[CT] first image load failed:", error);
  }

  console.log("[CT] fallback to no-webworkers");
  try {
    window.cornerstoneWADOImageLoader.configure({ useWebWorkers: false });
    const image = await loadImageWithTimeout(imageId, 12000);
    console.log("[CT] first image loaded");
    return image;
  } catch (error) {
    console.error("[CT] fallback load failed:", error);
    setViewerStatus("Failed to decode DICOM (check worker/codecs path or file format)");
    return null;
  }
}

async function loadStudyStack(study) {
  // E) Load study stack: listAll -> filter .dcm -> natural sort -> getDownloadURL -> wadouri ids.
  if (!storage || !study) return;
  const prefix = study.storagePrefix;
  state.stack = [];
  state.currentIndex = 0;
  updateFrameIndicator();
  updateViewerControls();

  if (!prefix) {
    setViewerStatus("Missing storage prefix for this study.");
    return;
  }

  ensureCornerstoneReady();

  const token = ++state.loadToken;
  setViewerStatus("Loading DICOM stack...");

  try {
    const res = await listAll(ref(storage, prefix));
    if (token !== state.loadToken) return;
    const dcmItems = res.items.filter((item) => isDicomFile(item.name));
    if (!dcmItems.length) {
      setViewerStatus("No DICOM files found for this study.");
      return;
    }

    dcmItems.sort((a, b) => {
      const natural = naturalCompare(a.name, b.name);
      if (Number.isNaN(natural) || !Number.isFinite(natural)) {
        return normalizeFilename(a.name).localeCompare(normalizeFilename(b.name));
      }
      return natural || normalizeFilename(a.name).localeCompare(normalizeFilename(b.name));
    });

    const urls = [];
    for (let i = 0; i < dcmItems.length; i += 1) {
      if (token !== state.loadToken) return;
      setViewerStatus(`Loading DICOM stack... (${i + 1}/${dcmItems.length})`);
      try {
        const url = await getDownloadURL(dcmItems[i]);
        urls.push(url);
      } catch (error) {
        console.error("[CT] getDownloadURL failed:", error);
        if (error?.code === "storage/unauthorized") {
          setViewerStatus("Storage permission denied (check rules)");
          return;
        }
        throw error;
      }
    }

    if (token !== state.loadToken) return;
    const imageIds = urls.map((url) => `wadouri:${url}`);
    state.stack = imageIds;
    state.currentIndex = 0;

    if (!imageIds.length) {
      setViewerStatus("No DICOM files found for this study.");
      return;
    }

    const firstImage = await loadFirstImageWithFallback(imageIds[0]);
    if (!firstImage || token !== state.loadToken) return;
    window.cornerstone.displayImage(ui.viewer, firstImage);
    setViewerStatus("");
    updateFrameIndicator();
    updateViewerControls();
  } catch (error) {
    if (token !== state.loadToken) return;
    console.error("[CT] load stack error:", error);
    if (error?.code === "storage/unauthorized") {
      setViewerStatus("Storage permission denied (check rules)");
      return;
    }
    setViewerStatus(error?.message || "Failed to load DICOM stack.");
  }
}

function changeFrame(delta) {
  // F) Prev/Next slice navigation.
  if (!state.stack.length) return;
  const next = Math.min(Math.max(state.currentIndex + delta, 0), state.stack.length - 1);
  if (next === state.currentIndex) return;
  state.currentIndex = next;
  displayImage(next);
}

function applyZoom(factor) {
  // G) Simple zoom using Cornerstone viewport scale.
  if (!state.viewerReady || !ui.viewer || !window.cornerstone) return;
  const viewport = window.cornerstone.getViewport(ui.viewer);
  if (!viewport) return;
  viewport.scale *= factor;
  window.cornerstone.setViewport(ui.viewer, viewport);
}

function stopSelectedListener() {
  if (state.unsubSelected) {
    state.unsubSelected();
    state.unsubSelected = null;
  }
}

function subscribeSelectedStudy(studyId) {
  stopSelectedListener();
  if (!db || !studyId) return;
  state.unsubSelected = onSnapshot(
    doc(db, "ctStudies", studyId),
    (docSnap) => {
      if (!docSnap.exists()) return;
      const data = { id: docSnap.id, ...(docSnap.data() || {}) };
      const idx = state.studies.findIndex((item) => item.id === docSnap.id);
      if (idx >= 0) {
        Object.assign(state.studies[idx], data);
      }
      if (state.selectedStudyId === docSnap.id) {
        updateStudyDetails(data);
      }
    },
    (error) => {
      console.error("[CT] selected study listener error:", error);
    }
  );
}

function selectStudy(study) {
  // D) Click study -> load stack.
  state.selectedStudyId = study?.id || "";
  subscribeSelectedStudy(state.selectedStudyId);
  updateStudyDetails(study);
  renderStudyList();
  setRunAiBusy(false);
  loadStudyStack(study);
}

function clearSelection() {
  stopSelectedListener();
  state.selectedStudyId = "";
  state.stack = [];
  state.currentIndex = 0;
  updateFrameIndicator();
  updateViewerControls();
  updateStudyDetails(null);
  setViewerStatus("Select a study to load DICOM images.");
}

function setStudyListStatus(message, detail) {
  if (!ui.studyListStatus) return;
  ui.studyListStatus.textContent = message || "";
  if (detail) {
    const small = document.createElement("small");
    small.style.display = "block";
    small.style.marginTop = "6px";
    small.textContent = detail;
    ui.studyListStatus.appendChild(small);
  }
}

function initViewer() {
  ensureCornerstoneReady();
}

function bindViewerEvents() {
  if (!ui.viewer) return;
  ui.viewer.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      const direction = event.deltaY > 0 ? 1 : -1;
      changeFrame(direction);
    },
    { passive: false }
  );

  window.addEventListener("resize", () => {
    if (state.viewerReady && window.cornerstone && ui.viewer) {
      window.cornerstone.resize(ui.viewer, true);
    }
  });
}

function setAnalysisError(message, detail, hint, study) {
  const update = {
    analysisStatus: "failed",
    analysisUpdatedAt: new Date().toISOString(),
    analysisError: {
      message: message || "Analysis failed.",
      detail: detail || "",
      hint: hint || ""
    },
    analysisResult: ""
  };
  if (study) {
    Object.assign(study, update);
    updateStudyDetails(study);
  } else {
    updateAnalysisPanel(update);
  }
}

function setAnalysisSuccess(message, study) {
  const update = {
    analysisStatus: "analyzed",
    analysisUpdatedAt: new Date().toISOString(),
    analysisError: null,
    analysisResult: message || "Analysis completed."
  };
  if (study) {
    Object.assign(study, update);
    updateStudyDetails(study);
  } else {
    updateAnalysisPanel(update);
  }
}

async function refreshSelectedStudy() {
  if (!state.selectedStudyId) return;
  try {
    const snap = await getDoc(doc(db, "ctStudies", state.selectedStudyId));
    if (!snap.exists()) return;
    const data = {
      id: snap.id,
      ...(snap.data() || {})
    };
    const idx = state.studies.findIndex((item) => item.id === snap.id);
    if (idx >= 0) {
      state.studies[idx] = data;
    } else {
      state.studies.push(data);
    }
    updateStudyDetails(data);
  } catch (error) {
    console.warn("[CT] refresh selected study failed:", error);
  }
}

async function runAnalysis() {
  if (state.runAiBusy) return;
  if (!state.selectedStudyId) {
    setAnalysisError("Select a study first.", "", "", null);
    return;
  }
  const user = auth?.currentUser;
  if (!user) {
    setAnalysisError("Please login.", "", "", null);
    return;
  }

  const study = state.studies.find((item) => item.id === state.selectedStudyId);
  const nowIso = new Date().toISOString();
  const payload = {
    studyId: state.selectedStudyId,
    patientId: study?.patientId || "",
    storagePrefix: study?.storagePrefix || ""
  };
  console.log("[CT] POST /api/ct/analyze payload", payload);
  if (study) {
    study.analysisStatus = "running";
    study.analysisUpdatedAt = nowIso;
    study.analysisError = null;
    updateStudyDetails(study);
  }

  setRunAiBusy(true);
  try {
    const response = await fetch("http://127.0.0.1:8001/api/ct/analyze", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      credentials: "omit",
      body: JSON.stringify(payload)
    });
    console.log("[CT] analyze status", response.status);
    if (response.status === 404 || response.status === 405) {
      setAnalysisError("Backend route missing / wrong method", "", "", study);
      return;
    }
    const rawText = await response.text();
    let data = null;
    if (rawText) {
      try {
        data = JSON.parse(rawText);
      } catch (error) {
        data = null;
      }
    }
    if (!response.ok || (data && data.ok === false)) {
      const message = data?.message || data?.error || `HTTP ${response.status}`;
      const detail = data?.detail || "";
      const hint = data?.hint || "";
      setAnalysisError(message, detail, hint, study);
      return;
    }
    console.log("[CT] analyze response", data);
    setAnalysisSuccess("Analysis completed.", study);
    await refreshSelectedStudy();
  } catch (error) {
    console.error("[CT] run analysis failed:", error);
    setAnalysisError(error?.message || "Analysis failed.", "", "", study);
  } finally {
    setRunAiBusy(false);
  }
}

function bindControls() {
  if (ui.studySearch) {
    ui.studySearch.addEventListener("input", (event) => {
      applySearch(event.target.value);
    });
  }
  if (ui.btnPrev) ui.btnPrev.addEventListener("click", () => changeFrame(-1));
  if (ui.btnNext) ui.btnNext.addEventListener("click", () => changeFrame(1));
  if (ui.btnZoomIn) ui.btnZoomIn.addEventListener("click", () => applyZoom(1.15));
  if (ui.btnZoomOut) ui.btnZoomOut.addEventListener("click", () => applyZoom(0.87));
  if (ui.btnRunAI) ui.btnRunAI.addEventListener("click", runAnalysis);
}

function handleStudiesSnapshot(snapshot) {
  console.log("[CT] studies count=", snapshot.size);
  if (snapshot.docs.length) {
    console.log("[CT] first study data=", snapshot.docs[0].data());
  }
  state.studies = snapshot.docs.map((docSnap) => ({
    id: docSnap.id,
    ...(docSnap.data() || {})
  }));
  applySearch(ui.studySearch?.value || "");
  preloadPatientNames(state.studies).then(() => {
    renderStudyList();
  });

  if (state.selectedStudyId) {
    const next = state.studies.find((study) => study.id === state.selectedStudyId);
    if (next) {
      updateStudyDetails(next);
    } else {
      clearSelection();
    }
  }
}

function loadStudiesForDoctor(uid) {
  // B) Firestore query for doctorId == uid, with index fallback.
  if (!db) return;
  if (state.unsubStudies) {
    state.unsubStudies();
    state.unsubStudies = null;
  }

  const baseQuery = query(
    collection(db, "ctStudies"),
    where("doctorId", "==", uid)
  );
  // Requires composite index: ctStudies, doctorId ASC, updatedAt DESC.
  const orderedQuery = query(
    collection(db, "ctStudies"),
    where("doctorId", "==", uid),
    orderBy("updatedAt", "desc")
  );

  const startListener = (q, label) => {
    console.log("[CT] ctStudies query:", label, "doctorId == ", uid);
    state.unsubStudies = onSnapshot(
      q,
      (snapshot) => {
        handleStudiesSnapshot(snapshot);
      },
      (error) => {
        const message = String(error?.message || error || "");
        const messageLower = message.toLowerCase();
        const missingIndex =
          error?.code === "failed-precondition" || messageLower.includes("index");
        if (label === "ordered" && missingIndex) {
          console.warn("Missing index, fallback to unordered");
          if (state.unsubStudies) {
            state.unsubStudies();
            state.unsubStudies = null;
          }
          setStudyListStatus(
            "Missing index for ordered results. Please create ctStudies index: doctorId ASC + updatedAt DESC.",
            "Falling back to unordered results."
          );
          startListener(baseQuery, "unordered");
          return;
        }
        console.error("[CT] load ctStudies failed:", error);
        setStudyListStatus("Failed to load CT studies.", message);
      }
    );
  };

  setStudyListStatus("Loading studies...");
  startListener(orderedQuery, "ordered");
}

window.addEventListener("DOMContentLoaded", () => {
  initViewer();
  bindViewerEvents();
  bindControls();
  setViewerStatus("Select a study to load DICOM images.");
  updateFrameIndicator();
  updateViewerControls();
  setRunAiBusy(false);

  // B) Auth guard -> load studies for doctor.
  onAuthStateChanged(auth, async (user) => {
    if (!user) {
      setStudyListStatus("Please login");
      setViewerStatus("Please login.");
      state.uid = "";
      if (state.unsubStudies) {
        state.unsubStudies();
        state.unsubStudies = null;
      }
      if (ui.studyList) ui.studyList.innerHTML = "";
      clearSelection();
      return;
    }
    console.log("[CT] uid=", user.uid);
    if (!db) {
      setStudyListStatus("Firestore not configured.");
      return;
    }
    const snap = await getDoc(doc(db, "users", user.uid));
    console.log("[CT] userDoc exists=", snap.exists(), "data=", snap.data());
    const role = String(snap?.data()?.role || "").toLowerCase();
    if (role !== "doctor") {
      setStudyListStatus("Not a doctor account");
      setViewerStatus("Not a doctor account.");
      if (ui.studyList) ui.studyList.innerHTML = "";
      if (state.unsubStudies) {
        state.unsubStudies();
        state.unsubStudies = null;
      }
      clearSelection();
      return;
    }
    state.uid = user.uid;
    loadStudiesForDoctor(user.uid);
  });
});
