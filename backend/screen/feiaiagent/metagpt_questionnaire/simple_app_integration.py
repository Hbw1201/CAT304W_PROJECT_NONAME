# -*- coding: utf-8 -*-
# NOTE: website_deployment 已被移除，本文件改为使用根目录 local_questionnaire。
"""
English-only simplified app integration
Uses the hospital local English questionnaire directly (no AI questionnaire design)
"""

import logging
import time
from typing import Dict, Any
from flask import jsonify, request

# 🚀 直接使用英文问卷 local_questionnaire（你已经翻译过的那一份）
from local_questionnaire import (
    QUESTIONS_STRUCTURED,
    QUESTIONS_BY_ID,
    generate_assessment_report
)

from .simple_questionnaire_manager import SimpleQuestionnaireManager

logger = logging.getLogger(__name__)

# Global session managers
_questionnaire_managers: Dict[str, SimpleQuestionnaireManager] = {}

def setup_simple_questionnaire_routes(app, _run_async, generate_tts_audio,
                                      shorten_for_avatar, report_manager):
    """Setup simplified questionnaire endpoints"""

    # ---------------------------------------------------------
    # Start the questionnaire
    # ---------------------------------------------------------
    @app.route("/api/simple_questionnaire/start", methods=["POST"])
    def simple_questionnaire_start():
        try:
            data = request.get_json(force=True)
            session_id = data.get("session_id", str(int(time.time() * 1000)))

            logger.info(f"🚀 Start questionnaire session: {session_id}")

            # ⚠️ No AI design — directly use English local_questionnaire.py
            manager = SimpleQuestionnaireManager()
            manager.initialize_with_structured_questions(QUESTIONS_STRUCTURED)

            _questionnaire_managers[session_id] = manager

            # First question
            first = _run_async(manager.get_next_question())

            if first["status"] != "next_question":
                return jsonify({"error": "Failed to load first question"}), 500

            question_text = first["question"]
            tts_url = generate_tts_audio(shorten_for_avatar(question_text), session_id)

            return jsonify({
                "session_id": session_id,
                "question": question_text,
                "question_id": first["question_id"],
                "category": first["category"],
                "progress": first["progress"],
                "tts_url": tts_url,
                "video_url": "/static/video/human.mp4",
                "video_stream_url": "/static/video/human.mp4",
                "is_complete": False
            })

        except Exception as e:
            logger.error(f"❌ Failed to start questionnaire: {e}")
            return jsonify({"error": f"Start failed: {str(e)}"}), 500

    # ---------------------------------------------------------
    # Reply to question
    # ---------------------------------------------------------
    @app.route("/api/simple_questionnaire/reply", methods=["POST"])
    def simple_questionnaire_reply():
        try:
            data = request.get_json(force=True)
            session_id = data["session_id"]
            answer = data.get("answer", "").strip()

            manager = _questionnaire_managers.get(session_id)
            if not manager:
                return jsonify({"error": "Session not found"}), 400

            result = _run_async(manager.get_next_question(answer))

            # Invalid answer → ask user again
            if result["status"] == "invalid_answer":
                hint = f"Your answer is not specific enough. Please try again: {result['question']}"
                tts_url = generate_tts_audio(shorten_for_avatar(hint), session_id)

                return jsonify({
                    "session_id": session_id,
                    "question": hint,
                    "tts_url": tts_url,
                    "video_url": "/static/video/human.mp4",
                    "video_stream_url": "/static/video/human.mp4",
                    "is_complete": False,
                    "invalid_answer": True,
                    "invalid_reason": result["error"],
                    "retry": True
                })

            # Questionnaire completed → generate report
            if result["status"] == "completed":
                report_text = result["report"]

                # Generate TTS
                tts_url = generate_tts_audio(shorten_for_avatar(report_text), session_id)

                # Save report
                try:
                    answers_map = manager.get_answers_map()
                    report_manager.save_report(report_text, answers_map, session_id)
                    report_manager.save_report_json(report_text, answers_map, session_id)
                except Exception as e:
                    logger.warning(f"⚠️ Failed to save report: {e}")

                return jsonify({
                    "session_id": session_id,
                    "question": report_text,
                    "tts_url": tts_url,
                    "video_url": "/static/video/human.mp4",
                    "video_stream_url": "/static/video/human.mp4",
                    "is_complete": True,
                    "progress": f"{result['total_questions']}/{result['total_questions']}",
                    "total_questions": result["total_questions"]
                })

            # Normal next question
            question_text = result["question"]
            tts_url = generate_tts_audio(shorten_for_avatar(question_text), session_id)

            return jsonify({
                "session_id": session_id,
                "question": question_text,
                "question_id": result["question_id"],
                "category": result["category"],
                "progress": result["progress"],
                "tts_url": tts_url,
                "video_url": "/static/video/human.mp4",
                "video_stream_url": "/static/video/human.mp4",
                "is_complete": False
            })

        except Exception as e:
            logger.error(f"❌ Reply failed: {e}")
            return jsonify({"error": f"Reply failed: {str(e)}"}), 500

    # ---------------------------------------------------------
    # Progress
    # ---------------------------------------------------------
    @app.route("/api/simple_questionnaire/progress", methods=["GET"])
    def simple_questionnaire_progress():
        try:
            session_id = request.args.get("session_id")
            manager = _questionnaire_managers.get(session_id)

            if not manager:
                return jsonify({"error": "Session not found"}), 404

            return jsonify({
                "status": "success",
                "progress": manager.get_progress()
            })

        except Exception as e:
            logger.error(f"❌ Progress failed: {e}")
            return jsonify({"error": str(e)}), 500

    # ---------------------------------------------------------
    # Reset session
    # ---------------------------------------------------------
    @app.route("/api/simple_questionnaire/reset", methods=["POST"])
    def simple_questionnaire_reset():
        try:
            session_id = request.get_json(force=True).get("session_id")
            manager = _questionnaire_managers.get(session_id)

            if manager:
                manager.reset_session()
                return jsonify({"status": "success", "message": "Session reset"})

            return jsonify({"error": "Session not found"}), 404

        except Exception as e:
            logger.error(f"❌ Reset failed: {e}")
            return jsonify({"error": str(e)}), 500

    logger.info("✅ Simple questionnaire routes ready")
