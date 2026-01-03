import { auth, db } from "../firebase-config.js";
import { onAuthStateChanged } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";
import {
  addDoc,
  collection,
  doc,
  getDoc,
  getDocs,
  limit,
  onSnapshot,
  orderBy,
  query,
  serverTimestamp,
  setDoc,
  updateDoc,
  where,
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js";
import {
  getCurrentUserProfile,
  getDoctorPatients,
  getPatientsByIds,
} from "./firestoreService.js";
import { buildFlowModel, normalizeSteps, FLOW_ORDER } from "../shared/flowProgress.js";

const doctorState = {
  patients: [],
  selectedPatient: null,
  chat: {
    messages: null,
    input: null,
    sendBtn: null,
    patientNameEl: null,
    metaEl: null,
    currentChatId: null,
    unsubscribe: null,
  },
  flow: {
    patientId: null,
    unsub: null,
    model: null,
    normalizedSteps: null,
    statusCurrent: "",
    progressTrack: null,
    progressBadge: null,
    listEl: null,
    statusEl: null,
    autoCompleteToggle: null,
    downgradeToggle: null,
  },
};

function getCachedProfile() {
  try {
    return JSON.parse(localStorage.getItem("userProfile") || "null");
  } catch (error) {
    console.warn("Failed to parse cached profile", error);
    return null;
  }
}

function applyDoctorInfo(profile) {
  const name = (profile?.fullName || profile?.name || "Doctor").trim();
  const hospital =
    (profile?.hospital ||
      profile?.hospital_name ||
      profile?.organization ||
      "Unknown hospital").trim();
  const brandName = document.getElementById("brand-name");
  const brandAvatar = document.getElementById("brand-avatar");
  const doctorNameEl = document.getElementById("doctor-name-display");
  const doctorHospitalEl = document.getElementById("doctor-hospital-display");

  if (brandName) brandName.textContent = name;
  if (doctorNameEl) doctorNameEl.textContent = name;
  if (doctorHospitalEl) doctorHospitalEl.textContent = hospital;
  if (brandAvatar) {
    const initial = name.charAt(0).toUpperCase() || "D";
    brandAvatar.textContent = initial;
  }
}

function fillProfileForm(profile) {
  const nameInput = document.getElementById("doctor-name-input");
  const hospitalInput = document.getElementById("doctor-hospital-input");
  const specialtyInput = document.getElementById("doctor-specialty-input");
  const emailInput = document.getElementById("doctor-email-input");
  const phoneInput = document.getElementById("doctor-phone-input");
  const addressInput = document.getElementById("doctor-address-input");
  const bioInput = document.getElementById("doctor-bio-input");

  if (!profile) return;

  if (nameInput && !nameInput.value)
    nameInput.value = profile.fullName || profile.name || "";
  if (hospitalInput && !hospitalInput.value)
    hospitalInput.value =
      profile.hospital ||
      profile.hospital_name ||
      profile.organization ||
      "";
  if (specialtyInput && !specialtyInput.value)
    specialtyInput.value = profile.specialty || "";
  if (emailInput && !emailInput.value)
    emailInput.value = profile.contactEmail || profile.email || "";
  if (phoneInput && !phoneInput.value)
    phoneInput.value =
      profile.contactPhone || profile.phone || profile.phoneNumber || "";
  if (addressInput && !addressInput.value)
    addressInput.value = profile.clinicAddress || profile.address || "";
  if (bioInput && !bioInput.value)
    bioInput.value = profile.bio || profile.about || "";
}

async function hydrateDoctorProfile(user) {
  const cached = getCachedProfile();
  applyDoctorInfo(cached);
  fillProfileForm(cached);

  if (!user || !db) return;

  try {
    const snap = await getDoc(doc(db, "users", user.uid));
    if (snap.exists()) {
      const data = snap.data();
      localStorage.setItem("userProfile", JSON.stringify(data));
      applyDoctorInfo(data);
      fillProfileForm(data);
    }
  } catch (error) {
    console.error("Failed to load doctor profile:", error);
  }
}

function initNavActiveState() {
  const navItems = document.querySelectorAll(".nav-item");
  navItems.forEach((item) => {
    item.addEventListener("click", () => {
      navItems.forEach((el) => el.classList.remove("active"));
      item.classList.add("active");
    });
  });
}

function attachLogout() {
  const logoutLinks = document.querySelectorAll("#logout-link, #logout-link-secondary");
  logoutLinks.forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      localStorage.clear();
      window.location.href = "../login.html";
    });
  });
}

