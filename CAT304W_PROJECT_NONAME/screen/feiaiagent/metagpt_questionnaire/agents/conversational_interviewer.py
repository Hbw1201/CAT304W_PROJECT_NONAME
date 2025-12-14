# -*- coding: utf-8 -*-
"""
Conversational Interviewer Agent (Intelligent Version)
- Dynamically selects the next best question based on conversation history and inferred facts.
- Handles complex skip logic and dependencies defined in the questionnaire.
- Rephrases questions to be less robotic and more like a real doctor.
"""

import logging
import json
import re
from typing import Dict, Any, Optional, List, Set

from .base_agent import BaseAgent, register_agent
from ..models.questionnaire import Questionnaire, Question, UserResponse

logger = logging.getLogger(__name__)

@register_agent
class ConversationalInterviewerAgent(BaseAgent):
    """An intelligent conversational agent that dynamically selects questions to conduct a personalized interview."""

    def __init__(self, name: str = "Dr. Aiden", description: str = "A friendly and intelligent AI doctor who conducts personalized health interviews.", expertise: List[str] = ["conversational_ai", "medical_interview", "dynamic_questioning"]):
        super().__init__(name, description, expertise)

    async def process(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        优化问题表述，使其更自然友好
        """
        try:
            question = context.get("question", "")
            conversation_history = context.get("conversation_history", [])
            question_category = context.get("question_category", "")
            
            # 使用DeepSeek优化问题表述
            optimized_question = await self._optimize_question_with_llm(
                question, conversation_history, question_category
            )
            
            return {
                "status": "success",
                "optimized_question": optimized_question,
                "original_question": question
            }
            
        except Exception as e:
            logger.error(f"❌ 问题优化失败: {e}")
            return {
                "status": "error",
                "optimized_question": context.get("question", ""),
                "error": str(e)
            }

    def _infer_facts_from_history(self, history: List[UserResponse], questionnaire: Questionnaire) -> Dict[str, str]:
        """Infers facts like gender or smoking status from free-text answers."""
        facts = {}
        full_text = " ".join([r.answer for r in history])

        # Infer gender
        if re.search(r"男|先生|先生", full_text):
            facts['gender'] = '1'
        elif re.search(r"女|女士|小姐", full_text):
            facts['gender'] = '2'

        # Infer smoking status from the relevant answer
        smoking_response = next((r.answer for r in history if r.question_id == 'smoking_history'), None)
        if smoking_response:
            # 先检查否定回答
            if re.search(r"不吸|不抽|没吸|没抽|否|没有|从不|不会", smoking_response):
                facts['smoking_history'] = '2'
            # 再检查肯定回答 - 更全面的模式匹配
            elif re.search(r"我吸|我抽|有吸|有抽|吸.*习惯|抽.*习惯|会吸|会抽|确实|是|有", smoking_response):
                facts['smoking_history'] = '1'
        
        # Infer passive smoking status
        passive_smoking_response = next((r.answer for r in history if r.question_id == 'passive_smoking'), None)
        if passive_smoking_response:
            # 先检查否定回答
            if re.search(r"不会|不吸|没吸|否|没有|从不|很少|不接触", passive_smoking_response):
                facts['passive_smoking'] = '2'
            # 再检查肯定回答 - 更全面的模式匹配
            elif re.search(r"会.*吸|有.*吸|经常.*吸|接触.*烟|吸.*二手|是|有|会|经常", passive_smoking_response):
                facts['passive_smoking'] = '1'
        
        # Infer kitchen fumes exposure
        kitchen_fumes_response = next((r.answer for r in history if r.question_id == 'kitchen_fumes'), None)
        if kitchen_fumes_response:
            # 先检查否定回答
            if re.search(r"不会|不接触|没接触|否|没有|从不|很少|不做饭|不炒菜", kitchen_fumes_response):
                facts['kitchen_fumes'] = '2'
            # 再检查肯定回答 - 更全面的模式匹配
            elif re.search(r"会.*做饭|有.*做饭|经常.*做饭|接触.*油烟|炒菜|做饭|是|有|会|经常", kitchen_fumes_response):
                facts['kitchen_fumes'] = '1'
        
        # Infer occupational exposure
        occupation_exposure_response = next((r.answer for r in history if r.question_id == 'occupation_exposure'), None)
        if occupation_exposure_response:
            # 先检查否定回答
            if re.search(r"不会|不接触|没接触|否|没有|从不|很少|不工作", occupation_exposure_response):
                facts['occupation_exposure'] = '2'
            # 再检查肯定回答 - 更全面的模式匹配
            elif re.search(r"会.*接触|有.*接触|经常.*接触|工作.*接触|接触.*物质|是|有|会|经常|可能", occupation_exposure_response):
                facts['occupation_exposure'] = '1'
        
        return facts

    def _get_skip_ids(self, answers: Dict[str, str]) -> Set[str]:
        """Returns a set of question IDs to skip based on known answers."""
        skip_ids = set()
        
        # 吸烟史相关跳题逻辑
        # 如果用户不吸烟，跳过所有吸烟史相关的详细问题
        if answers.get('smoking_history') == '2':
            skip_ids.update([
                'smoking_freq',           # 吸烟频率
                'smoking_years',          # 累计吸烟年数
                'smoking_quit',           # 目前是否戒烟
                'smoking_quit_years'      # 戒烟年数
            ])
        
        # 被动吸烟相关跳题逻辑
        # 如果用户不会被动吸烟，跳过所有被动吸烟相关的详细问题
        if answers.get('passive_smoking') == '2':
            skip_ids.update([
                'passive_smoking_freq',   # 被动吸烟频率
                'passive_smoking_years'   # 累计被动吸烟年数
            ])
        
        # 厨房油烟相关跳题逻辑
        # 如果用户不接触厨房油烟，跳过所有厨房油烟相关的详细问题
        if answers.get('kitchen_fumes') == '2':
            skip_ids.update([
                'kitchen_fumes_years'     # 累计厨房油烟接触年数
            ])
        
        # 职业致癌物质接触相关跳题逻辑
        # 如果用户不接触职业致癌物质，跳过所有职业暴露相关的详细问题
        if answers.get('occupation_exposure') == '2':
            skip_ids.update([
                'occupation_exposure_details'  # 致癌物类型及累计接触年数
            ])
        
        return skip_ids

    def _are_dependencies_met(self, question: Question, answers: Dict[str, str]) -> bool:
        """Checks if a question's dependencies are satisfied by the current answers."""
        deps = question.validation_rules.get('depends_on') if question.validation_rules else None
        if not deps:
            return True
        
        dep_id = deps.get('id')
        required_value = str(deps.get('value'))
        
        actual_answer = answers.get(dep_id)
        return actual_answer == required_value

    async def _determine_next_question(self, history: List[UserResponse], facts: Dict[str, str], candidates: List[Question], questionnaire: Questionnaire) -> Optional[Question]:
        """Uses an LLM to determine the most logical next question from a list of candidates."""
        history_str = "\n".join([
            f"- Q ({q.id}): {q.text} \n- A: {r.answer}"
            for r in history
            for q in questionnaire.questions if q.id == r.question_id
        ])
        if not history_str:
            history_str = "No questions have been answered yet."

        facts_str = json.dumps(facts, ensure_ascii=False)
        candidates_str = "\n".join([f"- ID: {q.id}, Question: {q.text}" for q in candidates])

        prompt = f"""You must respond in English only.
Do not output Chinese characters.

You are an experienced primary care physician conducting a lung cancer pre-screening interview. Based on the conversation history, inferred facts, and candidate questions, choose the single most appropriate next question.

Conversation history:
{history_str}

Inferred facts:
{facts_str}

Candidate questions:
{candidates_str}

Selection requirements:
1. Choose exactly one question from the candidate list.
2. Ensure the choice aligns with existing information and medical reasoning.
3. Prioritize questions that gather critical health information.
4. Consider urgency, importance, and the natural flow of the consultation.
5. Keep the interview coherent and patient-friendly.

Output strictly as JSON with this structure and nothing else:
{{"next_question_id": "QUESTION_ID"}}

If no suitable question exists, output:
{{"next_question_id": "none"}}"""

        try:
            llm_response = await self.call_llm(prompt)
            logger.debug(f"LLM response for next question: {llm_response}")
            match = re.search(r'{\s*"next_question_id"\s*:\s*"(.*?)"\s*}', llm_response)
            if match:
                next_question_id = match.group(1)
                selected = next((q for q in candidates if q.id == next_question_id), None)
                if not selected:
                    logger.warning(f"LLM selected an invalid question ID '{next_question_id}' not in candidates.")
                return selected
            else:
                logger.warning(f"LLM response was not in the expected JSON format: {llm_response}")
        except Exception as e:
            logger.error(f"Failed to determine next question using LLM: {e}")
        
        return None # Fallback

    async def _optimize_question_with_llm(self, question: str, conversation_history: List[Dict], question_category: str) -> str:
        """使用DeepSeek优化问题表述"""
        try:
            history_context = ""
            if conversation_history:
                history_context = "Recent dialogue:\n"
                for item in conversation_history[-2:]:  # 最新2轮对话
                    history_context += f"Doctor: {item.get('question', '')}\n"
                    history_context += f"Patient: {item.get('answer', '')}\n"
            
            prompt = f"""You must respond in English only.
Do not output Chinese characters.

You are a friendly health interviewer chatting with a patient. Rephrase the formal question so it feels natural and compassionate.

{history_context}

Current formal question: {question}
Category: {question_category}

Rephrasing requirements:
1. Maintain a warm, reassuring tone.
2. Use plain, easy-to-understand language.
3. Avoid overly technical medical jargon.
4. Preserve the core intent of the original question.
5. Add brief clarifications if helpful.
6. Output a complete sentence, not just keywords.

Provide only the rephrased question."""

            # 调用DeepSeek
            response = await self.call_llm(prompt)
            
            # 清理响应
            optimized = response.strip()
            if optimized and len(optimized) > 10:  # 确保有实际内容
                return optimized
            else:
                # 如果LLM优化失败，使用预设的问题模板
                return self._get_fallback_question(question, question_category)
                
        except Exception as e:
            logger.warning(f"⚠️ LLM问题优化失败: {e}")
            return self._get_fallback_question(question, question_category)
    
    def _get_fallback_question(self, question: str, question_category: str) -> str:
        """获取回退问题模板"""
        # 常见问题的预设模板
        question_templates = {
            "性别": "请问您的性别是？",
            "姓名": "请问怎么称呼您？",
            "年龄": "请问您今年多少岁？",
            "出生年份": "请问您是哪一年出生的？",
            "身高": "请问您的身高是多少？",
            "体重": "请问您的体重是多少？",
            "吸烟史": "请问您有吸烟的习惯吗？",
            "被动吸烟": "在您的生活或工作环境中，您会经常吸到二手烟吗？",
            "职业": "请问您目前从事什么职业？",
            "既往病史": "请问您以前得过什么疾病吗？",
            "家族史": "请问您的家人中有没有得过肿瘤的？"
        }
        
        # 直接匹配问题文本
        if question in question_templates:
            return question_templates[question]
        
        # 模糊匹配
        for key, template in question_templates.items():
            if key in question:
                return template
        
        # 根据问题分类提供通用模板
        if question_category == "基本信息":
            return f"请问{question}？"
        elif question_category == "身体指标":
            return f"请问您的{question}是多少？"
        elif question_category == "吸烟史":
            return f"请问{question}？"
        else:
            return f"请问{question}？"
