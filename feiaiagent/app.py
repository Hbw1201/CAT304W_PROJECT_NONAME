# app.py
import os, pathlib, shutil, subprocess, tempfile, logging, time, sys, asyncio, threading
import concurrent.futures
from functools import lru_cache
from pathlib import Path
from typing import Dict

# Early logger for path debugging (main logger configured later)
logger = logging.getLogger(__name__)

# Configure logging encoding to handle Windows GBK console properly
import codecs
if sys.platform == "win32":
    try:
        import locale
        if locale.getpreferredencoding() == 'cp936':  # GBK
            class UTF8StreamHandler(logging.StreamHandler):
                def __init__(self, stream=None):
                    super().__init__(stream)
                    if stream is None:
                        stream = sys.stdout
                    # Wrap the stream with UTF-8 encoder
                    self.stream = codecs.getwriter('utf-8')(stream.buffer, 'strict')
            
            root_logger = logging.getLogger()
            for handler in root_logger.handlers[:]:
                if isinstance(handler, logging.StreamHandler):
                    root_logger.removeHandler(handler)
            root_logger.addHandler(UTF8StreamHandler())
    except Exception:
        # If reconfiguration fails, continue with default logging behavior
        pass

# Path configuration management (force English questionnaire)
import pathlib
BASE_DIR = pathlib.Path(__file__).parent
LOCAL_QUESTIONNAIRE_PATH = str(BASE_DIR / "local_questionnaire.py")
os.environ["LOCAL_QUESTIONNAIRE_PATH"] = LOCAL_QUESTIONNAIRE_PATH

logger.info(f"[English Questionnaire] Using LOCAL_QUESTIONNAIRE_PATH = {LOCAL_QUESTIONNAIRE_PATH}")

from flask import Flask, request, jsonify, send_from_directory
# from flask_cors import CORS  # Temporarily disabled to avoid dependency issues

# External integrations (robust import)
# zhipu_agent has been removed, only MetaGPT framework is used

try:
    from huoshan_asr import asr_transcribe_file
    logging.getLogger(__name__).info("Volcano ASR loaded successfully")
except Exception as _e:
    logging.getLogger(__name__).warning(f"Failed to load Volcano ASR, ASR will return empty text: {_e}")
    def asr_transcribe_file(path):
        return ""

try:
    from config import validate_config, TTS_OUT_DIR, FFMPEG_PATH
except Exception as _e:
    logging.getLogger(__name__).warning(f"Failed to load config, using default values: {_e}")
    TTS_OUT_DIR = "static/tts"
    FFMPEG_PATH = "ffmpeg"
    def validate_config():
        return True

from local_questionnaire import questions, questionnaire_reference, generate_assessment_report, get_question_info

try:
    from huoshan_tts import tts_text_to_mp3
except Exception as _e:
    logging.getLogger(__name__).warning(f"Failed to load huoshan_tts, TTS will return placeholder: {_e}")
    def tts_text_to_mp3(text, out_dir, basename):
        return pathlib.Path(out_dir) / "warmup.wav"

from report_manager import report_manager
from intelligent_questionnaire_manager import IntelligentQuestionnaireManager

# ===== Global thread pool & cache =====
_global_thread_pool = None
_app_cache = {}
_cache_lock = threading.Lock()

# ===== MetaGPT questionnaire managers (session-based) =====
_questionnaire_managers = {}  # session_id -> SimpleQuestionnaireManager

def get_global_thread_pool():
    """Get the global thread pool."""
    global _global_thread_pool
    if _global_thread_pool is None:
        _global_thread_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=8,
            thread_name_prefix="app_worker"
        )
    return _global_thread_pool

def cleanup_global_thread_pool():
    """Shutdown the global thread pool."""
    global _global_thread_pool
    if _global_thread_pool:
        _global_thread_pool.shutdown(wait=True)
        _global_thread_pool = None

@lru_cache(maxsize=1000)
def _cached_question_processing(question_text: str, session_id: str) -> str:
    """Placeholder for question processing cache."""
    return question_text

# ===== MetaGPT questionnaire workflow (DeepSeek) =====
_metagpt_init_ok = False
_metagpt_lock = threading.Lock()
_metagpt_workflow = None
_metagpt_error = None

# ===== Shared Answer Validator instance (singleton) =====
_shared_answer_validator = None
_validator_lock = threading.Lock()

def get_shared_answer_validator():
    """Get the shared AnswerValidatorAgent instance (singleton)."""
    global _shared_answer_validator
    if _shared_answer_validator is None:
        with _validator_lock:
            if _shared_answer_validator is None:
                try:
                    from metagpt_questionnaire.agents.answer_validator import AnswerValidatorAgent
                    _shared_answer_validator = AnswerValidatorAgent()
                    logger.info("✅ Shared answer validator initialized successfully")
                except Exception as e:
                    logger.error(f"❌ Failed to initialize shared answer validator: {e}")
                    return None
    return _shared_answer_validator

def _init_metagpt_if_needed():
    """Lazy initialization of MetaGPT questionnaire workflow."""
    global _metagpt_init_ok, _metagpt_workflow, _metagpt_error
    if _metagpt_init_ok and _metagpt_workflow is not None:
        return True
    
    with _metagpt_lock:
        if _metagpt_init_ok and _metagpt_workflow is not None:
            return True
        
        try:
            logger.info("🚀 Initializing MetaGPT questionnaire workflow...")
            
            # 1. Ensure paths are correct
            current_file = pathlib.Path(__file__).resolve()
            project_root = current_file.parent  # feiaiagent/
            metagpt_dir = project_root / "metagpt_questionnaire"
            
            if not metagpt_dir.exists():
                _metagpt_error = f"MetaGPT directory does not exist: {metagpt_dir}"
                logger.error(_metagpt_error)
                return False
            
            # 2. Add project root to sys.path if needed
            paths_to_add = [str(project_root)]
            for path in paths_to_add:
                if path not in sys.path:
                    sys.path.insert(0, path)
            
            logger.info(f"✅ MetaGPT path confirmed: {metagpt_dir}")
            
            # 3. Check environment variables
            deepseek_key = os.getenv("DEEPSEEK_API_KEY")
            if not deepseek_key or deepseek_key.startswith("your-"):
                logger.warning("⚠️ DEEPSEEK_API_KEY is not configured, MetaGPT will run in degraded mode")
            
            # 4. Import core modules
            try:
                from metagpt_questionnaire.config.metagpt_config import validate_config as metagpt_validate_config, get_llm_config
                from metagpt_questionnaire.agents.base_agent import agent_registry
                logger.info("✅ Core MetaGPT modules imported successfully")
            except ImportError as e:
                _metagpt_error = f"Failed to import core MetaGPT modules: {e}"
                logger.error(_metagpt_error)
                return False
            
            # 5. Import and register agents (best effort)
            agent_classes = []
            try:
                from metagpt_questionnaire.agents.questionnaire_designer import QuestionnaireDesignerAgent
                agent_classes.append(QuestionnaireDesignerAgent)
            except Exception as e:
                logger.warning(f"Failed to import QuestionnaireDesignerAgent: {e}")
            
            try:
                from metagpt_questionnaire.agents.risk_assessor import RiskAssessorAgent
                agent_classes.append(RiskAssessorAgent)
            except Exception as e:
                logger.warning(f"Failed to import RiskAssessorAgent: {e}")
            
            try:
                from metagpt_questionnaire.agents.data_analyzer import DataAnalyzerAgent
                agent_classes.append(DataAnalyzerAgent)
            except Exception as e:
                logger.warning(f"Failed to import DataAnalyzerAgent: {e}")
            
            try:
                from metagpt_questionnaire.agents.report_generator import ReportGeneratorAgent
                agent_classes.append(ReportGeneratorAgent)
            except Exception as e:
                logger.warning(f"Failed to import ReportGeneratorAgent: {e}")
            
            try:
                from metagpt_questionnaire.agents.conversational_interviewer import ConversationalInterviewerAgent
                agent_classes.append(ConversationalInterviewerAgent)
            except Exception as e:
                logger.warning(f"Failed to import ConversationalInterviewerAgent: {e}")
            
            try:
                from metagpt_questionnaire.agents.answer_validator import AnswerValidatorAgent
                agent_classes.append(AnswerValidatorAgent)
            except Exception as e:
                logger.warning(f"Failed to import AnswerValidatorAgent: {e}")
            
            # 6. Register agents
            registered_count = 0
            for agent_class in agent_classes:
                try:
                    from metagpt_questionnaire.agents.base_agent import agent_registry
                    agent_registry.register(agent_class)
                    registered_count += 1
                except Exception as e:
                    logger.warning(f"Failed to register agent {agent_class.__name__}: {e}")
            
            logger.info(f"✅ Registered {registered_count} MetaGPT agents")
            
            # 7. Validate MetaGPT config (optional)
            config_ok = False
            try:
                config_ok = metagpt_validate_config()
                if config_ok:
                    logger.info("✅ MetaGPT config validation passed")
                else:
                    logger.warning("⚠️ MetaGPT config validation failed, running in degraded mode")
            except Exception as e:
                logger.warning(f"⚠️ MetaGPT config validation raised an exception: {e}")
            
            # 8. Create workflow
            try:
                from metagpt_questionnaire.workflows.questionnaire_workflow import create_workflow
                _metagpt_workflow = create_workflow("standard")
                
                # Test workflow status
                status = _metagpt_workflow.get_agent_status()
                logger.info(f"✅ MetaGPT workflow created, total agents: {status.get('total_agents', 0)}")
                
                _metagpt_init_ok = True
                _metagpt_error = None
                logger.info("🎉 MetaGPT questionnaire workflow initialization completed")
                return True
                
            except Exception as e:
                _metagpt_error = f"Failed to create MetaGPT workflow: {e}"
                logger.error(_metagpt_error)
                return False
                
        except Exception as e:
            _metagpt_error = f"MetaGPT initialization failed: {e}"
            logger.error(_metagpt_error)
            _metagpt_init_ok = False
            _metagpt_workflow = None
            return False

