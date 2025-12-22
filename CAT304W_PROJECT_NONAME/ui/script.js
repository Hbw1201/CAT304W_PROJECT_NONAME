import { auth, db, storage } from "./firebase-config.js";
import { createUserWithEmailAndPassword, signInWithEmailAndPassword } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";
import { doc, getDoc, setDoc, serverTimestamp } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js";

const icons = {
  mission: '<svg class="icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M5 10.1 12 4l7 6.1a2 2 0 0 1 .7 1.54V18a1 1 0 0 1-1 1h-4v-3.35a2.65 2.65 0 0 0-5.3 0V19H5a1 1 0 0 1-1-1v-6.36A2 2 0 0 1 5 10.1Z"/><path d="M9 17.5a3 3 0 0 0 6 0V16H9v1.5Z"/></svg>',
  location: '<svg class="icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2c-3.86 0-7 2.86-7 6.4 0 4.62 6.12 12.5 6.39 12.83a1 1 0 0 0 1.56 0C12.62 20.9 19 13.2 19 8.4 19 4.86 15.86 2 12 2Zm0 11a4 4 0 1 1 0-8 4 4 0 0 1 0 8Z"/></svg>',
  phone: '<svg class="icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M17.5 15.4c-.7-.2-1.4 0-1.9.5l-.7.8a1 1 0 0 1-1.1.22 10.55 10.55 0 0 1-3.5-3.5 1 1 0 0 1 .22-1.1l.78-.72c.52-.5.72-1.24.54-1.94l-.48-1.86A1.4 1.4 0 0 0 9 6.35H6.9C5.86 6.35 5 7.2 5 8.26a10.74 10.74 0 0 0 10.74 10.74c1.06 0 1.91-.86 1.91-1.9V15.9a1.4 1.4 0 0 0-1.15-.5Z"/></svg>',
  team: '<svg class="icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm0 2c-2.7 0-7 1.35-7 4v1a1 1 0 0 0 1 1h7.3a6 6 0 0 1-.3-2c0-1.54.58-2.95 1.54-4H1Zm10 0a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-6 4a4 4 0 1 0 8 0 4 4 0 0 0-8 0Z"/></svg>',
  spark: '<svg class="icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2.5 10 9H3l5.5 4-2 7 5.5-4.5L17.5 20l-2-7L21 9h-7l-2-6.5Z"/></svg>',
  chat: '<svg class="icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M4 4h16a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H8l-4 3v-3H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z"/></svg>',
  mail: '<svg class="icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Zm0 2v.51l8 5.5 8-5.5V6H4Zm0 2.83V18h16V8.83l-7.39 5.08a1 1 0 0 1-1.22 0L4 8.83Z"/></svg>',
};

