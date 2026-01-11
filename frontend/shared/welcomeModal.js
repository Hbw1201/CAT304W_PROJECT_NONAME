const STYLE_ID = "welcome-modal-styles";
const OVERLAY_ID = "welcome-modal-overlay";

function ensureStyles() {
  if (document.getElementById(STYLE_ID)) return;

  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = `
    .welcome-modal-overlay {
      position: fixed;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
      background: rgba(0, 0, 0, 0.55);
      z-index: 9999;
    }

    .welcome-modal-card {
      width: 92%;
      max-width: 520px;
      max-height: 70vh;
      padding: 24px;
      border-radius: 16px;
      background: #ffffff;
      color: #0f172a;
      box-shadow: 0 20px 50px rgba(15, 23, 42, 0.25);
      text-align: left;
      font-family: "Manrope", system-ui, -apple-system, sans-serif;
      display: flex;
      flex-direction: column;
      gap: 14px;
      overflow: hidden;
    }

    .welcome-modal-title {
      margin: 0;
      font-size: 22px;
      font-weight: 700;
    }

    .welcome-modal-subtitle {
      margin: 6px 0 0;
      font-size: 14px;
      line-height: 1.5;
      color: #475569;
    }

    .welcome-modal-content {
      flex: 1 1 auto;
      min-height: 0;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 12px;
      padding-right: 4px;
    }

    .welcome-modal-guide-title {
      margin: 0;
      font-size: 14px;
      font-weight: 700;
      color: #0f172a;
    }

    .welcome-modal-guide {
      display: grid;
      gap: 12px;
    }

    .welcome-modal-item {
      display: flex;
      gap: 12px;
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid #eaeaea;
      background: #ffffff;
    }

    .welcome-modal-icon {
      flex: 0 0 30px;
      width: 30px;
      height: 30px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 10px;
      background: #f1f5f9;
      border: 1px solid #e2e8f0;
      color: #1f9d68;
    }

    .welcome-modal-icon svg {
      width: 18px;
      height: 18px;
    }

    .welcome-modal-item-title {
      margin: 0;
      font-size: 14px;
      font-weight: 700;
      color: #0f172a;
    }

    .welcome-modal-item-body {
      margin: 4px 0 0;
      font-size: 13px;
      line-height: 1.4;
      color: #475569;
    }

    .welcome-modal-actions {
      display: flex;
      justify-content: center;
    }

    .welcome-modal-btn {
      min-width: 180px;
      min-height: 44px;
      padding: 12px 22px;
      border-radius: 999px;
      border: none;
      background: #1f9d68;
      color: #ffffff;
      font-weight: 600;
      font-size: 14px;
      cursor: pointer;
    }

    .welcome-modal-btn:focus {
      outline: 2px solid rgba(31, 157, 104, 0.4);
      outline-offset: 2px;
    }
  `;
  document.head.appendChild(style);
}

function resolveDisplayName(user, userData) {
  const rawName = String(userData?.name || "").trim();
  if (rawName) return rawName;

  const email = String(user?.email || userData?.email || "").trim();
  if (email) return email.split("@")[0] || email;

  return "User";
}