def get_metagpt_status():
    """Return MetaGPT initialization and workflow status."""
    return {
        "initialized": _metagpt_init_ok,
        "workflow": _metagpt_workflow is not None,
        "error": _metagpt_error
    }

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    validate_config()
except Exception as e:
    logging.getLogger(__name__).warning(f"Config validation failed (ignored for startup): {e}")

app = Flask(__name__, static_url_path="/static", static_folder="static")
# CORS(app)  # Temporarily disabled to avoid dependency issues

# --------- Cache control ---------
@app.after_request
def add_header(response):
    if 'Cache-Control' not in response.headers:
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

def check_tool_exists(tool_name_or_path):
    return shutil.which(tool_name_or_path) is not None or pathlib.Path(tool_name_or_path).exists()

# Clean TTS directory (before starting a new questionnaire)
def clear_tts_dir(keep_names=None) -> int:
    try:
        keep = set(keep_names or [])
        tts_dir = pathlib.Path(TTS_OUT_DIR)
        if not tts_dir.exists():
            return 0
        deleted = 0
        for p in list(tts_dir.glob("*.mp3")) + list(tts_dir.glob("*.wav")):
            if p.name in keep:
                continue
            try:
                p.unlink()
                deleted += 1
            except Exception as e:
                logger.warning(f"Failed to delete TTS file: {p} -> {e}")
        if deleted:
            logger.info(f"Cleaned {deleted} TTS files")
        return deleted
    except Exception as e:
        logger.warning(f"Error while cleaning TTS directory: {e}")
        return 0

def generate_tts_audio_async(text: str, session_id: str) -> str:
    """
    Generate audio with Volcano TTS (async wrapper).
    Returns the audio URL under /static/tts/.
    """
    # Cache key
    cache_key = f"tts_{hash(text)}_{session_id}"
    with _cache_lock:
        if cache_key in _app_cache:
            cached_url = _app_cache[cache_key]
            if cached_url and cached_url.startswith("/static/tts/"):
                file_path = pathlib.Path("static") / "tts" / cached_url.split("/")[-1]
                if file_path.exists():
                    logger.info(f"Using cached TTS: {cached_url}")
                    return cached_url
    
    thread_pool = get_global_thread_pool()
    future = thread_pool.submit(_generate_tts_audio_sync, text, session_id)
    
    try:
        result = future.result(timeout=30)
        if result:
            with _cache_lock:
                _app_cache[cache_key] = result
            return result
        return ""
    except concurrent.futures.TimeoutError:
        logger.error(f"TTS generation timed out: {text[:50]}...")
        return ""
    except Exception as e:
        logger.error(f"TTS generation error: {e}")
        return ""

def _generate_tts_audio_sync(text: str, session_id: str) -> str:
    """Synchronous TTS generation (internal use)."""
    try:
        tts_dir = pathlib.Path(TTS_OUT_DIR)
        tts_dir.mkdir(parents=True, exist_ok=True)
        
        audio_path = tts_text_to_mp3(text, tts_dir, f"session_{session_id}")
        
        if audio_path and audio_path.exists():
            audio_filename = audio_path.name
            tts_url = f"/static/tts/{audio_filename}"
            logger.info(f"TTS audio generated: {tts_url}")
            return tts_url
        else:
            warmup = tts_dir / "warmup.wav"
            if warmup.exists():
                logger.warning("TTS generation failed, falling back to warmup.wav")
                return f"/static/tts/{warmup.name}"
            logger.error("TTS generation failed and no fallback file found")
            return ""
            
    except Exception as e:
        logger.error(f"Error while generating TTS audio: {e}")
        try:
            tts_dir = pathlib.Path(TTS_OUT_DIR)
            warmup = tts_dir / "warmup.wav"
            if warmup.exists():
                return f"/static/tts/{warmup.name}"
        except Exception:
            pass
        return ""

def generate_tts_audio(text: str, session_id: str) -> str:
    """
    Public TTS helper.
    Uses async-based generation to avoid blocking the main thread.
    """
    return generate_tts_audio_async(text, session_id)

# Simple local answer validation (can be upgraded to LLM-based)
def validate_user_answer(answer_text: str, question_text: str) -> (bool, str):
    try:
        text = (answer_text or "").strip()
        if not text:
            return False, "No valid content detected."
        if len(text) < 2:
            return False, "The answer is too short."
        
        # Generic vague answers (Chinese and English) – kept here as negative examples
        generic_list = [
            "不知道", "不清楚", "随便", "无", "没了", "没有", "嗯", "啊",
            "ok", "好的", "还行", "是", "否", "不知道呢", "记不清", "忘了",
            "idk", "dont know", "don't know", "no idea", "whatever", "nothing"
        ]
        if any(g in text for g in generic_list):
            return False, "The answer is too vague."
        
        # If answer is almost repeating the question
        if question_text:
            qt = question_text.strip()
            if len(qt) >= 6 and text in qt:
                return False, "The answer seems to repeat the question instead of answering it."
        return True, "ok"
    except Exception:
        # On validation error, allow the answer to avoid blocking the flow
        return True, "ok"

@app.route("/")
def home():
    return send_from_directory("static", "index.html")

# Static routes
@app.route("/static/video/<path:filename>")
def serve_video(filename):
    return send_from_directory("static/video", filename)

@app.route("/static/tts/<path:filename>")
def serve_tts(filename):
    return send_from_directory("static/tts", filename)

# ========= Text length & chunking for avatar =========
AVG_CHARS_PER_SEC = 4.0   # Rough estimate, depends on TTS voice
TARGET_SECS = 7           # Target 6–8 seconds per segment
MAX_CHARS = 10000         # Allow long sentences for TTS output

def shorten_for_avatar(text: str, max_chars: int = MAX_CHARS) -> str:
    """Return the full text to ensure TTS receives the complete prompt."""
    return (text or "").strip()

def split_for_avatar(text: str, target_secs: int = TARGET_SECS):
    import re
    t = (text or "").strip()
    if not t:
        return []
    parts = re.split(r'(?<=[。！？\n.!?])', t)
    parts = [p.strip() for p in parts if p.strip()]
    chunks, cur, cur_len = [], [], 0
    target_chars = int(target_secs * AVG_CHARS_PER_SEC * 1.15)
    for p in parts:
        if cur_len + len(p) > target_chars and cur:
            chunks.append("".join(cur).strip())
            cur, cur_len = [], 0
        cur.append(p)
        cur_len += len(p)
    if cur:
        chunks.append("".join(cur).strip())
    out = []
    for ch in chunks:
        if len(ch) <= MAX_CHARS * 2:
            out.append(ch)
        else:
            for i in range(0, len(ch), MAX_CHARS):
                out.append(ch[i:i+MAX_CHARS])
    return out

