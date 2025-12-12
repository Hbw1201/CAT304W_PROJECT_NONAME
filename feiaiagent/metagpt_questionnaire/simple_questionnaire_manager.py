# -*- coding: utf-8 -*-
"""
Simplified intelligent questionnaire manager for MetaGPT conversational flows.
"""

import logging
import re
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from types import SimpleNamespace

from .models.questionnaire import UserResponse, Question, Questionnaire
from .agents.base_agent import agent_registry
from .persistent_agent_manager import process_with_persistent_agent

logger = logging.getLogger(__name__)
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")


class SimpleQuestionnaireManager:
    """Simplified questionnaire manager that coordinates multiple MetaGPT agents."""
    
    def __init__(self):
        self.questionnaire: Optional[Questionnaire] = None
        self.current_question_index: int = 0
        self.answered_questions: List[UserResponse] = []
        self.conversation_history: List[Dict[str, Any]] = []
        self.is_completed: bool = False

        # Connected MetaGPT agents (optional)
        self.answer_validator = agent_registry.get_agent("答案审核专家")
        self.question_selector = agent_registry.get_agent("智能问题选择专家")
        self.risk_assessor = agent_registry.get_agent("风险评估专家")
        self.data_analyzer = agent_registry.get_agent("数据分析专家")
        self.report_generator = agent_registry.get_agent("报告生成专家")
    
    def initialize_questionnaire(self, questionnaire: Questionnaire) -> bool:
        """Initialize questionnaire state."""
        try:
            self.questionnaire = questionnaire
            self.current_question_index = 0
            self.answered_questions.clear()
            self.conversation_history.clear()
            self.is_completed = False
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
                        "progress": f"{self.current_question_index + 1}/{len(self.questionnaire.questions)}",
                        "error": validation_result.get("reason", "Your answer is too vague."),
                        "suggestion": validation_result.get(
                            "suggestion",
                            "Please provide a more specific and detailed answer."
                        ),
                        "retry": True
                    }

                standardized_answer = user_answer.strip()
                if current_question.id in {
                    "smoking_history",
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

                self.current_question_index += 1

            if self.current_question_index >= len(self.questionnaire.questions):
                return await self._complete_questionnaire()

            answers_dict = self._build_answer_map()
            next_question_index = self._find_next_valid_question(answers_dict)

            if next_question_index == -1:
                return await self._complete_questionnaire()

            next_question_index = await self._maybe_use_question_selector(
                next_question_index, answers_dict
            )

            if next_question_index == -1:
                return await self._complete_questionnaire()

            self.current_question_index = next_question_index
            next_question = self.questionnaire.questions[self.current_question_index]
            optimized_question = await self._optimize_question_text(next_question)

            return {
                "status": "next_question",
                "question": optimized_question,
                "question_id": next_question.id,
                "category": next_question.category,
                "progress": f"{self.current_question_index + 1}/{len(self.questionnaire.questions)}",
                "is_complete": False
            }

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
                return await validator.run(
                    user_answer=answer,
                    question_text=question.text,
                    current_index=self.current_question_index,
                    total_questions=len(self.questionnaire.questions)
                )

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
            "progress": f"{target_index + 1}/{len(self.questionnaire.questions)}",
            "is_complete": False,
            "redo": True,
            "target_index": target_index
        }
    
    async def _handle_skip_request(self, validation_result: Dict[str, Any], current_question: Question) -> Dict[str, Any]:
        """处理跳过请求"""
        target_index = validation_result.get("target_index", self.current_question_index + 1)
        message = validation_result.get("message", "Okay, we will skip this question.")
        
        # 更新当前问题索引
        self.current_question_index = target_index
        
        # 检查是否完成
        if self.current_question_index >= len(self.questionnaire.questions):
            return await self._complete_questionnaire()
        
        # 获取下一个问题
        next_question = self.questionnaire.questions[self.current_question_index]
        optimized_question = await self._optimize_question_text(next_question)
        
        return {
            "status": "next_question",
            "question": f"{message}\n\n{optimized_question}",
            "question_id": next_question.id,
            "category": next_question.category,
            "progress": f"{self.current_question_index + 1}/{len(self.questionnaire.questions)}",
            "is_complete": False,
            "skip": True
        }
    
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
        try:
            return self._get_display_text(question)
        except Exception as e:
            logger.warning(f"Failed to build display text, fallback to original question: {e}")
            return question.text

    async def _complete_questionnaire(self) -> Dict[str, Any]:
        """Finalize the questionnaire and generate the English report."""
        try:
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

            analysis_data: Dict[str, Any] = {
                "questionnaire": self.questionnaire,
                "answered_questions": self.answered_questions,
                "conversation_history": self.conversation_history,
                "responses": self.answered_questions
            }

            if self.data_analyzer:
                try:
                    data_analysis = await self.data_analyzer.process({
                        "responses": self.answered_questions,
                        "questionnaire": self.questionnaire,
                        "analysis_type": "conversational"
                    })
                    analysis_data["data_analysis"] = data_analysis
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
                    else:
                        analysis_data["risk_assessment"] = risk_assessment
                except Exception as assess_error:
                    logger.warning(f"Risk assessor failed: {assess_error}")

            if self.report_generator:
                try:
                    result = await self.report_generator.process(analysis_data)
                    report_text = self._extract_report_text(result)
                    if report_text and not self._contains_cjk(report_text):
                        return report_text
                except Exception as generator_error:
                    logger.warning(f"Report generator failed, fallback to simple report: {generator_error}")

            return self._generate_simple_report()
        except Exception as e:
            logger.error(f"Report generation failed: {e}")
            return self._generate_simple_report()

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
        return {response.question_id: response.answer for response in self.answered_questions}

    async def _maybe_use_question_selector(self, fallback_index: int, answers_dict: Dict[str, Any]) -> int:
        """Optionally use the intelligent question selector agent to choose the next question."""
        if not self.question_selector or not self.questionnaire:
            return fallback_index

        available_questions, available_indices = self._collect_available_questions(answers_dict)
        if not available_questions:
            return fallback_index

        payload = {
            "answered_questions": self._build_selector_answer_context(),
            "available_questions": available_questions,
            "conversation_history": self.conversation_history,
            "user_profile": {"current_index": self.current_question_index},
            "current_index": self.current_question_index
        }

        try:
            selection_result = await self.question_selector.run(**payload)
            suggested_index = self._extract_selector_index(selection_result, available_indices, available_questions)
            if suggested_index is None:
                return fallback_index
            return suggested_index
        except Exception as e:
            logger.warning(f"Question selector failed; falling back to sequential order: {e}")
            return fallback_index

    def _collect_available_questions(self, answers_dict: Dict[str, Any]) -> Tuple[List[Question], List[int]]:
        """Build a list of candidate questions and their indices."""
        available_questions: List[Question] = []
        available_indices: List[int] = []
        if not self.questionnaire:
            return available_questions, available_indices

        for idx in range(self.current_question_index, len(self.questionnaire.questions)):
            question = self.questionnaire.questions[idx]
            if self._should_skip_question(question, answers_dict):
                continue
            if not self._is_question_available(question, answers_dict):
                continue
            available_questions.append(question)
            available_indices.append(idx)
        return available_questions, available_indices

    def _build_selector_answer_context(self) -> List[SimpleNamespace]:
        """Convert answered questions to lightweight namespace objects for the selector agent."""
        if not self.questionnaire:
            return []

        question_map = {q.id: q for q in self.questionnaire.questions}
        selector_answers: List[SimpleNamespace] = []
        for response in self.answered_questions:
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

    def _extract_selector_index(
        self,
        selection_result: Optional[Dict[str, Any]],
        available_indices: List[int],
        available_questions: List[Question]
    ) -> Optional[int]:
        """Parse the selector agent's response and convert it to a question index."""
        if not selection_result:
            return None

        status = selection_result.get("status")
        if status == "completed":
            return -1

        next_index = selection_result.get("next_index")
        if isinstance(next_index, int) and next_index in available_indices:
            return next_index

        next_question_id = (
            selection_result.get("next_question_id")
            or selection_result.get("question_id")
        )
        if next_question_id:
            idx = self._find_question_index_by_id(str(next_question_id))
            if idx in available_indices:
                return idx

        selected_question = (
            selection_result.get("selected_question")
            or selection_result.get("next_question")
        )
        if selected_question:
            if self.questionnaire and isinstance(selected_question, Question):
                if selected_question in self.questionnaire.questions:
                    return self.questionnaire.questions.index(selected_question)
            if isinstance(selected_question, dict):
                qid = selected_question.get("id")
                if qid:
                    idx = self._find_question_index_by_id(str(qid))
                    if idx in available_indices:
                        return idx

        return None

    def _find_question_index_by_id(self, question_id: str) -> Optional[int]:
        """Find a question index by id."""
        if not self.questionnaire:
            return None
        for idx, question in enumerate(self.questionnaire.questions):
            if question.id == question_id:
                return idx
        return None
    
    def _find_next_valid_question(self, answers_dict: Optional[Dict[str, Any]] = None) -> int:
        """Sequentially find the next valid question index given current answers."""
        if not self.questionnaire:
            return -1

        answers = answers_dict or self._build_answer_map()

        for i in range(self.current_question_index, len(self.questionnaire.questions)):
            question = self.questionnaire.questions[i]

            if self._should_skip_question(question, answers):
                logger.info(f"Skipping question {question.id} based on skip logic.")
                continue

            if self._is_question_available(question, answers):
                logger.info(f"Next question selected: {question.id} (index: {i})")
                return i
            else:
                logger.info(
                    f"Question {question.id} (index: {i}) skipped because dependencies are not satisfied."
                )

        return -1

    def _should_skip_question(self, question: Question, answers_dict: Dict[str, Any]) -> bool:
        """Check if the question should be skipped based on current answers."""
        skip_ids = self._get_skip_ids(answers_dict)

        if question.id in skip_ids:
            logger.info(f"Question {question.id} skipped according to branching rules.")
            return True

        return False
    def _should_skip_question(self, question: Question, answers_dict: Dict[str, Any]) -> bool:
        """检查问题是否应该被跳过（基于跳题逻辑）"""
        # 获取跳题逻辑
        skip_ids = self._get_skip_ids(answers_dict)
        
        # 检查当前问题是否在跳过列表中
        if question.id in skip_ids:
            logger.info(f"⏭️ 问题 {question.id} 被跳过: 根据跳题逻辑")
            return True
        
        return False
    
    def _get_skip_ids(self, answers: Dict[str, Any]) -> set:
        """
        返回基于已知答案应该跳过的问题ID集合
        内部统一使用标准化后答案（"是"/"否"），但也兼容英文/数字。
        """
        skip_ids = set()
        
        # 吸烟史相关跳题逻辑
        # 如果用户没有吸烟史，跳过所有吸烟史相关详细问题
        smoking_ans = str(answers.get('smoking_history', "")).strip()
        if smoking_ans == '2' or self._is_negative_answer(smoking_ans):
            skip_ids.update([
                'smoking_freq',           # 吸烟频率
                'smoking_years',          # 累计吸烟年数
                'smoking_quit',           # 目前是否戒烟
                'smoking_quit_years'      # 戒烟年数
            ])
        
        # 被动吸烟相关跳题逻辑
        passive_ans = str(answers.get('passive_smoking', "")).strip()
        if passive_ans == '2' or self._is_negative_answer(passive_ans):
            skip_ids.update([
                'passive_smoking_freq',   # 被动吸烟频率
                'passive_smoking_years'   # 累计被动吸烟年数
            ])
        
        # 厨房油烟相关跳题逻辑
        kitchen_ans = str(answers.get('kitchen_fumes', "")).strip()
        if kitchen_ans == '2' or self._is_negative_answer(kitchen_ans):
            skip_ids.update([
                'kitchen_fumes_years'     # 累计厨房油烟接触年数
            ])
        
        # 职业致癌物质接触相关跳题逻辑
        exposure_ans = str(answers.get('occupation_exposure', "")).strip()
        if exposure_ans == '2' or self._is_negative_answer(exposure_ans):
            skip_ids.update([
                'occupation_exposure_details'  # 致癌物类型及累计接触年数
            ])
        
        return skip_ids
    
    def _is_negative_answer(self, answer: str) -> bool:
        """检查回答是否为否定回答（支持中英文）"""
        if not answer:
            return False
        
        text = str(answer).lower()
        
        # 中文否定词汇模式
        negative_patterns_cn = [
            r"不吸|不抽|没吸|没抽|否|没有|从不|不会|不接触|没接触|很少|不做饭"
        ]
        
        # 英文否定词汇模式
        negative_patterns_en = [
            r"\bno\b",
            r"\bnot\b",
            r"\bnever\b",
            r"\bnone\b",
            r"\bnope\b",
            r"\bnah\b",
            r"don\'t",
            r"do not",
            r"doesn\'t",
            r"does not",
            r"can\'t",
            r"cannot",
            r"\brarely\b",
            r"\bseldom\b"
        ]
        
        for pattern in negative_patterns_cn:
            if re.search(pattern, answer):
                return True
        
        for pattern in negative_patterns_en:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        
        # 直接等于数字/英文否定
        if text.strip() in ["2", "no", "nope", "nah"]:
            return True
        
        return False
    def _is_question_available(self, question: Question, answers_dict: Dict[str, Any]) -> bool:
        """检查问题是否应该被问（基于依赖条件）"""
        # 检查依赖条件（validation_rules.depends_on）
        if question.validation_rules and "depends_on" in question.validation_rules:
            depends_on = question.validation_rules["depends_on"]
            if depends_on:
                dependent_question_id = depends_on.get("id")
                required_value = str(depends_on.get("value"))
                
                # 检查依赖问题的答案（统一转为字符串比较）
                dependent_answer = str(answers_dict.get(dependent_question_id, ""))
                if dependent_answer != required_value:
                    logger.info(
                        f"⏭️ 问题 {question.id} 被跳过: 依赖问题 {dependent_question_id} 的答案是 "
                        f"'{dependent_answer}'，需要 '{required_value}'"
                    )
                    return False
        
        return True

    async def _standardize_yes_no_answer(self, question: Question, user_answer: str) -> str:
        """
        使用规则 + 持久化智能体标准化是/否类问题的答案
        支持中英文回答，**内部统一标准化为 "1" (Yes) 或 "2" (No)**。
        """
        try:
            raw = user_answer.strip()
            lower = raw.lower()
            
            # ---------------------------
            # 1. 工具函数：根据中英文关键词快速判断
            # ---------------------------
            def match_keywords(pos_cn, neg_cn, pos_en, neg_en) -> Optional[str]:
                # 中文否定
                for kw in neg_cn:
                    if kw in raw:
                        logger.info(f"✅ 中文否定关键词命中: {kw} -> '2'")
                        return "2"
                # 中文肯定
                for kw in pos_cn:
                    if kw in raw:
                        logger.info(f"✅ 中文肯定关键词命中: {kw} -> '1'")
                        return "1"
                # 英文否定
                for kw in neg_en:
                    if kw in lower:
                        logger.info(f"✅ 英文否定关键词命中: {kw} -> '2'")
                        return "2"
                # 英文肯定
                for kw in pos_en:
                    if kw in lower:
                        logger.info(f"✅ 英文肯定关键词命中: {kw} -> '1'")
                        return "1"
                return None
            
            # 通用英文肯定/否定词
            positive_words_en = ["yes", "yeah", "yep", "yup", "sure", "of course", "correct", "right"]
            negative_words_en = ["no", "nope", "nah", "not really"]
            
            # ---------- 吸烟史 ----------
            if question.id == "smoking_history" or "吸烟" in question.text:
                pos_cn = [
                    "我吸烟", "我抽烟", "有吸烟", "有抽烟",
                    "吸烟的习惯", "抽烟的习惯", "会吸烟", "会抽烟",
                    "有这个习惯", "有习惯", "我吸过", "我抽过",
                    "吸过烟", "抽过烟"
                ]
                neg_cn = [
                    "不吸烟", "不抽烟", "没有吸烟", "没有抽烟",
                    "从不吸烟", "从不抽烟", "不会吸烟", "不会抽烟",
                    "没吸过", "没抽过", "从不吸", "从不抽"
                ]
                pos_en = [
                    "i smoke", "i am a smoker", "i'm a smoker",
                    "i used to smoke", "i used to be a smoker",
                    "i do smoke", "i still smoke",
                    "smoke every day", "smoke everyday", "smoke a lot",
                    "i smoke sometimes", "i smoke occasionally"
                ] + positive_words_en
                neg_en = [
                    "i don't smoke", "i do not smoke",
                    "i never smoke", "i have never smoked",
                    "i have not smoked", "i haven't smoked",
                    "non-smoker", "non smoker", "no smoking"
                ] + negative_words_en
                
                res = match_keywords(pos_cn, neg_cn, pos_en, neg_en)
                if res:
                    return res
            
            # ---------- 被动吸烟 ----------
            elif question.id == "passive_smoking" or "被动吸烟" in question.text:
                pos_cn = [
                    "会吸到二手烟", "有二手烟", "经常吸二手烟",
                    "接触二手烟", "被动吸烟"
                ]
                neg_cn = [
                    "不会吸到二手烟", "不吸到二手烟", "没有二手烟",
                    "从不吸到二手烟", "没有接触二手烟"
                ]
                pos_en = [
                    "second-hand smoke", "second hand smoke",
                    "passive smoking", "i breathe smoke",
                    "i often breathe second hand smoke",
                    "i am exposed to smoke at work",
                    "people smoke around me"
                ] + positive_words_en
                neg_en = [
                    "no second-hand smoke", "no second hand smoke",
                    "no one smokes around me",
                    "i am not exposed to smoke"
                ] + negative_words_en
                
                res = match_keywords(pos_cn, neg_cn, pos_en, neg_en)
                if res:
                    return res
            
            # ---------- 厨房油烟 ----------
            elif question.id == "kitchen_fumes" or "厨房油烟" in question.text:
                pos_cn = [
                    "会做饭", "有做饭", "经常做饭", "接触油烟",
                    "炒菜", "会炒菜", "经常炒菜", "厨房油烟", "油烟"
                ]
                neg_cn = [
                    "不会做饭", "不做饭", "没做饭", "不炒菜",
                    "从不做饭", "从不炒菜"
                ]
                pos_en = [
                    "i cook a lot", "i often cook", "i cook every day",
                    "i am exposed to cooking fumes",
                    "i often stay in kitchen",
                    "kitchen fumes", "oil fumes", "cooking smoke"
                ] + positive_words_en
                neg_en = [
                    "i don't cook", "i do not cook",
                    "i never cook", "i rarely cook",
                    "i seldom cook"
                ] + negative_words_en
                
                res = match_keywords(pos_cn, neg_cn, pos_en, neg_en)
                if res:
                    return res
            
            # ---------- 职业暴露 ----------
            elif question.id == "occupation_exposure" or "职业" in question.text or "致癌" in question.text:
                pos_cn = [
                    "会接触", "有接触", "经常接触", "工作接触",
                    "职业暴露", "致癌物质"
                ]
                neg_cn = [
                    "不会接触", "没接触", "不接触",
                    "从不接触", "没有接触"
                ]
                pos_en = [
                    "i work with", "i work around", "i am exposed to",
                    "i handle", "i deal with",
                    "asbestos", "radon", "coal tar", "chemical fumes",
                    "radioactive materials", "dust exposure"
                ] + positive_words_en
                neg_en = [
                    "i don't work with", "i do not work with",
                    "i'm not exposed to", "i am not exposed to",
                    "no exposure at work"
                ] + negative_words_en
                
                res = match_keywords(pos_cn, neg_cn, pos_en, neg_en)
                if res:
                    return res
            
            # ---------- 其他是/否类 ----------
            else:
                pos_cn = ["有", "是", "会", "确实", "对", "嗯"]
                neg_cn = ["没有", "不", "否", "没", "从不"]
                pos_en = positive_words_en
                neg_en = negative_words_en
                
                res = match_keywords(pos_cn, neg_cn, pos_en, neg_en)
                if res:
                    return res
            
            # ---------------------------
            # 2. 规则未命中 -> 调用持久化智能体
            # ---------------------------
            context = {
                "question": question.text,
                "user_answer": user_answer,
                "question_category": question.category,
                "task": "standardize_yes_no_answer",
                "instructions": """
                Please normalize the user's answer to a clear YES or NO.
                - If the meaning is affirmative (has / is / does / used to / still / often / sometimes), treat it as YES.
                - If the meaning is negative (no / not / never / none / don't / doesn't / cannot), treat it as NO.
                Return JSON:
                {
                    "standardized_answer": "是" or "否",
                    "reasoning": "why"
                }
                """
            }
            
            result = await process_with_persistent_agent("Dr. Aiden", context)
            ai_answer = result.get("standardized_answer", "否")
            
            # 把智能体的中文 "是"/"否" 转换为 "1"/"2"
            if ai_answer == "是":
                logger.info(f"✅ 持久化智能体标准化答案: '{user_answer}' -> '1'")
                return "1"
            else:
                logger.info(f"✅ 持久化智能体标准化答案: '{user_answer}' -> '2'")
                return "2"
        
        except Exception as e:
            logger.error(f"❌ 答案标准化失败: {e}")
            # 出错时默认按否处理，返回 "2"
            return "2"

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
