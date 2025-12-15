const path = require('path');
const express = require('express');
require('dotenv').config({ path: path.join(__dirname, '.env') });

const app = express();
const PORT = process.env.NODE_CHAT_PORT || 3000;
const API_KEY = process.env.DASHSCOPE_API_KEY;
const APP_ID = process.env.DASHSCOPE_APP_ID;
const UI_DIR = path.join(__dirname, 'ui');

const fetchFn =
  typeof fetch === 'function'
    ? fetch
    : (...args) => import('node-fetch').then(({ default: f }) => f(...args));

app.use(express.json({ limit: '1mb' }));
app.use(express.static(UI_DIR));

app.get('/health', (req, res) => {
  return res.json({ status: 'ok', port: Number(PORT) });
});

app.post('/api/chat', async (req, res) => {
  try {
    if (!API_KEY || !APP_ID) {
      return res.status(500).json({
        message: 'Server missing DashScope API key or app id env.',
      });
    }

    const messages = Array.isArray(req.body?.messages) ? req.body.messages : [];
    const userMessage = [...messages].reverse().find((m) => m.role === 'user');
    const prompt = userMessage?.content?.trim();

    if (!prompt) {
      return res.status(400).json({ message: 'No user prompt provided.' });
    }

    // ✅ 这里不再拼 SYSTEM_PROMPT，直接把用户 prompt 发给阿里云应用
    const payload = {
      input: { prompt },
      parameters: {},
    };

    const dashRes = await fetchFn(
      `https://dashscope.aliyuncs.com/api/v1/apps/${encodeURIComponent(APP_ID)}/completion`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
          Authorization: `Bearer ${API_KEY}`,
          'X-DashScope-SSE': 'disable',
        },
        body: JSON.stringify(payload),
      }
    );

    const contentType = dashRes.headers.get('content-type') || '';
    const data = contentType.includes('application/json')
      ? await dashRes.json()
      : await dashRes.text();

    if (!dashRes.ok) {
      const message =
        typeof data === 'string'
          ? data
          : data?.message || data?.error || `DashScope HTTP ${dashRes.status}`;
      return res.status(dashRes.status).json({ message });
    }

    const reply =
      (typeof data === 'string' ? data : data?.output?.text) ||
      data?.output?.choices?.[0]?.message?.content ||
      data?.choices?.[0]?.message?.content ||
      data?.message ||
      '';

    if (!reply) {
      return res.status(502).json({ message: 'No text returned from model.' });
    }

    return res.json({ reply });
  } catch (error) {
    console.error('Chat API error:', error);
    return res
      .status(500)
      .json({ message: error?.message || 'Unexpected server error.' });
  }
});

app.listen(PORT, '127.0.0.1', () => {
  console.log(`Chatbot server listening on http://127.0.0.1:${PORT}`);
});