const sections = {
  homepage: {
    eyebrow: "Our Mission",
    eyebrowIcon: "mission",
    title: "Reimagining telehealth with personalized AI.",
    body: "We build tailored, privacy-first digital care experiences that connect patients and providers with meaningful insight, not noise. Osler AI brings the warmth of in-person care to every remote conversation.",
    info: [
      { label: "Office Location", value: "Osler Tower<br>Davos Street Ave 2<br>North Texas, 4441", icon: "location" },
      { label: "Contact Info", value: "(+44) 123 456 887<br>info@oslerai.com<br>inquiry@oslerai.com", icon: "phone" },
      { label: "Availability", value: "24/7 patient support<br>Same-day clinician access<br>Multilingual concierge", icon: "spark" },
    ],
  },
  services: {
    eyebrow: "What We Offer",
    eyebrowIcon: "spark",
    title: "Services crafted for modern virtual care.",
    body: "From AI triage to ambient documentation, our toolset keeps your clinicians present and your patients engaged. Everything ships compliant, secure, and customizable.",
    detailTarget: "services.html",
    info: [
      { label: "Clinical AI Suite", value: "Symptom triage, risk scoring, and routing tuned to your protocols.", icon: "chat" },
      { label: "Care Operations", value: "Scheduling, reminders, and follow-ups with zero admin drag.", icon: "spark" },
      { label: "Analytics", value: "Real-time insight into throughput, wait times, and satisfaction.", icon: "team" },
    ],
  },
  about: {
    eyebrow: "Who We Are",
    eyebrowIcon: "team",
    title: "A team blending medicine, design, and machine learning.",
    body: "We’re builders and clinicians obsessed with compassionate digital care. Every release is co-designed with providers to keep bedside manner at the center.",
    detailTarget: "about.html",
    info: [
      { label: "Founding Story", value: "Born from frontline frustrations with clunky telehealth workflows.", icon: "mission" },
      { label: "Leadership", value: "Clinicians, ML researchers, and product leaders from top health systems.", icon: "team" },
      { label: "Values", value: "Safety first, empathy always, measurable outcomes over hype.", icon: "spark" },
    ],
  },
  contact: {
    eyebrow: "Contact Us",
    eyebrowIcon: "mail",
    title: "Let’s tailor a pilot for your care teams.",
    body: "Share your use case and we’ll assemble a demo built on your workflows—no obligation, just clarity on fit.",
    detailTarget: "contact.html",
    info: [
      { label: "Sales", value: "sales@oslerai.com<br>(+44) 123 456 887", icon: "phone" },
      { label: "Partnerships", value: "partners@oslerai.com<br>Joint go-to-market and research collabs.", icon: "mail" },
      { label: "Support", value: "support@oslerai.com<br>Rapid-response concierge for your teams.", icon: "chat" },
    ],
  },
};

const contentEl = document.getElementById("content");
const menuItems = document.querySelectorAll(".menu li");
const START_TARGET = "login.html"; // TODO: update to your actual login/register route

if (contentEl && menuItems.length) {
  function renderSection(key) {
    const data = sections[key] || sections.homepage;
    menuItems.forEach((item) => {
      item.classList.toggle("active", item.dataset.section === key);
    });

    const eyebrowIcon = icons[data.eyebrowIcon] || "";

    const showStart = key === "homepage";
    const showDetails = data.detailTarget && key !== "homepage";
    contentEl.innerHTML = `
      <div>
        <div class="eyebrow">
          ${eyebrowIcon}
          ${data.eyebrow}
        </div>
        <h1 class="headline">${data.title}</h1>
        <p class="lede">${data.body}</p>
      </div>
      ${showStart ? '<button class="cta-start" type="button">Start</button>' : ""}
      ${showDetails ? `<a class="more-details" href="${data.detailTarget}">More details</a>` : ""}
    `;

    if (showStart) {
      const startBtn = contentEl.querySelector(".cta-start");
      startBtn?.addEventListener("click", () => {
        window.location.href = START_TARGET;
      });
    }
  }

  menuItems.forEach((item) => {
    const key = item.dataset.section;
    item.addEventListener("mouseenter", () => renderSection(key));
    item.addEventListener("focus", () => renderSection(key));
    item.addEventListener("click", (e) => {
      e.preventDefault();
      renderSection(key);
    });
  });

  renderSection("homepage");
}

