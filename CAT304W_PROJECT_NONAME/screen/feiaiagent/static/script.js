let sessionId = null;
let mediaRecorder = null;
let chunks = [];
let audioContext = null;
let analyser = null;
let microphone = null;
let dataArray = null;
let animationId = null;
let isLocalQuestionnaire = false;
let isAgentMode = false;
let isMetaGPTMode = false;
let currentQuestionInfo = null;

const statusEl = document.getElementById("status");
const qEl = document.getElementById("questionText");
const aEl = document.getElementById("answerText");
const debugEl = document.getElementById("debugText");

// [ADD] 尝试获取或动态创建数字人视频元素

const assessmentReportEl = document.getElementById("assessmentReport");
const reportContentEl = document.getElementById("reportContent");
const reportAudioEl = document.getElementById("reportAudio");

const recordingIndicator = document.getElementById("recordingIndicator");
const volumeVisualizer = document.getElementById("volumeVisualizer");
const historyList = document.getElementById("historyList");
const historyContainer = document.getElementById("historyContainer");
const btnExpandHistory = document.getElementById("btnExpandHistory");
const btnCollapseHistory = document.getElementById("btnCollapseHistory");
const btnRestart = document.getElementById("btnRestart");
const textAnswerInput = document.getElementById("textAnswer");
const btnSendText = document.getElementById("btnSendText");
const btnVoice = document.getElementById("btnVoice");
const voiceFallback = document.getElementById("voiceFallback");

// 报告DOM
const btnRefreshReports = document.getElementById('btnRefreshReports');
const reportsListEl = document.getElementById('reportsList');
const reportsStatsEl = document.getElementById('reportsStats');

let conversationHistory = [];
let voiceInputEnabled = false;

function showVoiceFallback(message) {
  if (voiceFallback) {
    voiceFallback.textContent = message;
    voiceFallback.style.display = "block";
  }
}

function hideVoiceFallback() {
  if (voiceFallback) {
    voiceFallback.style.display = "none";
  }
}

function shouldAutoRecord() {
  return voiceInputEnabled;
}

function handleTextSubmit() {
  if (!textAnswerInput) return;
  const text = textAnswerInput.value.trim();
  if (!text) return;

  if (!sessionId || !isMetaGPTMode) {
    statusEl.textContent = "Status: Start MetaGPT Questionnaire first";
    return;
  }

  textAnswerInput.value = "";
  hideVoiceFallback();

  if (isMetaGPTMode) {
    submitMetaGPTAnswer(text);
  } else {
    submitAnswerText(text);
  }
}

function handleVoiceInput() {
  if (!sessionId || !isMetaGPTMode) {
    statusEl.textContent = "Status: Start MetaGPT Questionnaire first";
    return;
  }
  hideVoiceFallback();
  voiceInputEnabled = true;
  startRecording(true);
}

function log(message) {
  const timestamp = new Date().toLocaleTimeString();
  debugEl.textContent = `[${timestamp}] ${message}`;
  console.log(message);
}

function showAssessmentReport() {
  assessmentReportEl.style.display = "block";
  log("Show assessment report section");
}

function hideAssessmentReport() {
  assessmentReportEl.style.display = "none";
  log("Hide assessment report section");
}

// 显示TTS播放指示器
const SPEECH_SEGMENT_LENGTH = 220;
const SPEECH_DEBOUNCE_MS = 800;
let speechVoicesReadyPromise = null;
const speechMessageLocks = {};

function sanitizeTextForSpeech(text, removeChinese = true) {
  let normalized = (text || "").replace(/\s+/g, " ").trim();
  if (!normalized) {
    return "";
  }
  if (removeChinese && /[\u4e00-\u9fff]/.test(normalized)) {
    normalized = normalized.replace(/[\u4e00-\u9fff]/g, " ").replace(/\s+/g, " ").trim();
  }
  return normalized;
}

function splitTextIntoChunks(text, maxLen = SPEECH_SEGMENT_LENGTH) {
  const content = (text || "").trim();
  if (!content) {
    return [];
  }

  const sentences = [];
  let current = "";
  for (const char of content) {
    current += char;
    if (/[。！？!?\\.\\?\\!;,;]/.test(char)) {
      sentences.push(current.trim());
      current = "";
    }
  }
  if (current.trim()) {
    sentences.push(current.trim());
  }

  const chunks = [];
  let buffer = "";
  const pushBuffer = () => {
    if (buffer.trim()) {
      chunks.push(buffer.trim());
      buffer = "";
    }
  };

  sentences.forEach(sentence => {
    const candidate = buffer ? `${buffer} ${sentence}`.trim() : sentence;
    if (candidate.length <= maxLen) {
      buffer = candidate;
    } else {
      pushBuffer();
      if (sentence.length <= maxLen) {
        buffer = sentence;
      } else {
        let start = 0;
        while (start < sentence.length) {
          const slice = sentence.slice(start, start + maxLen).trim();
          if (slice) {
            chunks.push(slice);
          }
          start += maxLen;
        }
        buffer = "";
      }
    }
  });

  pushBuffer();
  return chunks.length ? chunks : [content.slice(0, maxLen)];
}

