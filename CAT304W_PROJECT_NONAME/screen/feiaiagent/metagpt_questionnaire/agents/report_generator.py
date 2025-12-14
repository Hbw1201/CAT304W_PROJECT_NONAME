# -*- coding: utf-8 -*-
"""
报告生成智能体
负责生成专业的分析报告和可视化内容
"""

import logging
import json
from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path

from .base_agent import BaseAgent, register_agent
from ..models.questionnaire import AnalysisReport, RiskAssessment, Questionnaire

logger = logging.getLogger(__name__)

@register_agent
class ReportGeneratorAgent(BaseAgent):
    """报告生成智能体"""
    
    def __init__(self):
        super().__init__(
            name="报告生成专家",
            description="专业生成分析报告的智能体，擅长报告写作和数据可视化",
            expertise=["报告写作", "医学写作", "数据可视化", "内容组织", "格式规范"]
        )
        self.report_templates = self._load_report_templates()
        self.generated_reports: List[AnalysisReport] = []
    
    def _load_report_templates(self) -> Dict[str, Dict[str, Any]]:
        """加载报告模板"""
        return {
            "comprehensive": {
                "name": "综合分析报告",
                "sections": [
                    "执行摘要",
                    "背景介绍",
                    "数据概览",
                    "风险评估",
                    "数据分析",
                    "关键发现",
                    "建议和行动方案",
                    "附录"
                ],
                "target_length": "3000-5000字"
            },
            "executive": {
                "name": "执行摘要报告",
                "sections": [
                    "核心发现",
                    "风险评估",
                    "关键建议",
                    "后续行动"
                ],
                "target_length": "500-1000字"
            },
            "technical": {
                "name": "技术分析报告",
                "sections": [
                    "技术摘要",
                    "方法学",
                    "数据分析",
                    "统计结果",
                    "技术讨论",
                    "结论"
                ],
                "target_length": "2000-3000字"
            },
            "patient_friendly": {
                "name": "患者友好报告",
                "sections": [
                    "简单摘要",
                    "您的健康状况",
                    "风险评估",
                    "健康建议",
                    "下一步行动",
                    "常见问题"
                ],
                "target_length": "1500-2500字"
            }
        }
    
    async def process(self, input_data: Any) -> Any:
        """处理报告生成请求"""
        if isinstance(input_data, dict):
            # 检查是否包含 analysis_data 键，如果有则使用新的处理方式
            if 'analysis_data' in input_data:
                return await self.generate_report(
                    analysis_data=input_data.get('analysis_data', {}),
                    risk_assessment=input_data.get('risk_assessment'),
                    questionnaire=input_data.get('questionnaire'),
                    report_type=input_data.get('report_type', 'comprehensive'),
                    user_profile=input_data.get('user_profile', {})
                )
            else:
                # 兼容旧的调用方式
                return await self._process_legacy(input_data)
        else:
            raise ValueError(f"不支持的输入类型: {type(input_data)}")
    
    async def _process_legacy(self, analysis_data: Dict[str, Any]) -> Dict[str, Any]:
        """生成分析报告（兼容旧版本）"""
        logger.info(f"📝 {self.name} 开始生成报告")
        
        try:
            # 提取数据
            questionnaire = analysis_data.get('questionnaire')
            answered_questions = analysis_data.get('answered_questions', [])
            conversation_history = analysis_data.get('conversation_history', [])
            
            # 使用DeepSeek生成报告
            report_content = await self._generate_report_with_llm(
                questionnaire, answered_questions, conversation_history
            )
            
            logger.info(f"✅ {self.name} 报告生成完成")
            return {
                "status": "success",
                "report_content": report_content,
                "generated_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"❌ {self.name} 报告生成失败: {e}")
            return {
                "status": "error",
                "report_content": f"报告生成失败: {str(e)}",
                "error": str(e)
            }
    
    async def generate_report(self, analysis_data: Dict[str, Any],
                            risk_assessment: Optional[RiskAssessment] = None,
                            questionnaire: Optional[Questionnaire] = None,
                            report_type: str = "comprehensive",
                            user_profile: Optional[Dict[str, Any]] = None) -> AnalysisReport:
        """生成分析报告"""
        logger.info(f"📝 {self.name} 开始生成{report_type}报告")
        
        try:
            # 获取报告模板
            template = self.report_templates.get(report_type, self.report_templates["comprehensive"])
            
            # 生成报告内容
            content = await self._generate_report_content(
                template, analysis_data, risk_assessment, questionnaire, user_profile
            )
            
            # 创建报告对象
            report = AnalysisReport(
                session_id=f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                title=template["name"],
                content=content,
                risk_assessment=risk_assessment,
                data_insights=analysis_data.get('insights', []),
                generated_at=datetime.now()
            )
            
            # 保存到历史记录
            self.generated_reports.append(report)
            
            logger.info(f"✅ {self.name} 报告生成完成: {report.title}")
            return report
            
        except Exception as e:
            logger.error(f"❌ {self.name} 报告生成失败: {e}")
            # 返回默认报告
            return self._create_fallback_report(questionnaire, analysis_data.get('answered_questions', []))
    
    def _create_fallback_report(self, questionnaire, answered_questions) -> AnalysisReport:
        """创建备用报告"""
        from ..models.questionnaire import RiskLevel
        
        # 创建默认风险评估
        risk_assessment = RiskAssessment(
            session_id="fallback",
            overall_risk=RiskLevel.MEDIUM,
            risk_score=3.0,
            risk_factors=[{
                "factor": "default",
                "name": "默认评估",
                "level": "moderate",
                "score": 3.0,
                "details": "报告生成失败，使用默认评估",
                "description": "建议进行专业医学评估"
            }],
            recommendations=[
                "建议进行专业医学评估",
                "定期进行健康检查",
                "保持健康生活方式"
            ]
        )
        
        # 生成简单报告内容
        content = self._generate_fallback_report(questionnaire, answered_questions)
        
        return AnalysisReport(
            session_id=f"fallback_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            title="默认分析报告",
            content=content,
            risk_assessment=risk_assessment,
            data_insights=[],
            generated_at=datetime.now()
        )
    
    async def _generate_report_with_llm(self, questionnaire, answered_questions, conversation_history) -> str:
        """使用DeepSeek生成报告"""
        try:
            # 构建问答数据
            qa_data = []
            for response in answered_questions:
                question_text = "未知问题"
                for q in questionnaire.questions:
                    if q.id == response.question_id:
                        question_text = q.text
                        break
                qa_data.append(f"问题：{question_text}\n回答：{response.answer}")
            
            qa_text = "\n\n".join(qa_data)
            
            prompt = f"""You must respond in English only.
Do not output Chinese characters.

You are a board-certified pulmonary specialist who must generate a comprehensive lung cancer early-screening risk report based on the questionnaire data below.

Questionnaire info:
- Title: {questionnaire.title}
- Total questions: {len(questionnaire.questions)}
- Answered questions: {len(answered_questions)}

Patient responses:
{qa_text}

Report requirements:
1. Summary of key background information.
2. Risk assessment with analysis of major risk factors drawn from the answers.
3. Main findings and observations.
4. Clear medical recommendations.
5. Follow-up guidance and monitoring suggestions.

Style guidelines:
- Professional yet easy to understand.
- Evidence-based medical reasoning.
- Structured sections with headings and concise paragraphs.

Output only the final report text."""

            # 调用DeepSeek
            response = await self.call_llm(prompt)
            
            # 清理响应
            report = response.strip()
            if report and len(report) > 100:  # 确保有实际内容
                return report
            else:
                return self._generate_fallback_report(questionnaire, answered_questions)
                
        except Exception as e:
            logger.warning(f"⚠️ LLM报告生成失败: {e}")
            return self._generate_fallback_report(questionnaire, answered_questions)
    
    def _generate_fallback_report(self, questionnaire, answered_questions) -> str:
        """生成备用报告"""
        report = "肺癌早筛风险评估报告\n\n" + "=" * 50 + "\n\n"
        
        # 基本信息
        report += f"问卷完成时间: {datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')}\n"
        report += f"问卷标题: {questionnaire.title}\n"
        report += f"总问题数: {len(questionnaire.questions)}\n"
        report += f"已回答数: {len(answered_questions)}\n\n"
        
        # 用户回答
        report += "【用户回答】\n"
        for i, response in enumerate(answered_questions, 1):
            question_text = "未知问题"
            for q in questionnaire.questions:
                if q.id == response.question_id:
                    question_text = q.text
                    break
            
            report += f"{i}. {question_text}\n"
            report += f"   回答: {response.answer}\n\n"
        
        # 简单建议
        report += "【医学建议】\n"
        report += "1. 建议定期进行健康体检\n"
        report += "2. 保持良好的生活习惯\n"
        report += "3. 如有异常症状请及时就医\n"
        report += "4. 定期进行胸部影像检查\n"
        
        return report

    async def _generate_report_content(self, template: Dict[str, Any],
                                     analysis_data: Dict[str, Any],
                                     risk_assessment: Optional[RiskAssessment],
                                     questionnaire: Optional[Questionnaire],
                                     user_profile: Optional[Dict[str, Any]]) -> str:
        """生成报告内容"""
        content = f"# {template['name']}\n\n"
        content += f"**生成时间**: {datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')}\n"
        content += f"**报告类型**: {template['name']}\n"
        content += f"**目标长度**: {template['target_length']}\n\n"
        
        # 生成各个章节内容
        for section in template['sections']:
            section_content = await self._generate_section_content(
                section, analysis_data, risk_assessment, questionnaire, user_profile
            )
            content += f"## {section}\n\n{section_content}\n\n"
        
        return content
    
    async def _generate_section_content(self, section: str,
                                      analysis_data: Dict[str, Any],
                                      risk_assessment: Optional[RiskAssessment],
                                      questionnaire: Optional[Questionnaire],
                                      user_profile: Optional[Dict[str, Any]]) -> str:
        """生成章节内容"""
        if section == "执行摘要":
            return await self._generate_executive_summary_section(analysis_data, risk_assessment)
        elif section == "背景介绍":
            return self._generate_background_section(questionnaire, user_profile)
        elif section == "数据概览":
            return self._generate_data_overview_section(analysis_data)
        elif section == "风险评估":
            return self._generate_risk_assessment_section(risk_assessment)
        elif section == "数据分析":
            return self._generate_data_analysis_section(analysis_data)
        elif section == "关键发现":
            return self._generate_key_findings_section(analysis_data)
        elif section == "建议和行动方案":
            return await self._generate_recommendations_section(analysis_data, risk_assessment, user_profile)
        elif section == "附录":
            return self._generate_appendix_section(analysis_data, questionnaire)
        elif section == "核心发现":
            return self._generate_core_findings_section(analysis_data)
        elif section == "关键建议":
            return await self._generate_key_recommendations_section(analysis_data, risk_assessment)
        elif section == "后续行动":
            return self._generate_next_steps_section(analysis_data, risk_assessment)
        elif section == "简单摘要":
            return self._generate_simple_summary_section(analysis_data, risk_assessment)
        elif section == "您的健康状况":
            return self._generate_health_status_section(analysis_data, risk_assessment)
        elif section == "健康建议":
            return await self._generate_health_advice_section(analysis_data, risk_assessment)
        elif section == "下一步行动":
            return self._generate_next_actions_section(analysis_data, risk_assessment)
        elif section == "常见问题":
            return self._generate_faq_section(analysis_data, risk_assessment)
        else:
            return f"## {section}\n\n该章节内容正在生成中...\n"
    
    async def _generate_executive_summary_section(self, analysis_data: Dict[str, Any],
                                                risk_assessment: Optional[RiskAssessment]) -> str:
        """生成执行摘要章节"""
        content = ""
        
        # 数据概览
        if analysis_data.get('responses_count'):
            content += f"本次分析基于{analysis_data['responses_count']}个问卷回答。"
        
        # 风险评估摘要
        if risk_assessment:
            risk_level_emoji = {
                "low": "🟢",
                "medium": "🟡", 
                "high": "🔴"
            }
            emoji = risk_level_emoji.get(risk_assessment.overall_risk.value, "⚪")
            content += f"\n\n**风险评估**: {emoji} {risk_assessment.overall_risk.value.upper()}风险"
            content += f"\n- 风险评分: {risk_assessment.risk_score:.1f}"
            content += f"\n- 主要风险因素: {len(risk_assessment.risk_factors)}个"
        
        # 关键洞察
        insights = analysis_data.get('insights', [])
        if insights:
            content += f"\n\n**关键洞察**:"
            for i, insight in enumerate(insights[:3], 1):  # 只显示前3个
                content += f"\n{i}. {insight.get('title', 'N/A')}"
        
        # 数据质量
        quality = analysis_data.get('data_quality', {})
        if quality.get('overall_score'):
            content += f"\n\n**数据质量**: 总体评分 {quality['overall_score']:.2f}/1.00"
        
        return content
    
    def _generate_background_section(self, questionnaire: Optional[Questionnaire],
                                   user_profile: Optional[Dict[str, Any]]) -> str:
        """生成背景介绍章节"""
        content = "## 背景介绍\n\n"
        
        if questionnaire:
            content += f"**问卷信息**:\n"
            content += f"- 标题: {questionnaire.title}\n"
            content += f"- 描述: {questionnaire.description}\n"
            content += f"- 版本: {questionnaire.version}\n"
            content += f"- 问题数量: {len(questionnaire.questions)}个\n"
            content += f"- 预计完成时间: {questionnaire.estimated_time}\n"
            content += f"- 问题分类: {', '.join(questionnaire.categories)}\n\n"
        
        if user_profile:
            content += f"**用户背景**:\n"
            for key, value in user_profile.items():
                if key not in ['session_id', 'password']:  # 排除敏感信息
                    content += f"- {key}: {value}\n"
            content += "\n"
        
        content += "本报告旨在通过专业的问卷分析，为用户提供个性化的健康风险评估和改善建议。"
        
        return content
    
    def _generate_data_overview_section(self, analysis_data: Dict[str, Any]) -> str:
        """生成数据概览章节"""
        content = "## 数据概览\n\n"
        
        # 基础统计
        basic_stats = analysis_data.get('basic_statistics', {})
        if basic_stats:
            content += f"**基础统计信息**:\n"
            content += f"- 总回答数: {basic_stats.get('total_responses', 0)}\n"
            content += f"- 唯一问题数: {basic_stats.get('unique_questions', 0)}\n"
            if basic_stats.get('completion_time'):
                content += f"- 完成时间: {basic_stats.get('completion_time'):.1f}秒\n"
            content += "\n"
        
        # 分类分析
        category_analysis = analysis_data.get('category_analysis', {})
        if category_analysis:
            content += f"**分类分析**:\n"
            for category, stats in category_analysis.items():
                completion_rate = stats.get('completion_rate', 0) * 100
                content += f"- {category}: {stats.get('question_count', 0)}个问题，"
                content += f"完成率 {completion_rate:.1f}%\n"
            content += "\n"
        
        # 数据质量
        quality = analysis_data.get('data_quality', {})
        if quality:
            content += f"**数据质量评估**:\n"
            content += f"- 完整性: {quality.get('completeness', 0):.1%}\n"
            content += f"- 一致性: {quality.get('consistency', 0):.1%}\n"
            content += f"- 有效性: {quality.get('validity', 0):.1%}\n"
            content += f"- 及时性: {quality.get('timeliness', 0):.1%}\n"
            content += f"- 总体评分: {quality.get('overall_score', 0):.2f}/1.00\n"
        
        return content
    
    def _generate_risk_assessment_section(self, risk_assessment: Optional[RiskAssessment]) -> str:
        """生成风险评估章节"""
        content = "## 风险评估\n\n"
        
        if not risk_assessment:
            content += "风险评估数据不可用。\n"
            return content
        
        # 总体风险评估
        risk_level_emoji = {
            "low": "🟢",
            "medium": "🟡",
            "high": "🔴"
        }
        emoji = risk_level_emoji.get(risk_assessment.overall_risk.value, "⚪")
        
        content += f"**总体风险评估**: {emoji} {risk_assessment.overall_risk.value.upper()}风险\n\n"
        content += f"- **风险评分**: {risk_assessment.risk_score:.1f}\n"
        content += f"- **评估时间**: {risk_assessment.assessed_at.strftime('%Y年%m月%d日 %H:%M:%S')}\n\n"
        
        # 风险因素分析
        if risk_assessment.risk_factors:
            content += f"**风险因素分析**:\n\n"
            for i, factor in enumerate(risk_assessment.risk_factors, 1):
                content += f"{i}. **{factor.get('name', 'N/A')}**\n"
                content += f"   - 风险等级: {factor.get('level', 'N/A')}\n"
                content += f"   - 风险评分: {factor.get('score', 0):.1f}\n"
                content += f"   - 详细信息: {factor.get('details', 'N/A')}\n"
                content += f"   - 说明: {factor.get('description', 'N/A')}\n\n"
        
        return content
    
    def _generate_data_analysis_section(self, analysis_data: Dict[str, Any]) -> str:
        """生成数据分析章节"""
        content = "## 数据分析\n\n"
        
        # 模式识别
        pattern_analysis = analysis_data.get('pattern_analysis', {})
        if pattern_analysis:
            content += f"**模式识别结果**:\n\n"
            
            identified_patterns = pattern_analysis.get('identified_patterns', [])
            if identified_patterns:
                content += f"**识别的主要模式**:\n"
                for i, pattern in enumerate(identified_patterns, 1):
                    content += f"{i}. {pattern}\n"
                content += "\n"
            
            anomaly_patterns = pattern_analysis.get('anomaly_patterns', [])
            if anomaly_patterns:
                content += f"**异常模式**:\n"
                for i, pattern in enumerate(anomaly_patterns, 1):
                    content += f"{i}. {pattern}\n"
                content += "\n"
            
            behavioral_insights = pattern_analysis.get('behavioral_insights', [])
            if behavioral_insights:
                content += f"**行为洞察**:\n"
                for i, insight in enumerate(behavioral_insights, 1):
                    content += f"{i}. {insight}\n"
                content += "\n"
        
        # 洞察分析
        insights = analysis_data.get('insights', [])
        if insights:
            content += f"**数据洞察**:\n\n"
            for i, insight in enumerate(insights, 1):
                significance_emoji = {
                    "high": "🔴",
                    "medium": "🟡",
                    "low": "🟢"
                }
                emoji = significance_emoji.get(insight.get('significance', 'medium'), '🟡')
                content += f"{i}. {emoji} **{insight.get('title', 'N/A')}**\n"
                content += f"   {insight.get('description', 'N/A')}\n"
                content += f"   重要性: {insight.get('significance', 'N/A')}\n\n"
        
        return content
    
    def _generate_key_findings_section(self, analysis_data: Dict[str, Any]) -> str:
        """生成关键发现章节"""
        content = "## 关键发现\n\n"
        
        # 数据质量发现
        quality = analysis_data.get('data_quality', {})
        if quality:
            content += f"**数据质量发现**:\n"
            if quality.get('overall_score', 0) >= 0.8:
                content += f"- 🟢 数据质量优秀，总体评分 {quality['overall_score']:.2f}/1.00\n"
            elif quality.get('overall_score', 0) >= 0.6:
                content += f"- 🟡 数据质量良好，总体评分 {quality['overall_score']:.2f}/1.00\n"
            else:
                content += f"- 🔴 数据质量需要改善，总体评分 {quality['overall_score']:.2f}/1.00\n"
            
            # 具体质量问题
            quality_issues = quality.get('quality_issues', [])
            if quality_issues:
                content += f"- 主要问题:\n"
                for issue in quality_issues:
                    content += f"  • {issue}\n"
            content += "\n"
        
        # 模式发现
        pattern_analysis = analysis_data.get('pattern_analysis', {})
        if pattern_analysis:
            identified_patterns = pattern_analysis.get('identified_patterns', [])
            if identified_patterns:
                content += f"**模式发现**:\n"
                for i, pattern in enumerate(identified_patterns, 1):
                    content += f"{i}. {pattern}\n"
                content += "\n"
        
        # 洞察发现
        insights = analysis_data.get('insights', [])
        if insights:
            content += f"**洞察发现**:\n"
            high_significance = [i for i in insights if i.get('significance') == 'high']
            if high_significance:
                content += f"- 发现 {len(high_significance)} 个高重要性洞察\n"
            content += f"- 总计 {len(insights)} 个洞察\n\n"
        
        return content
    
    async def _generate_recommendations_section(self, analysis_data: Dict[str, Any],
                                              risk_assessment: Optional[RiskAssessment],
                                              user_profile: Optional[Dict[str, Any]]) -> str:
        """生成建议和行动方案章节"""
        content = "## 建议和行动方案\n\n"
        
        # 风险评估建议
        if risk_assessment:
            content += f"**基于风险评估的建议**:\n\n"
            
            if risk_assessment.recommendations:
                for i, rec in enumerate(risk_assessment.recommendations, 1):
                    content += f"{i}. {rec}\n"
                content += "\n"
            
            # 根据风险等级给出具体建议
            risk_level = risk_assessment.overall_risk.value
            if risk_level == "high":
                content += f"**高风险人群特别建议**:\n"
                content += f"- 立即就医进行详细检查\n"
                content += f"- 定期进行专业医学评估\n"
                content += f"- 密切关注症状变化\n"
                content += f"- 寻求专业健康管理指导\n\n"
            elif risk_level == "medium":
                content += f"**中风险人群建议**:\n"
                content += f"- 定期体检和监测\n"
                content += f"- 改善生活方式\n"
                content += f"- 关注早期症状\n"
                content += f"- 建立健康档案\n\n"
            else:
                content += f"**低风险人群建议**:\n"
                content += f"- 保持健康生活方式\n"
                content += f"- 定期体检\n"
                content += f"- 预防性健康管理\n\n"
        
        # 数据质量建议
        quality = analysis_data.get('data_quality', {})
        if quality:
            content += f"**数据质量改善建议**:\n"
            if quality.get('completeness', 0) < 0.8:
                content += f"- 提高问卷完成率\n"
            if quality.get('consistency', 0) < 0.7:
                content += f"- 改善问题设计，提高答案一致性\n"
            if quality.get('validity', 0) < 0.9:
                content += f"- 加强答案验证机制\n"
            content += "\n"
        
        # 个性化建议
        if user_profile:
            content += f"**个性化建议**:\n"
            age = user_profile.get('age')
            if age and int(age) > 50:
                content += f"- 建议增加体检频率\n"
                content += f"- 关注年龄相关健康风险\n"
            content += "\n"
        
        return content
    
    def _generate_appendix_section(self, analysis_data: Dict[str, Any],
                                 questionnaire: Optional[Questionnaire]) -> str:
        """生成附录章节"""
        content = "## 附录\n\n"
        
        # 技术细节
        content += f"**技术信息**:\n"
        content += f"- 分析时间: {datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')}\n"
        content += f"- 分析工具: MetaGPT智能体系统\n"
        content += f"- 数据格式: JSON\n\n"
        
        # 问卷详情
        if questionnaire:
            content += f"**问卷详情**:\n"
            content += f"- 问卷ID: {questionnaire.id}\n"
            content += f"- 创建时间: {questionnaire.created_at.strftime('%Y年%m月%d日')}\n"
            content += f"- 问题分类: {', '.join(questionnaire.categories)}\n\n"
        
        # 数据统计
        if analysis_data:
            content += f"**数据统计**:\n"
            content += f"- 回答数量: {analysis_data.get('responses_count', 0)}\n"
            content += f"- 分析类型: {analysis_data.get('analysis_type', 'N/A')}\n"
            content += f"- 数据质量评分: {analysis_data.get('data_quality', {}).get('overall_score', 0):.2f}\n"
        
        return content
    
    async def _generate_executive_summary(self, report_content: str) -> str:
        """生成执行摘要"""
        try:
            prompt = f"""You must respond in English only.
Do not output Chinese characters.

You are a medical communications specialist. Write a concise executive summary of the following lung cancer screening report.

Full report content:
{report_content}

The summary should:
1. Highlight the patient's overall risk status and major findings.
2. Mention the most important recommendations.
3. Use 2-3 short paragraphs or bullet points.
4. Avoid repeating section headings verbatim.

Return only the executive summary."""
            llm_response = await self.call_llm(prompt)
            return llm_response
        except Exception as e:
            logger.error(f"❌ 执行摘要生成失败: {e}")
            return self._extract_executive_summary(report_content)
    
    def _extract_executive_summary(self, report_content: str) -> str:
        """提取执行摘要"""
        # 简单的摘要提取逻辑
        lines = report_content.split('\n')
        summary_lines = []
        
        for line in lines:
            if line.startswith('## 执行摘要') or line.startswith('## 核心发现'):
                continue
            elif line.startswith('##'):
                break
            elif line.strip():
                summary_lines.append(line.strip())
        
        return '\n'.join(summary_lines[:10])  # 限制前10行
    
    def _create_default_risk_assessment(self) -> RiskAssessment:
        """创建默认风险评估"""
        from ..models.questionnaire import RiskLevel
        
        return RiskAssessment(
            session_id="unknown",
            overall_risk=RiskLevel.MEDIUM,
            risk_score=3.0,
            risk_factors=[{
                "factor": "default",
                "name": "默认评估",
                "level": "moderate",
                "score": 3.0,
                "details": "风险评估数据不可用",
                "description": "建议进行专业医学评估"
            }],
            recommendations=[
                "建议进行专业医学评估",
                "定期进行健康检查",
                "保持健康生活方式"
            ]
        )
    
    def _create_error_report(self, error_message: str, user_profile: Optional[Dict[str, Any]]) -> AnalysisReport:
        """创建错误报告"""
        return AnalysisReport(
            session_id=user_profile.get('session_id', 'unknown') if user_profile else 'unknown',
            title="报告生成失败",
            content=f"报告生成过程中出现错误: {error_message}",
            risk_assessment=self._create_default_risk_assessment(),
            data_insights=[],
            generated_at=datetime.now()
        )
    
    # 其他章节生成方法的简化实现
    def _generate_core_findings_section(self, analysis_data: Dict[str, Any]) -> str:
        """生成核心发现章节（简化版）"""
        return self._generate_key_findings_section(analysis_data)
    
    async def _generate_key_recommendations_section(self, analysis_data: Dict[str, Any],
                                                  risk_assessment: Optional[RiskAssessment]) -> str:
        """生成关键建议章节（简化版）"""
        return await self._generate_recommendations_section(analysis_data, risk_assessment, {})
    
    def _generate_next_steps_section(self, analysis_data: Dict[str, Any],
                                   risk_assessment: Optional[RiskAssessment]) -> str:
        """生成后续行动章节"""
        content = "## 后续行动\n\n"
        content += "1. 定期复查和监测\n"
        content += "2. 执行健康改善计划\n"
        content += "3. 寻求专业医疗建议\n"
        content += "4. 建立健康档案\n"
        return content
    
    def _generate_simple_summary_section(self, analysis_data: Dict[str, Any],
                                       risk_assessment: Optional[RiskAssessment]) -> str:
        """生成简单摘要章节"""
        return self._generate_executive_summary_section(analysis_data, risk_assessment)
    
    def _generate_health_status_section(self, analysis_data: Dict[str, Any],
                                      risk_assessment: Optional[RiskAssessment]) -> str:
        """生成健康状况章节"""
        content = "## 您的健康状况\n\n"
        if risk_assessment:
            content += f"根据问卷分析，您的健康风险等级为: {risk_assessment.overall_risk.value.upper()}\n"
            content += f"风险评分: {risk_assessment.risk_score:.1f}\n"
        return content
    
    async def _generate_health_advice_section(self, analysis_data: Dict[str, Any],
                                            risk_assessment: Optional[RiskAssessment]) -> str:
        """生成健康建议章节"""
        return await self._generate_recommendations_section(analysis_data, risk_assessment, {})
    
    def _generate_next_actions_section(self, analysis_data: Dict[str, Any],
                                     risk_assessment: Optional[RiskAssessment]) -> str:
        """生成下一步行动章节"""
        return self._generate_next_steps_section(analysis_data, risk_assessment)
    
    def _generate_faq_section(self, analysis_data: Dict[str, Any],
                            risk_assessment: Optional[RiskAssessment]) -> str:
        """生成常见问题章节"""
        content = "## 常见问题\n\n"
        content += "**Q: 这个报告准确吗？**\n"
        content += "A: 本报告基于您提供的问卷回答生成，建议结合专业医疗建议使用。\n\n"
        content += "**Q: 我需要立即就医吗？**\n"
        content += "A: 请根据风险评估结果和您的具体情况决定，如有疑问请咨询医生。\n\n"
        content += "**Q: 如何改善我的健康状况？**\n"
        content += "A: 请参考报告中的具体建议，并咨询专业健康管理师。\n"
        return content
    
    def get_report_templates(self) -> Dict[str, Dict[str, Any]]:
        """获取报告模板"""
        return self.report_templates
    
    def get_generated_reports(self) -> List[AnalysisReport]:
        """获取已生成的报告"""
        return self.generated_reports
    
    def export_report(self, report: AnalysisReport, format: str = "markdown",
                     output_path: Optional[str] = None) -> str:
        """导出报告"""
        if format.lower() == "markdown":
            content = report.content
        elif format.lower() == "json":
            content = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
        elif format.lower() == "text":
            content = self._convert_markdown_to_text(report.content)
        else:
            raise ValueError(f"不支持的导出格式: {format}")
        
        # 保存到文件
        if output_path:
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info(f"✅ 报告已保存到: {output_path}")
        
        return content
    
    def _convert_markdown_to_text(self, markdown_content: str) -> str:
        """将Markdown转换为纯文本"""
        # 简单的Markdown到文本转换
        text = markdown_content
        
        # 移除标题标记
        text = text.replace('#', '')
        text = text.replace('**', '')
        text = text.replace('*', '')
        
        # 移除多余空行
        lines = text.split('\n')
        cleaned_lines = []
        for line in lines:
            if line.strip():
                cleaned_lines.append(line.strip())
        
        return '\n'.join(cleaned_lines)

if __name__ == "__main__":
    # 测试报告生成智能体
    print("=== 报告生成智能体测试 ===")
    
    # 创建智能体
    generator = ReportGeneratorAgent()
    print(f"智能体创建成功: {generator}")
    
    # 测试模板获取
    templates = generator.get_report_templates()
    print(f"可用模板: {list(templates.keys())}")
    
    # 测试报告生成
    import asyncio
    
    async def test_report_generation():
        # 模拟分析数据
        analysis_data = {
            "responses_count": 15,
            "insights": [
                {"title": "数据质量良好", "description": "问卷完成度高", "significance": "high"}
            ],
            "data_quality": {"overall_score": 0.85}
        }
        
        report = await generator.generate_report(
            analysis_data=analysis_data,
            report_type="executive"
        )
        
        print(f"报告生成成功: {report.title}")
        print(f"报告长度: {len(report.content)} 字符")
        
        # 导出报告
        markdown_content = generator.export_report(report, "markdown")
        print(f"Markdown报告预览:\n{markdown_content[:200]}...")
    
    # 运行测试
    asyncio.run(test_report_generation())
    
    print("✅ 报告生成智能体测试完成")