function isDoctorChatPage() {
  return window.location.pathname.toLowerCase().includes("/doctor/question.html");
}

function getCurrentTime() {
  const now = new Date();
  return now.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
}

function getPatientId(patient) {
  return patient?.id || patient?.patientId || patient?.uid || "";
}

function updateSelectedPatientUI(patient) {
  const name = patient?.fullName || patient?.name || patient?.email || "Select a patient";
  doctorState.chat.patientNameEl ??= document.getElementById("current-patient-name");
  doctorState.chat.metaEl ??= document.getElementById("selected-patient-meta");
  if (doctorState.chat.patientNameEl) {
    doctorState.chat.patientNameEl.textContent = name;
  }
  if (doctorState.chat.metaEl) {
    doctorState.chat.metaEl.textContent = patient
      ? `Patient ID: ${patient.id || patient.patientId || "N/A"}${patient.email ? ` • ${patient.email}` : ""}`
      : "Select a patient to begin chatting.";
  }
}

function appendDoctorMessage(senderType, text, time) {
  const messages = doctorState.chat.messages;
  if (!messages) return;
  const bubble = document.createElement("div");
  bubble.className = `chat-message ${senderType}`;

  const meta = document.createElement("div");
  meta.className = "chat-message-meta";

  const senderEl = document.createElement("span");
  senderEl.className = "chat-message-sender";
  senderEl.textContent = senderType === "doctor" ? "Me (doctor)" : "Patient";

  const timeEl = document.createElement("span");
  timeEl.className = "chat-message-time";
  timeEl.textContent = time || getCurrentTime();

  meta.appendChild(senderEl);
  meta.appendChild(timeEl);

  const textEl = document.createElement("p");
  textEl.className = "chat-message-text";
  textEl.textContent = text;

  bubble.appendChild(meta);
  bubble.appendChild(textEl);
  messages.appendChild(bubble);
  messages.scrollTop = messages.scrollHeight;
}

function resetChatMessages() {
  if (!doctorState.chat.messages) return;
  doctorState.chat.messages.innerHTML = "";
}

function formatMessageTime(value) {
  if (!value) return getCurrentTime();
  if (typeof value.toDate === "function") {
    return value
      .toDate()
      .toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
  }
  if (value instanceof Date) {
    return value.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
  }
  return getCurrentTime();
}

function detachDoctorChatListener() {
  if (doctorState.chat.unsubscribe) {
    doctorState.chat.unsubscribe();
    doctorState.chat.unsubscribe = null;
  }
}

function subscribeDoctorMessages(chatId) {
  if (!db || !chatId) return;
  detachDoctorChatListener();
  const q = query(
    collection(db, "chats", chatId, "messages"),
    orderBy("createdAt")
  );
  console.log("[LISTEN] doctor listening messages for", chatId);
  doctorState.chat.unsubscribe = onSnapshot(
    q,
    (snapshot) => {
      console.log("[LISTEN] doctor snapshot size =", snapshot.size);
      resetChatMessages();
      snapshot.forEach((docSnap) => {
        const data = docSnap.data() || {};
        const role = String(data.senderRole || "").toLowerCase();
        const senderType = role === "doctor" ? "doctor" : "patient";
        const text = data.text || "";
        const time = formatMessageTime(data.createdAt);
        appendDoctorMessage(senderType, text, time);
      });
    },
    (error) => {
      console.error("[LISTEN] doctor snapshot error", error);
    }
  );
}