function waitForSpeechVoices() {
  if (!("speechSynthesis" in window)) {
    return Promise.resolve([]);
  }
  if (speechVoicesReadyPromise) {
    return speechVoicesReadyPromise;
  }
  speechVoicesReadyPromise = new Promise(resolve => {
    const available = window.speechSynthesis.getVoices();
    if (available && available.length) {
      resolve(available);
      return;
    }

    let resolved = false;
    const handle = () => {
      if (resolved) return;
      resolved = true;
      window.speechSynthesis.onvoiceschanged = null;
      resolve(window.speechSynthesis.getVoices());
    };

    window.speechSynthesis.onvoiceschanged = handle;
    setTimeout(handle, 600);
  });
  return speechVoicesReadyPromise;
}

function createSpeechMessageId(prefix, text) {
  const base = (text || "")
    .slice(0, 48)
    .replace(/\s+/g, "-")
    .toLowerCase() || "chunk";
  const sessionPart = sessionId || "session";
  return `${prefix || "speech"}-${sessionPart}-${base}`;
}

async function speakTextReliable(text, options = {}) {
  if (!("speechSynthesis" in window)) {
    log("This browser does not support SpeechSynthesis.");
    return Promise.resolve();
  }

  const payload = sanitizeTextForSpeech(text, options.removeChinese !== false);
  if (!payload) {
    log("TTS content is empty; skipping speech synthesis.");
    return Promise.resolve();
  }

  const dedupeKey = options.messageId || createSpeechMessageId("speech", payload);
  const now = Date.now();
  if (dedupeKey) {
    const last = speechMessageLocks[dedupeKey] || 0;
    if (now - last < SPEECH_DEBOUNCE_MS) {
      log(`TTS在${SPEECH_DEBOUNCE_MS}ms内重复触发，忽略 messageId=${dedupeKey}`);
      return Promise.resolve();
    }
    speechMessageLocks[dedupeKey] = now;
  }

  await waitForSpeechVoices();
  window.speechSynthesis.cancel();

  const segments = splitTextIntoChunks(payload, options.segmentLength || SPEECH_SEGMENT_LENGTH);
  if (!segments.length) {
    return Promise.resolve();
  }

  return new Promise(resolve => {
    const keepAliveTimer = setInterval(() => {
      try {
        if (!window.speechSynthesis || !window.speechSynthesis.speaking) {
          clearInterval(keepAliveTimer);
        } else {
          window.speechSynthesis.resume();
        }
      } catch (_) {
        clearInterval(keepAliveTimer);
      }
    }, 1400);

    let index = 0;
    const playNext = () => {
      if (index >= segments.length) {
        clearInterval(keepAliveTimer);
        resolve();
        return;
      }

      const utterance = new SpeechSynthesisUtterance(segments[index]);
      utterance.lang = options.lang || "en-US";
      utterance.pitch = typeof options.pitch === "number" ? options.pitch : 1;
      utterance.rate = typeof options.rate === "number" ? options.rate : 1;

      utterance.onerror = (event) => {
        log(`TTS段落播放错误: ${event?.error || "unknown"}, 跳过 index=${index}`);
        index += 1;
        playNext();
      };

      utterance.onend = () => {
        index += 1;
        playNext();
      };

      try {
        window.speechSynthesis.speak(utterance);
      } catch (err) {
        log(`SpeechSynthesis调用failed: ${err.message}`);
        index += 1;
        playNext();
      }
    };

    playNext();
  });
}

function speakTextWithFallback(text, options = {}) {
  if (!text) {
    return Promise.resolve();
  }

  if (options.autoRecord && shouldAutoRecord() && (isAgentMode || isLocalQuestionnaire || isMetaGPTMode)) {
    setTimeout(() => {
      startRecording();
    }, 200);
  }

  return Promise.resolve();
}

function addToHistory(type, content) {
  const timestamp = new Date().toLocaleTimeString();
  const historyItem = {
    type: type,
    content: content,
    timestamp: timestamp
  };

  conversationHistory.push(historyItem);
  updateHistoryDisplay();
}

function updateHistoryDisplay() {
  historyList.innerHTML = '';

  conversationHistory.forEach(item => {
    const historyItem = document.createElement('div');
    historyItem.className = `history-item ${item.type}`;

    if (item.type === 'summary') {
      historyItem.style.backgroundColor = '#d4edda';
      historyItem.style.borderColor = '#c3e6cb';
      historyItem.style.color = '#155724';
    } else if (item.type === 'warning') {
      historyItem.style.backgroundColor = '#fff3cd';
      historyItem.style.borderColor = '#ffeaa7';
      historyItem.style.color = '#856404';
    }

    historyItem.innerHTML = `
      <div class="timestamp">${item.timestamp}</div>
      <div class="content">${item.content}</div>
    `;

    historyList.appendChild(historyItem);
  });

  if (historyList.scrollHeight > historyList.clientHeight) {
    historyList.scrollTop = historyList.scrollHeight;
  }
}



