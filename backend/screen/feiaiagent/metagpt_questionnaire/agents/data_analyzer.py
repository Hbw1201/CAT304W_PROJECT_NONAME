# -*- coding: utf-8 -*-
"""
数据分析智能体
负责分析问卷数据、识别模式、提供洞察
"""

import logging
import json
import os
import inspect
import traceback
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from collections import defaultdict, Counter
from types import SimpleNamespace

from .base_agent import BaseAgent, register_agent
from ..models.questionnaire import UserResponse, Question, Questionnaire

logger = logging.getLogger(__name__)

@register_agent
class DataAnalyzerAgent(BaseAgent):
    """数据分析智能体"""
    
    def __init__(self):
        super().__init__(
            name="数据分析专家",
            description="专业分析问卷数据的智能体，擅长数据洞察和模式识别",
            expertise=["数据分析", "统计学", "模式识别", "数据可视化", "洞察发现"]
        )
        self.analysis_history: List[Dict[str, Any]] = []
    
    async def process(self, input_data: Any) -> Any:
        """处理数据分析请求"""
        if isinstance(input_data, dict):
            responses = input_data.get('responses', [])
            questionnaire = input_data.get('questionnaire')
            analysis_type = input_data.get('analysis_type', 'comprehensive')
            return await self.analyze_data(responses, questionnaire, analysis_type)
        elif isinstance(input_data, list):
            return await self.analyze_data(input_data)
        else:
            raise ValueError(f"不支持的输入类型: {type(input_data)}")
    
    async def analyze_data(self, responses: List[UserResponse], 
                          questionnaire: Optional[Questionnaire] = None,
                          analysis_type: str = 'comprehensive') -> Dict[str, Any]:
        """分析问卷数据"""
        logger.info(f"📊 {self.name} 开始数据分析，回答数量: {len(responses)}")
        if os.getenv("SCREENING_DEBUG") == "1":
            print("[debug][analyzer] responses len:", len(responses))
            if responses:
                r0 = responses[0]
                print("[debug][analyzer] first response class:", r0.__class__)
                print("[debug][analyzer] first response module:", r0.__class__.__module__)
                try:
                    print("[debug][analyzer] source:", inspect.getsourcefile(r0.__class__))
                except Exception as _e:
                    print("[debug][analyzer] source: <unknown>", repr(_e))
                print("[debug][analyzer] has confidence?:", hasattr(r0, "confidence"))
                print("[debug][analyzer] dict keys:", list(getattr(r0, "__dict__", {}).keys()))
        
        try:
            responses = self._normalize_responses(responses)
            # 基础统计分析
            basic_stats = self._analyze_basic_statistics(responses)
            
            # 分类分析
            category_analysis = self._analyze_by_category(responses, questionnaire)
            
            # 模式识别
            pattern_analysis = await self._identify_patterns(responses)
            
            # 数据质量评估
            quality_assessment = self._assess_data_quality(responses)
            
            # 洞察发现
            insights = await self._discover_insights(responses, questionnaire)
            
            # 生成分析报告
            analysis_result = {
                "analysis_id": f"analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "timestamp": datetime.now().isoformat(),
                "responses_count": len(responses),
                "analysis_type": analysis_type,
                "basic_statistics": basic_stats,
                "category_analysis": category_analysis,
                "pattern_analysis": pattern_analysis,
                "data_quality": quality_assessment,
                "insights": insights,
                "summary": self._generate_summary(basic_stats, insights)
            }
            
            # 保存到历史记录
            self.analysis_history.append(analysis_result)
            
            logger.info(f"✅ {self.name} 数据分析完成")
            return analysis_result
            
        except Exception as e:
            logger.error(f"❌ {self.name} 数据分析失败: {e}")
            logger.error(traceback.format_exc())
            return self._create_error_analysis_result(str(e))

    def _safe_to_dict(self, response: Any) -> Dict[str, Any]:
        """Safely coerce a response object to dict."""
        if hasattr(response, "to_dict") and callable(getattr(response, "to_dict", None)):
            try:
                return response.to_dict()
            except Exception:
                pass
        if isinstance(response, dict):
            return response
        return {
            "question_id": getattr(response, "question_id", None)
            or getattr(response, "id", None)
            or getattr(response, "key", None),
            "answer": getattr(response, "answer", None) or getattr(response, "value", None),
            "confidence": getattr(response, "confidence", None)
            or getattr(response, "score", None)
            or getattr(response, "prob", None),
        }

    def _normalize_responses(self, responses: List[Any]) -> List[Any]:
        normalized: List[Any] = []
        for response in responses:
            if hasattr(response, "question_id") and hasattr(response, "answer"):
                normalized.append(response)
                continue
            payload = self._safe_to_dict(response)
            normalized.append(
                SimpleNamespace(
                    question_id=payload.get("question_id"),
                    answer=payload.get("answer"),
                    confidence=payload.get("confidence"),
                    timestamp=payload.get("timestamp"),
                )
            )
        return normalized
    
    def _analyze_basic_statistics(self, responses: List[UserResponse]) -> Dict[str, Any]:
        """基础统计分析"""
        stats = {
            "total_responses": len(responses),
            "unique_questions": len(set(r.question_id for r in responses)),
            "response_timestamps": [],
            "completion_time": None,
            "response_distribution": {}
        }
        
        if responses:
            # 时间分析
            timestamps = [r.timestamp for r in responses if r.timestamp]
            if timestamps:
                stats["response_timestamps"] = [ts.isoformat() for ts in timestamps]
                if len(timestamps) > 1:
                    completion_time = max(timestamps) - min(timestamps)
                    stats["completion_time"] = completion_time.total_seconds()
            
            # 回答分布分析
            question_ids = [r.question_id for r in responses]
            question_counter = Counter(question_ids)
            stats["response_distribution"] = dict(question_counter)
        
        return stats
    
    def _extract_min_questions(self, questionnaire: Any) -> List[tuple]:
        """从问卷对象或字典中提取 (question_id, category) 列表"""
        result: List[tuple] = []
        try:
            if not questionnaire:
                return result
            # 对象形式（有 questions 属性）
            if hasattr(questionnaire, "questions"):
                for q in getattr(questionnaire, "questions", []) or []:
                    try:
                        result.append((q.id, q.category))
                    except Exception:
                        continue
                return result
            # 字典形式（来自 to_dict 的结构）
            if isinstance(questionnaire, dict):
                for q in questionnaire.get("questions", []) or []:
                    try:
                        qid = q.get("id")
                        cat = q.get("category")
                        if qid and cat is not None:
                            result.append((qid, cat))
                    except Exception:
                        continue
                return result
        except Exception:
            return result
        return result

    def _analyze_by_category(self, responses: List[UserResponse], 
                           questionnaire: Any) -> Dict[str, Any]:
        """按分类分析数据（兼容对象或dict）"""
        category_analysis: Dict[str, Any] = {}
        
        questions_min = self._extract_min_questions(questionnaire)
        if not questions_min:
            return category_analysis
        
        # 按分类组织问题ID
        questions_by_category: Dict[str, List[str]] = defaultdict(list)
        for qid, cat in questions_min:
            questions_by_category[str(cat)].append(qid)
        
        # 分析每个分类的回答情况
        for category, qids in questions_by_category.items():
            qid_set = set(qids)
            category_responses = [r for r in responses if r.question_id in qid_set]
            if category_responses:
                category_stats = {
                    "question_count": len(qids),
                    "response_count": len(category_responses),
                    "completion_rate": (len(category_responses) / max(1, len(qids))),
                    "response_patterns": self._analyze_response_patterns(category_responses),
                    "common_answers": self._find_common_answers(category_responses)
                }
                category_analysis[category] = category_stats
        
        return category_analysis
    
    def _analyze_response_patterns(self, responses: List[UserResponse]) -> Dict[str, Any]:
        """分析回答模式"""
        patterns = {
            "answer_types": {},
            "response_lengths": [],
            "confidence_scores": []
        }
        
        for response in responses:
            # 分析答案类型
            answer_type = type(response.answer).__name__
            patterns["answer_types"][answer_type] = patterns["answer_types"].get(answer_type, 0) + 1
            
            # 分析答案长度
            if isinstance(response.answer, str):
                patterns["response_lengths"].append(len(response.answer))
            
            # 分析置信度
            try:
                confidence = self._safe_confidence(response)
                if confidence is not None:
                    patterns["confidence_scores"].append(confidence)
            except Exception as e:
                logger.warning(
                    "Failed to resolve response confidence question_id=%s error=%s",
                    getattr(response, "question_id", ""),
                    e,
                )
        
        # 计算统计信息
        if patterns["response_lengths"]:
            patterns["avg_response_length"] = sum(patterns["response_lengths"]) / len(patterns["response_lengths"])
            patterns["max_response_length"] = max(patterns["response_lengths"])
            patterns["min_response_length"] = min(patterns["response_lengths"])
        
        if patterns["confidence_scores"]:
            patterns["avg_confidence"] = sum(patterns["confidence_scores"]) / len(patterns["confidence_scores"])
            patterns["confidence_range"] = (min(patterns["confidence_scores"]), max(patterns["confidence_scores"]))
        
        return patterns

    def _safe_confidence(self, response: Any) -> float:
        """Safely resolve confidence for legacy UserResponse shapes."""
        conf = getattr(response, "confidence", None)
        if conf is None:
            conf = getattr(response, "score", None) or getattr(response, "prob", None)
        try:
            return float(conf) if conf is not None else 0.5
        except Exception:
            return 0.5

    def _find_common_answers(self, responses: List[UserResponse]) -> List[Dict[str, Any]]:
        """查找常见答案"""
        answer_counter = Counter()
        
        for response in responses:
            answer_str = str(response.answer)
            answer_counter[answer_str] += 1
        
        # 返回前5个最常见答案
        common_answers = []
        for answer, count in answer_counter.most_common(5):
            common_answers.append({
                "answer": answer,
                "count": count,
                "percentage": (count / len(responses)) * 100
            })
        
        return common_answers
    
    async def _identify_patterns(self, responses: List[UserResponse]) -> Dict[str, Any]:
        """识别数据模式"""
        logger.info(f"🔍 {self.name} 开始模式识别")
        
        try:
            response_payload = [self._safe_to_dict(r) for r in responses]
            prompt = f"""You must respond in English only.
Do not output Chinese characters.

You are an advanced healthcare data scientist who must analyze questionnaire answers and identify patterns.

Response dataset: {response_payload}

Your objectives:
1. Discover timing patterns in how users respond.
2. Identify consistency or inconsistency patterns across answers.
3. Detect potential response biases.
4. Describe user behavior patterns revealed in the data.
5. Highlight any anomalous or suspicious answer patterns.

Provide:
1. A list of identified patterns with concise explanations.
2. The significance or impact of each pattern.
3. A description of any anomalies.
4. Insights for improving the questionnaire flow."""
            
            # 调用LLM进行模式识别
            llm_response = await self.call_llm(prompt)
            
            # 解析模式识别结果
            patterns = self._parse_pattern_analysis(llm_response)
            
            # 如果没有识别到模式，使用基础模式分析
            if not patterns:
                patterns = self._basic_pattern_analysis(responses)
            
            logger.info(f"✅ 模式识别完成: {len(patterns)} 个模式")
            return patterns
            
        except Exception as e:
            logger.error(f"❌ 模式识别失败: {e}")
            return self._basic_pattern_analysis(responses)
    
    def _parse_pattern_analysis(self, llm_response: str) -> Dict[str, Any]:
        """解析LLM的模式分析结果"""
        patterns = {
            "identified_patterns": [],
            "pattern_significance": {},
            "anomaly_patterns": [],
            "behavioral_insights": []
        }
        
        # 简单的文本解析逻辑
        lines = llm_response.split('\n')
        current_section = None
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # 识别段落
            if "模式" in line and "：" in line:
                current_section = "identified_patterns"
            elif "异常" in line and "：" in line:
                current_section = "anomaly_patterns"
            elif "行为" in line and "：" in line:
                current_section = "behavioral_insights"
            elif line.startswith('-') or line.startswith('•'):
                # 提取内容
                content = line.lstrip('-• ').strip()
                if content and current_section:
                    if current_section == "identified_patterns":
                        patterns["identified_patterns"].append(content)
                    elif current_section == "anomaly_patterns":
                        patterns["anomaly_patterns"].append(content)
                    elif current_section == "behavioral_insights":
                        patterns["behavioral_insights"].append(content)
        
        return patterns
    
    def _basic_pattern_analysis(self, responses: List[UserResponse]) -> Dict[str, Any]:
        """基础模式分析"""
        patterns = {
            "identified_patterns": [],
            "pattern_significance": {},
            "anomaly_patterns": [],
            "behavioral_insights": []
        }
        
        if not responses:
            return patterns
        
        # 时间模式分析
        timestamps = [r.timestamp for r in responses if r.timestamp]
        if len(timestamps) > 1:
            time_diffs = []
            for i in range(1, len(timestamps)):
                diff = (timestamps[i] - timestamps[i-1]).total_seconds()
                time_diffs.append(diff)
            
            avg_time_diff = sum(time_diffs) / len(time_diffs)
            if avg_time_diff < 10:
                patterns["identified_patterns"].append("用户回答速度较快，可能对问卷内容熟悉")
            elif avg_time_diff > 60:
                patterns["identified_patterns"].append("用户回答速度较慢，可能需要更多时间思考")
        
        # 答案一致性分析
        answer_types = [type(r.answer).__name__ for r in responses]
        type_counter = Counter(answer_types)
        if len(type_counter) == 1:
            patterns["identified_patterns"].append("所有问题使用相同类型的答案")
        
        # 异常模式检测
        for response in responses:
            if isinstance(response.answer, str) and len(response.answer) > 100:
                patterns["anomaly_patterns"].append(f"问题 {response.question_id} 的回答异常长")
            elif isinstance(response.answer, str) and len(response.answer) < 2:
                patterns["anomaly_patterns"].append(f"问题 {response.question_id} 的回答异常短")
        
        return patterns
    
    def _assess_data_quality(self, responses: List[UserResponse]) -> Dict[str, Any]:
        """评估数据质量"""
        quality_metrics = {
            "completeness": 0.0,
            "consistency": 0.0,
            "validity": 0.0,
            "timeliness": 0.0,
            "overall_score": 0.0,
            "quality_issues": []
        }
        
        if not responses:
            return quality_metrics
        
        # 完整性评估
        total_questions = len(set(r.question_id for r in responses))
        unique_responses = len(set(r.question_id for r in responses))
        quality_metrics["completeness"] = unique_responses / total_questions if total_questions > 0 else 0.0
        
        # 一致性评估
        consistency_score = 0.0
        consistency_checks = 0
        
        for response in responses:
            if isinstance(response.answer, str):
                # 检查答案长度一致性
                if 2 <= len(response.answer) <= 100:
                    consistency_score += 1.0
                consistency_checks += 1
        
        if consistency_checks > 0:
            quality_metrics["consistency"] = consistency_score / consistency_checks
        
        # 有效性评估
        valid_responses = 0
        for response in responses:
            if response.answer and response.answer != "":
                valid_responses += 1
        
        quality_metrics["validity"] = valid_responses / len(responses) if responses else 0.0
        
        # 及时性评估
        if len(responses) > 1:
            timestamps = [r.timestamp for r in responses if r.timestamp]
            if timestamps:
                time_span = (max(timestamps) - min(timestamps)).total_seconds()
                if time_span < 300:  # 5分钟内完成
                    quality_metrics["timeliness"] = 1.0
                elif time_span < 1800:  # 30分钟内完成
                    quality_metrics["timeliness"] = 0.8
                elif time_span < 3600:  # 1小时内完成
                    quality_metrics["timeliness"] = 0.6
                else:
                    quality_metrics["timeliness"] = 0.4
        
        # 计算总体质量分数
        weights = {"completeness": 0.3, "consistency": 0.25, "validity": 0.25, "timeliness": 0.2}
        overall_score = sum(quality_metrics[metric] * weights[metric] for metric in weights.keys())
        quality_metrics["overall_score"] = overall_score
        
        # 识别质量问题
        if quality_metrics["completeness"] < 0.8:
            quality_metrics["quality_issues"].append("问卷完成度较低")
        if quality_metrics["consistency"] < 0.7:
            quality_metrics["quality_issues"].append("答案一致性较差")
        if quality_metrics["validity"] < 0.9:
            quality_metrics["quality_issues"].append("存在无效答案")
        if quality_metrics["timeliness"] < 0.6:
            quality_metrics["quality_issues"].append("完成时间过长")
        
        return quality_metrics
    
    async def _discover_insights(self, responses: List[UserResponse], 
                                questionnaire: Optional[Questionnaire]) -> List[Dict[str, Any]]:
        """发现数据洞察"""
        logger.info(f"💡 {self.name} 开始洞察发现")
        
        try:
            q_dict = questionnaire.to_dict() if hasattr(questionnaire, "to_dict") else (questionnaire or {})
            response_payload = [self._safe_to_dict(r) for r in responses]
            prompt = f"""You must respond in English only.
Do not output Chinese characters.

You are a senior medical data analyst tasked with extracting insights from questionnaire responses.

Questionnaire details: {q_dict}
Collected responses: {response_payload}

Analysis requirements:
1. Summarize overall data statistics and completion trends.
2. Highlight key findings and meaningful insights across categories.
3. Evaluate data quality (completeness, consistency, validity, timeliness).
4. Flag anomalies or responses that deserve attention.
5. Provide actionable recommendations and visualization ideas.

Return a structured narrative covering these items."""
            
            # 调用LLM进行洞察分析
            llm_response = await self.call_llm(prompt)
            
            # 解析洞察结果
            insights = self._parse_insights(llm_response)
            
            # 如果没有生成洞察，使用基础洞察
            if not insights:
                insights = self._generate_basic_insights(responses, questionnaire)
            
            logger.info(f"✅ 洞察发现完成: {len(insights)} 个洞察")
            return insights
            
        except Exception as e:
            logger.error(f"❌ 洞察发现失败: {e}")
            return self._generate_basic_insights(responses, questionnaire)
    
    def _parse_insights(self, llm_response: str) -> List[Dict[str, Any]]:
        """解析LLM生成的洞察"""
        insights = []
        
        # 简单的文本解析逻辑
        lines = llm_response.split('\n')
        current_insight = {}
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            if line.startswith('洞察') or line.startswith('发现') or line.startswith('关键'):
                if current_insight:
                    insights.append(current_insight)
                current_insight = {"title": line, "description": "", "significance": "medium"}
            elif line.startswith('-') or line.startswith('•'):
                if current_insight:
                    current_insight["description"] = line.lstrip('-• ').strip()
            elif "重要性" in line or "意义" in line:
                if current_insight:
                    if "高" in line or "重要" in line:
                        current_insight["significance"] = "high"
                    elif "低" in line or "一般" in line:
                        current_insight["significance"] = "low"
        
        # 添加最后一个洞察
        if current_insight:
            insights.append(current_insight)
        
        return insights
    
    def _generate_basic_insights(self, responses: List[UserResponse], 
                                questionnaire: Optional[Questionnaire]) -> List[Dict[str, Any]]:
        """生成基础洞察"""
        insights = []
        
        if not responses:
            return insights
        
        # 洞察1：回答模式
        response_count = len(responses)
        if response_count > 10:
            insights.append({
                "title": "问卷参与度高",
                "description": f"用户完成了{response_count}个问题的回答，参与度较高",
                "significance": "medium"
            })
        
        # 洞察2：时间模式
        timestamps = [r.timestamp for r in responses if r.timestamp]
        if len(timestamps) > 1:
            time_span = (max(timestamps) - min(timestamps)).total_seconds()
            if time_span < 300:
                insights.append({
                    "title": "快速完成模式",
                    "description": "用户在5分钟内完成问卷，可能对内容熟悉或急于完成",
                    "significance": "medium"
                })
        
        # 洞察3：答案类型分布
        answer_types = [type(r.answer).__name__ for r in responses]
        type_counter = Counter(answer_types)
        if len(type_counter) == 1:
            insights.append({
                "title": "答案类型一致",
                "description": "所有问题使用相同类型的答案，可能影响数据多样性",
                "significance": "low"
            })
        
        # 洞察4：数据质量
        valid_responses = sum(1 for r in responses if r.answer and r.answer != "")
        validity_rate = valid_responses / len(responses)
        if validity_rate < 0.9:
            insights.append({
                "title": "数据质量关注",
                "description": f"数据有效性为{validity_rate:.1%}，存在无效答案",
                "significance": "high"
            })
        
        return insights
    
    def _generate_summary(self, basic_stats: Dict[str, Any], 
                         insights: List[Dict[str, Any]]) -> Dict[str, Any]:
        """生成分析摘要"""
        summary = {
            "key_findings": [],
            "recommendations": [],
            "next_steps": []
        }
        
        # 关键发现
        if basic_stats["total_responses"] > 0:
            summary["key_findings"].append(f"共收集到{basic_stats['total_responses']}个回答")
        
        if basic_stats.get("completion_time"):
            summary["key_findings"].append(f"平均完成时间: {basic_stats['completion_time']:.1f}秒")
        
        # 添加洞察摘要
        high_significance_insights = [i for i in insights if i.get("significance") == "high"]
        if high_significance_insights:
            summary["key_findings"].append(f"发现{len(high_significance_insights)}个重要洞察")
        
        # 建议
        if basic_stats.get("total_responses", 0) < 5:
            summary["recommendations"].append("建议收集更多数据以提高分析可靠性")
        
        if insights:
            summary["recommendations"].append("建议深入分析发现的洞察")
        
        # 下一步
        summary["next_steps"].append("继续监控数据质量")
        summary["next_steps"].append("定期进行数据分析")
        
        return summary
    
    def _create_error_analysis_result(self, error_message: str) -> Dict[str, Any]:
        """创建错误分析结果"""
        return {
            "analysis_id": f"error_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "timestamp": datetime.now().isoformat(),
            "error": True,
            "error_message": error_message,
            "basic_statistics": {"total_responses": 0},
            "category_analysis": {},
            "pattern_analysis": {},
            "data_quality": {"overall_score": 0.0},
            "insights": [],
            "summary": {"key_findings": [], "recommendations": [], "next_steps": []}
        }
    
    def get_analysis_history(self) -> List[Dict[str, Any]]:
        """获取分析历史"""
        return self.analysis_history
    
    def export_analysis_report(self, analysis_result: Dict[str, Any], 
                              format: str = "json") -> str:
        """导出分析报告"""
        if format.lower() == "json":
            return json.dumps(analysis_result, ensure_ascii=False, indent=2)
        elif format.lower() == "text":
            return self._format_text_report(analysis_result)
        else:
            raise ValueError(f"不支持的导出格式: {format}")
    
    def _format_text_report(self, analysis_result: Dict[str, Any]) -> str:
        """格式化文本报告"""
        report = f"问卷数据分析报告\n"
        report += f"=" * 50 + "\n\n"
        
        report += f"分析ID: {analysis_result.get('analysis_id', 'N/A')}\n"
        report += f"分析时间: {analysis_result.get('timestamp', 'N/A')}\n"
        report += f"回答数量: {analysis_result.get('responses_count', 0)}\n\n"
        
        # 基础统计
        basic_stats = analysis_result.get('basic_statistics', {})
        report += f"基础统计:\n"
        report += f"- 总回答数: {basic_stats.get('total_responses', 0)}\n"
        report += f"- 唯一问题数: {basic_stats.get('unique_questions', 0)}\n"
        if basic_stats.get('completion_time'):
            report += f"- 完成时间: {basic_stats.get('completion_time'):.1f}秒\n"
        report += "\n"
        
        # 数据质量
        quality = analysis_result.get('data_quality', {})
        report += f"数据质量评估:\n"
        report += f"- 总体评分: {quality.get('overall_score', 0):.2f}\n"
        report += f"- 完整性: {quality.get('completeness', 0):.2f}\n"
        report += f"- 一致性: {quality.get('consistency', 0):.2f}\n"
        report += f"- 有效性: {quality.get('validity', 0):.2f}\n"
        report += f"- 及时性: {quality.get('timeliness', 0):.2f}\n\n"
        
        # 洞察
        insights = analysis_result.get('insights', [])
        if insights:
            report += f"关键洞察:\n"
            for i, insight in enumerate(insights, 1):
                report += f"{i}. {insight.get('title', 'N/A')}\n"
                report += f"   {insight.get('description', 'N/A')}\n"
                report += f"   重要性: {insight.get('significance', 'N/A')}\n\n"
        
        # 摘要
        summary = analysis_result.get('summary', {})
        if summary.get('key_findings'):
            report += f"主要发现:\n"
            for finding in summary['key_findings']:
                report += f"- {finding}\n"
            report += "\n"
        
        if summary.get('recommendations'):
            report += f"建议:\n"
            for rec in summary['recommendations']:
                report += f"- {rec}\n"
            report += "\n"
        
        return report

if __name__ == "__main__":
    # 测试数据分析智能体
    print("=== 数据分析智能体测试 ===")
    
    # 创建智能体
    analyzer = DataAnalyzerAgent()
    print(f"智能体创建成功: {analyzer}")
    
    # 测试数据分析
    from ..models.questionnaire import UserResponse
    
    # 模拟用户回答
    test_responses = [
        UserResponse("q1", "张三"),
        UserResponse("q2", "1"),
        UserResponse("q3", "55"),
        UserResponse("q4", "175"),
        UserResponse("q5", "70")
    ]
    
    import asyncio
    
    async def test_analysis():
        analysis_result = await analyzer.analyze_data(test_responses)
        print(f"数据分析完成")
        print(f"回答数量: {analysis_result.get('responses_count', 0)}")
        print(f"洞察数量: {len(analysis_result.get('insights', []))}")
        print(f"数据质量评分: {analysis_result.get('data_quality', {}).get('overall_score', 0):.2f}")
        
        # 导出报告
        text_report = analyzer.export_analysis_report(analysis_result, "text")
        print(f"\n文本报告:\n{text_report[:500]}...")
    
    # 运行测试
    asyncio.run(test_analysis())
    
    print("✅ 数据分析智能体测试完成")
