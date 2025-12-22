import { app } from "../firebase-config.js";
import { getAuth } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";

const DEBUG_HOSTS = ["localhost", "127.0.0.1"];
const isDebug =
  typeof window !== "undefined" &&
  DEBUG_HOSTS.includes(window.location.hostname);

export function debugLog(...args) {
  if (isDebug) {
    console.log(...args);
  }
}

function getCurrentUser() {
  const auth = getAuth(app);
  const user = auth.currentUser;
  if (!user) {
    throw new Error("Not authenticated");
  }
  return user;
}

function buildAuthHeaders(token, headers) {
  const merged = new Headers(headers || {});
  merged.set("Authorization", `Bearer ${token}`);
  merged.set("FirebaseIdToken", token);
  merged.set("X-Firebase-Token", token);
  return merged;
}

async function getToken(user, forceRefresh) {
  return user.getIdToken(forceRefresh);
}

export async function fetchWithAuth(url, options = {}) {
  const user = getCurrentUser();
  const method = options.method || "GET";
  const token = await getToken(user, true);
  const tokenPrefix = token.slice(0, 12);
  const requestOptions = {
    ...options,
    headers: buildAuthHeaders(token, options.headers),
  };

  let response = await fetch(url, requestOptions);
  debugLog(
    `[screening auth] ${method} ${url} status=${response.status} token_prefix=${tokenPrefix}`
  );

  if (response.status === 401 || response.status === 403) {
    const refreshed = await getToken(user, true);
    const refreshedPrefix = refreshed.slice(0, 12);
    response = await fetch(url, {
      ...options,
      headers: buildAuthHeaders(refreshed, options.headers),
    });
    debugLog(
      `[screening auth] retry ${method} ${url} status=${response.status} token_prefix=${refreshedPrefix}`
    );
  }

  return response;
}