// [ADD] 统一设置媒体资源（视频和TTS音频同时播放），并控制显示/隐藏
function restartConversation() {
  log("🔄 Restarting conversation");

  // 重置状态
  sessionId = null;
  isLocalQuestionnaire = false;
  isAgentMode = false;
  isMetaGPTMode = false;
  currentQuestionInfo = null;
  voiceInputEnabled = false;
  hideVoiceFallback();
  statusEl.textContent = "Status: Ready";
  statusEl.style.color = "#1976d2";
  statusEl.style.backgroundColor = "#e3f2fd";

  // 重置问题显示
  qEl.textContent = "(waiting to start)";
  qEl.style.color = "#333";
  qEl.style.fontWeight = "normal";

  // 重置回答显示
  aEl.textContent = "(waiting for input)";

  // 隐藏问题信息和进度
  document.getElementById("questionInfo").style.display = "none";
  document.getElementById("progressInfo").style.display = "none";

      // 重置按钮状态
    document.getElementById("btnStart").disabled = false;
    // 录音按钮现在是自动的，不需要手动设置

  // 隐藏重新开始按钮
  btnRestart.style.display = "none";

  // 隐藏评估报告
  hideAssessmentReport();

  // 清空音频
  conversationHistory = [];
  updateHistoryDisplay();
  hideAssessmentReport();

  // 重置状态
  isLocalQuestionnaire = false;
  isAgentMode = false;
  isMetaGPTMode = false;
  currentQuestionInfo = null;

  // 隐藏问题信息和进度
  document.getElementById("questionInfo").style.display = "none";
  document.getElementById("progressInfo").style.display = "none";

  log("Conversation reset; you can start again");
}



function toggleHistory() {
  const isCollapsed = historyContainer.classList.contains('collapsed');

  if (isCollapsed) {
    historyContainer.classList.remove('collapsed');
    btnExpandHistory.style.display = 'none';
    btnCollapseHistory.style.display = 'inline-block';
  } else {
    historyContainer.classList.add('collapsed');
    btnExpandHistory.style.display = 'inline-block';
    btnCollapseHistory.style.display = 'none';
  }
}

btnExpandHistory.addEventListener("click", toggleHistory);
btnCollapseHistory.addEventListener("click", toggleHistory);
  btnRestart.addEventListener("click", restartConversation);

function updateVolumeVisualizer(volume) {
  const bars = volumeVisualizer.querySelectorAll('.volume-bar');
  const normalizedVolume = Math.min(volume / 100, 1);

  bars.forEach((bar, index) => {
    const maxHeight = 30;
    const minHeight = 4;
    const height = minHeight + (maxHeight - minHeight) * normalizedVolume * (index + 1) / bars.length;
    bar.style.height = `${height}px`;
  });
}

function startVolumeVisualization(stream) {
  try {
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
    analyser = audioContext.createAnalyser();
    microphone = audioContext.createMediaStreamSource(stream);

    analyser.fftSize = 256;
    const bufferLength = analyser.frequencyBinCount;
    dataArray = new Uint8Array(bufferLength);

    microphone.connect(analyser);

    function animate() {
      animationId = requestAnimationFrame(animate);
      analyser.getByteFrequencyData(dataArray);

      let sum = 0;
      for (let i = 0; i < bufferLength; i++) {
        sum += dataArray[i];
      }
      const average = sum / bufferLength;

      updateVolumeVisualizer(average);
    }

    animate();
  } catch (error) {
    log(`音量可视化启动failed: ${error.message}`);
  }
}

function stopVolumeVisualization() {
  if (animationId) {
    cancelAnimationFrame(animationId);
    animationId = null;
  }

  // 停止静音检测
  if (window.silenceTimer) {
    cancelAnimationFrame(window.silenceTimer);
    window.silenceTimer = null;
    log("Silence detection stopped");
  }

  if (audioContext) {
    audioContext.close();
    audioContext = null;
  }

  const bars = volumeVisualizer.querySelectorAll('.volume-bar');
  bars.forEach(bar => {
    bar.style.height = '4px';
  });
}

async function fetchSystemStatus() {
    try {
        const response = await fetch('/api/questionnaire_status');
        const data = await response.json();
        log(`当前使用智谱AI系统`);
    } catch (error) {
        log(`获取系统状态failed: ${error.message}`);
        log("Defaulting to Zhipu Agent system");
    }
}