async function ensureDoctorChatId(patientId) {
  if (!db || !auth?.currentUser || !patientId) return "";
  const doctorId = auth.currentUser.uid;
  const q = query(
    collection(db, "chats"),
    where("doctorId", "==", doctorId),
    where("patientId", "==", patientId),
    where("status", "==", "active"),
    limit(1)
  );
  const snapshot = await getDocs(q);
  if (!snapshot.empty) {
    return snapshot.docs[0].id;
  }

  const payload = {
    doctorId,
    patientId,
    status: "active",
    lastMessage: "",
    lastMessageAt: serverTimestamp(),
    lastSenderRole: null,
    unreadCountDoctor: 0,
    unreadCountPatient: 0,
    lastReadAtDoctor: null,
    lastReadAtPatient: null,
    linkedReportId: null,
    linkedAppointmentId: null,
    riskLevelSnapshot: null,
    createdAt: serverTimestamp(),
    updatedAt: serverTimestamp(),
  };

  const ref = await addDoc(collection(db, "chats"), payload);
  return ref.id;
}

async function setActiveChatForPatient(patient) {
  doctorState.selectedPatient = patient;
  updateSelectedPatientUI(patient);
  resetChatMessages();
  detachDoctorChatListener();
  doctorState.chat.currentChatId = null;

  const patientId = getPatientId(patient);
  subscribeFlowForPatient(patientId);
  if (!patientId) return;

  try {
    const chatId = await ensureDoctorChatId(patientId);
    doctorState.chat.currentChatId = chatId;
    console.log("doctor chatId =", chatId);
    if (chatId) {
      subscribeDoctorMessages(chatId);
    }
  } catch (error) {
    console.error("Failed to initialize doctor chat:", error);
  }
}

function sendDoctorMessage() {
  console.log("[SEND] doctor send clicked");
  if (!doctorState.chat.input || !doctorState.selectedPatient) {
    alert("Select a patient to send messages.");
    return;
  }
  const text = doctorState.chat.input.value.trim();
  if (!text) return;

  const chatId = doctorState.chat.currentChatId;
  if (!chatId || !auth?.currentUser || !db) {
    console.warn("[SEND] missing chat or auth state");
    return;
  }

  console.log("[SEND] writing message to firestore", chatId);
  addDoc(collection(db, "chats", chatId, "messages"), {
    senderId: auth.currentUser.uid,
    senderRole: "doctor",
    type: "text",
    text,
    createdAt: serverTimestamp(),
  })
    .then(() => {
      doctorState.chat.input.value = "";
    })
    .catch((error) => {
      console.error("Failed to send doctor message:", error);
    });
}

function initDoctorChat() {
  if (!isDoctorChatPage()) return;

  doctorState.chat.messages = document.getElementById("doctor-chat-messages");
  doctorState.chat.input = document.getElementById("doctor-chat-input");
  doctorState.chat.sendBtn = document.getElementById("doctor-chat-send");
  doctorState.chat.patientNameEl = document.getElementById("current-patient-name");
  doctorState.chat.metaEl = document.getElementById("selected-patient-meta");

  if (!doctorState.chat.messages || !doctorState.chat.input || !doctorState.chat.sendBtn) return;

  updateSelectedPatientUI(doctorState.selectedPatient);

  doctorState.chat.sendBtn.addEventListener("click", sendDoctorMessage);
  doctorState.chat.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendDoctorMessage();
    }
  });
}

function initFlowControl() {
  if (!isDoctorChatPage()) return;
  doctorState.flow.progressTrack = document.getElementById("flowProgressTrack");
  doctorState.flow.progressBadge = document.getElementById("flowProgressBadge");
  doctorState.flow.listEl = document.getElementById("flowControlList");
  doctorState.flow.statusEl = document.getElementById("flowControlStatus");
  doctorState.flow.autoCompleteToggle = document.getElementById("flowAutoComplete");
  doctorState.flow.downgradeToggle = document.getElementById("flowDowngradeFuture");
  renderFlowEmpty();
}