async function handleRegister({
  mode,
  name,
  email,
  password,
  confirmPassword,
  age,
  gender,
  height,
  weight,
  relationship,
  termsChecked,
}) {
  if (!auth || !db) {
    alert("Firebase 未配置，请先填写 firebase-config.js");
    return;
  }

  if (!termsChecked) {
    alert("Please agree to the Terms & Privacy Policy.");
    return;
  }

  if (password !== confirmPassword) {
    alert("Passwords do not match.");
    return;
  }

  if (!name || !email || !gender) {
    alert("Please fill out all required fields.");
    return;
  }

  const numericAge = Number(age);
  if (mode === "self" && (Number.isNaN(numericAge) || numericAge < 18)) {
    alert("Age must be 18 or older for self registration.");
    return;
  }
  if (Number.isNaN(numericAge) || numericAge <= 0) {
    // allow 0 only for newborn? requirement says no restriction but keep >0
    alert("Please enter a valid age.");
    return;
  }

  const numericWeight = Number(weight);
  if (Number.isNaN(numericWeight) || numericWeight <= 0) {
    alert("Please enter a valid weight.");
    return;
  }

  const numericHeight = Number(height);
  if (Number.isNaN(numericHeight) || numericHeight <= 0) {
    alert("Please enter a valid height.");
    return;
  }

  try {
    const userCredential = await createUserWithEmailAndPassword(auth, email, password);
    const uid = userCredential.user?.uid;

    if (!uid) {
      throw new Error("User ID not found after registration");
    }

    const userRef = doc(db, "users", uid);
    const existing = await getDoc(userRef);
    if (!existing.exists()) {
      await setDoc(userRef, {
        uid,
        name,
        email,
        age: numericAge,
        gender,
        weight: numericWeight,
        height: numericHeight,
        relationship: mode === "other" ? relationship || "Unknown" : null,
        registeringFor: mode,
        riskLevel: "low",
        role: "patient",
        createdAt: serverTimestamp(),
      });
    }

    alert("注册成功");
    window.location.href = "login.html";
  } catch (error) {
    console.error("Registration failed:", error);
    alert(error?.message || "注册失败，请稍后重试");
  }
}

const registerSelfForm = document.getElementById("registerSelfForm");
if (registerSelfForm) {
  registerSelfForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      mode: "self",
      name: document.getElementById("patientNameSelf")?.value.trim() || "",
      email: document.getElementById("registerEmailSelf")?.value.trim() || "",
      password: document.getElementById("registerPasswordSelf")?.value || "",
      confirmPassword: document.getElementById("confirmPasswordSelf")?.value || "",
      age: document.getElementById("ageSelf")?.value,
      gender: document.getElementById("genderSelf")?.value || "",
      height: document.getElementById("heightSelf")?.value,
      weight: document.getElementById("weightSelf")?.value,
      relationship: null,
      termsChecked: document.getElementById("termsSelf")?.checked,
    };
    await handleRegister(payload);
  });
}

const registerOtherForm = document.getElementById("registerOtherForm");
if (registerOtherForm) {
  registerOtherForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      mode: "other",
      name: document.getElementById("patientNameOther")?.value.trim() || "",
      email: document.getElementById("registerEmailOther")?.value.trim() || "",
      password: document.getElementById("registerPasswordOther")?.value || "",
      confirmPassword: document.getElementById("confirmPasswordOther")?.value || "",
      age: document.getElementById("ageOther")?.value,
      gender: document.getElementById("genderOther")?.value || "",
      height: document.getElementById("heightOther")?.value,
      weight: document.getElementById("weightOther")?.value,
      relationship: document.getElementById("relationshipOther")?.value || "",
      termsChecked: document.getElementById("termsOther")?.checked,
    };
    await handleRegister(payload);
  });
}

function bindLoginHandlers() {
  const loginForm = document.getElementById("loginForm");
  const loginBtn = document.getElementById("loginBtn");
  if (!loginForm && !loginBtn) return;

  const getEmailInput = () => document.getElementById("email") || document.getElementById("loginEmail");
  const getPasswordInput = () => document.getElementById("password") || document.getElementById("loginPassword");

  const handleLogin = async (event) => {
    event?.preventDefault();

    const email = getEmailInput()?.value?.trim() || "";
    const password = getPasswordInput()?.value || "";
    console.log("[login] clicked", email, password.length);

    if (!auth || !db) {
      alert("Firebase 未配置，请先填写 firebase-config.js");
      return;
    }

    try {
      const userCredential = await signInWithEmailAndPassword(auth, email, password);
      const uid = userCredential.user?.uid;

      if (!uid) {
        throw new Error("User ID not found after login");
      }

      // Default to patient unless Firestore explicitly marks doctor.
      let role = "patient";
      let profileData = null;

      const userRef = doc(db, "users", uid);
      const userDoc = await getDoc(userRef);

      if (userDoc.exists()) {
        profileData = userDoc.data();
        const profileRole = (profileData.role || "").toLowerCase();
        if (profileRole === "doctor") {
          role = "doctor";
        }

        // Save profile data
        localStorage.setItem("userProfile", JSON.stringify(profileData));
      }

      // Persist uid and role
      localStorage.setItem("uid", uid);
      localStorage.setItem("userRole", role);

      const target = role === "doctor" ? "/doctor/dashboard.html" : "/patient/dashboard.html";
      console.log("[login] uid:", uid, "role:", role, "redirect:", target);
      window.location.href = target;
    } catch (error) {
      console.error("[login] error", error);
      alert(error?.message || "Login failed, please check your email and password");
    }
  };

  loginBtn?.addEventListener("click", handleLogin);
  loginForm?.addEventListener("submit", handleLogin);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", bindLoginHandlers);
} else {
  bindLoginHandlers();
}