document.addEventListener("DOMContentLoaded", function() {
  fetchSystemStatus();

  const btnStartMeta = document.getElementById("btnStartMeta");
  if (btnStartMeta) btnStartMeta.addEventListener("click", startMetaGPTQuestionnaire);

  if (btnSendText) btnSendText.addEventListener("click", handleTextSubmit);
  if (textAnswerInput) {
    textAnswerInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        handleTextSubmit();
      }
    });
  }
  if (btnVoice) btnVoice.addEventListener("click", handleVoiceInput);
  if (btnRestart) btnRestart.addEventListener("click", restartConversation);

  updateButtonStates();

  if (btnRefreshReports) {
    btnRefreshReports.addEventListener("click", loadReportsList);
    loadReportsList();
  }

  log("System initialization complete");
});
async function startConversation() {
  try {
    log("Starting Zhipu Agent...");
    statusEl.textContent = "Status: Starting Zhipu Agent...";

    hideAssessmentReport();
    isLocalQuestionnaire = false;
    isAgentMode = true;

    // 更新按钮状态
    updateButtonStates();

    sessionId = Date.now().toString();

    const res = await fetch("/api/agent/start", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({session_id: sessionId})});

    if (!res.ok) {
      const errorData = await res.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP ${res.status}: ${res.statusText}`);
    }

    const data = await res.json();

    if (data.error) {
      throw new Error(data.error);
    }

    sessionId = data.session_id;
    const question = data.question || "(none)";
    qEl.textContent = question;

    // [MOD] 优先使用视频，其次音频

    addToHistory('question', question);

    log(`智谱AI对话启动succeeded，会话ID: ${sessionId}`);
    log(`获取到问题: ${question}`);

    // [MOD] 统一播放并在结束后自动录音
      statusEl.textContent = "Status: Started, waiting for your answer";
      speakTextWithFallback(question, {
        prefix: "agent-question",
        label: "Playing audio...",
        autoRecord: true
      });
  } catch (error) {
    log(`启动智谱AI对话failed: ${error.message}`);
    statusEl.textContent = "Status: Start failed, please retry";
  }
}

// startLocalQuestionnaire 函数已删除

// switchToAgent 函数已删除

function updateButtonStates() {
  const btnStart = document.getElementById("btnStart");
  if (!btnStart) {
    return;
  }

  if (isMetaGPTMode) {
    // MetaGPT模式
    btnStart.disabled = true;
    log("🧠 Current mode: MetaGPT Questionnaire");
  } else {
    // 初始状态或Agent模式
    btnStart.disabled = false;
    log("🤖 Current mode: Zhipu Agent");
  }
}

// ===== MetaGPT 前端绑定 =====
async function startMetaGPTQuestionnaire() {
  try {
    log("Starting MetaGPT questionnaire...");
    statusEl.textContent = "Status: Starting MetaGPT questionnaire...";

    hideAssessmentReport();
    isLocalQuestionnaire = false;
    isAgentMode = false;
    isMetaGPTMode = true;

    updateButtonStates();

    sessionId = Date.now().toString();

    const res = await fetch("/api/metagpt_agent/start_conversational", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({session_id: sessionId})});
    if (!res.ok) {
      const errorData = await res.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP ${res.status}: ${res.statusText}`);
    }
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    sessionId = data.session_id;
    const question = data.question || "(none)";
    qEl.textContent = question;

    // 统一音视频播放
    addToHistory('question', `[MetaGPT] ${question}`);

      statusEl.textContent = "Status: MetaGPT questionnaire started, waiting for your answer";
      speakTextWithFallback(question, {
        prefix: "metagpt-question",
        label: "Playing audio...",
        autoRecord: true
      });
  } catch (error) {
    log(`启动 MetaGPT 问卷failed: ${error.message}`);
    statusEl.textContent = "Status: Start failed, please retry";
  }
}

