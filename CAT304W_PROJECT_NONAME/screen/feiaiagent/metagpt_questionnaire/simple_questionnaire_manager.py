# -*- coding: utf-8 -*-
"""
Simplified intelligent questionnaire manager for MetaGPT conversational flows.
"""

import logging
import re
from typing import Dict, Any, List, Optional, Tuple, Set
from datetime import datetime
from types import SimpleNamespace

from .models.questionnaire import UserResponse, Question, Questionnaire
from .agents.base_agent import agent_registry
from .persistent_agent_manager import process_with_persistent_agent

logger = logging.getLogger(__name__)
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")
_SMOKING_HISTORY_IDS = {"smoking_history", "smoking"}
_SMOKING_FREQ_IDS = {"smoking_freq", "daily_cigarettes"}
_SMOKING_YEARS_IDS = {"smoking_years"}
_SMOKING_QUIT_IDS = {"smoking_quit"}
_SMOKING_QUIT_YEARS_IDS = {"smoking_quit_years"}


class SimpleQuestionnaireManager:
    """Simplified questionnaire manager that coordinates multiple MetaGPT agents."""
    
    def __init__(self):
        self.questionnaire: Optional[Questionnaire] = None
        self.current_question_index: int = 0
        self.answered_questions: List[UserResponse] = []
        self.conversation_history: List[Dict[str, Any]] = []
        self.is_completed: bool = False
        self.structured_answers: Dict[str, Any] = {}
        self.pending_followup_ids: List[str] = []

        # Connected MetaGPT agents (optional)
        self.answer_validator = agent_registry.get_agent("答案审核专家")
        self.question_selector = agent_registry.get_agent("智能问题选择专家")
        self.risk_assessor = agent_registry.get_agent("风险评估专家")
        self.data_analyzer = agent_registry.get_agent("数据分析专家")
        self.report_generator = agent_registry.get_agent("报告生成专家")
        self.conversational_agent = agent_registry.get_agent("Dr. Aiden")
    
    def initialize_questionnaire(self, questionnaire: Questionnaire) -> bool:
        """Initialize questionnaire state."""
        try:
            self.questionnaire = questionnaire
            self.current_question_index = 0
            self.answered_questions.clear()
            self.conversation_history.clear()
            self.is_completed = False
            self.structured_answers.clear()
            self.pending_followup_ids.clear()
            logger.info(f"Questionnaire initialized with {len(questionnaire.questions)} questions.")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize questionnaire: {e}")
            return False
    
    async def get_next_question(self, user_answer: Optional[str] = None) -> Dict[str, Any]:
        """Return the next conversational question (always English for the user)."""
        try:
            if not self.questionnaire:
                raise RuntimeError("Questionnaire has not been initialized.")

            if user_answer and self.current_question_index < len(self.questionnaire.questions):
                current_question = self.questionnaire.questions[self.current_question_index]

                validation_result = await self._validate_answer_with_agent(user_answer, current_question)

                if validation_result.get("detected"):
                    return await self._handle_keyword_detection(validation_result, current_question)

                if validation_result.get("redo"):
                    return await self._handle_redo_request(validation_result, current_question)

                if validation_result.get("skip"):
                    return await self._handle_skip_request(validation_result, current_question)

                if not validation_result.get("valid", True):
                    display_question = await self._optimize_question_text(current_question)
                    return {
                        "status": "invalid_answer",
                        "question": display_question,
                        "question_id": current_question.id,
                        "category": current_question.category,
                        "question_type": getattr(current_question, "type", getattr(current_question, "question_type", "text")),
                        "progress": f"{self.current_question_index + 1}/{len(self.questionnaire.questions)}",
                        "error": validation_result.get("reason", "Your answer is too vague."),
                        "suggestion": validation_result.get(
                            "suggestion",
                            "Please provide a more specific and detailed answer."
                        ),
                        "retry": True
                    }

                standardized_answer = user_answer.strip()
                if current_question.id in _SMOKING_HISTORY_IDS:
                    smoking_info = self._normalize_smoking_response(user_answer)
                    status = smoking_info.get("status")
                    if status in {"current", "former"}:
                        standardized_answer = "yes"
                    elif status == "never":
                        standardized_answer = "no"
                    else:
                        normalized = self._normalize_yes_no(user_answer)
                        if normalized is True:
                            standardized_answer = "yes"
                        elif normalized is False:
                            standardized_answer = "no"
                        else:
                            standardized_answer = "unknown"
                    self._merge_smoking_details(smoking_info)
                    self._update_smoking_followup_queue()
                elif current_question.id in _SMOKING_QUIT_IDS:
                    normalized = self._normalize_yes_no(user_answer)
                    if normalized is True:
                        standardized_answer = "yes"
                        self._merge_smoking_details({
                            "status": "former",
                            "status_source": "explicit",
                            "raw": user_answer
                        })
                    elif normalized is False:
                        standardized_answer = "no"
                        self._merge_smoking_details({
                            "status": "current",
                            "status_source": "explicit",
                            "raw": user_answer
                        })
                    else:
                        standardized_answer = "unknown"
                    self._merge_smoking_details(self._extract_smoking_details(user_answer))
                    self._update_smoking_followup_queue()
                elif current_question.id in _SMOKING_FREQ_IDS:
                    details = self._extract_smoking_details(user_answer)
                    if details.get("cigarettes_per_day") is None and details.get("packs_per_day") is None:
                        numeric_value = self._parse_numeric_answer(user_answer)
                        if numeric_value is not None:
                            details["cigarettes_per_day"] = numeric_value
                    self._merge_smoking_details(details)
                    self._update_smoking_followup_queue()
                elif current_question.id in _SMOKING_YEARS_IDS:
                    details = self._extract_smoking_details(user_answer)
                    if details.get("years_smoked") is None:
                        numeric_value = self._parse_numeric_answer(user_answer)
                        if numeric_value is not None:
                            details["years_smoked"] = numeric_value
                    self._merge_smoking_details(details)
                    self._update_smoking_followup_queue()
                elif current_question.id in _SMOKING_QUIT_YEARS_IDS:
                    details = self._extract_smoking_details(user_answer)
                    if details.get("years_since_quit") is None and details.get("quit_year") is None:
                        numeric_value = self._parse_numeric_answer(user_answer)
                        if numeric_value is not None:
                            if numeric_value >= 1900:
                                details["quit_year"] = int(numeric_value)
                            else:
                                details["years_since_quit"] = numeric_value
                    if details.get("years_since_quit") is not None or details.get("quit_year") is not None:
                        details.setdefault("status", "former")
                        details.setdefault("status_source", "explicit")
                    self._merge_smoking_details(details)
                    self._update_smoking_followup_queue()
                elif current_question.id in {
                    "passive_smoking",
                    "kitchen_fumes",
                    "occupation_exposure",
                    "family_cancer",
                    "recent_symptoms"
                }:
                    standardized_answer = await self._standardize_yes_no_answer(current_question, user_answer)

                self.answered_questions.append(UserResponse(
                    question_id=current_question.id,
                    answer=standardized_answer
                ))

                self.conversation_history.append({
                    "question": self._get_display_text(current_question),
                    "answer": user_answer.strip(),
                    "standardized_answer": standardized_answer,
                    "timestamp": datetime.now().isoformat()
                })

            return await self._prepare_next_question_response()

        except Exception as e:
            logger.error(f"Failed to fetch next question: {e}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    async def _validate_answer_with_agent(self, answer: str, question: Question) -> Dict[str, Any]:
        """Validate the user's answer via the answer validator agent."""
        try:
            validator = self.answer_validator
            if validator:
                result = await validator.run(
                    user_answer=answer,
                    question_text=question.text,
                    current_index=self.current_question_index,
                    total_questions=len(self.questionnaire.questions)
                )
                logger.info(
                    f"🔍 AnswerValidatorAgent: answer valid={result.get('valid', True)} "
                    f"redo={result.get('redo', False)} skip={result.get('skip', False)}"
                )
                return result

            is_valid, msg = self._validate_answer(answer, question)
            return {"valid": is_valid, "reason": msg if not is_valid else "Answer accepted."}
        except Exception as e:
            logger.warning(f"Warning: answer validator failed, falling back to basic validation. {e}")
            is_valid, msg = self._validate_answer(answer, question)
            return {"valid": is_valid, "reason": msg if not is_valid else "Validator unavailable, basic checks passed."}

    async def _handle_keyword_detection(self, validation_result: Dict[str, Any], current_question: Question) -> Dict[str, Any]:
        """处理关键词检测结果"""
        intent_type = validation_result.get("intent_type")
        target_index = validation_result.get("target_index", self.current_question_index)
        message = validation_result.get("message", "Okay, let's restart from this question.")
        
        # 处理清空答案的逻辑
        if validation_result.get("clear_all_answers"):
            # 清空所有答案
            self.answered_questions.clear()
            self.conversation_history.clear()
            self.current_question_index = 0
            logger.info("🔄 已清空所有答案，重新开始问卷")
        elif validation_result.get("clear_previous_answer"):
            # 清空指定问题的答案
            self._clear_answer_at_index(target_index)
            self.current_question_index = target_index
            logger.info(f"🔄 已清空第{target_index + 1}题的答案")
        
        # 获取目标问题
        target_question = self.questionnaire.questions[target_index]
        optimized_question = await self._optimize_question_text(target_question)
        
        return {
            "status": "redo_question",
            "question": f"{message}\n\n{optimized_question}",
            "question_id": target_question.id,
            "category": target_question.category,
            "question_type": getattr(target_question, "type", getattr(target_question, "question_type", "text")),
            "progress": f"{target_index + 1}/{len(self.questionnaire.questions)}",
            "is_complete": False,
            "redo": True,
            "target_index": target_index,
            "intent_type": intent_type
        }
    
    async def _handle_redo_request(self, validation_result: Dict[str, Any], current_question: Question) -> Dict[str, Any]:
        """处理重新回答请求"""
        target_index = validation_result.get("target_index", self.current_question_index)
        message = validation_result.get("message", "Okay, let's answer this question again.")
        
        # 清空指定问题的答案
        self._clear_answer_at_index(target_index)
        self.current_question_index = target_index
        
        # 获取目标问题
        target_question = self.questionnaire.questions[target_index]
        optimized_question = await self._optimize_question_text(target_question)
        
        return {
            "status": "redo_question",
            "question": f"{message}\n\n{optimized_question}",
            "question_id": target_question.id,
            "category": target_question.category,
            "question_type": getattr(target_question, "type", getattr(target_question, "question_type", "text")),
            "progress": f"{target_index + 1}/{len(self.questionnaire.questions)}",
            "is_complete": False,
            "redo": True,
            "target_index": target_index
        }
    
    async def _handle_skip_request(self, validation_result: Dict[str, Any], current_question: Question) -> Dict[str, Any]:
        """处理跳过请求"""
        self.current_question_index = validation_result.get("target_index", self.current_question_index)
        message = validation_result.get("message", "Okay, we will skip this question.")

        response = await self._prepare_next_question_response()
        if response.get("status") == "next_question":
            response["question"] = f"{message}\n\n{response['question']}"
            response["skip"] = True
        return response

    async def _prepare_next_question_response(self) -> Dict[str, Any]:
        """Build the candidate pool, delegate selection to agents, and return the next question payload."""
        if not self.questionnaire:
            raise RuntimeError("Questionnaire has not been initialized.")

        forced_followup = self._next_pending_followup()
        if forced_followup:
            question_index = self._find_question_index_by_id(forced_followup.id)
            if question_index is None:
                question_index = len(self.questionnaire.questions) - 1
            self.current_question_index = question_index

            optimized_question = await self._optimize_question_text(forced_followup)
            logger.info(f"Next question forced by smoking follow-up: {forced_followup.id}")
            return {
                "status": "next_question",
                "question": optimized_question,
                "question_id": forced_followup.id,
                "category": forced_followup.category,
                "progress": f"{self.current_question_index + 1}/{len(self.questionnaire.questions)}",
                "question_type": getattr(forced_followup, "type", getattr(forced_followup, "question_type", "text")),
                "total_questions": len(self.questionnaire.questions),
                "is_complete": False
            }

        candidates, combined_answers, inferred_facts = self._build_candidate_context()
        if not candidates:
            logger.info("No remaining candidate questions. Completing questionnaire.")
            return await self._complete_questionnaire()

        next_question, source = await self._select_next_question(
            candidates,
            combined_answers,
            inferred_facts
        )

        if not next_question:
            logger.info("All selectors returned no question. Completing questionnaire.")
            return await self._complete_questionnaire()

        question_index = self._find_question_index_by_id(next_question.id)
        if question_index is None:
            question_index = len(self.questionnaire.questions) - 1
        self.current_question_index = question_index

        optimized_question = await self._optimize_question_text(next_question)
        logger.info(
            f"Next question selected via {source}: {next_question.id} "
            f"(candidate_count={len(candidates)})."
        )

        return {
            "status": "next_question",
            "question": optimized_question,
            "question_id": next_question.id,
            "category": next_question.category,
            "progress": f"{self.current_question_index + 1}/{len(self.questionnaire.questions)}",
            "question_type": getattr(next_question, "type", getattr(next_question, "question_type", "text")),
            "total_questions": len(self.questionnaire.questions),
            "is_complete": False
        }

    def _build_candidate_context(
        self,
        answers_dict: Optional[Dict[str, Any]] = None
    ) -> Tuple[List[Question], Dict[str, Any], Dict[str, Any]]:
        """Construct the candidate pool along with the combined answers and inferred facts."""
        if not self.questionnaire:
            return [], {}, {}

        normalized_history = self._normalize_answer_history()
        base_answers = answers_dict or {
            response.question_id: response.answer
            for response in normalized_history
            if response.question_id
        }

        inferred_facts = self._infer_additional_facts(normalized_history)
        combined_answers = {**base_answers, **inferred_facts}
        skip_ids = self._gather_skip_ids(combined_answers)
        answered_ids = {resp.question_id for resp in normalized_history if resp.question_id}

        candidates: List[Question] = []
        for question in self.questionnaire.questions:
            if question.id in answered_ids:
                continue
            if question.id in skip_ids:
                logger.info(f"Candidate {question.id} removed by skip logic.")
                continue
            if not self._dependencies_met(question, combined_answers):
                logger.info(f"Candidate {question.id} blocked by unmet dependencies.")
                continue
            candidates.append(question)

        logger.info(
            f"Candidate pool size: {len(candidates)} "
            f"(answered={len(answered_ids)}, skipped={len(skip_ids)})."
        )
        return candidates, combined_answers, inferred_facts

    def _normalize_answer_history(self, answers: Optional[List[Any]] = None) -> List[UserResponse]:
        """Ensure answered questions are stored as UserResponse objects."""
        normalized: List[UserResponse] = []
        source = answers if answers is not None else self.answered_questions

        for response in source:
            if isinstance(response, UserResponse):
                normalized.append(response)
                continue
            if isinstance(response, dict):
                question_id = (
                    response.get("question_id")
                    or response.get("id")
                    or response.get("questionId")
                )
                if not question_id:
                    continue
                normalized.append(
                    UserResponse(
                        question_id=str(question_id),
                        answer=response.get("answer") or response.get("value") or ""
                    )
                )
        return normalized

    def _infer_additional_facts(self, history: List[UserResponse]) -> Dict[str, Any]:
        """Use the conversational agent to infer structured facts from history."""
        if not self.conversational_agent or not hasattr(self.conversational_agent, "_infer_facts_from_history"):
            return {}
        try:
            facts = self.conversational_agent._infer_facts_from_history(history, self.questionnaire)  # type: ignore[attr-defined]
            logger.info(f"Inferred facts: {list(facts.keys()) if facts else []}")
            return facts or {}
        except Exception as exc:
            logger.warning(f"Conversational agent failed to infer facts: {exc}")
            return {}

    def _gather_skip_ids(self, answers: Dict[str, Any]) -> Set[str]:
        """Combine skip logic from all sources."""
        skip_ids: Set[str] = set()
        if self.conversational_agent and hasattr(self.conversational_agent, "_get_skip_ids"):
            try:
                skip_ids.update(self.conversational_agent._get_skip_ids(answers))  # type: ignore[attr-defined]
            except Exception as exc:
                logger.warning(f"Conversational agent skip logic failed: {exc}")
        skip_ids.update(self._get_skip_ids(answers))
        return skip_ids

    def _dependencies_met(self, question: Question, answers_lookup: Dict[str, Any]) -> bool:
        """Check both Question.depends_on and validation_rules-based dependencies."""
        dependency = getattr(question, "depends_on", None)
        if not dependency and question.validation_rules:
            dependency = question.validation_rules.get("depends_on")

        if not dependency:
            return True

        if isinstance(dependency, str):
            dep_id = dependency
            expected_values = []
        elif isinstance(dependency, dict):
            dep_id = dependency.get("id")
            expected_values = [
                str(value) for value in dependency.get("values", []) if value is not None
            ]
            if dependency.get("value") is not None:
                expected_values.append(str(dependency.get("value")))
        else:
            return True

        if not dep_id:
            return True

        actual_answer = str(answers_lookup.get(dep_id, "")).strip()

        if dep_id in _SMOKING_HISTORY_IDS:
            smoking = self.structured_answers.get("smoking") or {}
            status = smoking.get("status")
            if status in {"current", "former"}:
                actual_answer = "yes"
            elif status == "never":
                actual_answer = "no"
            elif actual_answer.lower() in {"current", "former"}:
                actual_answer = "yes"
            elif actual_answer.lower() == "never":
                actual_answer = "no"

        if not expected_values:
            return bool(actual_answer)

        actual_norm = actual_answer.lower()
        expected_norm = {value.lower() for value in expected_values}

        if actual_norm in {"1", "yes", "y", "true"}:
            actual_norm = "yes"
        elif actual_norm in {"2", "no", "n", "false", "never"}:
            actual_norm = "no"

        if actual_norm in expected_norm:
            return True

        if actual_norm == "yes" and expected_norm.intersection(
            {"smoke", "smoked", "smoker", "former smoker", "used to smoke", "current smoker"}
        ):
            return True

        if actual_norm == "no" and expected_norm.intersection({"never", "non-smoker", "non smoker"}):
            return True

        return False

    async def _select_next_question(
        self,
        candidates: List[Question],
        answers_dict: Dict[str, Any],
        inferred_facts: Dict[str, Any]
    ) -> Tuple[Optional[Question], str]:
        """Delegate question selection to the appropriate agents with fallbacks."""
        selector_choice = await self._select_with_question_selector(candidates, answers_dict)
        if selector_choice:
            return selector_choice, "IntelligentQuestionSelectorAgent"

        conversational_choice = await self._select_with_conversational_agent(
            candidates,
            inferred_facts
        )
        if conversational_choice:
            return conversational_choice, "ConversationalInterviewerAgent"

        logger.info("Falling back to sequential candidate order.")
        return (candidates[0] if candidates else None, "sequential_fallback")

    async def _select_with_question_selector(
        self,
        candidates: List[Question],
        answers_dict: Dict[str, Any]
    ) -> Optional[Question]:
        """Use the intelligent selector agent if available."""
        if not self.question_selector or not self.questionnaire:
            logger.warning("🎯 IntelligentQuestionSelectorAgent not available; using fallback selection")
            return None

        payload = {
            "answered_questions": self._build_selector_answer_context(),
            "available_questions": candidates,
            "conversation_history": self.conversation_history,
            "user_profile": {"current_index": self.current_question_index},
            "answers": answers_dict
        }

        try:
            if hasattr(self.question_selector, "process"):
                selection_result = await self.question_selector.process(payload)
            else:
                selection_result = await self.question_selector.run(payload)
            selected_question = self._extract_selector_question(selection_result, candidates)
            if selected_question:
                logger.info(
                    f"🎯 IntelligentQuestionSelectorAgent: selected question {selected_question.id} "
                    f"(candidates={len(candidates)})"
                )
                return selected_question
        except Exception as exc:
            logger.warning(f"Question selector failed; falling back. {exc}")
        return None

    async def _select_with_conversational_agent(
        self,
        candidates: List[Question],
        inferred_facts: Dict[str, Any]
    ) -> Optional[Question]:
        """Fallback selector that leverages the conversational interviewer."""
        if not self.conversational_agent:
            return None
        selector_fn = getattr(self.conversational_agent, "_determine_next_question", None)
        if not selector_fn:
            return None

        history = self._normalize_answer_history()
        try:
            question = await selector_fn(history, inferred_facts, candidates, self.questionnaire)  # type: ignore[misc]
            if question:
                logger.info(f"💬 ConversationalInterviewerAgent: reformulated next question {question.id}")
            return question
        except Exception as exc:
            logger.warning(f"Conversational interviewer selection failed: {exc}")
            return None

    def _extract_selector_question(
        self,
        selection_result: Optional[Dict[str, Any]],
        candidates: List[Question]
    ) -> Optional[Question]:
        """Normalize the selector agent's output into a Question object."""
        if not selection_result:
            return None

        candidate_map = {question.id: question for question in candidates}

        selected_question = selection_result.get("selected_question")
        if isinstance(selected_question, Question):
            return selected_question
        if isinstance(selected_question, dict):
            qid = selected_question.get("id")
            if qid and qid in candidate_map:
                return candidate_map[qid]

        next_question_id = (
            selection_result.get("next_question_id")
            or selection_result.get("question_id")
        )
        if next_question_id and next_question_id in candidate_map:
            return candidate_map[next_question_id]

        if selection_result.get("status") == "completed":
            return None

        return candidates[0] if candidates else None
    
    def _clear_answer_at_index(self, target_index: int):
        """Remove the stored answer for a specific question index."""
        if not self.questionnaire:
            return
        
        # 从已回答问题列表中移除
        target_id = self.questionnaire.questions[target_index].id
        self.answered_questions = [
            response for response in self.answered_questions 
            if response.question_id != target_id
        ]
        
        # 从对话历史中移除
        target_text = self.questionnaire.questions[target_index].text
        self.conversation_history = [
            history for history in self.conversation_history
            if history.get("question") != target_text
        ]
        
        logger.info(f"Cleared answer for question #{target_index + 1}.")
    
    def _validate_answer(self, answer: str, question: Question) -> Tuple[bool, str]:
        """Basic local validation when the answer validator agent is unavailable."""
        if not answer or answer.strip() == "":
            return False, "Please provide your answer."

        if len(answer.strip()) < 2:
            return False, "Your answer is too short, please be more specific."

        return True, "Answer accepted."

    async def _optimize_question_text(self, question: Question) -> str:
        """Return the English display text for the question (prefer help_text)."""
        base_text = self._get_display_text(question)

        if not self.conversational_agent:
            return base_text

        try:
            result = await self.conversational_agent.process({
                "question": base_text,
                "conversation_history": self.conversation_history,
                "question_category": question.category
            })
            optimized = result.get("optimized_question")
            if optimized:
                if optimized != base_text:
                    logger.info(f"💬 ConversationalInterviewerAgent: reformulated question {question.id}")
                return optimized
        except Exception as e:
            logger.warning(f"Conversational agent failed to optimize question '{question.id}': {e}")

        return base_text

    async def _complete_questionnaire(self) -> Dict[str, Any]:
        """Finalize the questionnaire and generate the English report."""
        try:
            self.is_completed = True
            report = await self._generate_report()
            return {
                "status": "completed",
                "is_complete": True,
                "report": report,
                "total_questions": len(self.questionnaire.questions) if self.questionnaire else 0,
                "answered_questions": len(self.answered_questions)
            }
        except Exception as e:
            logger.error(f"Failed to complete questionnaire: {e}")
            return {
                "status": "completed",
                "is_complete": True,
                "error": str(e),
                "report": "Report generation failed. Please try again later."
            }

    async def _generate_report(self) -> str:
        """Use connected agents to generate a detailed English report."""
        try:
            if not self.questionnaire:
                return self._generate_simple_report()

            smoking_summary = ""
            analysis_data: Dict[str, Any] = {
                "questionnaire": self.questionnaire,
                "answered_questions": self.answered_questions,
                "conversation_history": self.conversation_history,
                "responses": self.answered_questions,
                "structured_answers": self.structured_answers
            }
            smoking_summary = self._build_smoking_summary()
            if smoking_summary:
                analysis_data["smoking_summary"] = smoking_summary

            if self.data_analyzer:
                try:
                    data_analysis = await self.data_analyzer.process({
                        "responses": self.answered_questions,
                        "questionnaire": self.questionnaire,
                        "analysis_type": "conversational"
                    })
                    analysis_data["data_analysis"] = data_analysis
                    feature_count = len(data_analysis.get("insights", [])) if isinstance(data_analysis, dict) else 0
                    logger.info(f"📊 DataAnalyzerAgent: extracted {feature_count} risk features")
                except Exception as analyzer_error:
                    logger.warning(f"Data analyzer failed: {analyzer_error}")

            if self.risk_assessor:
                try:
                    risk_assessment = await self.risk_assessor.process({
                        "responses": self.answered_questions,
                        "questionnaire": self.questionnaire,
                        "user_profile": {"session_id": "metagpt_conversational"}
                    })
                    if hasattr(risk_assessment, "to_dict"):
                        analysis_data["risk_assessment"] = risk_assessment.to_dict()  # type: ignore[arg-type]
                        overall = analysis_data["risk_assessment"].get("overall_risk")
                    else:
                        analysis_data["risk_assessment"] = risk_assessment
                        overall = getattr(risk_assessment, "overall_risk", None)
                    if overall:
                        logger.info(f"⚠️ RiskAssessorAgent: overall risk = {overall}")
                except Exception as assess_error:
                    logger.warning(f"Risk assessor failed: {assess_error}")

            if self.report_generator:
                try:
                    result = await self.report_generator.process(analysis_data)
                    report_text = self._extract_report_text(result)
                    if report_text and not self._contains_cjk(report_text):
                        logger.info(f"📝 ReportGeneratorAgent: report generated ({len(report_text)} chars)")
                        if smoking_summary:
                            report_text = f"{report_text}\n\n{smoking_summary}"
                        return report_text
                except Exception as generator_error:
                    logger.warning(f"Report generator failed, fallback to simple report: {generator_error}")

            report_text = self._generate_simple_report()
            if smoking_summary:
                report_text = f"{report_text}\n\n{smoking_summary}"
            return report_text
        except Exception as e:
            logger.error(f"Report generation failed: {e}")
            report_text = self._generate_simple_report()
            if smoking_summary:
                report_text = f"{report_text}\n\n{smoking_summary}"
            return report_text

    def _generate_simple_report(self) -> str:
        """Fallback English report when smart agents are unavailable."""
        report = "Lung Cancer Early Screening Report\n\n" + "=" * 50 + "\n\n"
        
        report += f"Completion Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        if self.questionnaire:
            report += f"Total Questions: {len(self.questionnaire.questions)}\n"
        report += f"Answered: {len(self.answered_questions)}\n\n"
        
        report += "User Answers\n"
        if self.questionnaire:
            for i, response in enumerate(self.answered_questions, 1):
                question_text = "Unknown Question"
                for q in self.questionnaire.questions:
                    if q.id == response.question_id:
                        question_text = q.text
                        break
                
                report += f"{i}. {question_text}\n"
                report += f"   Answer: {response.answer}\n\n"
        
        report += "Basic Suggestions\n"
        report += "1. Regular health check-ups are recommended.\n"
        report += "2. Maintain a healthy lifestyle.\n"
        report += "3. Seek medical attention promptly if symptoms worsen.\n"
        
        return report

    def _extract_report_text(self, result: Any) -> str:
        """Normalize different report generator outputs into a string."""
        if result is None:
            return ""
        if isinstance(result, dict):
            return (
                result.get("report_content")
                or result.get("report")
                or result.get("content")
                or ""
            )
        if hasattr(result, "content"):
            return getattr(result, "content")
        if isinstance(result, str):
            return result
        return str(result)

    def _contains_cjk(self, text: str) -> bool:
        """Detect whether the text contains CJK characters."""
        if not text:
            return False
        return bool(_CJK_PATTERN.search(text))
    def get_progress(self) -> Dict[str, Any]:
        """Return user-friendly progress details."""
        if not self.questionnaire:
            return {
                "current_index": 0,
                "total_questions": 0,
                "answered_count": len(self.answered_questions),
                "progress_percentage": 0
            }
        
        return {
            "current_index": self.current_question_index,
            "total_questions": len(self.questionnaire.questions),
            "answered_count": len(self.answered_questions),
            "progress_percentage": (
                self.current_question_index / len(self.questionnaire.questions) * 100
                if self.questionnaire and len(self.questionnaire.questions) > 0 else 0
            )
        }
    
    def reset_session(self):
        """Reset in-memory state for a new session."""
        self.answered_questions.clear()
        self.current_question_index = 0
        self.conversation_history.clear()
        self.is_completed = False
        logger.info("Session has been reset.")
    
    def _build_answer_map(self) -> Dict[str, Any]:
        """Return a mapping of question_id -> latest standardized answer."""
        normalized = self._normalize_answer_history()
        return {
            response.question_id: response.answer
            for response in normalized
            if response.question_id
        }

    def _build_selector_answer_context(self) -> List[SimpleNamespace]:
        """Convert answered questions to lightweight namespace objects for the selector agent."""
        if not self.questionnaire:
            return []

        question_map = {q.id: q for q in self.questionnaire.questions}
        selector_answers: List[SimpleNamespace] = []
        for response in self._normalize_answer_history():
            question = question_map.get(response.question_id)
            if not question:
                continue
            selector_answers.append(
                SimpleNamespace(
                    id=question.id,
                    text=self._get_display_text(question),
                    category=question.category,
                    answer=str(response.answer),
                    question=question,
                    user_answer=str(response.answer)
                )
            )
        return selector_answers

    def _find_question_index_by_id(self, question_id: str) -> Optional[int]:
        """Find a question index by id."""
        if not self.questionnaire:
            return None
        for idx, question in enumerate(self.questionnaire.questions):
            if question.id == question_id:
                return idx
        return None
    
    def _get_skip_ids(self, answers: Dict[str, Any]) -> set:
        """
        返回基于已知答案应该跳过的问题ID集合
        内部统一使用标准化后答案（"是"/"否"），但也兼容英文/数字。
        """
        skip_ids = set()
        
        # 吸烟史相关跳题逻辑
        # 如果用户没有吸烟史，跳过所有吸烟史相关详细问题
        smoking_ans = str(answers.get('smoking_history', "")).strip()
        smoking_status = (self.structured_answers.get("smoking") or {}).get("status")
        if smoking_status == "never" or smoking_ans.lower() == "no" or self._is_negative_answer(smoking_ans):
            skip_ids.update([
                'smoking_freq',           # 吸烟频率
                'smoking_years',          # 累计吸烟年数
                'smoking_quit',           # 目前是否戒烟
                'smoking_quit_years'      # 戒烟年数
            ])
        
        # 被动吸烟相关跳题逻辑
        passive_ans = str(answers.get('passive_smoking', "")).strip()
        passive_flag = self._normalize_yes_no(passive_ans)
        if passive_flag is False or passive_ans.lower() == "no" or self._is_negative_answer(passive_ans):
            skip_ids.update([
                'passive_smoking_freq',   # 被动吸烟频率
                'passive_smoking_years'   # 累计被动吸烟年数
            ])
        
        # 厨房油烟相关跳题逻辑
        kitchen_ans = str(answers.get('kitchen_fumes', "")).strip()
        kitchen_flag = self._normalize_yes_no(kitchen_ans)
        if kitchen_flag is False or kitchen_ans.lower() == "no" or self._is_negative_answer(kitchen_ans):
            skip_ids.update([
                'kitchen_fumes_years'     # 累计厨房油烟接触年数
            ])
        
        # 职业致癌物质接触相关跳题逻辑
        exposure_ans = str(answers.get('occupation_exposure', "")).strip()
        exposure_flag = self._normalize_yes_no(exposure_ans)
        if exposure_flag is False or exposure_ans.lower() == "no" or self._is_negative_answer(exposure_ans):
            skip_ids.update([
                'occupation_exposure_details'  # 致癌物类型及累计接触年数
            ])
        
        return skip_ids
    
    def _is_negative_answer(self, answer: str) -> bool:
        """Return True if the answer expresses a negative response."""
        if not answer:
            return False

        text = str(answer).strip().lower()
        if text in {"2", "0", "no", "n", "nope", "nah", "false", "never"}:
            return True

        if re.search(r"\b(no|not|never|none|don't|do not|doesn't|does not|cannot|can't|no longer)\b", text):
            return True

        if re.search(r"\u4e0d\u5438|\u4e0d\u62bd|\u6ca1\u5438|\u6ca1\u62bd|\u4ece\u4e0d|\u5426|\u4e0d\u4f1a", str(answer)):
            return True

        return False

    def _normalize_yes_no(self, text: str) -> Optional[bool]:
        if not text:
            return None

        raw = str(text).strip()
        lowered = raw.lower()

        if self._is_negative_answer(raw):
            return False

        if lowered in {"yes", "yeah", "yep", "yup", "y", "true", "sure", "ok", "okay", "correct", "right"}:
            return True

        if re.search(r"\b(yes|yeah|yep|yup|true|sure|correct|right|smoker|smoke|smoking)\b", lowered):
            return True

        if re.search(r"\u662f|\u6709|\u4f1a|\u5438\u70df|\u62bd\u70df|\u66fe\u7ecf|\u4ee5\u524d|\u5076\u5c14", raw):
            return True

        return None

    def _normalize_smoking_response(self, text: str) -> Dict[str, Any]:
        raw = str(text or "").strip()
        lowered = raw.lower()
        result = {
            "status": "unknown",
            "status_source": "unknown",
            "raw": raw
        }

        if not raw:
            return result

        if (
            self._is_negative_answer(raw)
            or re.search(r"\b(never|non-smoker|non smoker)\b", lowered)
            or re.search(r"\u4ece\u4e0d|\u4e0d\u5438|\u4e0d\u62bd", raw)
        ):
            result["status"] = "never"
            result["status_source"] = "explicit"
        else:
            former_patterns = r"\b(former|ex-smoker|used to|quit|stopped|no longer)\b"
            current_patterns = r"\b(current|still|now|smoker|smoke|smoking|occasionally|sometimes|daily)\b"

            if re.search(former_patterns, lowered) or re.search(r"\u6212\u70df|\u5df2\u6212|\u4ee5\u524d|\u66fe\u7ecf", raw):
                result["status"] = "former"
                result["status_source"] = "explicit"
            elif re.search(current_patterns, lowered) or re.search(r"\u73b0\u5728|\u8fd8\u5728|\u5438\u70df|\u62bd\u70df|\u5076\u5c14|\u7ecf\u5e38", raw):
                result["status"] = "current"
                result["status_source"] = "explicit"
            else:
                normalized = self._normalize_yes_no(raw)
                if normalized is True:
                    result["status"] = "current"
                    result["status_source"] = "implicit"
                elif normalized is False:
                    result["status"] = "never"
                    result["status_source"] = "explicit"

        details = self._extract_smoking_details(raw)
        result.update(details)
        return result

    def _extract_smoking_details(self, text: str) -> Dict[str, Any]:
        details: Dict[str, Any] = {}
        if not text:
            return details

        raw = str(text).strip()
        lowered = raw.lower()
        details["raw"] = raw

        pack_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:packs?|ppd)\b", lowered)
        if pack_match:
            details["packs_per_day"] = float(pack_match.group(1))

        pack_day_match = re.search(r"(\d+(?:\.\d+)?)\s*pack(?:s)?\s*/\s*day", lowered)
        if pack_day_match:
            details["packs_per_day"] = float(pack_day_match.group(1))

        cigs_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:cigarettes|cigs|cig|sticks)\b", lowered)
        if cigs_match:
            details["cigarettes_per_day"] = float(cigs_match.group(1))

        if "cigarettes_per_day" not in details:
            per_day_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:/|per)\s*day", lowered)
            if per_day_match:
                details["cigarettes_per_day"] = float(per_day_match.group(1))

        cn_cigs_match = re.search(r"(\d+(?:\.\d+)?)\s*\u652f", raw)
        if cn_cigs_match:
            details["cigarettes_per_day"] = float(cn_cigs_match.group(1))

        years_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:years|yrs)\s*(?:of\s*)?(?:smoking|smoked)", lowered)
        if years_match:
            details["years_smoked"] = float(years_match.group(1))
        else:
            for_years_match = re.search(r"\bfor\s*(\d+(?:\.\d+)?)\s*(?:years|yrs)\b", lowered)
            if for_years_match:
                details["years_smoked"] = float(for_years_match.group(1))

        quit_year_match = re.search(r"(?:quit|stopped|since)\s*(?:in|around)?\s*(\d{4})", lowered)
        if quit_year_match:
            details["quit_year"] = int(quit_year_match.group(1))

        cn_quit_year = re.search(r"\u6212\u70df.*?(\d{4})", raw)
        if cn_quit_year:
            details["quit_year"] = int(cn_quit_year.group(1))

        quit_years_match = re.search(r"(?:quit|stopped)\s*(?:about\s*)?(\d+(?:\.\d+)?)\s*(?:years|yrs)", lowered)
        if quit_years_match:
            details["years_since_quit"] = float(quit_years_match.group(1))
        else:
            quit_years_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:years|yrs)\s*(?:since|after).*quit", lowered)
            if quit_years_match:
                details["years_since_quit"] = float(quit_years_match.group(1))

        cn_quit_years = re.search(r"\u6212\u70df\s*(\d+(?:\.\d+)?)\s*\u5e74", raw)
        if cn_quit_years:
            details["years_since_quit"] = float(cn_quit_years.group(1))

        if details.get("years_smoked") is None:
            cn_years_match = re.search(r"(\d+(?:\.\d+)?)\s*\u5e74", raw)
            if cn_years_match and not re.search(r"\u6212\u70df", raw):
                details["years_smoked"] = float(cn_years_match.group(1))

        return details

    def _parse_numeric_answer(self, text: str) -> Optional[float]:
        if not text:
            return None
        raw = str(text).strip().lower()
        raw = raw.replace(",", " ")
        match = re.search(r"(\d+(?:\.\d+)?)", raw)
        if not match:
            return None
        try:
            return float(match.group(1))
        except (TypeError, ValueError):
            return None

    def _ensure_smoking_structured(self) -> Dict[str, Any]:
        smoking = self.structured_answers.get("smoking")
        if not smoking:
            smoking = {
                "status": "unknown",
                "status_source": "unknown",
                "cigarettes_per_day": None,
                "years_smoked": None,
                "quit_year": None,
                "years_since_quit": None,
                "pack_years": None,
                "raw": None
            }
            self.structured_answers["smoking"] = smoking
        return smoking

    def _merge_smoking_details(self, details: Dict[str, Any]) -> None:
        smoking = self._ensure_smoking_structured()

        for key in [
            "status",
            "status_source",
            "cigarettes_per_day",
            "years_smoked",
            "quit_year",
            "years_since_quit",
            "raw"
        ]:
            if details.get(key) is not None:
                smoking[key] = details.get(key)

        if details.get("packs_per_day") is not None:
            smoking["cigarettes_per_day"] = float(details["packs_per_day"]) * 20.0

        if smoking.get("quit_year") and not smoking.get("years_since_quit"):
            current_year = datetime.now().year
            if current_year >= smoking["quit_year"]:
                smoking["years_since_quit"] = float(current_year - smoking["quit_year"])

        smoking["pack_years"] = self._compute_pack_years(
            smoking.get("cigarettes_per_day"),
            smoking.get("years_smoked")
        )

    def _compute_pack_years(self, cigarettes_per_day: Optional[float], years_smoked: Optional[float]) -> Optional[float]:
        if cigarettes_per_day is None or years_smoked is None:
            return None
        try:
            return round((float(cigarettes_per_day) / 20.0) * float(years_smoked), 2)
        except (TypeError, ValueError):
            return None

    def _update_smoking_followup_queue(self) -> None:
        if not self.questionnaire:
            return

        smoking = self.structured_answers.get("smoking") or {}
        status = smoking.get("status")
        status_source = smoking.get("status_source")

        if status not in {"current", "former"}:
            self.pending_followup_ids = []
            return

        available_ids = {q.id for q in self.questionnaire.questions}

        queue: List[str] = []
        if "smoking_quit" in available_ids and status_source != "explicit":
            queue.append("smoking_quit")

        if smoking.get("cigarettes_per_day") is None:
            for candidate in ["smoking_freq", "daily_cigarettes"]:
                if candidate in available_ids:
                    queue.append(candidate)
                    break

        if smoking.get("years_smoked") is None and "smoking_years" in available_ids:
            queue.append("smoking_years")

        if status == "former" and smoking.get("years_since_quit") is None and smoking.get("quit_year") is None:
            if "smoking_quit_years" in available_ids:
                queue.append("smoking_quit_years")

        self.pending_followup_ids = queue

    def _next_pending_followup(self) -> Optional[Question]:
        if not self.pending_followup_ids or not self.questionnaire:
            return None

        answered_ids = {resp.question_id for resp in self._normalize_answer_history()}
        while self.pending_followup_ids:
            candidate_id = self.pending_followup_ids[0]
            if candidate_id in answered_ids:
                self.pending_followup_ids.pop(0)
                continue
            question = next((q for q in self.questionnaire.questions if q.id == candidate_id), None)
            if question:
                return question
            self.pending_followup_ids.pop(0)

        return None

    def _build_smoking_summary(self) -> str:
        smoking = self.structured_answers.get("smoking")
        if not smoking:
            return ""

        lines = ["Smoking History Summary"]
        status = smoking.get("status", "unknown")
        if status == "never":
            lines.append("- Status: Never smoker.")
        elif status == "former":
            lines.append("- Status: Former smoker.")
        elif status == "current":
            lines.append("- Status: Current smoker.")
        else:
            lines.append("- Status: Smoking history reported, current/former unclear.")

        cigs = smoking.get("cigarettes_per_day")
        years = smoking.get("years_smoked")
        if cigs is not None:
            lines.append(f"- Cigarettes per day: {cigs:.1f}.")
        if years is not None:
            lines.append(f"- Years smoked: {years:.1f}.")

        if smoking.get("quit_year") is not None:
            lines.append(f"- Quit year: {int(smoking['quit_year'])}.")
        elif smoking.get("years_since_quit") is not None:
            lines.append(f"- Years since quitting: {smoking['years_since_quit']:.1f}.")

        pack_years = smoking.get("pack_years")
        if pack_years is not None:
            lines.append(f"- Pack-years: {pack_years:.1f}.")

        missing = []
        if status in {"current", "former"}:
            if cigs is None:
                missing.append("cigarettes per day")
            if years is None:
                missing.append("years smoked")
            if status == "former" and smoking.get("quit_year") is None and smoking.get("years_since_quit") is None:
                missing.append("quit year or years since quit")
            if missing:
                lines.append(f"- Missing details: {', '.join(missing)}.")
            else:
                eligibility = self._evaluate_ldct_eligibility(smoking)
                if eligibility:
                    lines.append(f"- LDCT eligibility (USPSTF): {eligibility}.")

        return "\n".join(lines)

    def _evaluate_ldct_eligibility(self, smoking: Dict[str, Any]) -> Optional[str]:
        pack_years = smoking.get("pack_years")
        if pack_years is None:
            return None

        age = self._resolve_age_from_answers()
        status = smoking.get("status")
        if status not in {"current", "former"}:
            return "Not eligible (never smoker)"

        if age is None:
            return "Unable to determine (age missing)"

        if age < 50 or age > 80:
            return "Not eligible (age outside 50-80)"

        years_since_quit = smoking.get("years_since_quit")
        if status == "former" and years_since_quit is not None and years_since_quit > 15:
            return "Not eligible (quit > 15 years ago)"

        if pack_years >= 20:
            return "Eligible (>= 20 pack-years)"
        return "Not eligible (< 20 pack-years)"

    def _resolve_age_from_answers(self) -> Optional[int]:
        answers = self._build_answer_map()
        age_val = answers.get("age")
        birth_year_val = answers.get("birth_year")
        current_year = datetime.now().year

        try:
            if age_val is not None and str(age_val).strip():
                return int(float(age_val))
        except (TypeError, ValueError):
            pass

        try:
            if birth_year_val is not None and str(birth_year_val).strip():
                birth_year = int(float(birth_year_val))
                if 1900 <= birth_year <= current_year:
                    return current_year - birth_year
        except (TypeError, ValueError):
            pass

        return None

    async def _standardize_yes_no_answer(self, question: Question, user_answer: str) -> str:
        """Normalize a yes/no answer into "yes" or "no" (or "unknown")."""
        try:
            raw = user_answer.strip()
            normalized = self._normalize_yes_no(raw)
            if normalized is True:
                return "yes"
            if normalized is False:
                return "no"

            context = {
                "question": question.text,
                "user_answer": user_answer,
                "question_category": question.category,
                "task": "standardize_yes_no_answer",
                "instructions": (
                    "Normalize the user's answer to a clear YES or NO. "
                    "If the meaning is affirmative, return yes. "
                    "If the meaning is negative, return no. "
                    "If unclear, return unknown. "
                    "Return JSON: {\"standardized_answer\": \"yes|no|unknown\", \"reasoning\": \"...\"}"
                )
            }

            result = await process_with_persistent_agent("Dr. Aiden", context)
            ai_answer = str(result.get("standardized_answer", "unknown")).strip().lower()
            if ai_answer in {"yes", "y", "true"}:
                return "yes"
            if ai_answer in {"no", "n", "false"}:
                return "no"
            return "unknown"

        except Exception as e:
            logger.error(f"Failed to standardize yes/no answer: {e}")
            return "unknown"

    # ============================================================
    # 工具：选择输出给用户的文本（优先英文）
    # ============================================================

    def _get_display_text(self, question: Question) -> str:
        """
        返回展示给用户的问题：
        - 优先使用 question.help_text（网站/英文版本里写好的英文 prompt）
        - 如果没有 help_text，则 fallback 到 question.text
        """
        if question.help_text and isinstance(question.help_text, str):
            return question.help_text.strip()
        return question.text.strip()

    # ============================================================
    # 依赖条件解析（备用工具）
    # ============================================================

    def _parse_depends_on(self, rules: Dict[str, Any]) -> Optional[Tuple[str, str]]:
        """
        工具方法：解析 validation_rules 中的 depends_on
        返回 (依赖问题ID, 需要的答案)
        """
        if not rules or "depends_on" not in rules:
            return None

        dep = rules["depends_on"]
        if not isinstance(dep, dict):
            return None

        qid = dep.get("id")
        val = dep.get("value")
        if qid and val is not None:
            return (qid, str(val))
        return None

    # ============================================================
    # 问卷状态总结（调试用）
    # ============================================================

    def summarize(self) -> Dict[str, Any]:
        """
        提供一个问卷当前状态的总结（调试用）
        """
        return {
            "current_question_index": self.current_question_index,
            "answered_count": len(self.answered_questions),
            "is_completed": self.is_completed,
            "history_preview": self.conversation_history[-5:],
        }