// Privacy modal
const openPrivacyBtn = document.getElementById("open-privacy");
const closePrivacyBtn = document.getElementById("close-privacy");
const privacyModal = document.getElementById("privacy-modal");
const openLegalBtn = document.getElementById("open-legal");
const closeLegalBtn = document.getElementById("close-legal");
const legalModal = document.getElementById("legal-modal");

function openPrivacy() {
  privacyModal.classList.add("show");
  privacyModal.setAttribute("aria-hidden", "false");
}

function closePrivacy() {
  privacyModal.classList.remove("show");
  privacyModal.setAttribute("aria-hidden", "true");
}

openPrivacyBtn?.addEventListener("click", openPrivacy);
closePrivacyBtn?.addEventListener("click", closePrivacy);
privacyModal?.addEventListener("click", (e) => {
  if (e.target === privacyModal) closePrivacy();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closePrivacy();
});

function openLegal() {
  legalModal.classList.add("show");
  legalModal.setAttribute("aria-hidden", "false");
}

function closeLegal() {
  legalModal.classList.remove("show");
  legalModal.setAttribute("aria-hidden", "true");
}

openLegalBtn?.addEventListener("click", openLegal);
closeLegalBtn?.addEventListener("click", closeLegal);
legalModal?.addEventListener("click", (e) => {
  if (e.target === legalModal) closeLegal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeLegal();
    closePrivacy();
  }
});

// Question 页面：前端假数据聊天逻辑
window.addEventListener("DOMContentLoaded", () => {
  const isQuestionPage = location.pathname.toLowerCase().includes("question.html");
  if (!isQuestionPage) return;

  const chatMessages = document.getElementById("chat-messages");
  const chatInput = document.getElementById("chat-input");
  const chatSendBtn = document.getElementById("chat-send");

  // 如果结构缺失则直接返回，避免在其它页面报错
  if (!chatMessages || !chatInput || !chatSendBtn) return;

  // 初始化假数据消息
  const mockMessages = [
    { sender: "doctor", text: "Hi, I am the on-duty doctor. If you have questions about your screening results or reports, ask me here.", time: getCurrentTime() },
    { sender: "user", text: "Doctor, I want to understand what my screening risk level means.", time: getCurrentTime() },
  ];

  // 时间格式化：返回当前的小时:分钟
  function getCurrentTime() {
    const now = new Date();
    return now.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
  }

  // 追加一条消息到聊天窗口
  function appendMessage(sender, text, time) {
    const wrapper = document.createElement("div");
    wrapper.className = `chat-message ${sender}`;

    const metaRow = document.createElement("div");
    metaRow.className = "chat-message-meta";

    const senderEl = document.createElement("span");
    senderEl.className = "chat-message-sender";
    senderEl.textContent = sender === "user" ? "Me" : "Doctor";

    const timeEl = document.createElement("span");
    timeEl.className = "chat-message-time";
    timeEl.textContent = time || getCurrentTime();

    metaRow.appendChild(senderEl);
    metaRow.appendChild(timeEl);

    const textEl = document.createElement("p");
    textEl.className = "chat-message-text";
    textEl.textContent = text;

    wrapper.appendChild(metaRow);
    wrapper.appendChild(textEl);
    chatMessages.appendChild(wrapper);

    // 滚动到底部，保证最新消息可见
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  // 渲染初始的模拟对话
  mockMessages.forEach((msg) => {
    appendMessage(msg.sender, msg.text, msg.time);
  });

  // 发送消息封装
  function sendUserMessage() {
    const text = chatInput.value.trim();
    if (!text) return;

    appendMessage("user", text, getCurrentTime());
    chatInput.value = "";

    // 模拟医生 1~2 秒后回复
    const replyDelay = 800 + Math.random() * 800;
    setTimeout(() => {
      appendMessage("doctor", "(Auto-reply) I have received your question and will get back to you shortly.", getCurrentTime());
    }, replyDelay);
  }

  chatSendBtn.addEventListener("click", sendUserMessage);

  // Enter 发送，Shift+Enter 换行
  chatInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendUserMessage();
    }
  });
});