async function submitMetaGPTAnswer(text) {
  try {
    aEl.textContent = text;
    log(`提交回答(MetaGPT): "${text}"`);
    addToHistory('answer', text);

    const res = await fetch("/api/metagpt_agent/reply_conversational", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ session_id: sessionId, answer: text })
    });
    if (!res.ok) {
      const errorData = await res.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP ${res.status}: ${res.statusText}`);
    }
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    sessionId = data.session_id;
    const question = data.question || "(none)";
    qEl.textContent = question;

    const playLabel = data.invalid_answer ? "Answer unclear; replaying question..." : (data.is_complete ? "Playing assessment result..." : "Playing new question...");

    if (data.is_complete) {
      addToHistory('summary', question);
      qEl.style.color = "#28a745";
      qEl.style.fontWeight = "bold";
      showAssessmentReport();

      // 渲染报告
      let reportHtml;
      try { reportHtml = marked.parse(question); } catch (_) { reportHtml = question.replace(/\n/g, '<br>'); }
      reportContentEl.innerHTML = `<div class="report-text markdown-content">${reportHtml}</div>`;

        speakTextWithFallback(question, {
          prefix: "metagpt-summary",
          label: "Playing assessment report...",
          autoRecord: false
        });
    } else {
      if (data.invalid_answer) {
        addToHistory('warning', `回答None效：${data.invalid_reason || 'Not specific / unrecognized'}`);
      }
      addToHistory('question', `[MetaGPT] ${question}`);
        statusEl.textContent = "Status: Next question loaded";
        speakTextWithFallback(question, {
          prefix: "metagpt-question",
          label: playLabel || "Playing audio...",
          autoRecord: true
        });
    }
  } catch (error) {
    log(`提交回答(MetaGPT)failed: ${error.message}`);
    statusEl.textContent = "Status: Submit failed, please retry";
  }
}

// ===== MetaGPT 集成 =====
async function initMetaGPT() {
  try {
    log("🛠️ Initializing MetaGPT...");
    const res = await fetch('/api/metagpt/init', { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Initialization failed');
    log(`✅ MetaGPT 初始化: ${data.initialized ? 'succeeded' : 'failed'}`);
    statusEl.textContent = `Status: MetaGPT initialization ${data.initialized ? 'succeeded' : 'failed'}`;
  } catch (e) {
    log(`❌ MetaGPT Initialization failed: ${e.message}`);
    statusEl.textContent = 'Status: MetaGPT initialization failed';
  }
}

async function runMetaGPTDemo() {
  try {
    log("🎯 Running MetaGPT demo workflow...");
    statusEl.textContent = 'Status: Running MetaGPT demo...';
    const res = await fetch('/api/metagpt/demo', { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Demo execution failed');

    // 将结果显示到报告区域
    showAssessmentReport();
    const fr = data.final_results || {};
    const report = (fr.report && fr.report.content) || 'Demo complete, but no report content returned';
    const reportHtml = marked.parse(report);
    reportContentEl.innerHTML = `<div class="report-text markdown-content">${reportHtml}</div>`;
    addToHistory('summary', `MetaGPT demo complete: ${data.workflow_id || ''}`);
    statusEl.textContent = 'Status: MetaGPT demo complete';
  } catch (e) {
    log(`❌ MetaGPT 演示failed: ${e.message}`);
    statusEl.textContent = 'Status: MetaGPT demo failed';
  }
}

async function submitAnswerText(text) {
  try {
    aEl.textContent = text;
    log(`提交回答: "${text}"`);

    addToHistory('answer', text);

    let res, data;

    if (isLocalQuestionnaire) {
      // 本地问卷
      res = await fetch("/api/local_questionnaire/reply", {
        method: "POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ session_id: sessionId, answer: text })
      });
    } else {
      // 智谱AI对话
      res = await fetch("/api/agent/reply", {
        method: "POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ session_id: sessionId, answer: text })
      });
    }

    if (!res.ok) {
      const errorData = await res.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP ${res.status}: ${res.statusText}`);
    }

    data = await res.json();

    if (data.error) {
      throw new Error(data.error);
    }

    // 添加调试日志
    log(`🔍 调试信息：`);
    log(`  - is_complete: ${data.is_complete}`);
    log(`  - question长度: ${data.question ? data.question.length : 0}`);
    log(`  - question内容预览: ${data.question ? data.question.substring(0, 100) + '...' : 'None'}`);

    sessionId = data.session_id;
    const question = data.question || "(none)";
    qEl.textContent = question;

    // [MOD] 优先使用视频，其次音频

    if (data.is_complete) {
      log("🎉 Questionnaire completed!");
      log(`✅ 后端返回is_complete=True，开始显示评估报告`);
      statusEl.textContent = "Status: Questionnaire complete, showing summary report";

      addToHistory('summary', question);

      qEl.style.color = "#28a745";
      qEl.style.fontWeight = "bold";

      document.getElementById("btnRec").disabled = true;
      document.getElementById("btnStop").disabled = true;

      log(`📊 调用showAssessmentReport()Show assessment report section`);
      showAssessmentReport();

      // 检查是否Assessment report（多种关键词匹配）
      const isReport = question.includes("肺癌早筛风险评估报告") ||
                      question.includes("评估报告") ||
                      question.includes("风险评估") ||
                      question.includes("报告") ||
                      question.length > 500;  // 长文本可能是报告

      log(`🔍 报告检测结果：`);
      log(`  - 包含"肺癌早筛风险评估报告": ${question.includes("肺癌早筛风险评估报告")}`);
      log(`  - 包含"评估报告": ${question.includes("评估报告")}`);
      log(`  - 包含"风险评估": ${question.includes("风险评估")}`);
      log(`  - 包含"报告": ${question.includes("报告")}`);
      log(`  - 文本长度>500: ${question.length > 500}`);
      log(`  - 最终判断: ${isReport ? 'Assessment report' : 'Not an assessment report'}`);

      if (isReport) {
        // 尝试解析为Markdown格式
        let reportHtml;
        try {
          reportHtml = marked.parse(question);
          log(`✅ Markdown解析succeeded`);
        } catch (e) {
          // 如果Markdown解析failed，直接显示文本
          reportHtml = question.replace(/\n/g, '<br>');
          log(`⚠️ Markdown解析failed，使用HTML换行: ${e.message}`);
        }

        log(`📝 设置报告内容到reportContentEl`);
        reportContentEl.innerHTML = `<div class="report-text markdown-content">${reportHtml}</div>`;
        log("Assessment report detected; showing content directly");
        log(`报告内容长度: ${question.length}`);
        log(`报告类型: ${isReport ? '评估报告' : 'General reply'}`);

        // [MOD] 统一播放（视频优先），结束后不再自动录音（完成态一般不再录）
      } else {
        // 虽然不是明确的评估报告，但可能是其他形式的完成结果
        log(`📝 设置完成结果内容到reportContentEl（非标准报告格式）`);
        reportContentEl.innerHTML = `
          <div class="info-message">
            <h4>问卷已完成</h4>
            <p>以下是智谱AI的回复：</p>
            <div class="completion-text">${question.replace(/\n/g, '<br>')}</div>
          </div>
        `;
        log("Questionnaire complete; showing completion result");
        log(`完成结果长度: ${question.length}`);

        // [MOD] 同样仅播放一次（不再触发自动录音）
      }
    } else {
      log(`⏳ 问卷未完成，继续下一题`);
      // 检查是否是API调用failed
      if (question.includes("Zhipu Agent temporarily unavailable") || question.includes("System temporarily unavailable")) {
        log("?? Zhipu Agent call failed, please try again later");
        statusEl.textContent = "Status: Zhipu Agent temporarily unavailable, please try again later";
        statusEl.style.color = "#dc3545";
        statusEl.style.backgroundColor = "#f8d7da";

        addToHistory('error', question);
        qEl.style.color = "#dc3545";

        // 显示重新开始按钮
        btnRestart.style.display = "inline-block";

        return; // 不继续处理
      }

      // 检查是否是Agent workflow error（需要重新询问）
      if (question.includes("Agent workflow error")) {
        log("?? Agent workflow error detected; re-asking the question...");
        statusEl.textContent = "Status: Re-asking the question...";
        statusEl.style.color = "#ffc107";
        statusEl.style.backgroundColor = "#fff3cd";

        addToHistory('warning', "The previous question had an error; re-asking...");
        qEl.style.color = "#ffc107";

        // [MOD] 统一播放（视频/音频），本分支不自动录音
        return;
      }

      if (isLocalQuestionnaire) {
        if (data.question_info) {
          currentQuestionInfo = data.question_info;
          document.getElementById("questionInfo").style.display = "block";
          document.getElementById("questionInfoText").textContent = `${currentQuestionInfo.category} - ${currentQuestionInfo.format}`;
        }

        if (data.progress) {
          document.getElementById("progressInfo").style.display = "block";
          document.getElementById("progressInfo").textContent = data.progress;
        }

        addToHistory('question', `[Local Questionnaire] ${question}`);
        log(`Local questionnaire next question: "${question}"`);
        log(`问题分类: ${currentQuestionInfo?.category}, 格式要求: ${currentQuestionInfo?.format}`);
      } else {
        // 智谱AI对话处理
        if (question.includes("Zhipu Agent temporarily unavailable") || question.includes("System temporarily unavailable")) {
          log("?? Zhipu Agent call failed, please try again later");
          statusEl.textContent = "Status: Zhipu Agent temporarily unavailable, please try again later";
          statusEl.style.color = "#dc3545";
          statusEl.style.backgroundColor = "#f8d7da";

          addToHistory('error', question);
          qEl.style.color = "#dc3545";

          // 禁用录音按钮
          document.getElementById("btnRec").disabled = true;
          document.getElementById("btnStop").disabled = true;

          // 显示重新开始按钮
          btnRestart.style.display = "inline-block";

          return;
        }

        if (question.includes("Agent workflow error")) {
          log("?? Agent workflow error detected; re-asking the question...");
          statusEl.textContent = "Status: Re-asking the question...";
          statusEl.style.color = "#ffc107";
          statusEl.style.backgroundColor = "#fff3cd";

          addToHistory('warning', "The previous question had an error; re-asking...");
          qEl.style.color = "#ffc107";

          // [MOD] 统一播放（视频/音频），结束后自动录音
          return;
        }

        addToHistory('question', question);
        log(`获取到下一题: "${question}"`);
      }

      // [MOD] 统一播放新问题（视频优先），播放后自动录音
      statusEl.textContent = "Status: Next question loaded";
      speakTextWithFallback(question, {
        prefix: isMetaGPTMode ? "metagpt-question" : "agent-question",
        label: "Playing audio...",
        autoRecord: true
      });
    }
  } catch (error) {
    log(`提交回答failed: ${error.message}`);
    statusEl.textContent = "Status: Submit failed, please retry";
  }
}

