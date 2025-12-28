"use strict";

// Load environment variables from .env file (if exists)
require("dotenv").config();
console.log("### MAQ_CHAT BOT VERSION ###", new Date().toISOString(), __filename);

// Dependencies
const express = require("express");
const cors = require("cors");
const fetch = require("node-fetch");  // node-fetch v2: must be explicitly required

// DashScope API configuration (loaded before app creation)
const DASHSCOPE_API_KEY = process.env.DASHSCOPE_API_KEY;
const DASHSCOPE_MODEL = process.env.DASHSCOPE_MODEL || "qwen-plus";
const DASHSCOPE_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions";

function main() {
  const app = express();
  app.use(cors());
  app.use(express.json({ limit: "1mb" }));  // JSON body parser with 1mb limit

  const port = Number(process.env.PORT || process.env.NODE_CHAT_PORT || 3000);

  // Startup self-check logs
  console.log("[maq_chat] fetch type:", typeof fetch);
  console.log("[maq_chat] DASHSCOPE_API_KEY set:", Boolean(DASHSCOPE_API_KEY));
  console.log("[maq_chat] model:", DASHSCOPE_MODEL);

  app.get("/health", (req, res) => {
    res.json({ ok: true, service: "maq_chat", port });
  });

  // probe-friendly endpoints
  app.get("/api/chat", (req, res) => {
    res.json({ ok: true, service: "maq_chat", message: "chat endpoint ready" });
  });
  app.head("/api/chat", (req, res) => res.sendStatus(200));

  // real chat handler with DashScope integration
  app.post("/api/chat", async (req, res) => {
    try {
      // Extract user message from request body
      const userMessage = req.body.message || req.body.text || req.body.prompt;
      
      // Validate input
      if (!userMessage || typeof userMessage !== "string" || userMessage.trim().length === 0) {
        return res.status(400).json({
          ok: false,
          error: "missing_message",
          message: "Message, text, or prompt field is required and must be non-empty"
        });
      }

      // Check API key
      if (!DASHSCOPE_API_KEY) {
        console.error("[maq_chat] DASHSCOPE_API_KEY not set in environment variables");
        return res.status(502).json({
          ok: false,
          error: "dashscope_error",
          message: "DashScope API key not configured"
        });
      }

      // Call DashScope API (OpenAI compatible mode) with timeout
      const fetchPromise = fetch(DASHSCOPE_ENDPOINT, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${DASHSCOPE_API_KEY}`
        },
        body: JSON.stringify({
          model: DASHSCOPE_MODEL,
          messages: [
            {
              role: "system",
              content: "You are LungCare AI Assistant. Provide evidence-based lung cancer screening guidance. Keep answers concise and safe. Do not provide personal diagnoses or replace medical professionals."
            },
            {
              role: "user",
              content: userMessage.trim()
            }
          ],
          temperature: 0.3
        })
      });

      // Timeout wrapper (30 seconds)
      const timeoutPromise = new Promise((_, reject) => {
        setTimeout(() => reject(new Error("Request timeout")), 30000);
      });

      const response = await Promise.race([fetchPromise, timeoutPromise]);

      if (!response.ok) {
        const errorText = await response.text();
        console.error(`[maq_chat] DashScope API error: ${response.status} ${response.statusText}`, errorText);
        return res.status(502).json({
          ok: false,
          error: "dashscope_error",
          message: `DashScope API returned ${response.status}: ${response.statusText}`
        });
      }

      const data = await response.json();
      
      // Extract reply from DashScope response
      const reply = data.choices?.[0]?.message?.content || data.choices?.[0]?.message?.text || "";
      
      if (!reply) {
        console.error("[maq_chat] DashScope response missing content:", JSON.stringify(data));
        return res.status(502).json({
          ok: false,
          error: "dashscope_error",
          message: "DashScope API response missing content"
        });
      }

      // Return unified response structure
      return res.json({
        ok: true,
        reply: reply,
        answer: reply // alias for compatibility
      });

    } catch (err) {
      console.error("[maq_chat] Error processing chat request:", err);
      
      // Handle timeout specifically
      if (err.message === "Request timeout" || err.message.includes("timeout")) {
        return res.status(504).json({
          ok: false,
          error: "dashscope_error",
          message: "Request timeout: DashScope API did not respond within 30 seconds"
        });
      }
      
      return res.status(502).json({
        ok: false,
        error: "dashscope_error",
        message: err.message || "Failed to process chat request"
      });
    }
  });

  app.listen(port, "127.0.0.1", () => {
    console.log(`[maq_chat] listening on http://127.0.0.1:${port}`);
  });
}

try {
  main();
} catch (err) {
  console.error("[maq_chat] fatal:", err);
  process.exit(1);
}
