import { auth, db, storage } from "../firebase-config.js";
import { onAuthStateChanged } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";
import {
  collection,
  doc,
  getDoc,
  getDocs,
  limit,
  orderBy,
  query,
  where,
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js";
import {
  getDownloadURL,
  ref as storageRef,
  listAll,
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-storage.js";

const ui = {
  status: document.getElementById("reportStatus"),
  list: document.getElementById("reportList"),
  empty: document.getElementById("reportEmpty"),
  search: document.getElementById("reportSearch"),
  previewFrame: document.getElementById("reportPreview"),
  previewPlaceholder: document.getElementById("previewPlaceholder"),
  previewSkeleton: document.getElementById("previewSkeleton"),
  textPreview: document.getElementById("reportText"),
  summary: document.getElementById("reportSummary"),
  error: document.getElementById("reportError"),
  errorText: document.getElementById("reportErrorText"),
  scopeNote: document.getElementById("reportScopeNote"),
  previewError: document.getElementById("previewError"),
  previewErrorText: document.getElementById("previewErrorText"),
  previewDebugButton: document.getElementById("checkStorageFolder"),
};

const state = {
  uid: "",
  reports: [],
  filtered: [],
  pdfCache: new Map(),
  selectedId: null,
  selectedPdfPath: null,
  debugContext: null,
};

const patientNameCache = new Map();
const patientNameRequests = new Map();

const isDev =
  location.hostname === "localhost" ||
  location.hostname === "127.0.0.1" ||
  location.hostname === "" ||
  location.hostname.endsWith(".local");

function devLog(...args) {
  if (isDev) console.log(...args);
}

function logStorageContext(storagePath) {
  const bucket = storage?.app?.options?.storageBucket || "(unknown)";
  const projectId = storage?.app?.options?.projectId || "(unknown)";
  console.log("[doctor-report] storage context", { bucket, projectId, storagePath });
}

const DEFAULT_PREVIEW_TEXT = "Select a report to preview";
const PREVIEW_UNAVAILABLE_MESSAGE = "No PDF preview available. Please download the PDF.";
const PDF_PREVIEW_FAILED_MESSAGE = "Preview failed. Please download the PDF.";
const PDF_NOT_READY_MESSAGE = "PDF not ready";
const PDF_NOT_UPLOADED_MESSAGE = "PDF not uploaded";
const PDF_PENDING_MESSAGE = "PDF pending";
const PDF_FAILED_MESSAGE = "PDF failed";

function setHidden(el, hidden) {
  if (!el) return;
  el.hidden = hidden;
}

function normalizeReportId(report) {
  return String(report?.reportId || report?.report_id || report?.id || "").trim();
}

function normalizePatientId(report) {
  return String(
    report?.patientId || report?.patientUid || report?.patient_id || ""
  ).trim();
}

function normalizeDoctorId(report) {
  return String(report?.doctorId || report?.doctorUid || report?.doctor_id || "").trim();
}

function toMillis(value) {
  if (!value) return 0;
  if (typeof value === "number") return value;
  if (typeof value === "string") {
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  if (typeof value.toMillis === "function") return value.toMillis();
  if (typeof value.toDate === "function") return value.toDate().getTime();
  if (typeof value.seconds === "number") return value.seconds * 1000;
  return 0;
}

function pad2(value) {
  return String(value).padStart(2, "0");
}

function formatDate(value) {
  if (!value) return "-";
  const ms = toMillis(value);
  if (!ms) return "-";
  const date = new Date(ms);
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

function riskClass(level) {
  const val = String(level || "").toLowerCase();
  if (val === "high") return "risk-high";
  if (val === "medium" || val === "med") return "risk-medium";
  if (val === "low") return "risk-low";
  return "";
}

function normalizeStoragePath(value) {
  if (!value || typeof value !== "string") return "";
  return value.trim();
}

function normalizePdfStatus(value) {
  if (value === null || value === undefined) return "";
  return String(value).trim().toLowerCase();
}

function isPdfStatusReady(status) {
  return status === "ready" || status === "" || status === "null" || status === "undefined";
}

function getPdfStatusLabel(storagePath, pdfStatus) {
  if (!storagePath) return PDF_NOT_UPLOADED_MESSAGE;
  if (isPdfStatusReady(pdfStatus)) return "";
  if (pdfStatus === "pending") return PDF_PENDING_MESSAGE;
  if (pdfStatus === "failed" || pdfStatus === "error") return PDF_FAILED_MESSAGE;
  return PDF_NOT_READY_MESSAGE;
}

function getReportPdfState(report) {
  const reportId = normalizeReportId(report);
  const patientId = normalizePatientId(report);
  const storagePath = normalizeStoragePath(
    report?.storagePath || report?.pdfPath || report?.pdf?.storagePath
  );
  const rawStatus = report?.pdfStatus ?? report?.pdf_status ?? report?.status ?? report?.pdf?.status ?? "";
  const pdfStatus = normalizePdfStatus(rawStatus);
  const hasStoragePath = Boolean(storagePath);
  const isReady = Boolean(hasStoragePath && isPdfStatusReady(pdfStatus));

  devLog("[doctor-report] storage path", { reportId, patientId, storagePath, pdfStatus });

  return {
    reportId,
    patientId,
    storagePath,
    pdfStatus,
    hasStoragePath,
    isReady,
    statusLabel: getPdfStatusLabel(storagePath, pdfStatus),
  };
}

function isStorageNotFound(error) {
  const code = typeof error === "string" ? error : error?.code;
  return String(code || "").toLowerCase() === "storage/object-not-found";
}

function setStatus(message) {
  if (ui.status) ui.status.textContent = message || "";
}

function showErrorNotice(show, message) {
  if (!ui.error) return;
  if (ui.errorText) {
    ui.errorText.textContent = show ? message || "Failed to load reports" : "";
  }
  ui.error.hidden = !show;
}

function setReportsVisibility(hasReports) {
  if (ui.list) ui.list.style.display = hasReports ? "flex" : "none";
  if (ui.empty) ui.empty.hidden = hasReports;
}

function resetReportsUI() {
  showErrorNotice(false);
  if (ui.list) {
    ui.list.style.display = "none";
    ui.list.innerHTML = "";
  }
  if (ui.empty) ui.empty.hidden = true;
  clearPreviewError();
}

function showEmptyState(message) {
  const text = message || "No reports yet";
  showErrorNotice(false);
  setReportsVisibility(false);
  if (ui.list) ui.list.innerHTML = "";
  if (ui.empty) {
    ui.empty.hidden = false;
    const titleEl = ui.empty.querySelector("[data-empty-title]");
    if (titleEl) {
      titleEl.textContent = text;
    } else {
      ui.empty.textContent = text;
    }
  }
  setStatus(text);
}

function showList() {
  showErrorNotice(false);
  setReportsVisibility(true);
}

function showGenericError(message) {
  const text = message || "Failed to load reports";
  showErrorNotice(true, text);
  setReportsVisibility(false);
  if (ui.list) ui.list.innerHTML = "";
  setStatus(text);
}

function setPreviewError(message, options = {}) {
  const text = String(message || "").trim();
  const show = Boolean(text);
  if (ui.previewErrorText) {
    ui.previewErrorText.textContent = text;
  } else if (ui.previewError) {
    ui.previewError.textContent = text;
  }
  if (ui.previewError) {
    ui.previewError.hidden = !show;
  }
  if (ui.previewDebugButton) {
    const showDebug = Boolean(options.showDebug);
    ui.previewDebugButton.hidden = !showDebug;
    ui.previewDebugButton.disabled = !options.patientId;
  }
  if (options.showDebug) {
    state.debugContext = {
      reportId: options.reportId || "",
      patientId: options.patientId || "",
      storagePath: options.storagePath || "",
    };
  } else {
    state.debugContext = null;
  }
}

function clearPreviewError() {
  setPreviewError("");
}

function setPreviewPlaceholder(text) {
  if (!ui.previewPlaceholder) return;
  const content = text || DEFAULT_PREVIEW_TEXT;
  const titleEl = ui.previewPlaceholder.querySelector("[data-preview-title]");
  const subtitleEl = ui.previewPlaceholder.querySelector("[data-preview-subtitle]");

  if (titleEl) {
    titleEl.textContent = content;
  } else {
    ui.previewPlaceholder.textContent = content;
  }

  if (subtitleEl) {
    subtitleEl.hidden = content !== DEFAULT_PREVIEW_TEXT;
  }
}

function setPreviewSelection(hasSelected) {
  if (!ui.previewPlaceholder) return;
  if (!hasSelected) {
    if (ui.summary) {
      ui.summary.hidden = true;
      ui.summary.innerHTML = "";
    }
    clearPreviewError();
    if (ui.textPreview) {
      ui.textPreview.hidden = true;
      ui.textPreview.textContent = "";
    }
    if (ui.previewSkeleton) ui.previewSkeleton.hidden = true;
    if (ui.previewFrame) {
      ui.previewFrame.hidden = true;
      ui.previewFrame.removeAttribute("src");
    }
    ui.previewPlaceholder.hidden = false;
    setPreviewPlaceholder(DEFAULT_PREVIEW_TEXT);
    return;
  }
  ui.previewPlaceholder.hidden = true;
}

function withCacheBuster(url) {
  if (!url || typeof url !== "string") return url;
  const [base, hash] = url.split("#");
  if (/[?&](X-Goog-Signature|X-Amz-Signature|Signature)=/i.test(base)) {
    return url;
  }
  const sep = base.includes("?") ? "&" : "?";
  const stamped = `${base}${sep}t=${Date.now()}`;
  return hash ? `${stamped}#${hash}` : stamped;
}

function setPdfPreview(url) {
  if (!ui.previewFrame || !ui.previewPlaceholder) return;
  clearPreviewError();
  if (ui.textPreview) {
    ui.textPreview.hidden = true;
    ui.textPreview.textContent = "";
  }
  if (ui.previewSkeleton) ui.previewSkeleton.hidden = true;
  if (url) {
    const previewUrl = withCacheBuster(url);
    ui.previewPlaceholder.hidden = true;
    if (ui.previewSkeleton) ui.previewSkeleton.hidden = false;
    ui.previewFrame.hidden = true;
    ui.previewFrame.src = previewUrl;
    ui.previewFrame.onload = () => {
      if (ui.previewSkeleton) ui.previewSkeleton.hidden = true;
      ui.previewFrame.hidden = false;
    };
    ui.previewFrame.onerror = () => {
      if (ui.previewSkeleton) ui.previewSkeleton.hidden = true;
      ui.previewFrame.hidden = true;
      ui.previewFrame.removeAttribute("src");
      setTextPreview(PDF_PREVIEW_FAILED_MESSAGE);
    };
  } else {
    if (ui.previewSkeleton) ui.previewSkeleton.hidden = true;
    ui.previewFrame.hidden = true;
    ui.previewFrame.removeAttribute("src");
    ui.previewPlaceholder.hidden = false;
  }
}

function setTextPreview(text) {
  if (!ui.textPreview || !ui.previewPlaceholder || !ui.previewFrame) return;
  const content = String(text || "").trim();
  clearPreviewError();
  if (!content) {
    if (ui.summary) {
      ui.summary.hidden = true;
      ui.summary.innerHTML = "";
    }
    ui.textPreview.hidden = true;
    ui.textPreview.textContent = "";
    ui.previewPlaceholder.hidden = false;
    if (ui.previewSkeleton) ui.previewSkeleton.hidden = true;
    ui.previewFrame.hidden = true;
    ui.previewFrame.removeAttribute("src");
    return;
  }
  if (ui.previewSkeleton) ui.previewSkeleton.hidden = true;
  ui.previewFrame.hidden = true;
  ui.previewFrame.removeAttribute("src");
  ui.previewPlaceholder.hidden = true;
  ui.textPreview.textContent = content;
  ui.textPreview.hidden = false;
}

function showPreviewError(message, options = {}) {
  setPreviewError(message || "PDF preview not available.", options);
  if (ui.previewSkeleton) ui.previewSkeleton.hidden = true;
  if (ui.previewFrame) {
    ui.previewFrame.hidden = true;
    ui.previewFrame.removeAttribute("src");
  }
  if (ui.textPreview) {
    ui.textPreview.hidden = true;
    ui.textPreview.textContent = "";
  }
  if (ui.previewPlaceholder) ui.previewPlaceholder.hidden = true;
}

function getRawPatientName(report) {
  return String(
    report?.summaryUserInfo?.name ||
      report?.patientName ||
      report?.patient_name ||
      report?.patientFullName ||
      ""
  ).trim();
}

function getPatientName(report) {
  const name = getRawPatientName(report);
  if (name) return name;
  const patientId = normalizePatientId(report);
  return patientId || "Patient";
}

async function resolvePatientName(patientId) {
  if (!patientId || !db) return "";
  if (patientNameCache.has(patientId)) return patientNameCache.get(patientId);
  if (patientNameRequests.has(patientId)) return patientNameRequests.get(patientId);
  const request = (async () => {
    try {
      const snap = await getDoc(doc(db, "users", patientId));
      if (snap.exists()) {
        const data = snap.data() || {};
        const name = String(data.fullName || data.name || data.displayName || data.email || "").trim();
        if (name) {
          patientNameCache.set(patientId, name);
          return name;
        }
      }
    } catch (error) {
      console.warn("[doctor-report] patient lookup failed", error);
    } finally {
      patientNameRequests.delete(patientId);
    }
    return "";
  })();
  patientNameRequests.set(patientId, request);
  return request;
}

async function hydratePatientNames(reports) {
  const pending = reports.filter(
    (report) => !getRawPatientName(report) && normalizePatientId(report)
  );
  if (!pending.length) return false;
  await Promise.all(
    pending.map(async (report) => {
      const patientId = normalizePatientId(report);
      if (!patientId) return;
      const name = await resolvePatientName(patientId);
      if (name) report.patientName = name;
    })
  );
  return true;
}

async function resolvePdfUrl(report) {
  const { reportId, patientId, storagePath, pdfStatus, isReady } = getReportPdfState(report);
  if (!reportId) {
    return { url: null, errorCode: "missing-report-id", reportId, patientId, storagePath, pdfStatus };
  }

  const cached = state.pdfCache.get(reportId);
  if (cached && cached.storagePath === storagePath && cached.pdfStatus === pdfStatus) {
    return cached;
  }

  if (!storagePath || !storage || !isReady) {
    const errorCode = !storagePath ? "missing-storage-path" : "pdf-not-ready";
    const result = { url: null, errorCode, reportId, patientId, storagePath, pdfStatus };
    state.pdfCache.set(reportId, result);
    return result;
  }

  try {
    logStorageContext(storagePath);
    const url = await getDownloadURL(storageRef(storage, storagePath));
    devLog("[doctor-report] pdf url ok:", reportId);
    const result = { url, errorCode: "", reportId, patientId, storagePath, pdfStatus };
    state.pdfCache.set(reportId, result);
    return result;
  } catch (error) {
    console.warn("[doctor-report] pdf url error", error);
    const errorCode = String(error?.code || "");
    const result = { url: null, errorCode, reportId, patientId, storagePath, pdfStatus };
    state.pdfCache.set(reportId, result);
    return result;
  }
}

async function listStorageFolder(context) {
  if (!context || !storage) return;
  const { reportId, patientId, storagePath } = context;
  if (!patientId) {
    devLog("[doctor-report] listAll skipped, missing patientId", context);
    return;
  }
  try {
    const folderRef = storageRef(storage, `reports/${patientId}`);
    const result = await listAll(folderRef);
    const items = result.items.map((item) => item.name);
    const fullPaths = result.items.map((item) => item.fullPath);
    devLog("[doctor-report] listAll result", {
      reportId,
      patientId,
      storagePath,
      items,
      fullPaths,
    });
  } catch (error) {
    console.warn("[doctor-report] listAll failed", error);
  }
}

function setActiveItem(reportId) {
  if (!ui.list) return;
  ui.list.querySelectorAll(".report-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.reportId === reportId);
  });
}

function renderSummary(report) {
  if (!ui.summary) return;
  if (!report) {
    ui.summary.hidden = true;
    ui.summary.innerHTML = "";
    return;
  }
  const reportId = normalizeReportId(report);
  const patientName = getPatientName(report);
  const riskLevel = report?.riskLevel || report?.risk || report?.risk_level || "-";
  const createdAt = report?.createdAt || report?.created_at || "-";
  const status = report?.status || "-";
  const source = report?.source || report?.reportType || report?.type || "-";
  ui.summary.innerHTML = `
    <div class="summary-grid">
      <div class="summary-item"><strong>Patient</strong><span>${patientName || "-"}</span></div>
      <div class="summary-item"><strong>Risk</strong><span>${riskLevel || "-"}</span></div>
      <div class="summary-item"><strong>Created</strong><span>${formatDate(createdAt)}</span></div>
      <div class="summary-item"><strong>Status</strong><span>${String(status || "-")}</span></div>
    </div>
    <div class="summary-grid">
      <div class="summary-item"><strong>Report ID</strong><span>${reportId || "-"}</span></div>
      <div class="summary-item"><strong>Source</strong><span>${String(source || "-")}</span></div>
    </div>
  `;
  ui.summary.hidden = false;
}

async function handleView(report) {
  const reportId = normalizeReportId(report);
  if (!reportId) return;
  state.selectedId = reportId;
  setActiveItem(reportId);
  setPreviewSelection(true);
  clearPreviewError();
  renderSummary(report);
  if (ui.previewSkeleton) ui.previewSkeleton.hidden = false;
  const result = await resolvePdfUrl(report);
  state.selectedPdfPath = result.storagePath || null;
  if (result.url) {
    setPdfPreview(result.url);
    return;
  }
  if (result.errorCode === "pdf-not-ready" || result.errorCode === "missing-storage-path") {
    showPreviewError(getPdfStatusLabel(result.storagePath, result.pdfStatus));
    return;
  }
  if (isStorageNotFound(result.errorCode)) {
    showPreviewError(PDF_NOT_READY_MESSAGE, {
      showDebug: true,
      reportId: result.reportId,
      patientId: result.patientId,
      storagePath: result.storagePath,
    });
    return;
  }
  showPreviewError(PREVIEW_UNAVAILABLE_MESSAGE);
}

async function handleDownload(report) {
  clearPreviewError();
  const result = await resolvePdfUrl(report);
  state.selectedPdfPath = result.storagePath || null;
  if (result.url) {
    window.open(result.url, "_blank", "noopener");
    return;
  }
  if (result.errorCode === "pdf-not-ready" || result.errorCode === "missing-storage-path") {
    showPreviewError(getPdfStatusLabel(result.storagePath, result.pdfStatus));
    return;
  }
  if (isStorageNotFound(result.errorCode)) {
    showPreviewError(PDF_NOT_READY_MESSAGE, {
      showDebug: true,
      reportId: result.reportId,
      patientId: result.patientId,
      storagePath: result.storagePath,
    });
  }
}

function renderList(reports) {
  if (!ui.list) return;
  ui.list.innerHTML = "";

  const hasReports = Array.isArray(reports) && reports.length > 0;
  if (!hasReports) {
    showEmptyState("No reports yet");
    setPreviewSelection(false);
    return;
  }

  showList();
  setStatus(`${reports.length} report${reports.length === 1 ? "" : "s"}`);
  const hasSelected = Boolean(
    state.selectedId &&
      reports.some((report) => normalizeReportId(report) === state.selectedId)
  );
  if (!hasSelected) {
    setPreviewSelection(false);
  } else {
    setPreviewSelection(true);
  }

  reports.forEach((report) => {
    const reportId = normalizeReportId(report);
    const displayName = getPatientName(report);
    const riskLevel = report?.riskLevel || report?.risk || report?.risk_level || "-";
    const createdAt = report?.createdAt || report?.created_at;
    const source = report?.source || report?.reportType || report?.type || "-";
    const pdfState = getReportPdfState(report);

    const item = document.createElement("div");
    item.className = "report-item";
    item.dataset.reportId = reportId;

    const meta = document.createElement("div");
    meta.className = "report-meta";
    meta.innerHTML = `
      <div><strong>Name:</strong> ${displayName || "-"}</div>
      <div><strong>Risk:</strong> <span class="pill ${riskClass(riskLevel)}">${riskLevel || "-"}</span></div>
      <div><strong>Created:</strong> ${formatDate(createdAt)}</div>
      <div><strong>Source:</strong> ${source || "-"}</div>
      <div><strong>Report ID:</strong> ${reportId || "-"}</div>
    `;

    const actions = document.createElement("div");
    actions.className = "report-actions";

    const viewBtn = document.createElement("button");
    viewBtn.textContent = "View Details";
    viewBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      handleView(report);
    });

    const downloadBtn = document.createElement("button");
    downloadBtn.textContent = "Download PDF";
    downloadBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      handleDownload(report);
    });

    const status = document.createElement("span");
    status.className = "label";
    status.textContent = pdfState.statusLabel;

    downloadBtn.disabled = !pdfState.isReady;
    actions.append(viewBtn, downloadBtn, status);

    item.append(meta, actions);
    item.addEventListener("click", (event) => {
      if (event.target.closest("button")) return;
      handleView(report);
    });
    ui.list.appendChild(item);

    if (state.selectedId === reportId) item.classList.add("active");
  });
}