export function renderWelcomeOnboardingModal({ displayName, onClose }) {
  ensureStyles();
  const overlay = document.createElement("div");
  overlay.id = OVERLAY_ID;
  overlay.className = "welcome-modal-overlay";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-labelledby", "welcome-modal-title");
  overlay.setAttribute("aria-describedby", "welcome-modal-subtitle");

  const card = document.createElement("div");
  card.className = "welcome-modal-card";
  card.addEventListener("click", (event) => event.stopPropagation());

  const header = document.createElement("div");

  const title = document.createElement("h2");
  title.id = "welcome-modal-title";
  title.className = "welcome-modal-title";
  title.textContent = `Welcome, ${displayName}!`;

  const subtitle = document.createElement("p");
  subtitle.id = "welcome-modal-subtitle";
  subtitle.className = "welcome-modal-subtitle";
  subtitle.textContent = "Thanks for joining! Here's a quick guide to help you get started.";

  header.appendChild(title);
  header.appendChild(subtitle);

  const content = document.createElement("div");
  content.className = "welcome-modal-content";

  const guideTitle = document.createElement("p");
  guideTitle.className = "welcome-modal-guide-title";
  guideTitle.textContent = "Quick Guide";

  const guide = document.createElement("div");
  guide.className = "welcome-modal-guide";

  const guideItems = [
    {
      label: "Report",
      description: "View your screening results and generated medical reports.",
      icon: `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M8 3.5h6l4 4V19a1.5 1.5 0 0 1-1.5 1.5H8A1.5 1.5 0 0 1 6.5 19V5A1.5 1.5 0 0 1 8 3.5z"/>
          <path d="M14 3.5V8h4"/>
          <path d="M9 12h6"/>
          <path d="M9 15h6"/>
        </svg>
      `,
    },
    {
      label: "Chatbot",
      description: "Ask health-related questions and get instant AI assistance.",
      icon: `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M5 6.5h14a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H10l-4 3v-3H5a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2z"/>
        </svg>
      `,
    },
    {
      label: "Question",
      description: "Complete questionnaires to help assess your health risk.",
      icon: `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M9.5 9a2.5 2.5 0 0 1 5 0c0 2-2.5 2-2.5 4"/>
          <circle cx="12" cy="18" r="1"/>
        </svg>
      `,
    },
    {
      label: "Appointment",
      description: "Book and manage your appointments with doctors.",
      icon: `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <rect x="4" y="5" width="16" height="15" rx="2"/>
          <path d="M8 3v4M16 3v4M4 9h16"/>
        </svg>
      `,
    },
    {
      label: "Screening",
      description: "Start or continue your health screening process.",
      icon: `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M4 12h4l2-3 3 6 2-3h5"/>
        </svg>
      `,
    },
  ];

  guideItems.forEach((item) => {
    const row = document.createElement("div");
    row.className = "welcome-modal-item";

    const icon = document.createElement("div");
    icon.className = "welcome-modal-icon";
    icon.innerHTML = item.icon.trim();

    const text = document.createElement("div");

    const label = document.createElement("p");
    label.className = "welcome-modal-item-title";
    label.textContent = item.label;

    const desc = document.createElement("p");
    desc.className = "welcome-modal-item-body";
    desc.textContent = item.description;

    text.appendChild(label);
    text.appendChild(desc);

    row.appendChild(icon);
    row.appendChild(text);
    guide.appendChild(row);
  });

  content.appendChild(guideTitle);
  content.appendChild(guide);

  const actions = document.createElement("div");
  actions.className = "welcome-modal-actions";

  const okBtn = document.createElement("button");
  okBtn.type = "button";
  okBtn.className = "welcome-modal-btn";
  okBtn.textContent = "Got it, let's start";

  actions.appendChild(okBtn);

  card.appendChild(header);
  card.appendChild(content);
  card.appendChild(actions);
  overlay.appendChild(card);
  document.body.appendChild(overlay);

  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    overlay.remove();
    document.removeEventListener("keydown", onKeyDown);
    if (typeof onClose === "function") onClose();
  };

  const onKeyDown = (event) => {
    if (event.key === "Escape") {
      close();
    }
  };

  overlay.addEventListener("click", close);
  okBtn.addEventListener("click", close);
  document.addEventListener("keydown", onKeyDown);

  requestAnimationFrame(() => okBtn.focus());
  return { overlay, okBtn };
}

export function maybeShowWelcomeModal(user, userData) {
  if (!user?.uid || !userData) return;
  if (document.getElementById(OVERLAY_ID)) return;

  const createdAt = userData.createdAt;
  if (!createdAt || typeof createdAt.toMillis !== "function") return;

  const createdMs = createdAt.toMillis();
  if (!Number.isFinite(createdMs)) return;

  // Eligibility: only show within the first hour after account creation.
  const ageSeconds = (Date.now() - createdMs) / 1000;
  if (!(ageSeconds >= 0 && ageSeconds < 3600)) return;

  const storageKey = `welcomeShown_${user.uid}`;
  // Guard: only show once per user per browser.
  if (localStorage.getItem(storageKey)) return;

  renderWelcomeOnboardingModal({
    displayName: resolveDisplayName(user, userData),
    onClose: () => {
      // Persist the dismissal so this user doesn't see it again.
      localStorage.setItem(storageKey, "1");
    },
  });
}