# ========= Legacy MetaGPT agent endpoints =========

@app.route("/api/agent/start", methods=["POST"])
def agent_start():
    """
    Start a legacy MetaGPT questionnaire session (non-conversational, legacy flow).
    """
    data = request.get_json(force=True)
    session_id = data["session_id"]

    # Clean previous TTS files (keep warmup/placeholders)
    clear_tts_dir(keep_names=["warmup.wav", "beep.wav"])

    try:
        logger.info(f"MetaGPT questionnaire system started. Session ID: {session_id}")

        from metagpt_questionnaire.simple_questionnaire_manager import SimpleQuestionnaireManager
        from metagpt_questionnaire.agents.questionnaire_designer import QuestionnaireDesignerAgent
        
        designer = QuestionnaireDesignerAgent()
        
        questionnaire = _run_async(designer.design_questionnaire({
            "source": "local",
            "local_questionnaire_path": LOCAL_QUESTIONNAIRE_PATH
        }))
        
        manager = SimpleQuestionnaireManager()
        if not manager.initialize_questionnaire(questionnaire):
            raise Exception("Failed to initialize questionnaire.")
        
        _questionnaire_managers[session_id] = manager
        
        result = _run_async(manager.get_next_question())
        
        if result["status"] == "next_question":
            # For legacy flow, result["question"] may be a dict; handle both cases
            q = result["question"]
            if isinstance(q, dict):
                question = q.get("text", "")
            else:
                question = str(q)
            final_session_id = session_id
        else:
            raise Exception("Failed to get the first question.")

    except Exception as e:
        logger.error(f"MetaGPT questionnaire system failed to start: {e}")
        question = "The system is temporarily unavailable. Please try again later."
        final_session_id = session_id

    video_url = "/static/video/human.mp4"
    video_stream_url = "/static/video/human.mp4"
    tts_url = generate_tts_audio(shorten_for_avatar(question), final_session_id)

    return jsonify({
        "session_id": final_session_id,
        "question": question,
        "tts_url": tts_url,
        "video_url": video_url,
        "video_stream_url": video_stream_url,
        "is_complete": False
    })
# ========= MetaGPT questionnaire (step-by-step) =========
@app.route("/api/metagpt_agent/start", methods=["POST"])
def metagpt_agent_start():
    """
    Start a step-by-step MetaGPT questionnaire session.
    - Initialize MetaGPT if needed
    - Build questionnaire from local template
    - Return first question with TTS and fixed avatar video
    """
    try:
        data = request.get_json(force=True)
        session_id = data.get("session_id", str(int(time.time() * 1000)))

        # Initialize MetaGPT workflow
        if not _init_metagpt_if_needed():
            return jsonify({"error": "MetaGPT initialization failed"}), 500

        # Clean previous TTS
        clear_tts_dir(keep_names=["warmup.wav", "beep.wav"])

        from metagpt_questionnaire.agents.questionnaire_designer import QuestionnaireDesignerAgent
        designer = QuestionnaireDesignerAgent()
        questionnaire = _run_async(designer.design_questionnaire({
            "source": "local",
            "local_questionnaire_path": os.environ.get("LOCAL_QUESTIONNAIRE_PATH")
        }))

        # Session state
        if not hasattr(app, "metagpt_sessions"):
            app.metagpt_sessions = {}
        app.metagpt_sessions[session_id] = {
            "questionnaire": questionnaire,
            "current_index": 0,
            "responses": [],
            "start_time": time.time()
        }

        first_q = questionnaire.questions[0]
        question_text = first_q.text

        # TTS + fixed avatar video
        video_url = "/static/video/human.mp4"
        video_stream_url = "/static/video/human.mp4"
        tts_url = generate_tts_audio(shorten_for_avatar(question_text), session_id)

        return jsonify({
            "session_id": session_id,
            "question": question_text,
            "tts_url": tts_url,
            "video_url": video_url,
            "video_stream_url": video_stream_url,
            "is_complete": False
        })
    except Exception as e:
        logger.error(f"MetaGPT step-by-step start failed: {e}")
        return jsonify({"error": f"Failed to start: {str(e)}"}), 500