function applySearch() {
  const queryText = String(ui.search?.value || "").trim().toLowerCase();
  if (!queryText) {
    state.filtered = [...state.reports];
    renderList(state.filtered);
    return;
  }

  state.filtered = state.reports.filter((report) => {
    const reportId = normalizeReportId(report).toLowerCase();
    const patientName = getPatientName(report).toLowerCase();
    const patientId = normalizePatientId(report).toLowerCase();
    const createdAt = formatDate(report?.createdAt || report?.created_at).toLowerCase();
    return (
      reportId.includes(queryText) ||
      patientName.includes(queryText) ||
      patientId.includes(queryText) ||
      createdAt.includes(queryText)
    );
  });

  if (!state.filtered.length && state.reports.length) {
    showEmptyState("No reports match your search");
    return;
  }

  renderList(state.filtered);
}

function setScopeNote(show) {
  if (!ui.scopeNote) return;
  ui.scopeNote.hidden = !show;
}

function isIndexError(error) {
  const code = String(error?.code || "").toLowerCase();
  if (code === "failed-precondition") return true;
  const message = String(error?.message || "").toLowerCase();
  return message.includes("requires an index");
}

async function fetchReports(uid) {
  if (!db || !uid) return [];
  const reportsRef = collection(db, "reports");

  let snapshot = null;
  let usedFallback = false;

  try {
    const doctorQuery = query(
      reportsRef,
      where("doctorId", "==", uid),
      orderBy("createdAt", "desc"),
      limit(50)
    );
    snapshot = await getDocs(doctorQuery);
  } catch (error) {
    if (isIndexError(error)) {
      console.warn("[doctor-report] doctorId query requires index, fallback to all reports");
    } else {
      console.warn("[doctor-report] doctorId query failed", error);
    }
    snapshot = null;
  }

  if (!snapshot || snapshot.size === 0) {
    usedFallback = true;
    const allQuery = query(reportsRef, orderBy("createdAt", "desc"), limit(200));
    snapshot = await getDocs(allQuery);
  }

  setScopeNote(usedFallback);

  const reports = snapshot.docs.map((docSnap) => ({
    id: docSnap.id,
    ...docSnap.data(),
  }));

  devLog("[doctor-report] fetched reports count:", reports.length);
  reports.slice(0, 3).forEach((report) => {
    devLog("[doctor-report] report sample:", {
      reportId: normalizeReportId(report),
      patientId: normalizePatientId(report),
      doctorId: normalizeDoctorId(report),
      createdAt: report?.createdAt || report?.created_at || null,
      storagePath: report?.storagePath || report?.fileName || report?.file_name || null,
    });
  });

  return reports;
}