function setFlowStatus(message) {
  if (doctorState.flow.statusEl) {
    doctorState.flow.statusEl.textContent = message || "";
  }
}

function formatFlowDate(value) {
  if (!value) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number") {
    const dt = new Date(value);
    return Number.isNaN(dt.getTime()) ? "" : dt.toISOString().slice(0, 10);
  }
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? "" : value.toISOString().slice(0, 10);
  }
  if (typeof value.toDate === "function") {
    const dt = value.toDate();
    return Number.isNaN(dt.getTime()) ? "" : dt.toISOString().slice(0, 10);
  }
  if (typeof value.seconds === "number") {
    const dt = new Date(value.seconds * 1000);
    return Number.isNaN(dt.getTime()) ? "" : dt.toISOString().slice(0, 10);
  }
  return "";
}

function formatDateInput(value) {
  if (!value) return "";
  if (typeof value === "string") return value.trim().slice(0, 10);
  return formatFlowDate(value);
}

function flowSubText(step, displayState) {
  if (displayState === "done") {
    const dateText = formatFlowDate(step?.date);
    return dateText || "Done";
  }
  if (displayState === "current") {
    const note = String(step?.note || "").trim();
    return note || "In progress";
  }
  return "\u2014";
}

function renderFlowProgress(model) {
  if (!model) return;
  const { steps, progressMeta } = model;
  const track = doctorState.flow.progressTrack;

  if (doctorState.flow.progressBadge) {
    doctorState.flow.progressBadge.textContent = `Progress ${progressMeta.doneCount} / ${progressMeta.totalCount}`;
  }

  if (track) {
    const pct = Math.max(0, Math.min(100, Number(progressMeta.percent) || 0));
    track.style.setProperty("--flow-progress", pct + "%");
  }

  steps.forEach((step) => {
    const el = track?.querySelector(`.flow-step[data-step="${step.key}"]`);
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
    if (sub) sub.textContent = flowSubText(step, step.state);
  });
}

async function saveFlowStep(stepKey, nextState, note, date, button) {
  const patientId = doctorState.flow.patientId;
  if (!patientId || !db) return;
  const statusCurrent = doctorState.flow.statusCurrent || "";
  const originalSteps = doctorState.flow.normalizedSteps || normalizeSteps(null);
  const originalStates = {};
  FLOW_ORDER.forEach((key) => {
    originalStates[key] = originalSteps[key]?.state || "todo";
  });

  const updatedStates = { ...originalStates, [stepKey]: nextState };
  const currentIndex = FLOW_ORDER.indexOf(stepKey);
  const autoComplete = !!doctorState.flow.autoCompleteToggle?.checked;
  const downgradeFuture = !!doctorState.flow.downgradeToggle?.checked;

  if (nextState === "current" && currentIndex !== -1) {
    if (autoComplete) {
      for (let i = 0; i < currentIndex; i += 1) {
        const key = FLOW_ORDER[i];
        if (updatedStates[key] === "todo") updatedStates[key] = "done";
      }
    }
    if (downgradeFuture) {
      for (let i = currentIndex + 1; i < FLOW_ORDER.length; i += 1) {
        const key = FLOW_ORDER[i];
        if (updatedStates[key] === "done") updatedStates[key] = "todo";
      }
    }
  }

  let nextCurrent = statusCurrent;
  if (nextState === "current") {
    nextCurrent = stepKey;
  } else if (nextState === "done" && statusCurrent === stepKey) {
    nextCurrent = FLOW_ORDER.find((key) => updatedStates[key] !== "done") || FLOW_ORDER[FLOW_ORDER.length - 1];
  }

  const payload = {
    [`status.steps.${stepKey}.state`]: nextState,
    [`status.steps.${stepKey}.note`]: String(note || "").trim(),
    [`status.steps.${stepKey}.date`]: String(date || "").trim(),
  };

  FLOW_ORDER.forEach((key) => {
    if (updatedStates[key] !== originalStates[key]) {
      payload[`status.steps.${key}.state`] = updatedStates[key];
    }
  });

  if (nextState === "current") {
    payload["status.current"] = stepKey;
  } else if (nextCurrent && nextCurrent !== statusCurrent) {
    payload["status.current"] = nextCurrent;
  }

  if (button) button.disabled = true;
  setFlowStatus("Saving...");
  try {
    await updateDoc(doc(db, "users", patientId), payload);
    setFlowStatus("Saved.");
  } catch (error) {
    console.error("[flow] save error", error);
    setFlowStatus("Save failed.");
  } finally {
    if (button) button.disabled = false;
  }
}

