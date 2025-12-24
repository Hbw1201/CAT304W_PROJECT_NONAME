const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');
const express = require('express');
const admin = require('firebase-admin');
require('dotenv').config({ path: path.join(__dirname, '.env') });

const app = express();
const PORT = process.env.NODE_CHAT_PORT || 3000;
const API_KEY = process.env.DASHSCOPE_API_KEY;
const APP_ID = process.env.DASHSCOPE_APP_ID;
const UI_DIR = path.join(__dirname, 'ui');
const SERVICE_ACCOUNT_PATH =
  process.env.FIREBASE_SERVICE_ACCOUNT || path.join(__dirname, 'secrets', 'serviceAccount.json');

const fetchFn =
  typeof fetch === 'function'
    ? fetch
    : (...args) => import('node-fetch').then(({ default: f }) => f(...args));

app.use(express.json({ limit: '1mb' }));
app.use(express.static(UI_DIR));

function initFirebaseAdmin() {
  if (admin.apps.length) return admin.app();
  if (!fs.existsSync(SERVICE_ACCOUNT_PATH)) {
    throw new Error(`Firebase service account not found: ${SERVICE_ACCOUNT_PATH}`);
  }
  const serviceAccount = JSON.parse(fs.readFileSync(SERVICE_ACCOUNT_PATH, 'utf-8'));
  return admin.initializeApp({
    credential: admin.credential.cert(serviceAccount),
    projectId: serviceAccount.project_id,
  });
}

function getFirestore() {
  initFirebaseAdmin();
  return admin.firestore();
}

async function requireAuth(req, res, next) {
  try {
    const header = req.headers.authorization || '';
    const [scheme, token] = header.split(' ');
    if (scheme !== 'Bearer' || !token) {
      return res.status(401).json({ message: 'Missing Firebase ID token.' });
    }
    initFirebaseAdmin();
    const decoded = await admin.auth().verifyIdToken(token);
    req.user = decoded;
    return next();
  } catch (error) {
    console.error('Auth verify failed:', error);
    return res.status(401).json({ message: 'Invalid Firebase ID token.' });
  }
}

function runInferPython({ patchRoot, outJson, outCsv, modelPath }) {
  return new Promise((resolve, reject) => {
    const pythonBin = process.env.PYTHON_BIN || 'python';
    const inferPy = process.env.CT_INFER_PY || path.join(__dirname, 'ct', 'infer_ct.py');
    const args = [inferPy, '--patch_root', patchRoot, '--out_json', outJson];
    if (outCsv) {
      args.push('--out_csv', outCsv);
    }
    if (modelPath) {
      args.push('--model_path', modelPath);
    }
    const proc = spawn(pythonBin, args, { cwd: __dirname, env: process.env });
    let stdout = '';
    let stderr = '';
    proc.stdout.on('data', (buf) => {
      stdout += buf.toString();
    });
    proc.stderr.on('data', (buf) => {
      stderr += buf.toString();
    });
    proc.on('close', (code) => {
      if (code !== 0) {
        const err = new Error(`infer_ct.py failed with code ${code}`);
        err.stdout = stdout;
        err.stderr = stderr;
        return reject(err);
      }
      return resolve({ stdout, stderr });
    });
  });
}

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

app.post('/api/ct/analyze', requireAuth, async (req, res) => {
  const studyId = req.body?.studyId;
  if (!studyId || typeof studyId !== 'string') {
    return res.status(400).json({ message: 'studyId is required.' });
  }

  const db = getFirestore();
  const studyRef = db.collection('ctStudies').doc(studyId);
  const studySnap = await studyRef.get();
  if (!studySnap.exists) {
    return res.status(404).json({ message: 'ctStudy not found.' });
  }

  const study = studySnap.data() || {};
  const patchRoot = study.patchRoot;
  if (!patchRoot) {
    return res.status(400).json({ message: 'no patchRoot configured for this study' });
  }

  const analysisId = db.collection('ctAnalyses').doc().id;
  const tmpJson = path.join(os.tmpdir(), `ct-analysis-${analysisId}.json`);
  const tmpCsv = path.join(os.tmpdir(), `ct-analysis-${analysisId}.csv`);
  const modelPath = process.env.CT_MODEL_PATH || path.join(__dirname, 'ct', 'best_resnet_nodule.pt');
  const now = admin.firestore.FieldValue.serverTimestamp();

  try {
    await studyRef.set(
      {
        analysisStatus: 'running',
        analysisError: '',
        analysisUpdatedAt: now,
      },
      { merge: true }
    );

    await runInferPython({
      patchRoot,
      outJson: tmpJson,
      outCsv: tmpCsv,
      modelPath,
    });

    const result = JSON.parse(fs.readFileSync(tmpJson, 'utf-8'));
    const payload = {
      studyId,
      doctorId: study.doctorId || '',
      patientId: study.patientId || '',
      createdAt: now,
      model: result.model,
      result,
    };
    await db.collection('ctAnalyses').doc(analysisId).set(payload);
    await studyRef.set(
      {
        lastAnalysisAt: now,
        lastAnalysisId: analysisId,
        analysisStatus: 'done',
        analysisUpdatedAt: now,
        analysisResult: result,
        analysisError: '',
      },
      { merge: true }
    );

    return res.json({ ok: true, analysisId, result });
  } catch (error) {
    console.error('CT analyze failed:', error);
    await studyRef.set(
      {
        analysisStatus: 'failed',
        analysisError: error?.message || 'CT analysis failed.',
        analysisUpdatedAt: now,
      },
      { merge: true }
    );
    return res.status(500).json({
      message: error?.message || 'CT analysis failed.',
      ok: false,
    });
  } finally {
    try {
      if (fs.existsSync(tmpJson)) fs.unlinkSync(tmpJson);
      if (fs.existsSync(tmpCsv)) fs.unlinkSync(tmpCsv);
    } catch (cleanupError) {
      console.warn('Failed to cleanup temp files:', cleanupError);
    }
  }
});

app.listen(PORT, '127.0.0.1', () => {
  console.log(`Chatbot server listening on http://127.0.0.1:${PORT}`);
});
