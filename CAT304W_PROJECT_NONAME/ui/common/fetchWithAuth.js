import { auth } from "../firebase-config.js";

const LOGIN_PATH = "/patient/login.html";
const isLocalhost = typeof window !== "undefined" && ["localhost", "127.0.0.1"].includes(window.location.hostname);

function requireUser() {
  const user = auth?.currentUser;
  if (!user) {
    console.error("[fetchWithAuth] No signed-in user; redirecting to login");
    window.location.href = LOGIN_PATH;
    throw new Error("Not authenticated");
  }
  return user;
}

export async function fetchWithAuth(url, options = {}) {
  const user = requireUser();

  let token;
  try {
    token = await user.getIdToken(true);
  } catch (error) {
    console.error("[fetchWithAuth] Failed to get ID token", error);
    alert("Session expired. Please sign in again.");
    window.location.href = LOGIN_PATH;
    throw error;
  }

  const headers = {
    ...(options.headers || {}),
    Authorization: `Bearer ${token}`,
  };

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (!response.ok) {
    console.error("[fetchWithAuth] HTTP error", response.status, response.statusText);
  }

  return response;
}

if (typeof window !== "undefined") {
  window.fetchWithAuth = fetchWithAuth;
  if (isLocalhost) {
    window.__debugGetIdToken = async () => {
      const user = requireUser();
      return user.getIdToken(true);
    };
  }
}