function renderFlowControls(model) {
  const listEl = doctorState.flow.listEl;
  if (!listEl) return;
  listEl.innerHTML = "";

  if (!doctorState.flow.patientId) {
    setFlowStatus("Select a patient to edit flow.");
    return;
  }

  setFlowStatus("");

  model.steps.forEach((step) => {
    const row = document.createElement("div");
    row.className = "flow-step";
    row.dataset.step = step.key;

    const main = document.createElement("div");
    main.className = "flow-step-main";
    const title = document.createElement("p");
    title.className = "flow-title";
    title.textContent = step.label;
    const text = document.createElement("p");
    text.className = "flow-text";
    text.textContent = flowSubText(step, step.state);
    main.append(title, text);

    const controls = document.createElement("div");
    controls.className = "flow-step-controls";

    const stateSelect = document.createElement("select");
    ["todo", "current", "done"].forEach((option) => {
      const opt = document.createElement("option");
      opt.value = option;
      opt.textContent = option;
      stateSelect.appendChild(opt);
    });
    stateSelect.value = step.state;

    const noteInput = document.createElement("input");
    noteInput.type = "text";
    noteInput.placeholder = "Note";
    noteInput.value = step.note || "";

    const dateInput = document.createElement("input");
    dateInput.type = "date";
    dateInput.value = formatDateInput(step.date);

    const saveBtn = document.createElement("button");
    saveBtn.type = "button";
    saveBtn.className = "pill plain";
    saveBtn.textContent = "Save";
    saveBtn.addEventListener("click", async () => {
      await saveFlowStep(step.key, stateSelect.value, noteInput.value, dateInput.value, saveBtn);
    });

    controls.append(stateSelect, noteInput, dateInput, saveBtn);
    row.append(main, controls);
    listEl.appendChild(row);
  });
}

function renderFlowEmpty() {
  doctorState.flow.model = buildFlowModel(null);
  doctorState.flow.normalizedSteps = normalizeSteps(null);
  doctorState.flow.statusCurrent = "";
  renderFlowProgress(doctorState.flow.model);
  if (doctorState.flow.listEl) doctorState.flow.listEl.innerHTML = "";
  setFlowStatus("Select a patient to edit flow.");
}

function applyFlowSnapshot(userDoc) {
  doctorState.flow.model = buildFlowModel(userDoc);
  doctorState.flow.normalizedSteps = normalizeSteps(userDoc?.status?.steps);
  doctorState.flow.statusCurrent = userDoc?.status?.current || "";
  renderFlowProgress(doctorState.flow.model);
  renderFlowControls(doctorState.flow.model);
}

function clearFlowSubscription() {
  if (doctorState.flow.unsub) {
    doctorState.flow.unsub();
    doctorState.flow.unsub = null;
  }
}

function subscribeFlowForPatient(patientId) {
  if (!isDoctorChatPage()) return;
  clearFlowSubscription();
  doctorState.flow.patientId = patientId || null;

  if (!patientId) {
    renderFlowEmpty();
    return;
  }

  const ref = doc(db, "users", patientId);
  doctorState.flow.unsub = onSnapshot(
    ref,
    (snap) => {
      const data = snap.exists() ? snap.data() : null;
      applyFlowSnapshot(data);
    },
    (error) => {
      console.error("[flow] snapshot error", error);
      renderFlowEmpty();
    }
  );
}