async function startRecording(isManual = false) {
  if (!voiceInputEnabled && !isManual) {
    return;
  }
  try {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      voiceInputEnabled = false;
      statusEl.textContent = "Status: Microphone unavailable. Please use text input.";
      showVoiceFallback("Microphone unavailable. Please use text input.");
      return;
    }

    log("Requesting microphone permission...");
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    log("Microphone permission granted");
    hideVoiceFallback();

    recordingIndicator.style.display = 'flex';

    startVolumeVisualization(stream);

    const options = {
      mimeType: 'audio/speex;rate=16000',
      audioBitsPerSecond: 16000
    };

    try {
      mediaRecorder = new MediaRecorder(stream, options);
      log("Recording in Speex-WB format (16kHz)");
    } catch (e) {
      log(`Speex格式不支持: ${e.message}`);

      const fallbackOptions = [
        'audio/webm;codecs=opus',
        'audio/webm',
        'audio/ogg;codecs=opus',
        'audio/mp4'
      ];

      let recorder = null;
      for (const mimeType of fallbackOptions) {
        try {
          if (MediaRecorder.isTypeSupported(mimeType)) {
            recorder = new MediaRecorder(stream, { mimeType });
            log(`回退到格式: ${mimeType}`);
            break;
          }
        } catch (e2) {
          continue;
        }
      }

      if (!recorder) {
        recorder = new MediaRecorder(stream);
        log("Using default format");
      }

      mediaRecorder = recorder;
    }

    chunks = [];
    mediaRecorder.ondataavailable = e => {
      if (e.data.size > 0) {
        chunks.push(e.data);
        log(`录音数据块: ${e.data.size} bytes`);
      }
    };

    mediaRecorder.onstop = async () => {
      recordingIndicator.style.display = 'none';

      // 停止静音检测
      if (window.silenceTimer) {
        cancelAnimationFrame(window.silenceTimer);
        window.silenceTimer = null;
        log("Silence detection stopped");
      }

      stopVolumeVisualization();

      const mimeType = mediaRecorder.mimeType || 'audio/speex';
      let fileExtension = 'webm';

      if (mimeType.includes('speex')) {
        fileExtension = 'spx';
      } else if (mimeType.includes('opus')) {
        fileExtension = 'opus';
      } else if (mimeType.includes('mp4')) {
        fileExtension = 'm4a';
      } else if (mimeType.includes('ogg')) {
        fileExtension = 'ogg';
      }

      log(`录音完成，格式: ${mimeType}, 扩展名: ${fileExtension}`);
      log(`录音数据大小: ${chunks.reduce((sum, chunk) => sum + chunk.size, 0)} bytes`);

      const blob = new Blob(chunks, { type: mimeType });
      const fd = new FormData();
      fd.append("audio", blob, `record.${fileExtension}`);

      statusEl.textContent = "Status: Transcribing...";
      log("Starting speech recognition...");

      const res = await fetch("/api/asr", { method: "POST", body: fd });
      const data = await res.json();
      const text = data.text || "";

      log(`语音识别结果: "${text}"`);
      statusEl.textContent = "Status: Transcription complete, submitting to agent...";

      if (textAnswerInput) {
        textAnswerInput.value = text;
      }
      if (text) {
        handleTextSubmit();
      } else {
        statusEl.textContent = "Status: No speech detected. Please use text input.";
        showVoiceFallback("Microphone unavailable. Please use text input.");
      }
    };

    mediaRecorder.start();
    statusEl.textContent = "Status: Recording...";
    statusEl.classList.add("recording");

    // 隐藏录音按钮（现在是自动录音）
    document.getElementById("btnRec").style.display = 'none';
    document.getElementById("btnStop").style.display = 'none';

    // 启动6秒None声音自动停止录音的定时器
    let lastVolume = 0;
    let silenceStartTime = null;

    // 音量检测函数
    const checkSilence = () => {
      if (analyser && dataArray) {
        analyser.getByteFrequencyData(dataArray);
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) {
          sum += dataArray[i];
        }
        const currentVolume = sum / dataArray.length;

        // 每100次检测输出一次音量信息（避免日志过多）
        if (!window.volumeLogCounter) window.volumeLogCounter = 0;
        window.volumeLogCounter++;
        if (window.volumeLogCounter % 100 === 0) {
          log(`🔊 当前音量: ${currentVolume.toFixed(2)}, 静音阈值: 100, 静音计时: ${silenceStartTime ? ((Date.now() - silenceStartTime) / 1000).toFixed(1) + 's' : 'not started'}`);
        }

        // 如果音量很低（静音）
        if (currentVolume < 100) {
          if (silenceStartTime === null) {
            silenceStartTime = Date.now();
            log("🔇 Silence detected; starting timer...");
          } else {
            const silenceDuration = Date.now() - silenceStartTime;
            if (silenceDuration > 2500) { // 1坤秒静音
              log(`⏰ 检测到${(silenceDuration / 1000).toFixed(1)}秒静音，自动停止录音`);
              stopRecording();
              return;
            }
          }
        } else {
          // 有声音，重置静音计时
          if (silenceStartTime !== null) {
            log(`🔊 检测到声音(${currentVolume.toFixed(2)})，重置静音计时`);
            silenceStartTime = null;
          }
        }

        lastVolume = currentVolume;
      } else {
        log("?? Audio analyzer not ready; cannot detect volume");
      }

      // 继续检测
      window.silenceTimer = requestAnimationFrame(checkSilence);
    };

    // 开始音量检测
    window.silenceTimer = requestAnimationFrame(checkSilence);

    log("Recording started; 2.5s auto-stop on silence enabled");
  } catch (error) {
    log(`录音启动failed: ${error.message}`);
    statusEl.textContent = "Status: Microphone unavailable. Please use text input.";
    showVoiceFallback("Microphone unavailable. Please use text input.");
    voiceInputEnabled = false;

    recordingIndicator.style.display = 'none';
  }
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    // 停止静音检测
    if (window.silenceTimer) {
      cancelAnimationFrame(window.silenceTimer);
      window.silenceTimer = null;
      log("Silence detection stopped");
    }

    mediaRecorder.stop();
    statusEl.classList.remove("recording");

    // 隐藏录音按钮（现在是自动录音）
    document.getElementById("btnRec").style.display = 'none';
    document.getElementById("btnStop").style.display = 'none';

    log("Recording stopped");
  }
}


