export const FLOW_ORDER = [
  "questionnaire",
  "appointment",
  "ct",
  "ai",
  "doctor",
  "followup"
];

export const FLOW_LABELS = {
  questionnaire: "Questionnaire",
  appointment: "Appointment",
  ct: "CT scan",
  ai: "AI analysis",
  doctor: "Doctor review",
  followup: "Follow-up"
};

const VALID_STATES = new Set(["todo", "current", "done"]);

function normalizeState(value) {
  const raw = String(value || "").toLowerCase().trim();
  return VALID_STATES.has(raw) ? raw : "todo";
}

export function normalizeSteps(rawSteps) {
  const steps = {};
  const source = rawSteps && typeof rawSteps === "object" ? rawSteps : {};
  FLOW_ORDER.forEach((key) => {
    const entry = source[key] && typeof source[key] === "object" ? source[key] : {};
    const note = entry.note;
    const date = entry.date;
    steps[key] = {
      state: normalizeState(entry.state),
      note: typeof note === "string" ? note : note == null ? "" : String(note),
      date: date == null ? "" : date
    };
  });
  return steps;
}

export function computeProgress(steps, statusCurrent) {
  const safeSteps = steps && typeof steps === "object" ? steps : {};
  const totalCount = FLOW_ORDER.length;
  const doneCount = FLOW_ORDER.reduce(
    (count, key) => count + (safeSteps[key]?.state === "done" ? 1 : 0),
    0
  );

  const currentCandidate = typeof statusCurrent === "string"
    ? statusCurrent.toLowerCase().trim()
    : "";
  let currentKey = FLOW_ORDER.includes(currentCandidate) ? currentCandidate : "";
  if (!currentKey) {
    currentKey = FLOW_ORDER.find((key) => safeSteps[key]?.state === "current") || "";
  }
  if (!currentKey) {
    currentKey = FLOW_ORDER.find((key) => safeSteps[key]?.state !== "done") || "";
  }
  if (!currentKey && FLOW_ORDER.length) {
    currentKey = FLOW_ORDER[FLOW_ORDER.length - 1];
  }

  const percent = totalCount ? (doneCount / totalCount) * 100 : 0;

  return { doneCount, totalCount, percent, currentKey };
}

function formatStepDate(value) {
  if (!value) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number") {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "" : date.toISOString().slice(0, 10);
  }
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? "" : value.toISOString().slice(0, 10);
  }
  if (typeof value.toDate === "function") {
    const date = value.toDate();
    return Number.isNaN(date.getTime()) ? "" : date.toISOString().slice(0, 10);
  }
  if (typeof value.seconds === "number") {
    const date = new Date(value.seconds * 1000);
    return Number.isNaN(date.getTime()) ? "" : date.toISOString().slice(0, 10);
  }
  return "";
}

export function deriveDisplayState(stepKey, steps, currentKey) {
  const existing = steps?.[stepKey]?.state;
  if (existing) return existing;
  if (!currentKey) return "todo";
  const stepIndex = FLOW_ORDER.indexOf(stepKey);
  const currentIndex = FLOW_ORDER.indexOf(currentKey);
  if (stepIndex === -1 || currentIndex === -1) return "todo";
  if (stepIndex < currentIndex) return "done";
  if (stepIndex === currentIndex) return "current";
  return "todo";
}

export function toUiModel(steps, currentKey, rawSteps) {
  const safeSteps = steps && typeof steps === "object" ? steps : {};
  const raw = rawSteps && typeof rawSteps === "object" ? rawSteps : {};
  const resolvedCurrent = currentKey || computeProgress(safeSteps, "").currentKey;
  const hasExplicitCurrent = FLOW_ORDER.some((key) => safeSteps[key]?.state === "current");

  return FLOW_ORDER.map((key) => {
    const normalized = safeSteps[key] || { state: "todo", note: "", date: "" };
    const rawEntry = raw[key];
    const hasState = rawEntry && typeof rawEntry.state === "string" && rawEntry.state.trim() !== "";
    let state = hasState ? normalized.state : deriveDisplayState(key, safeSteps, resolvedCurrent);
    if (!hasExplicitCurrent && resolvedCurrent === key && normalized.state !== "done") {
      state = "current";
    }
    return {
      key,
      label: FLOW_LABELS[key] || key,
      state,
      note: normalized.note ?? "",
      date: normalized.date ?? ""
    };
  });
}

export function renderFlow(container, model, meta) {
  if (!container || !Array.isArray(model)) return;
  const percent = Math.max(0, Math.min(100, Number(meta?.percent) || 0));
  container.style.setProperty("--sf-progress", `${percent}%`);
  container.style.setProperty("--flow-progress", `${percent}%`);

  model.forEach((step) => {
    const el = container.querySelector(`.flow-step[data-step="${step.key}"]`);
    if (!el) return;
    el.classList.remove("done", "current", "todo", "active");
    if (step.state === "done") {
      el.classList.add("done");
    } else if (step.state === "current") {
      el.classList.add("current", "active");
    } else {
      el.classList.add("todo");
    }

    const sub = el.querySelector(`[data-sub="${step.key}"]`);
    if (!sub) return;
    if (step.state === "done") {
      const dateText = formatStepDate(step.date);
      sub.textContent = dateText ? `Done: ${dateText}` : "Done";
    } else if (step.state === "current") {
      const noteText = String(step.note || "").trim();
      sub.textContent = noteText || "In progress";
    } else {
      sub.textContent = "\u2014";
    }
  });
}

export function buildFlowModel(userDoc) {
  const status = userDoc?.status && typeof userDoc.status === "object" ? userDoc.status : {};
  const rawSteps = status.steps && typeof status.steps === "object" ? status.steps : {};
  const steps = normalizeSteps(rawSteps);
  const progressMeta = computeProgress(steps, status.current);
  const items = toUiModel(steps, progressMeta.currentKey, rawSteps);
  return { steps: items, progressMeta };
}