@app.route("/api/metagpt_agent/reply", methods=["POST"])
def metagpt_agent_reply():
    """
    Step-by-step MetaGPT questionnaire reply:
    - Record answer to current question
    - If not complete: return next question
    - If complete: run workflow, generate report, save it and return full report text
    """
    try:
        data = request.get_json(force=True)
        session_id = data["session_id"]
        answer_text = data.get("answer", "").strip()

        if not hasattr(app, "metagpt_sessions") or session_id not in app.metagpt_sessions:
            return jsonify({"error": "Session not found"}), 400

        sess = app.metagpt_sessions[session_id]
        questionnaire = sess["questionnaire"]
        idx = sess["current_index"]

        from metagpt_questionnaire.models.questionnaire import UserResponse
        current_q = questionnaire.questions[idx]
        
        # Use shared answer validator
        try:
            validator = get_shared_answer_validator()
            if validator:
                decision = _run_async(validator.run(
                    answer_text,
                    current_q.text,
                    idx,
                    len(questionnaire.questions)
                ))
                logger.info(f"🔍 Answer validation result: {decision}")
            else:
                raise Exception("Answer validator not available")
        except Exception as e:
            logger.warning(f"Answer validator failed, falling back to simple validation: {e}")
            is_valid, reason = validate_user_answer(answer_text, current_q.text)
            decision = {
                "redo": False,
                "valid": is_valid,
                "reason": reason if not is_valid else "Basic validation passed"
            }

        # 1. Handle keyword detection / redo intent first
        if decision.get("detected") or decision.get("redo"):
            target_index = int(decision.get("target_index", idx))
            target_index = max(0, min(target_index, len(questionnaire.questions) - 1))
            sess["current_index"] = target_index

            # Clear answers if needed
            if decision.get("clear_all_answers"):
                sess["responses"].clear()
                logger.info("🔄 All answers cleared, restarting questionnaire")
            elif decision.get("clear_previous_answer"):
                sess["responses"] = [
                    response for response in sess["responses"]
                    if response.question_id != questionnaire.questions[target_index].id
                ]
                logger.info(f"🔄 Cleared answer for question {target_index + 1}")

            target_q = questionnaire.questions[target_index]
            base_message = decision.get(
                "message",
                f"Okay, let's go back to question {target_index + 1}"
            )
            message = f"{base_message}, please answer again: {target_q.text}"
            
            video_url = "/static/video/human.mp4"
            video_stream_url = "/static/video/human.mp4"
            tts_url = generate_tts_audio(shorten_for_avatar(message), session_id)

            return jsonify({
                "session_id": session_id,
                "question": message,
                "tts_url": tts_url,
                "video_url": video_url,
                "video_stream_url": video_stream_url,
                "is_complete": False,
                "progress": f"{target_index + 1}/{len(questionnaire.questions)}",
                "total_questions": len(questionnaire.questions),
                "redo": True,
                "redo_target_index": target_index,
                "intent_type": decision.get("intent_type", "redo"),
                "message": decision.get("message", "Please answer this question again.")
            })

        # 2. Handle skip intent
        if decision.get("skip"):
            target_index = int(decision.get("target_index", idx + 1))
            target_index = max(0, min(target_index, len(questionnaire.questions) - 1))
            sess["current_index"] = target_index
            
            message = decision.get("message", "Okay, we will skip this question.")

            # Completed?
            if target_index >= len(questionnaire.questions):
                report_text = "The questionnaire has been completed. Thank you for your participation."
                video_url = "/static/video/human.mp4"
                video_stream_url = "/static/video/human.mp4"
                tts_url = generate_tts_audio(shorten_for_avatar(report_text), session_id)
                
                return jsonify({
                    "session_id": session_id,
                    "question": report_text,
                    "tts_url": tts_url,
                    "video_url": video_url,
                    "video_stream_url": video_stream_url,
                    "is_complete": True,
                    "progress": f"{len(questionnaire.questions)}/{len(questionnaire.questions)}",
                    "total_questions": len(questionnaire.questions),
                    "skip": True
                })
            
            # Next question after skip
            next_q = questionnaire.questions[target_index]
            next_message = f"{message}\n\n{next_q.text}"
            
            video_url = "/static/video/human.mp4"
            video_stream_url = "/static/video/human.mp4"
            tts_url = generate_tts_audio(shorten_for_avatar(next_message), session_id)
            
            return jsonify({
                "session_id": session_id,
                "question": next_message,
                "tts_url": tts_url,
                "video_url": video_url,
                "video_stream_url": video_stream_url,
                "is_complete": False,
                "progress": f"{target_index + 1}/{len(questionnaire.questions)}",
                "total_questions": len(questionnaire.questions),
                "skip": True
            })

        # 3. Handle invalid answer
        if not decision.get("valid", True):
            reason = decision.get("reason", "Your answer is too vague.")
            suggestion = decision.get(
                "suggestion",
                "Please provide a more specific and detailed answer."
            )
            hint = f"{reason} {suggestion} Please answer this question again: {current_q.text}"
            
            video_url = "/static/video/human.mp4"
            video_stream_url = "/static/video/human.mp4"
            tts_url = generate_tts_audio(shorten_for_avatar(hint), session_id)

            return jsonify({
                "session_id": session_id,
                "question": hint,
                "tts_url": tts_url,
                "video_url": video_url,
                "video_stream_url": video_stream_url,
                "is_complete": False,
                "progress": f"{idx + 1}/{len(questionnaire.questions)}",
                "total_questions": len(questionnaire.questions),
                "invalid_answer": True,
                "invalid_reason": reason,
                "suggestion": suggestion,
                "retry": True
            })

        # 4. Valid answer: record and move on
        if decision.get("sensitive_skip"):
            sess["responses"].append(UserResponse(
                question_id=current_q.id,
                answer="Prefer not to answer"
            ))
            logger.info(f"🔒 User chose not to answer a sensitive question: {current_q.text}")
        else:
            sess["responses"].append(UserResponse(
                question_id=current_q.id,
                answer=answer_text
            ))

        next_index = idx + 1
        if next_index < len(questionnaire.questions):
            sess["current_index"] = next_index
            next_q = questionnaire.questions[next_index]

            video_url = "/static/video/human.mp4"
            video_stream_url = "/static/video/human.mp4"
            tts_url = generate_tts_audio(shorten_for_avatar(next_q.text), session_id)

            return jsonify({
                "session_id": session_id,
                "question": next_q.text,
                "tts_url": tts_url,
                "video_url": video_url,
                "video_stream_url": video_stream_url,
                "is_complete": False,
                "progress": f"{next_index + 1}/{len(questionnaire.questions)}",
                "total_questions": len(questionnaire.questions)
            })
        else:
            # Questionnaire completed -> run MetaGPT workflow
            if not _init_metagpt_if_needed():
                return jsonify({"error": "MetaGPT initialization failed"}), 500
            try:
                from metagpt_questionnaire.main import MetaGPTQuestionnaireApp
                app_q = MetaGPTQuestionnaireApp()
                if not app_q.initialize():
                    return jsonify({"error": "MetaGPT workflow initialization failed"}), 500

                result = _run_async(app_q.run_complete_workflow(
                    user_responses=sess["responses"],
                    user_profile={"session_id": session_id}
                ))

                # Extract report
                report_text = None
                try:
                    report_text = result.get("final_results", {}).get("report", {}).get("content")
                except Exception:
                    report_text = None

                if not report_text:
                    ra = result.get("final_results", {}).get("risk_assessment") or {}
                    report_text = (
                        "Lung Cancer Early Screening Risk Assessment Report\n\n"
                        f"Overall risk: {ra.get('overall_risk', 'unknown')}\n"
                        f"Risk score: {ra.get('risk_score', '-')}\n"
                    )

                # Save report
                try:
                    answers_map = {}
                    for r in sess["responses"]:
                        try:
                            q_text = next(
                                (q.text for q in questionnaire.questions if q.id == r.question_id),
                                r.question_id
                            )
                            answers_map[q_text] = str(r.answer)
                        except Exception:
                            answers_map[r.question_id] = str(r.answer)
                    _ = report_manager.save_report(report_text, answers_map, session_id)
                    _ = report_manager.save_report_json(report_text, answers_map, session_id)
                    _ = report_manager.save_report_pdf(report_text, answers_map, session_id)
                except Exception as _:
                    logger.warning("Failed to save MetaGPT report (ignored).")

                first_seg = shorten_for_avatar(report_text)
                video_url = "/static/video/human.mp4"
                video_stream_url = "/static/video/human.mp4"
                tts_url = generate_tts_audio(first_seg, session_id)

                sess["completed"] = True

                return jsonify({
                    "session_id": session_id,
                    "question": report_text,
                    "tts_url": tts_url,
                    "video_url": video_url,
                    "video_stream_url": video_stream_url,
                    "is_complete": True,
                    "progress": f"{len(questionnaire.questions)}/{len(questionnaire.questions)}",
                    "total_questions": len(questionnaire.questions)
                })
            except Exception as e:
                logger.error(f"MetaGPT workflow execution failed: {e}")
                return jsonify({"error": str(e)}), 500
    except Exception as e:
        logger.error(f"MetaGPT step-by-step reply failed: {e}")
        return jsonify({"error": f"Failed to reply: {str(e)}"}), 500


# ========= MetaGPT questionnaire (conversational) =========
@app.route("/api/metagpt_agent/start_conversational", methods=["POST"])
def metagpt_agent_start_conversational():
    """
    Start a conversational interview using the ConversationalInterviewerAgent
    and SimpleQuestionnaireManager.
    """
    try:
        data = request.get_json(force=True)
        session_id = data.get("session_id", str(int(time.time() * 1000)))

        if not _init_metagpt_if_needed():
            return jsonify({"error": "MetaGPT initialization failed"}), 500

        clear_tts_dir(keep_names=["warmup.wav", "beep.wav"])

        from metagpt_questionnaire.agents.questionnaire_designer import QuestionnaireDesignerAgent
        designer = QuestionnaireDesignerAgent()
        questionnaire = _run_async(designer.design_questionnaire({
            "source": "local",
            "local_questionnaire_path": os.environ.get("LOCAL_QUESTIONNAIRE_PATH")
        }))

        if not hasattr(app, "metagpt_sessions"):
            app.metagpt_sessions = {}
        app.metagpt_sessions[session_id] = {
            "questionnaire": questionnaire,
            "current_index": 0,
            "responses": [],
            "start_time": time.time()
        }

        from metagpt_questionnaire.simple_questionnaire_manager import SimpleQuestionnaireManager
        manager = SimpleQuestionnaireManager()
        if not manager.initialize_questionnaire(questionnaire):
            return jsonify({"error": "Failed to initialize questionnaire manager"}), 500
        
        # First question
        result = _run_async(manager.get_next_question())
        if result["status"] != "next_question":
            return jsonify({"error": "Failed to get the first question"}), 500
        
        question_text = result["question"]

        app.metagpt_sessions[session_id]["manager"] = manager
        app.metagpt_sessions[session_id]["current_index"] = 0

        video_url = "/static/video/human.mp4"
        video_stream_url = "/static/video/human.mp4"
        tts_url = generate_tts_audio(shorten_for_avatar(question_text), session_id)

        return jsonify({
            "session_id": session_id,
            "question": question_text,
            "tts_url": tts_url,
            "video_url": video_url,
            "video_stream_url": video_stream_url,
            "is_complete": False
        })
    except Exception as e:
        logger.error(f"MetaGPT conversational start failed: {e}")
        return jsonify({"error": f"Failed to start: {str(e)}"}), 500


