import { auth } from "../firebase-config.js";
import { onAuthStateChanged } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";

const DEBUG_HOSTS = ["localhost", "127.0.0.1"];
const isDebug =
  typeof window !== "undefined" &&
  DEBUG_HOSTS.includes(window.location.hostname);

export function debugLog(...args) {
  if (isDebug) {
    console.log(...args);
  }
}

export function waitForAuthUser(timeoutMs = 8000) {
  return new Promise((resolve) => {
    let settled = false;
    let unsubscribe = null;

    const finish = (user) => {
      if (settled) return;
      settled = true;
      if (unsubscribe) {
        unsubscribe();
      }
      resolve(user || null);
    };

    const timer = window.setTimeout(() => finish(null), timeoutMs);
    unsubscribe = onAuthStateChanged(auth, (user) => {
      window.clearTimeout(timer);
      finish(user);
    });
  });
}

export async function getBearerToken() {
  const user = auth.currentUser || await waitForAuthUser();
  if (!user) return null;
  try {
    return await user.getIdToken();
  } catch (err) {
    console.warn("[screening auth] getIdToken failed:", err);
    return null;
  }
}

export async function fetchWithAuth(url, options = {}) {
  const token = await getBearerToken();
  const headers = new Headers(options.headers || {});
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  headers.set("Accept", "application/json");

  const body = options.body;
  const hasContentType = headers.has("Content-Type");
  const isFormData = typeof FormData !== "undefined" && body instanceof FormData;
  const isBlob = typeof Blob !== "undefined" && body instanceof Blob;
  const isArrayBuffer = typeof ArrayBuffer !== "undefined" && body instanceof ArrayBuffer;

  if (body && !hasContentType && !isFormData && !isBlob && !isArrayBuffer) {
    headers.set("Content-Type", "application/json");
  }

  const method = options.method || "GET";
  const tokenAttached = Boolean(headers.get("Authorization"));
  console.info(`[screening auth] ${method} ${url} token_attached=${tokenAttached}`);

  const response = await fetch(url, { ...options, headers });

  if (response.status === 401 || response.status === 403) {
    window.screeningUI?.appendSystemMessage?.(
      "Authentication expired, please sign in again."
    );
    window.location.href = "/patient/login.html";
  }

  return response;
}

export async function readJsonWithRequestId(response) {
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  const headerRequestId = response.headers.get("X-Request-Id");
  if (
    headerRequestId &&
    data &&
    typeof data === "object" &&
    !Array.isArray(data) &&
    !data.request_id
  ) {
    data.request_id = headerRequestId;
  }
  return {
    data,
    requestId: data?.request_id || headerRequestId || "",
  };
}
