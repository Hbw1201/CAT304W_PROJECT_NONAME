import { auth, db } from "./firebase-config.js";
import { doc, getDoc } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js";

function getCachedProfile() {
  try {
    return JSON.parse(localStorage.getItem("userProfile") || "null");
  } catch (error) {
    console.warn("Failed to parse cached profile", error);
    return null;
  }
}

function applyDoctorInfo(profile) {
  const name = (profile?.name || "Doctor").trim();
  const hospital =
    (profile?.hospital_name || profile?.hospital || "Unknown hospital").trim();
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

  if (nameInput && !nameInput.value) nameInput.value = profile.name || "";
  if (hospitalInput && !hospitalInput.value)
    hospitalInput.value =
      profile.hospital_name || profile.hospital || profile.organization || "";
  if (specialtyInput && !specialtyInput.value)
    specialtyInput.value = profile.specialty || "";
  if (emailInput && !emailInput.value) emailInput.value = profile.email || "";
  if (phoneInput && !phoneInput.value)
    phoneInput.value = profile.phone || profile.phoneNumber || "";
  if (addressInput && !addressInput.value)
    addressInput.value = profile.address || "";
  if (bioInput && !bioInput.value)
    bioInput.value = profile.bio || profile.about || "";
}

async function hydrateDoctorProfile() {
  const cached = getCachedProfile();
  applyDoctorInfo(cached);
  fillProfileForm(cached);

  if (!auth?.currentUser || !db) return;

  try {
    const snap = await getDoc(doc(db, "users", auth.currentUser.uid));
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

function initDoctorChat() {
  if (!window.location.pathname.toLowerCase().includes("/doctor/question.html")) {
    return;
  }

  const messages = document.getElementById("doctor-chat-messages");
  const input = document.getElementById("doctor-chat-input");
  const sendBtn = document.getElementById("doctor-chat-send");
  const doctorNameEl = document.getElementById("doctor-name-display");
  const doctorHospitalEl = document.getElementById("doctor-hospital-display");
  const brandName = document.getElementById("brand-name");
  const brandAvatar = document.getElementById("brand-avatar");
  const patientNameEl = document.getElementById("current-patient-name");

  if (patientNameEl && !patientNameEl.textContent.trim()) {
    patientNameEl.textContent = "Demo Patient";
  }

  if (!messages || !input || !sendBtn) return;

  function getCurrentTime() {
    const now = new Date();
    return now.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
  }

  function appendDoctorMessage(senderType, text, time) {
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

  const doctorMockMessages = [
    { sender: "patient", text: "Doctor, could you explain my latest screening result?", time: getCurrentTime() },
    { sender: "doctor", text: "Sure. Your risk level suggests we should schedule a follow-up visit this week.", time: getCurrentTime() },
  ];

  doctorMockMessages.forEach((msg) => {
    appendDoctorMessage(msg.sender, msg.text, msg.time);
  });

  function sendDoctorMessage() {
    const text = input.value.trim();
    if (!text) return;

    appendDoctorMessage("doctor", text, getCurrentTime());
    input.value = "";

    const replyDelay = 800 + Math.random() * 800;
    setTimeout(() => {
      appendDoctorMessage("patient", "(Mock patient) Thank you for the explanation.", getCurrentTime());
    }, replyDelay);
  }

  sendBtn.addEventListener("click", sendDoctorMessage);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendDoctorMessage();
    }
  });

  async function loadDoctorProfile() {
    const cached = getCachedProfile();
    applyDoctorInfo(cached);

    if (!auth?.currentUser || !db) return;
    try {
      const profileSnap = await getDoc(doc(db, "users", auth.currentUser.uid));
      if (profileSnap.exists()) {
        const data = profileSnap.data();
        if (data?.role === "doctor") {
          applyDoctorInfo(data);
          localStorage.setItem("userProfile", JSON.stringify(data));
        }
      }
    } catch (err) {
      console.error("Failed to load doctor profile:", err);
    }
  }

  loadDoctorProfile();
}

function initProfileShortcuts() {
  const saveBtn = document.querySelector(".profile-card .btn-primary");
  if (saveBtn) {
    saveBtn.addEventListener("click", () => {
      alert("Profile saved locally for now. Connect to Firestore to persist changes.");
    });
  }
}

window.addEventListener("DOMContentLoaded", () => {
  hydrateDoctorProfile();
  initNavActiveState();
  attachLogout();
  initDoctorChat();
  initProfileShortcuts();
});