@app.route("/api/metagpt_agent/reply_conversational", methods=["POST"])
def metagpt_agent_reply_conversational():
    """
    Handle a user's reply in a conversational interview using SimpleQuestionnaireManager.
    """
    try:
        data = request.get_json(force=True)
        session_id = data["session_id"]
        answer_text = data.get("answer", "").strip()

        if not hasattr(app, "metagpt_sessions") or session_id not in app.metagpt_sessions:
            return jsonify({"error": "Session not found"}), 400

        sess = app.metagpt_sessions[session_id]
        manager = sess.get("manager")

        if not manager:
            return jsonify({"error": "Questionnaire manager not found"}), 400

        questionnaire = sess.get("questionnaire")

        result = _run_async(manager.get_next_question(answer_text))
        sess["current_index"] = manager.current_question_index

        status = result.get("status")

        if status == "invalid_answer":
            question_text = result.get("question", "")
            error_msg = result.get("error", "Your answer is too vague.")
            suggestion = result.get(
                "suggestion",
                "Please provide a more specific and detailed answer."
            )
            hint = f"{error_msg}. {suggestion} Please answer again: {question_text}"

            video_url = "/static/video/human.mp4"
            video_stream_url = "/static/video/human.mp4"
            tts_url = generate_tts_audio(shorten_for_avatar(hint), session_id)

            return jsonify({
                "session_id": session_id,
                "question": hint,
                "tts_url": tts_url,
                "video_url": video_url,
                "video_stream_url": video_stream_url,
                "is_complete": False,
                "invalid_answer": True,
                "invalid_reason": error_msg,
                "suggestion": suggestion,
                "retry": True,
                "question_id": result.get("question_id"),
                "category": result.get("category"),
                "progress": result.get("progress")
            })

        if status == "completed":
            report_text = result.get("report", "")

            try:
                manager_obj = sess.get("manager")
                if manager_obj and hasattr(manager_obj, "answered_questions"):
                    answers_map: Dict[str, str] = {}
                    if questionnaire:
                        for response in manager_obj.answered_questions:
                            try:
                                q_text = next(
                                    (q.text for q in questionnaire.questions if q.id == response.question_id),
                                    response.question_id
                                )
                                answers_map[q_text] = str(response.answer)
                            except Exception:
                                answers_map[response.question_id] = str(response.answer)
                    _ = report_manager.save_report(report_text, answers_map, session_id)
                    _ = report_manager.save_report_json(report_text, answers_map, session_id)
                    _ = report_manager.save_report_pdf(report_text, answers_map, session_id)
                    logger.info(f"Conversational MetaGPT report saved: {session_id}")
                else:
                    _ = report_manager.save_report(report_text, {}, session_id)
                    _ = report_manager.save_report_json(report_text, {}, session_id)
                    _ = report_manager.save_report_pdf(report_text, {}, session_id)
                    logger.info(f"Conversational MetaGPT report saved (no answer data): {session_id}")
            except Exception as e:
                logger.warning(f"Failed to save conversational MetaGPT report: {e}")

            video_url = "/static/video/human.mp4"
            video_stream_url = "/static/video/human.mp4"
            tts_url = generate_tts_audio(shorten_for_avatar(report_text), session_id)

            total_questions = result.get("total_questions", 0)
            return jsonify({
                "session_id": session_id,
                "question": report_text,
                "tts_url": tts_url,
                "video_url": video_url,
                "video_stream_url": video_stream_url,
                "is_complete": True,
                "progress": f"{total_questions}/{total_questions}",
                "total_questions": total_questions
            })

        if status in {"next_question", "redo_question"}:
            question_text = result.get("question", "")
            video_url = "/static/video/human.mp4"
            video_stream_url = "/static/video/human.mp4"
            tts_url = generate_tts_audio(shorten_for_avatar(question_text), session_id)

            response_payload = {
                "session_id": session_id,
                "question": question_text,
                "question_id": result.get("question_id"),
                "category": result.get("category"),
                "progress": result.get("progress"),
                "tts_url": tts_url,
                "video_url": video_url,
                "video_stream_url": video_stream_url,
                "is_complete": False
            }
            if status == "redo_question":
                response_payload["redo"] = True
            if result.get("skip"):
                response_payload["skip"] = True
            return jsonify(response_payload)

        if status == "error":
            return jsonify({"error": result.get("error", "Unknown error.")}), 500

        return jsonify({"error": "Unexpected questionnaire status."}), 500

    except Exception as e:
        logger.error(f"MetaGPT conversational reply failed: {e}")
        return jsonify({"error": f"Failed to reply: {str(e)}"}), 500
@app.route("/api/agent/reply", methods=["POST"])
def agent_reply():
    """
    Legacy MetaGPT questionnaire reply endpoint (non-conversational).
    """
    data = request.get_json(force=True)
    session_id = data["session_id"]
    answer_text = data["answer"]

    try:
        logger.info(f"MetaGPT legacy questionnaire continues. Session ID: {session_id}, user answer preview: {answer_text[:50]}...")

        if session_id not in _questionnaire_managers:
            raise Exception("Session not found, please restart the questionnaire.")
        
        manager = _questionnaire_managers[session_id]
        
        result = _run_async(manager.get_next_question(answer_text))
        
        if result["status"] == "next_question":
            q = result["question"]
            if isinstance(q, dict):
                question = q.get("text", "")
            else:
                question = str(q)
            is_complete = False
            final_session_id = session_id
            
            logger.info(f"MetaGPT legacy questionnaire next question: {question}")
            
        elif result["status"] == "completed":
            question = result.get("report", "Questionnaire completed.")
            is_complete = True
            final_session_id = session_id
            
            try:
                answers = result.get("answers", {})
                _ = report_manager.save_report(question, answers, final_session_id)
                _ = report_manager.save_report_json(question, answers, final_session_id)
                _ = report_manager.save_report_pdf(question, answers, final_session_id)
            except Exception as _:
                logger.warning("Failed to save MetaGPT report (ignored).")
                
        else:
            raise Exception(f"Questionnaire processing failed: {result.get('error', 'Unknown error')}")

    except Exception as e:
        logger.error(f"MetaGPT legacy call failed: {e}")
        question = "The system is temporarily unavailable. Please try again later."
        final_session_id = session_id
        is_complete = False

    video_url = "/static/video/human.mp4"
    video_stream_url = "/static/video/human.mp4"
    tts_url = generate_tts_audio(shorten_for_avatar(question), final_session_id)

    return jsonify({
        "session_id": final_session_id,
        "question": question,
        "tts_url": tts_url,
        "video_url": video_url,
        "video_stream_url": video_stream_url,
        "is_complete": is_complete
    })


