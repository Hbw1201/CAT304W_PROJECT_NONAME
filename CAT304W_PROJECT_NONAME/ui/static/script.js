// Compatibility wrapper for legacy /static/script.js requests.
// This file re-exports the root /script.js by creating a script element.
(function () {
  try {
    var s = document.createElement("script");
    s.src = "/script.js";
    s.async = false;
    document.head.appendChild(s);
  } catch (e) {
    console.error("[static/script.js] failed to load /script.js", e);
  }
})();