function renderPatientList(patients) {
  const listEl = document.getElementById("patient-list");
  const statusEl = document.getElementById("patient-list-status");
  if (!listEl || !statusEl) return;

  listEl.innerHTML = "";

  if (!patients.length) {
    statusEl.textContent = "No patients assigned yet.";
    return;
  }

  statusEl.textContent = "";
  patients.forEach((patient) => {
    const li = document.createElement("li");
    li.className = "patient-list-item";
    const title = document.createElement("div");
    title.className = "patient-name";
    title.textContent = patient.fullName || patient.name || "Unnamed patient";
    const meta = document.createElement("div");
    meta.className = "patient-email";
    meta.textContent = patient.contactEmail || patient.email || "";
    li.appendChild(title);
    li.appendChild(meta);
    li.addEventListener("click", () => {
      setActiveChatForPatient(patient);
    });
    listEl.appendChild(li);
  });
}

async function loadDoctorPatients(user) {
  if (!isDoctorChatPage()) return;
  const statusEl = document.getElementById("patient-list-status");
  if (statusEl) statusEl.textContent = "Loading patients...";

  if (!user) {
    window.location.href = "../login.html";
    return;
  }

  const profile = await getCurrentUserProfile();
  const role = (profile?.role || "").toLowerCase();
  console.log("[doctorPatients] uid:", user.uid, "role:", role);
  if (role !== "doctor") {
    window.location.href = "/patient/dashboard.html";
    return;
  }

  const links = await getDoctorPatients(user.uid);
  const patientIds = links.map((l) => l.patientId).filter(Boolean);
  console.log("[doctorPatients] patientIds:", patientIds.length);

  let patients = [];
  if (patientIds.length) {
    patients = await getPatientsByIds(patientIds);
  }
  doctorState.patients = patients;
  renderPatientList(patients);

  if (patients.length) {
    await setActiveChatForPatient(patients[0]);
  } else {
    setActiveChatForPatient(null);
  }
}

function initDashboardPatients() {
  const stateEl = document.getElementById("patientsState");
  const table = document.getElementById("patientsTable");
  const tbody = table?.querySelector("tbody");
  const searchInput = document.getElementById("patientSearch");

  if (!stateEl || !table || !tbody) return;

  const renderRows = (patients) => {
    tbody.innerHTML = "";
    if (!patients.length) {
      stateEl.textContent = "No patients assigned yet.";
      table.hidden = true;
      return;
    }
    stateEl.textContent = "";
    table.hidden = false;

    patients.forEach((p) => {
      const tr = document.createElement("tr");
      tr.dataset.patientId = p.id || p.patientId || "";
      const fullName = p.fullName || p.name || "-";
      const email = p.contactEmail || p.email || "-";
      const age = p.age ?? "-";
      const gender = p.gender || "-";
      const status = "Active";
      let created = "-";
      const ts = p.createdAt || p.created_at;
      if (ts?.toDate) {
        created = ts.toDate().toLocaleString();
      } else if (typeof ts === "string") {
        created = ts;
      }

      const cells = [fullName, email, age, gender, status, created];
      cells.forEach((val) => {
        const td = document.createElement("td");
        td.textContent = val;
        tr.appendChild(td);
      });

      tr.addEventListener("click", () => {
        const pid = tr.dataset.patientId;
        if (pid) {
          window.location.href = `/doctor/question.html?patientId=${encodeURIComponent(pid)}`;
        }
      });

      tbody.appendChild(tr);
    });
  };

  const applySearch = () => {
    const term = (searchInput?.value || "").trim().toLowerCase();
    if (!term) {
      renderRows(doctorState.patients);
      return;
    }
    const filtered = doctorState.patients.filter((p) => {
      const name = (p.fullName || p.name || "").toLowerCase();
      const email = (p.contactEmail || p.email || "").toLowerCase();
      return name.includes(term) || email.includes(term);
    });
    renderRows(filtered);
  };

  if (searchInput) {
    searchInput.addEventListener("input", applySearch);
  }

  const refreshPatients = async (user) => {
    try {
      stateEl.textContent = "Loading...";
      table.hidden = true;

      if (!user) {
        stateEl.textContent = "Not logged in";
        return;
      }

      const profile = await getCurrentUserProfile();
      const role = (profile?.role || "").toLowerCase();
      console.log("[doctor-dashboard] uid=", user?.uid, "role=", role);
      if (role !== "doctor") {
        window.location.href = "/patient/dashboard.html";
        return;
      }

      const links = await getDoctorPatients(user.uid);
      const patientIds = [...new Set(links.map((l) => l.patientId).filter(Boolean))];
      console.log("[doctor-dashboard] patientIds length", patientIds.length);

      const patients = patientIds.length ? await getPatientsByIds(patientIds) : [];
      console.log("[doctor-dashboard] patients=", patients.length);
      doctorState.patients = patients;
      renderRows(patients);
    } catch (err) {
      console.error("[doctor-dashboard] load patients error", err);
      stateEl.textContent = err?.message || "Failed to load patients.";
      doctorState.patients = [];
      table.hidden = true;
    } finally {
      if (!doctorState.patients.length && stateEl.textContent === "Loading...") {
        stateEl.textContent = "No patients assigned yet.";
      }
    }
  };

  onAuthStateChanged(auth, (user) => {
    if (!user) {
      stateEl.textContent = "Not logged in";
      return;
    }
    refreshPatients(user);
  });
}