# ========= ASR API =========
@app.route("/api/asr", methods=["POST"])
def asr():
    try:
        logger.info("=== ASR endpoint invoked ===")
        if "audio" not in request.files:
            logger.error("ASR error: 'audio' field not found in request.")
            return jsonify({"text": "", "error": "no audio field"}), 400

        f = request.files["audio"]
        td = tempfile.mkdtemp(prefix="asr_")
        td_path = pathlib.Path(td)
        suffix = f.filename.split(".")[-1] or "webm"
        in_path = td_path / f"input.{suffix}"
        out_wav = td_path / "converted.wav"

        try:
            f.save(in_path)

            if in_path.suffix.lower() in [".spx", ".speex"]:
                if check_tool_exists("speexdec"):
                    subprocess.run(
                        ["speexdec", str(in_path), str(out_wav)],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    subprocess.run(
                        [
                            FFMPEG_PATH,
                            "-y",
                            "-i",
                            str(in_path),
                            "-ac",
                            "1",
                            "-ar",
                            "16000",
                            "-acodec",
                            "pcm_s16le",
                            str(out_wav),
                        ],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
            else:
                subprocess.run(
                    [
                        FFMPEG_PATH,
                        "-y",
                        "-i",
                        str(in_path),
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        "-acodec",
                        "pcm_s16le",
                        str(out_wav),
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

            text = asr_transcribe_file(str(out_wav))
            return jsonify({"text": text or ""})

        finally:
            # Try to remove the temp directory a few times in case of file locks
            for _ in range(5):
                try:
                    shutil.rmtree(td_path, ignore_errors=False)
                    break
                except PermissionError:
                    time.sleep(0.1)
                except Exception:
                    break

    except Exception as e:
        return jsonify({"text": "", "error": f"ASR exception: {str(e)}"}), 500


@app.route("/api/health")
def health():
    return jsonify({"ok": True})


# ===== MetaGPT workflow management API =====
@app.route("/api/metagpt/init", methods=["POST", "GET"])
def metagpt_init():
    ok = _init_metagpt_if_needed()
    status = get_metagpt_status()
    return jsonify({
        "initialized": ok,
        "status": status,
        "message": "MetaGPT initialized successfully" if ok else f"MetaGPT initialization failed: {status.get('error', 'Unknown error')}"
    })


@app.route("/api/metagpt/status", methods=["GET"])
def metagpt_status():
    """Get detailed MetaGPT status."""
    status = get_metagpt_status()
    
    diagnostic_info = {
        "paths": {
            "current_file": str(pathlib.Path(__file__).resolve()),
            "project_root": str(pathlib.Path(__file__).resolve().parent.parent),
            "metagpt_dir": str(pathlib.Path(__file__).resolve().parent.parent / "metagpt_questionnaire"),
            "metagpt_exists": (pathlib.Path(__file__).resolve().parent.parent / "metagpt_questionnaire").exists()
        },
        "environment": {
            "deepseek_key_set": bool(os.getenv("DEEPSEEK_API_KEY")) and not os.getenv("DEEPSEEK_API_KEY", "").startswith("your-"),
            "python_path": sys.path[:3],
        },
        "workflow_info": None
    }
    
    if _metagpt_workflow:
        try:
            diagnostic_info["workflow_info"] = _metagpt_workflow.get_agent_status()
        except Exception as e:
            diagnostic_info["workflow_info"] = {"error": str(e)}
    
    return jsonify({
        "status": status,
        "diagnostic": diagnostic_info,
        "timestamp": time.time()
    })


def _run_async(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        return asyncio.run_coroutine_threadsafe(coro, loop).result()
    else:
        return asyncio.run(coro)


@app.route("/api/metagpt/demo", methods=["POST"])
def metagpt_demo():
    if not _init_metagpt_if_needed():
        return jsonify({"error": "MetaGPT initialization failed"}), 500
    try:
        from metagpt_questionnaire.main import MetaGPTQuestionnaireApp
        app_q = MetaGPTQuestionnaireApp()
        if not app_q.initialize():
            return jsonify({"error": "MetaGPT workflow initialization failed"}), 500
        result = _run_async(app_q.run_demo_workflow())
        output_file = app_q.export_results(result)
        return jsonify({
            "status": result.get("status"),
            "workflow_id": result.get("workflow_id"),
            "stages": result.get("stages", []),
            "final_results": result.get("final_results", {}),
            "output_file": output_file
        })
    except Exception as e:
        logger.error(f"MetaGPT demo execution failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/metagpt/custom", methods=["POST"])
def metagpt_custom():
    if not _init_metagpt_if_needed():
        return jsonify({"error": "MetaGPT initialization failed"}), 500
    try:
        data = request.get_json(force=True)
        workflow_config = data or {}
        from metagpt_questionnaire.main import MetaGPTQuestionnaireApp
        app_q = MetaGPTQuestionnaireApp()
        if not app_q.initialize():
            return jsonify({"error": "MetaGPT workflow initialization failed"}), 500
        result = _run_async(app_q.run_custom_workflow(workflow_config))
        output_file = app_q.export_results(result)
        return jsonify({
            "status": result.get("status"),
            "workflow_id": result.get("workflow_id"),
            "stages": result.get("stages", []),
            "final_results": result.get("final_results", {}),
            "output_file": output_file
        })
    except Exception as e:
        logger.error(f"MetaGPT custom workflow execution failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/asr/health")
def asr_health():
    try:
        health_status = {
            "status": "ok",
            "ffmpeg": {
                "path": FFMPEG_PATH,
                "exists": check_tool_exists(FFMPEG_PATH),
                "version": None
            },
            "speexdec": {
                "exists": check_tool_exists("speexdec"),
                "path": shutil.which("speexdec")
            },
            "temp_dir": {
                "writable": True,
                "path": str(tempfile.gettempdir())
            }
        }

        if check_tool_exists(FFMPEG_PATH):
            try:
                result = subprocess.run(
                    [FFMPEG_PATH, "-version"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    health_status["ffmpeg"]["version"] = result.stdout.split("\n")[0]
            except Exception as e:
                health_status["ffmpeg"]["version"] = f"Failed to get version: {str(e)}"

        try:
            from config import XFYUN_APPID, XFYUN_APIKEY, XFYUN_APISECRET
            health_status["xfyun"] = {
                "appid": XFYUN_APPID,
                "apikey": "set" if XFYUN_APIKEY else "not set",
                "apisecret": "set" if XFYUN_APISECRET else "not set"
            }
        except Exception as e:
            health_status["xfyun"] = {"error": str(e)}

        return jsonify(health_status)
    except Exception as e:
        return jsonify({"status": "error", "error": str(e), "timestamp": time.time()}), 500
@app.route("/api/questionnaire_status", methods=["GET"])
def get_questionnaire_status():
    return jsonify({"current_system": "MetaGPT Questionnaire System", "use_metagpt": True})


# ========= Local questionnaire (rule-based) =========
@app.route("/api/local_questionnaire/start", methods=["POST"])
def local_questionnaire_start():
    try:
        data = request.get_json(force=True)
        session_id = data.get("session_id", str(int(time.time() * 1000)))

        clear_tts_dir(keep_names=["warmup.wav", "beep.wav"])

        if not hasattr(app, "questionnaire_sessions"):
            app.questionnaire_sessions = {}

        app.questionnaire_sessions[session_id] = {
            "current_question_index": 0,
            "answers": {},
            "start_time": time.time()
        }

        from local_questionnaire import questions_structured
        first_question_obj = questions_structured[0]
        first_question = first_question_obj.get("prompt", first_question_obj["text"])
        question_info = get_question_info(0)

        video_url = "/static/video/human.mp4"
        video_stream_url = "/static/video/human.mp4"
        tts_url = generate_tts_audio(shorten_for_avatar(first_question), session_id)

        return jsonify({
            "session_id": session_id,
            "question": first_question,
            "question_info": question_info,
            "tts_url": tts_url,
            "video_url": video_url,
            "video_stream_url": video_stream_url,
            "progress": f"1/{len(questions)}",
            "total_questions": len(questions)
        })
    except Exception as e:
        logger.error(f"Failed to start local questionnaire: {e}")
        return jsonify({"error": f"Failed to start: {str(e)}"}), 500


from local_questionnaire import questions_structured

def find_next_question_index(current_index: int, answers: dict) -> int:
    """
    Find the index of the next valid question, applying auto-fill logic.
    Returns -1 if the questionnaire is complete.
    """
    next_index = current_index + 1
    while next_index < len(questions_structured):
        question_data = questions_structured[next_index]
        dependency = question_data.get("depends_on")

        if not dependency:
            return next_index

        dependent_question_id = dependency.get("id")
        required_value = dependency.get("value")
        possible_values = dependency.get("values", [required_value])
        auto_fill_value = question_data.get("auto_fill_value", "0")

        dependent_question_text = None
        for q in questions_structured:
            if q["id"] == dependent_question_id:
                dependent_question_text = q["text"]
                break
        
        actual_answer = answers.get(dependent_question_text)
        
        dependency_met = False
        if actual_answer:
            answer_text = str(actual_answer).lower()
            for value in possible_values:
                value_lower = value.lower()
                if (
                    value_lower in answer_text
                    and not any(neg in answer_text for neg in ["不", "没", "无", "否", "没有", "不会"])
                ):
                    dependency_met = True
                    break
        
        if dependency_met:
            return next_index
        else:
            question_text = question_data.get("text")
            if question_text and question_text not in answers:
                answers[question_text] = auto_fill_value
                print(f"🔄 Auto-filled: {question_text} = {auto_fill_value}")
            next_index += 1
    
    return -1


@app.route("/api/local_questionnaire/reply", methods=["POST"])
def local_questionnaire_reply():
    try:
        data = request.get_json(force=True)
        session_id = data["session_id"]
        answer_text = data["answer"]

        if session_id not in app.questionnaire_sessions:
            return jsonify({"error": "Session not found"}), 400

        session = app.questionnaire_sessions[session_id]
        current_index = session["current_question_index"]
        
        current_question_obj = questions_structured[current_index]
        session["answers"][current_question_obj["text"]] = answer_text

        next_index = find_next_question_index(current_index, session["answers"])

        if next_index == -1:
            report = generate_assessment_report(session["answers"])
            session["completed"] = True
            session["report"] = report

            try:
                _ = report_manager.save_report(report, session["answers"], session_id)
                _ = report_manager.save_report_json(report, session["answers"], session_id)
                _ = report_manager.save_report_pdf(report, session["answers"], session_id)
            except Exception as _:
                logger.warning("Failed to save local questionnaire report (ignored).")

            first_seg = shorten_for_avatar(report)

            video_url = "/static/video/human.mp4"
            video_stream_url = "/static/video/human.mp4"
            tts_url = generate_tts_audio(first_seg, session_id)

            return jsonify({
                "session_id": session_id,
                "question": report,
                "tts_url": tts_url,
                "video_url": video_url,
                "video_stream_url": video_stream_url,
                "is_complete": True,
                "progress": f"{len(questions)}/{len(questions)}",
                "total_questions": len(questions)
            })
        else:
            next_question_obj = questions_structured[next_index]
            next_question = next_question_obj.get("prompt", next_question_obj["text"])
            question_info = get_question_info(next_index)
            session["current_question_index"] = next_index

            video_url = "/static/video/human.mp4"
            video_stream_url = "/static/video/human.mp4"
            tts_url = generate_tts_audio(shorten_for_avatar(next_question), session_id)

            return jsonify({
                "session_id": session_id,
                "question": next_question,
                "question_info": question_info,
                "tts_url": tts_url,
                "video_url": video_url,
                "video_stream_url": video_stream_url,
                "is_complete": False,
                "progress": f"{next_index + 1}/{len(questions)}",
                "total_questions": len(questions)
            })

    except Exception as e:
        logger.error(f"Local questionnaire reply failed: {e}")
        return jsonify({"error": f"Failed to submit: {str(e)}"}), 500


@app.route("/api/local_questionnaire/status/<session_id>", methods=["GET"])
def get_local_questionnaire_status(session_id):
    try:
        if session_id not in app.questionnaire_sessions:
            return jsonify({"error": "Session not found"}), 404

        session = app.questionnaire_sessions[session_id]
        current_index = session["current_question_index"]

        current_question_text = None
        if current_index < len(questions_structured):
            q_obj = questions_structured[current_index]
            current_question_text = q_obj.get("prompt", q_obj["text"])

        return jsonify({
            "session_id": session_id,
            "current_question_index": current_index,
            "current_question": current_question_text,
            "progress": f"{current_index + 1}/{len(questions_structured)}",
            "total_questions": len(questions_structured),
            "completed": session.get("completed", False),
            "answers_count": len(session["answers"])
        })
    except Exception as e:
        logger.error(f"Failed to get local questionnaire status: {e}")
        return jsonify({"error": f"Failed to get status: {str(e)}"}), 500


@app.route("/api/assessment_report/<session_id>", methods=["GET"])
def get_assessment_report(session_id):
    try:
        return jsonify({
            "session_id": session_id,
            "has_report": True,
            "message": "Assessment report has been generated. Please check the conversation history."
        })
    except Exception as e:
        logger.error(f"Failed to get assessment report: {e}")
        return jsonify({"error": f"Failed to get report: {str(e)}"}), 500


# ----------------- Report viewing/downloading -----------------
@app.route("/api/reports", methods=["GET"])
def list_reports():
    try:
        reports = report_manager.get_reports_list()
        stats = report_manager.get_reports_stats()
        return jsonify({"reports": reports, "stats": stats})
    except Exception as e:
        logger.error(f"Failed to get report list: {e}")
        return jsonify({"error": f"Failed to get report list: {str(e)}"}), 500


@app.route("/api/reports/content/<path:filename>", methods=["GET"])
def get_report_content_api(filename):
    try:
        content = report_manager.get_report_content(filename)
        if content is None:
            return jsonify({"error": "Report not found"}), 404
        return jsonify({"filename": filename, "content": content})
    except Exception as e:
        logger.error(f"Failed to read report: {e}")
        return jsonify({"error": f"Failed to read report: {str(e)}"}), 500


@app.route("/api/reports/download/<path:filename>", methods=["GET"])
def download_report(filename):
    try:
        reports_dir = str(report_manager.reports_dir)
        return send_from_directory(reports_dir, filename, as_attachment=True)
    except Exception as e:
        logger.error(f"Failed to download report: {e}")
        return jsonify({"error": f"Failed to download report: {str(e)}"}), 500


@app.route("/api/reports/export_pdf/<path:filename>", methods=["GET"])
def export_report_pdf(filename):
    """
    Export TXT report to PDF.
    """
    try:
        original_path = report_manager.reports_dir / filename
        if not original_path.exists():
            return jsonify({"error": "Report file not found"}), 404
        
        if not filename.endswith(".txt"):
            return jsonify({"error": "Only TXT reports can be exported to PDF"}), 400
        
        with open(original_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        name_part = filename.replace(".txt", "")
        parts = name_part.split("_")
        
        answers = {}
        lines = content.split("\n")
        in_answers_section = False
        
        for line in lines:
            line = line.strip()
            if line == "【用户信息】":
                in_answers_section = True
                continue
            elif line == "【会话信息】":
                in_answers_section = False
                break
            elif in_answers_section and ":" in line:
                key, value = line.split(":", 1)
                answers[key.strip()] = value.strip()
        
        pdf_path = report_manager.save_report_pdf(
            content,
            answers,
            parts[-1] if len(parts) > 2 else "unknown"
        )
        
        if pdf_path:
            return send_from_directory(
                report_manager.reports_dir,
                Path(pdf_path).name,
                as_attachment=True,
                mimetype="application/pdf"
            )
        else:
            return jsonify({"error": "Failed to generate PDF"}), 500
            
    except Exception as e:
        logger.error(f"Failed to export PDF: {e}")
        return jsonify({"error": f"Failed to export PDF: {str(e)}"}), 500


@app.route("/api/reports/create_pdf", methods=["POST"])
def create_report_pdf():
    """
    Directly create a PDF report from given content.
    """
    try:
        data = request.get_json(force=True)
        report_content = data.get("report_content", "")
        answers = data.get("answers", {})
        session_id = data.get("session_id", str(int(time.time() * 1000)))
        
        if not report_content:
            return jsonify({"error": "Report content cannot be empty"}), 400
        
        pdf_path = report_manager.save_report_pdf(report_content, answers, session_id)
        
        if pdf_path:
            return jsonify({
                "success": True,
                "pdf_path": pdf_path,
                "filename": Path(pdf_path).name,
                "download_url": f"/api/reports/download/{Path(pdf_path).name}"
            })
        else:
            return jsonify({"error": "Failed to generate PDF"}), 500
            
    except Exception as e:
        logger.error(f"Failed to create PDF report: {e}")
        return jsonify({"error": f"Failed to create PDF report: {str(e)}"}), 500


@app.route("/api/debug/metagpt", methods=["POST"])
def debug_metagpt():
    try:
        data = request.get_json(force=True)
        test_prompt = data.get("prompt", "Please reply briefly: test succeeded.")
        
        from metagpt_questionnaire.agents.questionnaire_designer import QuestionnaireDesignerAgent
        designer = QuestionnaireDesignerAgent()
        response = _run_async(designer.design_questionnaire({
            "source": "test",
            "prompt": test_prompt
        }))
        
        return jsonify({
            "success": True,
            "response": str(response),
            "conversation_id": "metagpt_test",
            "response_length": len(str(response)) if response else 0,
            "has_error": False
        })
    except Exception as e:
        logger.error(f"MetaGPT debug failed: {e}")
        return jsonify({"success": False, "error": str(e), "error_type": type(e).__name__}), 500


@app.route("/api/cleanup", methods=["POST", "GET"])
def cleanup():
    """
    Clean up sessions and temporary files.
    Supports both POST and GET for easier debugging.
    """
    try:
        logger.info(f"Cleanup request received. Method: {request.method}")
        
        if request.method == "POST":
            data = request.get_json(force=True) if request.is_json else {}
            session_id = data.get("session_id", "")
        else:
            session_id = request.args.get("session_id", "")
        
        logger.info(f"Starting cleanup for session: {session_id}")
        
        sessions_cleaned = 0
        if hasattr(app, "questionnaire_sessions"):
            if session_id and session_id in app.questionnaire_sessions:
                del app.questionnaire_sessions[session_id]
                sessions_cleaned += 1
                logger.info(f"Local questionnaire session cleaned: {session_id}")
            elif not session_id:
                sessions_cleaned = len(app.questionnaire_sessions)
                app.questionnaire_sessions.clear()
                logger.info(f"All local questionnaire sessions cleaned: {sessions_cleaned}")
        
        deleted_count = 0
        try:
            tts_dir = pathlib.Path("static/tts")
            if tts_dir.exists():
                tts_files = list(tts_dir.glob("*.mp3")) + list(tts_dir.glob("*.wav"))
                tts_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
                
                files_to_delete = tts_files[10:]
                for file_path in files_to_delete:
                    try:
                        file_path.unlink()
                        deleted_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to delete TTS file: {file_path}, error: {e}")
                
                if deleted_count > 0:
                    logger.info(f"Cleaned {deleted_count} old TTS files")
        except Exception as e:
            logger.warning(f"Error while cleaning TTS files: {e}")
        
        result = {
            "success": True,
            "message": "Cleanup completed",
            "session_id": session_id,
            "sessions_cleaned": sessions_cleaned,
            "files_cleaned": deleted_count,
            "method": request.method
        }
        
        logger.info(f"Cleanup finished: {result}")
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "method": request.method
        }), 500


@app.errorhandler(Exception)
def handle_exception(e):
    logger.error(f"Unhandled exception: {e}")
    return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "API endpoint not found"}), 404


def cleanup_all_resources():
    """Clean all resources on shutdown."""
    try:
        cleanup_global_thread_pool()
        
        # Cleanup TTS thread pool if available
        try:
            from huoshan_tts import cleanup_tts_thread_pool
            cleanup_tts_thread_pool()
        except ImportError:
            pass
        
        # Cleanup digital human thread pool if available
        try:
            from digital_human import cleanup_digital_human_thread_pool
            cleanup_digital_human_thread_pool()
        except ImportError:
            pass
        
        logger.info("All resources have been cleaned up")
    except Exception as e:
        logger.error(f"Error while cleaning up resources: {e}")


@app.route("/api/metagpt/agent_stats", methods=["GET"])
def metagpt_agent_stats():
    """Get persistent agent usage statistics."""
    try:
        from metagpt_questionnaire.persistent_agent_manager import get_agent_session_stats
        stats = get_agent_session_stats()
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ========= Intelligent dynamic questionnaire API =========
@app.route("/api/intelligent_questionnaire/start", methods=["POST"])
def intelligent_questionnaire_start():
    """
    Start the intelligent dynamic questionnaire:
    - Pre-generate base questions
    - Support dynamic question generation
    - Intelligent skip and dependency handling
    """
    try:
        data = request.get_json(force=True)
        session_id = data.get("session_id", str(int(time.time() * 1000)))

        clear_tts_dir(keep_names=["warmup.wav", "beep.wav"])

        if not hasattr(app, "intelligent_sessions"):
            app.intelligent_sessions = {}
        
        manager = IntelligentQuestionnaireManager()
        app.intelligent_sessions[session_id] = {
            "manager": manager,
            "start_time": time.time()
        }

        result = manager.get_next_question()
        
        if result["status"] != "next_question":
            return jsonify({"error": "Failed to get the first question"}), 500
        
        question_text = result["question"]
        
        video_url = "/static/video/human.mp4"
        video_stream_url = "/static/video/human.mp4"
        tts_url = generate_tts_audio(shorten_for_avatar(question_text), session_id)

        return jsonify({
            "session_id": session_id,
            "question": question_text,
            "question_id": result["question_id"],
            "category": result["category"],
            "question_type": result["question_type"],
            "progress": result["progress"],
            "total_questions": result["total_questions"],
            "tts_url": tts_url,
            "video_url": video_url,
            "video_stream_url": video_stream_url,
            "is_complete": False
        })
    except Exception as e:
        logger.error(f"Intelligent questionnaire start failed: {e}")
        return jsonify({"error": f"Failed to start: {str(e)}"}), 500


@app.route("/api/intelligent_questionnaire/reply", methods=["POST"])
def intelligent_questionnaire_reply():
    """
    Handle reply for the intelligent dynamic questionnaire:
    - Generate follow-up questions dynamically
    - Handle dependencies and skipping
    - Complete questionnaire and generate report
    """
    try:
        data = request.get_json(force=True)
        session_id = data["session_id"]
        answer_text = data.get("answer", "").strip()

        if not hasattr(app, "intelligent_sessions") or session_id not in app.intelligent_sessions:
            return jsonify({"error": "Session not found"}), 400

        sess = app.intelligent_sessions[session_id]
        manager = sess["manager"]
        
        if not manager:
            return jsonify({"error": "Questionnaire manager not found"}), 400
        
        result = manager.get_next_question(answer_text)
        sess["current_index"] = manager.current_question_index

        if result.get("status") == "completed":
            report_text = result["report"]
            
            try:
                answers_map = {}
                for response in manager.user_responses:
                    question_text = manager._get_question_text(response.question_id)
                    answers_map[question_text] = str(response.answer)
                
                _ = report_manager.save_report(report_text, answers_map, session_id)
                _ = report_manager.save_report_json(report_text, answers_map, session_id)
                _ = report_manager.save_report_pdf(report_text, answers_map, session_id)
                logger.info(f"✅ Intelligent questionnaire report saved: {session_id}")
            except Exception as e:
                logger.warning(f"Failed to save intelligent questionnaire report: {e}")
            
            video_url = "/static/video/human.mp4"
            video_stream_url = "/static/video/human.mp4"
            tts_url = generate_tts_audio(shorten_for_avatar(report_text), session_id)
            
            return jsonify({
                "session_id": session_id,
                "question": report_text,
                "tts_url": tts_url,
                "video_url": video_url,
                "video_stream_url": video_stream_url,
                "is_complete": True,
                "progress": f"{result['total_questions']}/{result['total_questions']}",
                "total_questions": result["total_questions"],
                "basic_questions": result["basic_questions"],
                "dynamic_questions": result["dynamic_questions"],
                "answered_questions": result["answered_questions"]
            })
        
        elif result.get("status") == "next_question":
            question_text = result["question"]
            video_url = "/static/video/human.mp4"
            video_stream_url = "/static/video/human.mp4"
            tts_url = generate_tts_audio(shorten_for_avatar(question_text), session_id)
            
            return jsonify({
                "session_id": session_id,
                "question": question_text,
                "question_id": result["question_id"],
                "category": result["category"],
                "question_type": result["question_type"],
                "progress": result["progress"],
                "total_questions": result["total_questions"],
                "tts_url": tts_url,
                "video_url": video_url,
                "video_stream_url": video_stream_url,
                "is_complete": False
            })
        
        else:
            return jsonify({"error": result.get("error", "Unknown error")}), 500

    except Exception as e:
        logger.error(f"Intelligent questionnaire reply failed: {e}")
        return jsonify({"error": f"Failed to submit: {str(e)}"}), 500


@app.route("/api/intelligent_questionnaire/stats/<session_id>", methods=["GET"])
def get_intelligent_questionnaire_stats(session_id):
    """Get intelligent questionnaire statistics."""
    try:
        if not hasattr(app, "intelligent_sessions") or session_id not in app.intelligent_sessions:
            return jsonify({"error": "Session not found"}), 404

        sess = app.intelligent_sessions[session_id]
        manager = sess["manager"]
        
        if not manager:
            return jsonify({"error": "Questionnaire manager not found"}), 400
        
        stats = manager.get_questionnaire_stats()
        return jsonify({
            "session_id": session_id,
            "stats": stats,
            "start_time": sess["start_time"]
        })
    except Exception as e:
        logger.error(f"Failed to get intelligent questionnaire stats: {e}")
        return jsonify({"error": f"Failed to get stats: {str(e)}"}), 500


if __name__ == "__main__":
    print("Starting Flask server...")
    print("Visit: http://localhost:8080")
    
    try:
        app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)
    finally:
        cleanup_all_resources()