async function loadReportsList() {
  try {
    reportsListEl.innerHTML = '<div class="loading">Loading report list...</div>';
    reportsStatsEl.textContent = 'Loading stats...';

    const res = await fetch('/api/reports');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    const reports = data.reports || [];
    const stats = data.stats || {};

    reportsStatsEl.textContent = `数量: ${stats.total_reports || 0}，总大小: ${stats.total_size_mb || 0} MB，目录: ${stats.reports_dir || ''}`;

    if (reports.length === 0) {
      reportsListEl.innerHTML = '<div class="info-message">No reports available</div>';
      return;
    }

    const frag = document.createDocumentFragment();
    reports.forEach(r => {
      const item = document.createElement('div');
      item.className = 'report-item';
      const name = document.createElement('div');
      name.className = 'report-name';
      name.textContent = `${r.filename} （${r.created}）`;

      const actions = document.createElement('div');
      actions.className = 'report-actions';

      const viewBtn = document.createElement('button');
      viewBtn.className = 'secondary-btn';
      viewBtn.textContent = 'View';
      viewBtn.onclick = () => viewReportContent(r.filename);

      const dlBtn = document.createElement('button');
      dlBtn.textContent = 'Download';
      dlBtn.onclick = () => {
        window.open(`/api/reports/download/${encodeURIComponent(r.filename)}`, '_blank');
      };

      actions.appendChild(viewBtn);
      actions.appendChild(dlBtn);
      item.appendChild(name);
      item.appendChild(actions);
      frag.appendChild(item);
    });

    reportsListEl.innerHTML = '';
    reportsListEl.appendChild(frag);
  } catch (e) {
    reportsListEl.innerHTML = `<div class="error-message">加载failed: ${e.message}</div>`;
  }
}

