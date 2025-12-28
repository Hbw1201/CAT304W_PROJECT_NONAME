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
  where,
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js";
import {
  getCurrentUserProfile,
  getDoctorPatients,
  getPatientsByIds,
} from "./firestoreService.js";

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

  if (!patient) return;
  const patientId = patient.id || patient.patientId || patient.uid;
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
});
