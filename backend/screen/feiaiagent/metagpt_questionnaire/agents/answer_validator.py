# -*- coding: utf-8 -*-
"""
答案审核智能体
使用DeepSeek审核用户回答，判断是否需要重新回答
优化版本：支持缓存、批量处理、预编译正则表达式
"""

import logging
import re
from typing import Dict, Any, List, Optional
import asyncio
import concurrent.futures
from functools import lru_cache
import threading
import time

from .base_agent import BaseAgent, register_agent

logger = logging.getLogger(__name__)

@register_agent
class AnswerValidatorAgent(BaseAgent):
    """答案审核智能体（优化版本）"""
    
    def __init__(self):
        super().__init__(
            name="答案审核专家",
            description="专业的答案审核智能体，负责验证用户回答的质量和完整性",
            expertise=["答案验证", "质量控制", "医学知识", "逻辑判断"]
        )
        
        # 性能优化配置
        self._validation_cache = {}
        self._cache_lock = threading.Lock()
        self._thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="validator_worker")
        self._cache_ttl = 300  # 缓存5分钟
        
        # 性能统计
        self._stats = {
            "total_validations": 0,
            "cache_hits": 0,
            "llm_calls": 0,
            "keyword_detections": 0,
            "avg_validation_time": 0.0,
            "last_reset": time.time()
        }
        self._stats_lock = threading.Lock()
        
        # 关键词识别配置
        self.keyword_patterns = {
            "返回上一题": [
                r"上一题", r"上一道题", r"上一个问题", r"前面一题", r"前面一道题",
                r"回到上一题", r"回到上一道题", r"回到上一个问题", r"回到前面一题",
                r"重新回答上一题", r"重新回答上一道题", r"重新回答上一个问题",
                r"返回", r"回去", r"回到前面", r"回到上题", r"回到上道题"
            ],
            "返回指定题": [
                r"第(\d+)题", r"第(\d+)道题", r"第(\d+)个问题", r"(\d+)题", r"(\d+)道题",
                r"回到第(\d+)题", r"回到第(\d+)道题", r"回到第(\d+)个问题",
                r"重新回答第(\d+)题", r"重新回答第(\d+)道题", r"重新回答第(\d+)个问题",
                r"跳到第(\d+)题", r"跳到第(\d+)道题", r"跳到第(\d+)个问题"
            ],
            "重新开始": [
                r"重新开始", r"重新来", r"重新填写", r"重新回答", r"重新来一遍",
                r"从头开始", r"从头来", r"重新来过", r"重新做", r"重新填"
            ],
            "跳过当前题": [
                r"跳过", r"下一题", r"下一道题", r"下一个问题", r"过", r"不要了",
                r"不回答", r"不填", r"跳过这题", r"跳过这道题", r"跳过这个问题"
            ]
        }
        
        # 预编译正则表达式（性能优化）
        self._compiled_patterns = self._compile_patterns()
    
    def _compile_patterns(self) -> Dict[str, List[re.Pattern]]:
        """预编译正则表达式以提高性能"""
        compiled = {}
        for category, patterns in self.keyword_patterns.items():
            compiled[category] = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
        return compiled
    
    def _get_cache_key(self, question_text: str, user_answer: str, question_category: str = "") -> str:
        """生成缓存键"""
        return f"{hash(question_text)}_{hash(user_answer)}_{question_category}"
    
    def _get_cached_result(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """获取缓存结果"""
        with self._cache_lock:
            if cache_key in self._validation_cache:
                result, timestamp = self._validation_cache[cache_key]
                if time.time() - timestamp < self._cache_ttl:
                    logger.debug(f"使用验证缓存: {cache_key[:20]}...")
                    return result
                else:
                    # 缓存过期，删除
                    del self._validation_cache[cache_key]
        return None
    
    def _set_cached_result(self, cache_key: str, result: Dict[str, Any]):
        """设置缓存结果"""
        with self._cache_lock:
            self._validation_cache[cache_key] = (result, time.time())
            # 限制缓存大小
            if len(self._validation_cache) > 1000:
                # 删除最旧的缓存
                oldest_key = min(self._validation_cache.keys(), 
                               key=lambda k: self._validation_cache[k][1])
                del self._validation_cache[oldest_key]
    
    def _update_stats(self, stat_name: str, value: float = 1.0):
        """更新统计信息"""
        with self._stats_lock:
            if stat_name in self._stats:
                self._stats[stat_name] += value
            else:
                self._stats[stat_name] = value
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """获取性能统计信息"""
        with self._stats_lock:
            stats = self._stats.copy()
            stats["cache_hit_rate"] = (
                stats["cache_hits"] / max(stats["total_validations"], 1) * 100
            )
            stats["uptime"] = time.time() - stats["last_reset"]
            return stats
    
    def reset_stats(self):
        """重置统计信息"""
        with self._stats_lock:
            self._stats = {
                "total_validations": 0,
                "cache_hits": 0,
                "llm_calls": 0,
                "keyword_detections": 0,
                "avg_validation_time": 0.0,
                "last_reset": time.time()
            }
    
    def cleanup_resources(self):
        """清理资源"""
        if hasattr(self, '_thread_pool'):
            self._thread_pool.shutdown(wait=True)
        with self._cache_lock:
            self._validation_cache.clear()
        logger.info(f"✅ {self.name} 资源清理完成")
    
    async def process(self, input_data: Any) -> Any:
        """处理答案审核请求"""
        if isinstance(input_data, dict):
            return await self.validate_answer(
                question_text=input_data.get("question_text", ""),
                user_answer=input_data.get("user_answer", ""),
                question_category=input_data.get("question_category", ""),
                validation_rules=input_data.get("validation_rules", {})
            )
        else:
            raise ValueError(f"不支持的输入类型: {type(input_data)}")
    
    async def run(self, user_answer: str, question_text: str, current_index: int, total_questions: int) -> Dict[str, Any]:
        """运行答案验证和意图分析（简化版本）"""
        logger.info(f"🔍 {self.name} 开始分析用户意图和答案质量")
        
        try:
            # 使用LLM进行综合分析（意图分析+答案验证，只调用一次）
            analysis_result = await self._comprehensive_analysis(
                user_answer=user_answer,
                question_text=question_text,
                current_index=current_index,
                total_questions=total_questions
            )
            
            # 处理分析结果
            if analysis_result.get("wants_redo"):
                logger.info(f"🎯 用户想要重新回答第{analysis_result.get('target_index', current_index) + 1}题")
                return {
                    "redo": True,
                    "target_index": analysis_result.get("target_index", current_index),
                    "reason": analysis_result.get("reason", "用户想要重新回答前面的问题"),
                    "message": "Alright, let's revisit the previous question and answer it again."
                }
            
            # 处理答案验证结果
            if analysis_result.get("valid"):
                logger.info(f"✅ 答案审核通过：{analysis_result.get('reason')}")
                return {
                    "redo": False,
                    "valid": True,
                    "quality_score": analysis_result.get("quality_score", 0.8),
                    "relevance_score": analysis_result.get("relevance_score", 0.8),
                    "reason": analysis_result.get("reason", "答案审核通过"),
                    "retry": False
                }
            else:
                logger.warning(f"⚠️ 答案审核不通过：{analysis_result.get('reason')}")
                return {
                    "redo": False,
                    "valid": False,
                    "reason": analysis_result.get("reason", "答案不符合要求"),
                    "suggestion": analysis_result.get(
                        "suggestion",
                        "Please provide a more specific answer."
                    ),
                    "retry": True
                }
                
        except Exception as e:
            logger.error(f"❌ {self.name} 运行失败: {e}")
            return {
                "redo": False,
                "valid": False,
                "reason": f"验证过程出错: {str(e)}",
                "retry": False
            }
    
    async def validate_answer(self, 
                            question_text: str, 
                            user_answer: str, 
                            question_category: str = "",
                            validation_rules: Dict[str, Any] = None) -> Dict[str, Any]:
        """审核用户回答（优化版本，支持缓存）"""
        start_time = time.time()
        self._update_stats("total_validations")
        
        logger.info(f"🔍 {self.name} 开始审核答案: {question_text[:30]}...")
        
        try:
            # 检查缓存
            cache_key = self._get_cache_key(question_text, user_answer, question_category)
            cached_result = self._get_cached_result(cache_key)
            if cached_result:
                self._update_stats("cache_hits")
                logger.debug(f"使用验证缓存: {question_text[:20]}...")
                return cached_result
            
            # 基本检查（增强版本）
            basic_check = self._basic_validation(user_answer, validation_rules, question_text)
            if not basic_check["valid"]:
                result = {
                    "status": "invalid",
                    "valid": False,
                    "reason": basic_check["reason"],
                    "suggestion": basic_check.get("suggestion", ""),
                    "retry": True
                }
                # 缓存基本验证结果
                self._set_cached_result(cache_key, result)
                return result
            
            # 如果用户选择不回答敏感信息问题，直接通过
            if basic_check.get("sensitive_skip"):
                result = {
                    "status": "valid",
                    "valid": True,
                    "reason": "The user chose not to answer the sensitive question.",
                    "sensitive_skip": True,
                    "retry": False
                }
                self._set_cached_result(cache_key, result)
                return result
            
            # 使用DeepSeek进行智能审核
            self._update_stats("llm_calls")
            llm_validation = await self._llm_validation(
                question_text, user_answer, question_category
            )
            
            if llm_validation["valid"]:
                result = {
                    "status": "valid",
                    "valid": True,
                    "reason": "The answer passed validation.",
                    "quality_score": llm_validation.get("quality_score", 0.8),
                    "retry": False
                }
            else:
                result = {
                    "status": "invalid", 
                    "valid": False,
                    "reason": llm_validation["reason"],
                    "suggestion": llm_validation.get("suggestion", ""),
                    "retry": True
                }
            
            # 缓存结果
            self._set_cached_result(cache_key, result)
            
            # 更新平均验证时间
            validation_time = time.time() - start_time
            with self._stats_lock:
                total_validations = self._stats["total_validations"]
                current_avg = self._stats["avg_validation_time"]
                self._stats["avg_validation_time"] = (
                    (current_avg * (total_validations - 1) + validation_time) / total_validations
                )
            
            return result
                
        except Exception as e:
            logger.error(f"❌ {self.name} 答案审核失败: {e}")
            return {
                "status": "error",
                "valid": False,
                "reason": f"审核过程出错: {str(e)}",
                "retry": False
            }
    
    def _detect_keywords(self, user_answer: str, current_index: int, total_questions: int) -> Dict[str, Any]:
        """检测用户回答中的关键词（优化版本，使用预编译正则表达式）"""
        try:
            self._update_stats("keyword_detections")
            answer_lower = user_answer.lower().strip()
            
            # 检测"返回上一题"关键词
            for pattern in self._compiled_patterns["返回上一题"]:
                if pattern.search(answer_lower):
                    target_index = max(0, current_index - 1)
                    return {
                        "detected": True,
                        "intent_type": "返回上一题",
                        "redo": True,
                        "target_index": target_index,
                        "reason": "Detected keywords requesting a return to the previous question.",
                        "message": f"Alright, let's go back to question {target_index + 1}.",
                        "clear_previous_answer": True
                    }
            
            # 检测"返回指定题"关键词
            for pattern in self._compiled_patterns["返回指定题"]:
                match = pattern.search(answer_lower)
                if match:
                    try:
                        question_num = int(match.group(1))
                        target_index = question_num - 1  # 转换为0基索引
                        target_index = max(0, min(target_index, total_questions - 1))
                        return {
                            "detected": True,
                            "intent_type": "返回指定题",
                            "redo": True,
                            "target_index": target_index,
                        "reason": f"Detected keywords requesting a return to question {question_num}.",
                        "message": f"Alright, let's return to question {question_num}.",
                            "clear_previous_answer": True
                        }
                    except (ValueError, IndexError):
                        continue
            
            # 检测"重新开始"关键词
            for pattern in self._compiled_patterns["重新开始"]:
                if pattern.search(answer_lower):
                    return {
                        "detected": True,
                        "intent_type": "重新开始",
                        "redo": True,
                        "target_index": 0,
                        "reason": "Detected keywords requesting a restart.",
                        "message": "Alright, let's start over from the beginning.",
                        "clear_all_answers": True
                    }
            
            # 检测"跳过当前题"关键词
            for pattern in self._compiled_patterns["跳过当前题"]:
                if pattern.search(answer_lower):
                    return {
                        "detected": True,
                        "intent_type": "跳过当前题",
                        "skip": True,
                        "target_index": current_index + 1,
                        "reason": "Detected keywords requesting to skip the current question.",
                        "message": "Alright, we'll skip this question and continue to the next one."
                    }
            
            # 没有检测到关键词
            return {"detected": False}
            
        except Exception as e:
            logger.warning(f"⚠️ 关键词检测失败: {e}")
            return {"detected": False}
    
    def _basic_validation(self, user_answer: str, validation_rules: Dict[str, Any] = None, question_text: str = "") -> Dict[str, Any]:
        """基本验证（极度宽松版本）"""
        if not user_answer or user_answer.strip() == "":
            return {
                "valid": False,
                "reason": "The answer cannot be empty.",
                "suggestion": "Please provide your answer."
            }
        
        # 极度宽松：只要不是完全空白就认为有效
        if len(user_answer.strip()) < 1:
            return {
                "valid": False,
                "reason": "The answer is too short.",
                "suggestion": "Please provide a more detailed answer."
            }
        
        # 检查是否是完全无关的内容（极度宽松，只有明显无关才拒绝）
        answer_lower = user_answer.lower().strip()
        
        # 只有以下情况才认为无效：
        # 1. 完全无关的内容（如回答天气、时间等来回答医学问题）
        # 2. 明显的恶意回答（如乱码、重复字符等）
        unrelated_patterns = [
            r"^今天.*天气", r"^现在.*时间", r"^几点.*了", r"^星期.*几",
            r"^[a-z]{10,}$",  # 10个以上连续字母（可能是乱码）
            r"^.{1,3}\1{3,}$",  # 重复字符
            r"^[0-9]{20,}$"  # 20个以上连续数字（可能是乱码）
        ]
        
        for pattern in unrelated_patterns:
            if re.search(pattern, answer_lower):
                return {
                    "valid": False,
                    "reason": "The answer is not related to the question.",
                    "suggestion": "Please respond with information relevant to the question."
                }
        
        # 其他所有情况都认为有效
        return {"valid": True}
    
    
    
    
    
    async def _comprehensive_analysis(self, 
                                    user_answer: str, 
                                    question_text: str, 
                                    current_index: int, 
                                    total_questions: int) -> Dict[str, Any]:
        """综合分析：意图分析+答案验证（只调用一次LLM）"""
        try:
            # 构建简化的综合分析提示词
            prompt = f"""You must respond in English only.
Do not output Chinese characters.
Use English for all content, but keep the following fixed Chinese labels exactly as written: 是否重新回答：, 目标问题索引：, 答案是否有效：, 原因：, 审核结果：, 质量评分：, 相关性评分：, 不通过原因：, 改进建议：, 意图类型：, 是否返回：, 回复消息：. Do not use any other Chinese.

You are a professional medical questionnaire assistant who must evaluate whether the user's answer is valid.

Current context:
- Question index: {current_index + 1}/{total_questions}
- Question: {question_text}
- User answer: {user_answer}

Your tasks:
1. Decide if the user is trying to redo a previous question (phrases like "redo question X", "go back", etc.).
2. Determine whether the answer adequately addresses the current question.

Validation policy (extremely lenient):
- Accept any content unless it is blank, purely whitespace, blatantly irrelevant, or obvious malicious gibberish.
- Accept single words, short phrases, numbers, slang, and informal speech.
- Accept uncertain expressions such as "I am not sure", "maybe", or "I forgot".
- Accept any units and any yes/no wording.
- Only treat the answer as invalid when it is empty, fully unrelated (e.g., talking about the weather when asked about weight), or intentionally nonsensical spam.

Respond using the following format (keep the labels exactly in Chinese; all explanations after the labels must be English):
是否重新回答：是/否
目标问题索引：[provide the question index for redo, otherwise -1]
答案是否有效：是/否
原因：[concise English justification]

Output nothing else."""

            # 调用DeepSeek进行综合分析
            self._update_stats("llm_calls")
            response = await self.call_llm(prompt)
            
            # 解析响应
            return self._parse_simple_response(response, current_index, total_questions)
            
        except Exception as e:
            logger.warning(f"⚠️ 综合分析失败: {e}")
            # 降级到基本验证
            return {
                "wants_redo": False,
                "valid": True,
                "quality_score": 0.7,
                "relevance_score": 0.7,
                "reason": "Comprehensive analysis failed; using the default validation."
            }
    
    def _parse_simple_response(self, response: str, current_index: int, total_questions: int) -> Dict[str, Any]:
        """解析简化的综合分析响应"""
        try:
            response = response.strip()
            
            # 使用预编译的正则表达式进行解析
            wants_redo_pattern = re.compile(r'是否重新回答：([^\n]+)')
            target_index_pattern = re.compile(r'目标问题索引：(\d+)')
            answer_valid_pattern = re.compile(r'答案是否有效：([^\n]+)')
            reason_pattern = re.compile(r'原因：([^\n]+)')
            
            # 解析是否重新回答
            wants_redo = False
            target_index = current_index
            
            wants_redo_match = wants_redo_pattern.search(response)
            if wants_redo_match and "是" in wants_redo_match.group(1):
                wants_redo = True
                
                target_index_match = target_index_pattern.search(response)
                if target_index_match:
                    try:
                        target_index = int(target_index_match.group(1)) - 1  # 转换为0基索引
                        target_index = max(0, min(target_index, total_questions - 1))
                    except:
                        target_index = max(0, current_index - 1)
                else:
                    target_index = max(0, current_index - 1)
            
            # 解析答案是否有效
            valid = True
            answer_valid_match = answer_valid_pattern.search(response)
            if answer_valid_match and "否" in answer_valid_match.group(1):
                valid = False
            
            # 提取原因
            reason = "答案审核通过"
            reason_match = reason_pattern.search(response)
            if reason_match:
                reason = reason_match.group(1).strip()
            
            return {
                "wants_redo": wants_redo,
                "target_index": target_index,
                "valid": valid,
                "quality_score": 0.8 if valid else 0.3,
                "relevance_score": 0.8 if valid else 0.3,
                "reason": reason,
                "suggestion": (
                    "Please provide a more specific answer."
                    if not valid else ""
                )
            }
            
        except Exception as e:
            logger.warning(f"⚠️ 解析简化响应失败: {e}")
            return {
                "wants_redo": False,
                "valid": True,
                "quality_score": 0.7,
                "relevance_score": 0.7,
                "reason": "Parsing failed; using the default validation."
            }
    
    async def _validate_answer_inline(self, 
                                    question_text: str, 
                                    user_answer: str, 
                                    question_category: str = "",
                                    validation_rules: Dict[str, Any] = None) -> Dict[str, Any]:
        """内联答案验证（避免重复调用和日志）"""
        start_time = time.time()
        self._update_stats("total_validations")
        
        try:
            # 检查缓存
            cache_key = self._get_cache_key(question_text, user_answer, question_category)
            cached_result = self._get_cached_result(cache_key)
            if cached_result:
                self._update_stats("cache_hits")
                return cached_result
            
            # 基本检查（增强版本）
            basic_check = self._basic_validation(user_answer, validation_rules, question_text)
            if not basic_check["valid"]:
                result = {
                    "status": "invalid",
                    "valid": False,
                    "reason": basic_check["reason"],
                    "suggestion": basic_check.get("suggestion", ""),
                    "retry": True
                }
                # 缓存基本验证结果
                self._set_cached_result(cache_key, result)
                return result
            
            # 如果用户选择不回答敏感信息问题，直接通过
            if basic_check.get("sensitive_skip"):
                result = {
                    "status": "valid",
                    "valid": True,
                    "reason": "The user chose not to answer the sensitive question.",
                    "sensitive_skip": True,
                    "retry": False
                }
                self._set_cached_result(cache_key, result)
                return result
            
            # 使用DeepSeek进行智能审核（不重复日志）
            self._update_stats("llm_calls")
            llm_validation = await self._llm_validation(
                question_text, user_answer, question_category
            )
            
            if llm_validation["valid"]:
                result = {
                    "status": "valid",
                    "valid": True,
                    "reason": "The answer passed validation.",
                    "quality_score": llm_validation.get("quality_score", 0.8),
                    "retry": False
                }
            else:
                result = {
                    "status": "invalid", 
                    "valid": False,
                    "reason": llm_validation["reason"],
                    "suggestion": llm_validation.get("suggestion", ""),
                    "retry": True
                }
            
            # 缓存结果
            self._set_cached_result(cache_key, result)
            
            # 更新平均验证时间
            validation_time = time.time() - start_time
            with self._stats_lock:
                total_validations = self._stats["total_validations"]
                current_avg = self._stats["avg_validation_time"]
                self._stats["avg_validation_time"] = (
                    (current_avg * (total_validations - 1) + validation_time) / total_validations
                )
            
            return result
                
        except Exception as e:
            logger.error(f"❌ {self.name} 答案审核失败: {e}")
            return {
                "status": "error",
                "valid": False,
                "reason": f"审核过程出错: {str(e)}",
                "retry": False
            }
    
    async def _validate_answer_direct(self, 
                                    question_text: str, 
                                    user_answer: str, 
                                    question_category: str = "",
                                    validation_rules: Dict[str, Any] = None) -> Dict[str, Any]:
        """直接验证答案（内部使用，不重复日志）"""
        start_time = time.time()
        self._update_stats("total_validations")
        
        try:
            # 检查缓存
            cache_key = self._get_cache_key(question_text, user_answer, question_category)
            cached_result = self._get_cached_result(cache_key)
            if cached_result:
                self._update_stats("cache_hits")
                return cached_result
            
            # 基本检查（增强版本）
            basic_check = self._basic_validation(user_answer, validation_rules, question_text)
            if not basic_check["valid"]:
                result = {
                    "status": "invalid",
                    "valid": False,
                    "reason": basic_check["reason"],
                    "suggestion": basic_check.get("suggestion", ""),
                    "retry": True
                }
                # 缓存基本验证结果
                self._set_cached_result(cache_key, result)
                return result
            
            # 如果用户选择不回答敏感信息问题，直接通过
            if basic_check.get("sensitive_skip"):
                result = {
                    "status": "valid",
                    "valid": True,
                    "reason": "The user chose not to answer the sensitive question.",
                    "sensitive_skip": True,
                    "retry": False
                }
                self._set_cached_result(cache_key, result)
                return result
            
            # 使用DeepSeek进行智能审核
            self._update_stats("llm_calls")
            llm_validation = await self._llm_validation(
                question_text, user_answer, question_category
            )
            
            if llm_validation["valid"]:
                result = {
                    "status": "valid",
                    "valid": True,
                    "reason": "The answer passed validation.",
                    "quality_score": llm_validation.get("quality_score", 0.8),
                    "retry": False
                }
            else:
                result = {
                    "status": "invalid", 
                    "valid": False,
                    "reason": llm_validation["reason"],
                    "suggestion": llm_validation.get("suggestion", ""),
                    "retry": True
                }
            
            # 缓存结果
            self._set_cached_result(cache_key, result)
            
            # 更新平均验证时间
            validation_time = time.time() - start_time
            with self._stats_lock:
                total_validations = self._stats["total_validations"]
                current_avg = self._stats["avg_validation_time"]
                self._stats["avg_validation_time"] = (
                    (current_avg * (total_validations - 1) + validation_time) / total_validations
                )
            
            return result
                
        except Exception as e:
            logger.error(f"❌ {self.name} 答案审核失败: {e}")
            return {
                "status": "error",
                "valid": False,
                "reason": f"审核过程出错: {str(e)}",
                "retry": False
            }
    
    async def _llm_validation(self, question_text: str, user_answer: str, question_category: str) -> Dict[str, Any]:
        """使用DeepSeek进行智能审核（人性化版本）"""
        try:
            # 构建审核提示词
            prompt = f"""You must respond in English only.
Do not output Chinese characters.
Use English for all content, but keep the following fixed Chinese labels exactly as written: 是否重新回答：, 目标问题索引：, 答案是否有效：, 原因：, 审核结果：, 质量评分：, 相关性评分：, 不通过原因：, 改进建议：, 意图类型：, 是否返回：, 回复消息：. Do not use any other Chinese.

You are a calm and professional medical questionnaire reviewer who evaluates patient answers with empathy.

Question: {question_text}
Category: {question_category}
Patient answer: {user_answer}

Use a warm, understanding tone and assess the answer on these dimensions:
1. Completeness: does it fully address the question?
2. Relevance: is it on topic (watch for answers that miss the question)?
3. Specificity: is it detailed and concrete?
4. Logic: does it make sense?
5. Medical plausibility: is it medically reasonable?
6. Natural expression: does it sound human and conversational?

Scoring guidelines:
- 质量评分: 0.0-1.0 (0.0-0.3 very poor, 0.3-0.5 poor, 0.5-0.7 fair, 0.7-0.9 good, 0.9-1.0 excellent)
- 相关性评分: 0.0-1.0 (0.0-0.3 unrelated, 0.3-0.5 partially related, 0.5-0.7 related, 0.7-1.0 highly related)

Important reminders:
- Recognize synonymous expressions (e.g., different words for smoking).
- Accept colloquial interjections like "um" or "yeah".
- Accept alternative units (e.g., weight in jin).
- Respect the patient's choice not to answer sensitive questions.
- If the answer is completely unrelated, set 相关性评分 to 0.1-0.3 and mark as not approved.
- If the answer merely repeats the question, set 相关性评分 to 0.2-0.4 and mark as not approved.
- If the answer is too vague or overly brief, set 质量评分 to 0.2-0.4 and mark as not approved.
- For choice questions, ensure the selection is valid even if expressed flexibly.
- For numeric questions, ensure the value is within a reasonable range.
- If 质量评分 < 0.5 or 相关性评分 < 0.5, the answer must be marked as not approved.

Provide the results using the following format (labels must remain in Chinese; descriptions must be English):
审核结果：通过/不通过
质量评分：0.0-1.0
相关性评分：0.0-1.0
不通过原因：[if not approved, give an English explanation in a gentle tone]
改进建议：[if not approved, give an encouraging English suggestion]

Return only this formatted response."""

            # 调用DeepSeek
            response = await self.call_llm(prompt)
            
            # 解析响应
            return self._parse_validation_response(response, question_text)
            
        except Exception as e:
            logger.warning(f"⚠️ LLM审核失败: {e}")
            # 降级到基本验证
            return {
                "valid": True,
                "quality_score": 0.7,
                "reason": "LLM review failed; using basic validation."
            }
    
    def _parse_validation_response(self, response: str, question_text: str = "") -> Dict[str, Any]:
        """解析LLM审核响应（优化版本，减少字符串操作）"""
        try:
            response = response.strip()
            
            # 使用预编译的正则表达式进行解析
            pass_pattern = re.compile(r'审核结果：通过|通过')
            quality_pattern = re.compile(r'质量评分：(\d+\.?\d*)')
            relevance_pattern = re.compile(r'相关性评分：(\d+\.?\d*)')
            reason_pattern = re.compile(r'不通过原因：([^\n]+)')
            suggestion_pattern = re.compile(r'改进建议：(.+)', re.DOTALL)
            
            # 检查是否通过
            if pass_pattern.search(response):
                # 提取质量评分
                quality_score = 0.8
                quality_match = quality_pattern.search(response)
                if quality_match:
                    try:
                        quality_score = float(quality_match.group(1))
                    except:
                        pass
                
                # 提取相关性评分
                relevance_score = 0.8
                relevance_match = relevance_pattern.search(response)
                if relevance_match:
                    try:
                        relevance_score = float(relevance_match.group(1))
                    except:
                        pass
                
                return {
                    "valid": True,
                    "quality_score": quality_score,
                    "relevance_score": relevance_score,
                    "reason": "LLM review passed."
                }
            else:
                # 提取不通过原因和建议
                reason = "回答质量不符合要求"
                suggestion = "请提供更详细、准确的回答"
                
                reason_match = reason_pattern.search(response)
                if reason_match:
                    reason = reason_match.group(1).strip()
                
                suggestion_match = suggestion_pattern.search(response)
                if suggestion_match:
                    suggestion = suggestion_match.group(1).strip()
                
                # 检查是否是相关性问题，提供更人性化的回复
                if any(keyword in reason.lower() for keyword in ["不相关", "答非所问", "无关", "偏离"]):
                    question_type = self._get_question_type(question_text)
                    reason = "您的回答似乎与问题不太相关，让我们重新来回答这个问题"
                    suggestion = self._generate_encouraging_message(question_type)
                
                return {
                    "valid": False,
                    "reason": reason,
                    "suggestion": suggestion
                }
                
        except Exception as e:
            logger.warning(f"⚠️ 解析审核响应失败: {e}")
            return {
                "valid": True,
                "quality_score": 0.7,
                "relevance_score": 0.7,
                "reason": "Parsing failed; approving by default."
            }
    
    async def batch_validate_answers(self, qa_pairs: List[Dict[str, str]], max_concurrent: int = 5) -> List[Dict[str, Any]]:
        """批量审核答案（优化版本，支持并发处理）"""
        if not qa_pairs:
            return []
        
        # 创建信号量限制并发数
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def validate_single(qa_pair: Dict[str, str]) -> Dict[str, Any]:
            async with semaphore:
                return await self.validate_answer(
                    question_text=qa_pair.get("question", ""),
                    user_answer=qa_pair.get("answer", ""),
                    question_category=qa_pair.get("category", "")
                )
        
        # 并发执行所有验证任务
        tasks = [validate_single(qa_pair) for qa_pair in qa_pairs]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理异常结果
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"批量验证第{i+1}个答案时出错: {result}")
                processed_results.append({
                    "status": "error",
                    "valid": False,
                    "reason": f"验证过程出错: {str(result)}",
                    "retry": False
                })
            else:
                processed_results.append(result)
        
        return processed_results
    
    async def _analyze_redo_intent(self, user_answer: str, current_index: int, total_questions: int) -> Dict[str, Any]:
        """分析用户是否想返回前面的问题重新回答"""
        try:
            # 构建分析提示词
            prompt = f"""You must respond in English only.
Do not output Chinese characters.
Use English for all content, but keep the following fixed Chinese labels exactly as written: 是否重新回答：, 目标问题索引：, 答案是否有效：, 原因：, 审核结果：, 质量评分：, 相关性评分：, 不通过原因：, 改进建议：, 意图类型：, 是否返回：, 回复消息：. Do not use any other Chinese.

You are a professional medical questionnaire assistant who must analyze the user's intent.

Current context:
- Question index: {current_index + 1}/{total_questions}
- User answer: {user_answer}

Identify whether the user wants to:
1. Redo a previous question.
2. Skip the current question.
3. Jump to a specific earlier question.
4. Continue answering normally.

Common expressions for these intents include:
- "I want to redo question X"
- "Go back"
- "Fill it again"
- "Change my previous answer"
- "Take me to question X"
- "Redo"
- "Start over"
- "Let's do it again"

Interpretation guidance:
- If the user clearly states they want to redo a question, mark the intent as returning.
- If they simply say "redo" with no index, default to the previous question.
- "Start over" means go to question 1.
- "Go back" means return to the previous question unless a specific index is given.

Respond using this format (labels remain in Chinese; explanations/messages must be English):
意图类型：[重新回答/跳过/返回特定/正常回答]
是否返回：是/否
目标问题索引：[if returning to a specific question, give the index; otherwise -1]
原因：[brief English explanation]
回复消息：[English message to the user]

Return only this formatted output."""

            # 调用DeepSeek分析
            response = await self.call_llm(prompt)
            
            # 解析响应
            return self._parse_redo_intent_response(response, current_index, total_questions)
            
        except Exception as e:
            logger.warning(f"⚠️ 重新回答意图分析失败: {e}")
            return {
                "wants_redo": False,
                "target_index": current_index,
                "reason": "Analysis failed; continue with the current question.",
                "message": "Please continue answering the current question."
            }
    
    def _parse_redo_intent_response(self, response: str, current_index: int, total_questions: int) -> Dict[str, Any]:
        """解析重新回答意图分析响应"""
        try:
            response = response.strip()
            
            # 检查是否想要重新回答
            if "是否返回：是" in response or "是否返回：true" in response.lower():
                # 提取目标问题索引
                target_index = current_index
                if "目标问题索引：" in response:
                    try:
                        index_text = response.split("目标问题索引：")[1].split()[0]
                        target_index = int(index_text) - 1  # 转换为0基索引
                        # 确保索引在有效范围内
                        target_index = max(0, min(target_index, total_questions - 1))
                    except:
                        target_index = max(0, current_index - 1)  # 默认返回上一个问题
                
                # 提取原因和消息
                reason = "用户想要重新回答前面的问题"
                message = "Alright, let's revisit the previous question and answer it again."
                
                if "原因：" in response:
                    try:
                        reason = response.split("原因：")[1].split("\n")[0].strip()
                    except:
                        pass
                
                if "回复消息：" in response:
                    try:
                        message = response.split("回复消息：")[1].strip()
                    except:
                        pass
                
                return {
                    "wants_redo": True,
                    "target_index": target_index,
                    "reason": reason,
                    "message": message
                }
            else:
                return {
                    "wants_redo": False,
                    "target_index": current_index,
                    "reason": "The user is answering the current question normally.",
                    "message": "Continue with the current question."
                }
                
        except Exception as e:
            logger.warning(f"⚠️ 解析重新回答意图失败: {e}")
            return {
                "wants_redo": False,
                "target_index": current_index,
                "reason": "Parsing failed; continue with the current question.",
                "message": "Please continue answering the current question."
            }