async function viewReportContent(filename) {
  try {
    const res = await fetch(`/api/reports/content/${encodeURIComponent(filename)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const content = data.content || '';

    // 弹窗显示（简单实现）
    const preview = document.createElement('div');
    preview.style.position = 'fixed';
    preview.style.left = '0';
    preview.style.top = '0';
    preview.style.right = '0';
    preview.style.bottom = '0';
    preview.style.background = 'rgba(0,0,0,0.5)';
    preview.style.display = 'flex';
    preview.style.alignItems = 'center';
    preview.style.justifyContent = 'center';
    preview.style.zIndex = '9999';

    const box = document.createElement('div');
    box.style.width = '90%';
    box.style.maxWidth = '800px';
    box.style.maxHeight = '80%';
    box.style.overflow = 'auto';
    box.style.background = '#fff';
    box.style.borderRadius = '8px';
    box.style.padding = '16px';
    box.innerHTML = `<h3 style="margin-top:0;">${filename}</h3><pre style="white-space:pre-wrap;">${content.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}</pre>`;

    const closeBtn = document.createElement('button');
    closeBtn.className = 'secondary-btn';
    closeBtn.textContent = 'Close';
    closeBtn.style.marginTop = '10px';
    closeBtn.onclick = () => document.body.removeChild(preview);
    box.appendChild(closeBtn);

    preview.appendChild(box);
    document.body.appendChild(preview);
  } catch (e) {
    alert(`读取failed: ${e.message}`);
  }
}

// 测试函数：测试评估报告显示
function testAssessmentReport() {
  log("🧪 Starting assessment report display test");

  // 测试1：检查DOM元素是否present
  log(`📋 DOM元素检查:`);
  log(`  - assessmentReportEl: ${assessmentReportEl ? 'present' : 'absent'}`);
  log(`  - reportContentEl: ${reportContentEl ? 'present' : 'absent'}`);
  log(`  - reportAudioEl: ${reportAudioEl ? 'present' : 'absent'}`);

  // 测试2：检查当前显示状态
  if (assessmentReportEl) {
    const currentDisplay = assessmentReportEl.style.display;
    log(`  - 当前评估报告显示状态: ${currentDisplay}`);
    log(`  - 当前评估报告visible性: ${assessmentReportEl.offsetParent !== null ? 'visible' : 'hidden'}`);
  }

  // 测试3：测试显示/隐藏功能
  log(`🔄 测试显示/隐藏功能`);
  showAssessmentReport();
  log(`✅ 调用showAssessmentReport()完成`);

  // 测试4：设置测试内容
  if (reportContentEl) {
    const testContent = `
      <div class="report-text markdown-content">
        <h1>🧪 测试评估报告</h1>
        <p>这是一个测试报告，用于验证评估报告显示功能是否正常工作。</p>
        <h2>测试内容</h2>
        <ul>
          <li>✅ 报告区域显示</li>
          <li>✅ 内容渲染</li>
          <li>✅ 样式应用</li>
        </ul>
        <p><strong>如果能看到这个测试报告，说明显示功能正常！</strong></p>
      </div>
    `;
    reportContentEl.innerHTML = testContent;
    log(`📝 设置测试内容完成`);
  }

  // 测试5：检查最终状态
  setTimeout(() => {
    if (assessmentReportEl) {
      const finalDisplay = assessmentReportEl.style.display;
      log(`📊 最终状态检查:`);
      log(`  - 显示状态: ${finalDisplay}`);
      log(`  - visible性: ${assessmentReportEl.offsetParent !== null ? 'visible' : 'hidden'}`);
      log(`  - 内容长度: ${reportContentEl ? reportContentEl.innerHTML.length : 0}`);
    }
    log(`🧪 测试完成`);
  }, 100);
}

// 测试函数：测试完成状态
function testCompleteStatus() {
  log("Starting completion state test");

  const mockCompleteData = {
    session_id: "test_session_" + Date.now(),
    question: "Lung cancer screening assessment report.\n\nThis is a test payload.",
    is_complete: true
  };

  qEl.textContent = mockCompleteData.question;
  qEl.style.color = "#28a745";
  qEl.style.fontWeight = "bold";

  showAssessmentReport();

  if (reportContentEl) {
    const reportHtml = marked.parse(mockCompleteData.question);
    reportContentEl.innerHTML = `<div class="report-text markdown-content">${reportHtml}</div>`;
  }

  statusEl.textContent = "Status: Test completion state - questionnaire complete, showing summary report";
  statusEl.style.color = "#28a745";
  statusEl.style.backgroundColor = "#d4edda";
  log("Completion state test done");
}

// 在页面加载完成后添加测试按钮