// 医生端聊天模块：医生视角回复患者，后续可接入 Firestore 消息流
window.addEventListener("DOMContentLoaded", () => {
  if (!window.location.pathname.toLowerCase().includes("doctor/question.html")) return;

  const messages = document.getElementById("doctor-chat-messages");
  const input = document.getElementById("doctor-chat-input");
  const sendBtn = document.getElementById("doctor-chat-send");
  const doctorNameEl = document.getElementById("doctor-name-display");
  const doctorHospitalEl = document.getElementById("doctor-hospital-display");
  const brandName = document.getElementById("brand-name");
  const brandAvatar = document.getElementById("brand-avatar");

  // 如果结构缺失则不执行，避免在其它页面报错
  if (!messages || !input || !sendBtn) return;

  // 获取当前时间字符串
  function getCurrentTime() {
    const now = new Date();
    return now.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
  }

  // 追加一条消息到医生端聊天窗口
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

    // 保持列表滚动在底部
    messages.scrollTop = messages.scrollHeight;
  }

  // 初始化假数据：患者提问 + 医生回复
  const doctorMockMessages = [
    { sender: "patient", text: "Doctor, could you explain my latest screening result?", time: getCurrentTime() },
    { sender: "doctor", text: "Sure. Your risk level suggests we should schedule a follow-up visit this week.", time: getCurrentTime() },
  ];

  doctorMockMessages.forEach((msg) => {
    appendDoctorMessage(msg.sender, msg.text, msg.time);
  });

  // 发送医生消息封装
  function sendDoctorMessage() {
    const text = input.value.trim();
    if (!text) return;

    appendDoctorMessage("doctor", text, getCurrentTime());
    input.value = "";

    // 模拟患者 1~2 秒后回复；后续接入 Firestore 时改为监听实时消息
    const replyDelay = 800 + Math.random() * 800;
    setTimeout(() => {
      appendDoctorMessage("patient", "(Mock patient) Thank you for the explanation.", getCurrentTime());
    }, replyDelay);
  }

  sendBtn.addEventListener("click", sendDoctorMessage);

  // Enter 发送，Shift+Enter 换行
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendDoctorMessage();
    }
  });

  // 读取医生信息以展示侧边栏与品牌，失败则保持默认文案
  async function loadDoctorProfile() {
    if (!auth?.currentUser) return;
    try {
      const profileSnap = await getDoc(doc(db, "users", auth.currentUser.uid));
      if (profileSnap.exists()) {
        const data = profileSnap.data();
        if (data?.role === "doctor") {
          const displayName = (data.name || "Doctor").trim();
          const hospital = (data.hospital_name || "Unknown hospital").trim();
          if (doctorNameEl) doctorNameEl.textContent = displayName;
          if (brandName) brandName.textContent = displayName;
          if (brandAvatar) brandAvatar.textContent = displayName.charAt(0).toUpperCase() || "D";
          if (doctorHospitalEl) doctorHospitalEl.textContent = hospital;
        }
      }
    } catch (err) {
      console.error("Failed to load doctor profile:", err);
    }
  }

  // 未来接 Firebase 时，可在此处扩展：登录后拉取医生资料、监听患者消息 onSnapshot 等
  loadDoctorProfile();
});
