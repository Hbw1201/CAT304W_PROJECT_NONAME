(function () {
  const burgerBtn = document.getElementById("burgerBtn");
  const drawer = document.getElementById("patientDrawer");
  const overlay = document.getElementById("drawerOverlay");
  const titleEl = document.getElementById("topbarTitle");

  if (!burgerBtn || !drawer || !overlay) return;

  function isNarrow() {
    return window.matchMedia("(max-width: 1023px)").matches;
  }

  function openDrawer() {
    if (!isNarrow()) return;
    drawer.classList.add("is-open");
    overlay.classList.remove("is-hidden");
    drawer.setAttribute("aria-hidden", "false");
    burgerBtn.setAttribute("aria-expanded", "true");
    document.body.style.overflow = "hidden";
  }

  function closeDrawer() {
    drawer.classList.remove("is-open");
    overlay.classList.add("is-hidden");
    drawer.setAttribute("aria-hidden", "true");
    burgerBtn.setAttribute("aria-expanded", "false");
    document.body.style.overflow = "";
  }

  burgerBtn.addEventListener("click", () => {
    if (drawer.classList.contains("is-open")) {
      closeDrawer();
    } else {
      openDrawer();
    }
  });

  overlay.addEventListener("click", closeDrawer);

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeDrawer();
  });

  drawer.addEventListener("click", (event) => {
    const anchor = event.target.closest("a");
    if (!anchor) return;
    if (isNarrow()) closeDrawer();
  });

  window.addEventListener("resize", () => {
    if (!isNarrow()) {
      overlay.classList.add("is-hidden");
      drawer.classList.remove("is-open");
      document.body.style.overflow = "";
      drawer.setAttribute("aria-hidden", "false");
      burgerBtn.setAttribute("aria-expanded", "false");
    } else {
      drawer.setAttribute("aria-hidden", "true");
    }
  });

  try {
    const active = drawer.querySelector(".nav-item.active, .menu a.active, .nav a.active, a.active");
    if (active && titleEl) {
      titleEl.textContent = active.textContent.trim();
    } else if (titleEl) {
      titleEl.textContent = document.title || "Menu";
    }
  } catch (_) {}

  if (isNarrow()) {
    drawer.setAttribute("aria-hidden", "true");
  } else {
    drawer.setAttribute("aria-hidden", "false");
  }
})();