async function loadReports(uid) {
  try {
    setStatus("Loading...");
    resetReportsUI();
    setPreviewSelection(false);

    const reports = await fetchReports(uid);
    state.reports = reports || [];
    state.filtered = [...state.reports];

    if (!state.reports.length) {
      showEmptyState("No reports yet");
      return;
    }

    renderList(state.filtered);

    const didUpdate = await hydratePatientNames(state.reports);
    if (didUpdate) {
      applySearch();
    }
  } catch (error) {
    console.error("[doctor-report] failed to load reports", error);
    showGenericError("Failed to load reports");
  }
}

function bindEvents() {
  if (ui.search) {
    ui.search.addEventListener("input", () => {
      applySearch();
    });
  }
  if (ui.previewDebugButton) {
    ui.previewDebugButton.addEventListener("click", () => {
      listStorageFolder(state.debugContext);
    });
  }
}

bindEvents();
setPreviewSelection(false);

onAuthStateChanged(auth, (user) => {
  const uid = user?.uid || "";
  devLog("[doctor-report] auth uid:", uid || "null");
  state.uid = uid;

  if (!uid) {
    state.reports = [];
    state.filtered = [];
    resetReportsUI();
    setStatus("");
    setScopeNote(false);
    setPreviewSelection(false);
    return;
  }

  loadReports(uid);
});