function initProfileShortcuts() {
  const saveBtn = document.getElementById("save-profile-btn");
  if (!saveBtn) return;

  saveBtn.addEventListener("click", async () => {
    if (!auth?.currentUser) {
      alert("Please sign in again to save your profile.");
      return;
    }
    if (!db) {
      alert("Firestore is not configured. Please update firebase-config.js.");
      return;
    }

    const payload = {
      fullName: document.getElementById("doctor-name-input")?.value?.trim() || "",
      hospital: document.getElementById("doctor-hospital-input")?.value?.trim() || "",
      specialty: document.getElementById("doctor-specialty-input")?.value?.trim() || "",
      contactEmail: document.getElementById("doctor-email-input")?.value?.trim() || "",
      contactPhone: document.getElementById("doctor-phone-input")?.value?.trim() || "",
      clinicAddress: document.getElementById("doctor-address-input")?.value?.trim() || "",
      bio: document.getElementById("doctor-bio-input")?.value?.trim() || "",
      // Keep legacy-friendly aliases without touching role.
      name: document.getElementById("doctor-name-input")?.value?.trim() || "",
      hospital_name: document.getElementById("doctor-hospital-input")?.value?.trim() || "",
      email: document.getElementById("doctor-email-input")?.value?.trim() || "",
      phone: document.getElementById("doctor-phone-input")?.value?.trim() || "",
      address: document.getElementById("doctor-address-input")?.value?.trim() || "",
    };

    try {
      await setDoc(doc(db, "users", auth.currentUser.uid), payload, { merge: true });
      const cached = getCachedProfile() || {};
      localStorage.setItem("userProfile", JSON.stringify({ ...cached, ...payload }));
      alert("Profile saved.");
    } catch (error) {
      console.error("Failed to save profile to Firestore:", error);
      alert("Could not save profile. Please try again.");
    }
  });
}

window.addEventListener("DOMContentLoaded", () => {
  hydrateDoctorProfile(auth?.currentUser || null);
  onAuthStateChanged(auth, (user) => {
    hydrateDoctorProfile(user);
    initProfileShortcuts();
    loadDoctorPatients(user);
    initDashboardPatients();
  });
  initNavActiveState();
  attachLogout();
  initDoctorChat();
  initFlowControl();
});
