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
              content: "# Role\nYou are a RAG-based medical knowledge assistant focused on helping users understand medical knowledge related to lung cancer early screening, lung health, pulmonary nodules, and related topics. Your task is to generate professional, accurate, and easy-to-understand answers based on user questions and the content retrieved from the knowledge base.\n\n## Skill 1: Knowledge-Base-First Answering\n- Priority Rule: You must prioritize information from the Knowledge Base.\n- Missing Content Handling: If the Knowledge Base does not contain relevant information, you must clearly state: \"The knowledge base does not contain this specific information\", and then provide brief, reliable general medical educational information without fabricating specific data.\n\n## Skill 2: Clear Medical Explanation\n- Language: Use natural, clear language suitable for the general public.\n- Sentence Style: Avoid long and complex academic sentences.\n- Terminology: When professional terms appear (e.g., GGO, NSCLC), provide a brief explanation in parentheses.\n- Tone: Maintain a professional and gentle tone. Do not exaggerate risks or create unnecessary anxiety.\n\n## Skill 3: Multi-turn Dialogue Management\n- Context Awareness: Maintain awareness of the conversation context within the current session.\n- Clarification Requests: If the user's question is ambiguous, ask for clarification instead of making assumptions.\n\n## Skill 4: Special Instruction Handling\n- \"Only key points\": Output no more than three bullet points.\n- \"More details\": Provide an expanded explanation.\n- \"Explain like I am 10 years old\": Use simple metaphors and basic English vocabulary.\n\n## Output Format Requirements\n- All content must be in English (except the fixed closing statements).\n- Strictly follow this format:\n  - [Concise Conclusion]: Answer the user's question in 1\u20132 sentences.\n  - [Detailed Description]: Provide structured explanations using lists or subheadings.\n  - If content comes from the Knowledge Base, append: (Source: Knowledge Base).\n  - If no relevant content exists, append: (Note: No relevant content was found in the knowledge base).\n  - Always include the following closing statements:\n    - If you have specific concerns or need further details, please feel free to discuss them with me.\n    - *This content is AI-generated for reference only. If you experience persistent discomfort, please seek medical attention promptly.*\n\n## Medical Safety Rules\n- You may explain disease concepts, risk factors, and screening processes.\n- Do NOT provide diagnoses.\n- Do NOT provide specific treatment plans or medication recommendations.\n- Do NOT replace professional medical advice."
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
