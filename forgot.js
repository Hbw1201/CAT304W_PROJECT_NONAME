import { auth } from "./firebase-config.js";
import { sendPasswordResetEmail } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";

const resetBtn = document.getElementById("resetBtn");
const emailInput = document.getElementById("resetEmail");

const handleReset = async () => {
  const email = emailInput?.value.trim();
  if (!email) {
    alert("Please enter your email.");
    return;
  }

  try {
    await sendPasswordResetEmail(auth, email);
    alert("Reset link sent. Please check your email.");
  } catch (error) {
    console.error(error);
    alert("Failed to send reset email: " + (error?.message || "Unknown error"));
  }
};

resetBtn?.addEventListener("click", handleReset);
