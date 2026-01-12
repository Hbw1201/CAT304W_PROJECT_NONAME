import { app } from "../firebase-config.js";
import { getAuth, onAuthStateChanged } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";

const DEBUG_HOSTS = ["localhost", "127.0.0.1"];
const isDebug =
  typeof window !== "undefined" &&
  DEBUG_HOSTS.includes(window.location.hostname);
const auth = getAuth(app);
const inFlight = new Map();

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

export async function getIdTokenOrThrow() {
  let user = auth.currentUser;
  if (!user) {
    user = await waitForAuthUser();
  }
  if (!user) {
    throw new Error("AUTH_NOT_READY");
  }
  let token;
  try {
    token = await user.getIdToken(true);
  } catch (err) {
    console.warn("[screening auth] getIdToken failed:", err);
    throw err;
  }
  const dotCount = typeof token === "string" ? token.split(".").length - 1 : -1;
  const tokenLen = typeof token === "string" ? token.length : 0;
  console.log("[screening auth] token_shape", { tokenLen, dotCount });
  if (dotCount !== 2) {
    throw new Error("INVALID_ID_TOKEN_FORMAT");
  }
  return { token, tokenLen, dotCount };
}

export async function getBearerToken() {
  const { token } = await getIdTokenOrThrow();
  return token;
}

export async function fetchWithAuth(url, options = {}) {
  const headers = new Headers(options.headers || {});
  const method = (options.method || "GET").toUpperCase();
  const key = `${method} ${url}`;
  const isMetaNext =
    method === "POST" && url.includes("/api/screen/metagpt/next");
  if (isMetaNext) {
    if (inFlight.get(key)) {
      const timestamp = new Date().toISOString();
      console.warn("[screening auth] blocked duplicate in-flight", key, timestamp);
      throw new Error("REQUEST_IN_FLIGHT_BLOCKED");
    }
    inFlight.set(key, true);
  }
  try {
    const { token: idToken, tokenLen, dotCount } = await getIdTokenOrThrow();
    headers.set("Authorization", `Bearer ${idToken}`);
    headers.set("Accept", "application/json");

    const body = options.body;
    const hasContentType = headers.has("Content-Type");
    const isFormData = typeof FormData !== "undefined" && body instanceof FormData;
    const isBlob = typeof Blob !== "undefined" && body instanceof Blob;
    const isArrayBuffer = typeof ArrayBuffer !== "undefined" && body instanceof ArrayBuffer;

    if (body && !hasContentType && !isFormData && !isBlob && !isArrayBuffer) {
      headers.set("Content-Type", "application/json");
    }

    const tokenAttached = Boolean(headers.get("Authorization"));
    console.info(
      `[screening auth] ${method} ${url} token_attached=${tokenAttached} tokenLen=${tokenLen} dotCount=${dotCount}`
    );

    const response = await fetch(url, { ...options, method, headers });

    if (response.status === 401 || response.status === 403) {
      window.screeningUI?.appendSystemMessage?.(
        "Authentication expired, please sign in again."
      );
      window.location.href = "/patient/login.html";
    }

    return response;
  } finally {
    if (isMetaNext) {
      inFlight.delete(key);
    }
  }
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
