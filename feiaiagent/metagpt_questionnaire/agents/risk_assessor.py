# -*- coding: utf-8 -*-
"""
风险评估智能体
负责分析用户回答并评估健康风险
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

from .base_agent import BaseAgent, register_agent
from ..models.questionnaire import RiskAssessment, RiskLevel, UserResponse, Question

logger = logging.getLogger(__name__)

@register_agent
class RiskAssessorAgent(BaseAgent):
    """风险评估智能体"""
    
    def __init__(self):
        super().__init__(
            name="风险评估专家",
            description="专业评估健康风险的智能体，基于科学证据进行风险评估",
            expertise=["医学诊断", "风险评估", "预防医学", "健康管理"]
        )
        self.risk_factors = self._load_risk_factors()
        self.assessment_history: List[RiskAssessment] = []
    
    def _load_risk_factors(self) -> Dict[str, Dict[str, Any]]:
        """加载风险因素定义"""
        return {
            "smoking": {
                "name": "吸烟史",
                "weight": 2.0,
                "description": "吸烟是肺癌的主要风险因素",
                "levels": {
                    "none": {"score": 0, "description": "无吸烟史"},
                    "light": {"score": 1, "description": "轻度吸烟（<10包年）"},
                    "moderate": {"score": 2, "description": "中度吸烟（10-30包年）"},
                    "heavy": {"score": 3, "description": "重度吸烟（>30包年）"}
                }
            },
            "age": {
                "name": "年龄",
                "weight": 1.5,
                "description": "年龄是癌症风险的重要因素",
                "levels": {
                    "young": {"score": 0, "description": "40岁以下"},
                    "middle": {"score": 1, "description": "40-60岁"},
                    "elderly": {"score": 2, "description": "60岁以上"}
                }
            },
            "occupational_exposure": {
                "name": "职业暴露",
                "weight": 2.0,
                "description": "接触致癌物质增加风险",
                "levels": {
                    "none": {"score": 0, "description": "无职业暴露"},
                    "low": {"score": 1, "description": "低度暴露"},
                    "high": {"score": 2, "description": "高度暴露"}
                }
            },
            "family_history": {
                "name": "家族史",
                "weight": 2.0,
                "description": "遗传因素影响风险",
                "levels": {
                    "none": {"score": 0, "description": "无家族史"},
                    "first_degree": {"score": 2, "description": "一级亲属"},
                    "second_degree": {"score": 1, "description": "二级亲属"}
                }
            },
            "symptoms": {
                "name": "症状",
                "weight": 3.0,
                "description": "特定症状提示高风险",
                "levels": {
                    "none": {"score": 0, "description": "无症状"},
                    "mild": {"score": 1, "description": "轻度症状"},
                    "severe": {"score": 3, "description": "严重症状"}
                }
            },
            "lifestyle": {
                "name": "生活方式",
                "weight": 1.0,
                "description": "不健康生活方式增加风险",
                "levels": {
                    "healthy": {"score": 0, "description": "健康生活方式"},
                    "moderate": {"score": 1, "description": "一般生活方式"},
                    "unhealthy": {"score": 2, "description": "不健康生活方式"}
                }
            }
        }
    
    async def process(self, input_data: Any) -> Any:
        """处理风险评估请求"""
        if isinstance(input_data, dict) and 'responses' in input_data:
            # 如果是包含回答的字典
            responses = input_data['responses']
            questionnaire = input_data.get('questionnaire')
            user_profile = input_data.get('user_profile', {})
            return await self.assess_risk(responses, questionnaire, user_profile)
        elif isinstance(input_data, list):
            # 如果是回答列表
            return await self.assess_risk(input_data)
        else:
            raise ValueError(f"不支持的输入类型: {type(input_data)}")
    
    async def assess_risk(self, responses: List[UserResponse], 
                         questionnaire: Optional[Any] = None,
                         user_profile: Optional[Dict[str, Any]] = None) -> RiskAssessment:
        """评估健康风险"""
        logger.info(f"🔍 {self.name} 开始风险评估，回答数量: {len(responses)}")
        
        try:
            # 解析用户回答
            parsed_responses = self._parse_responses(responses)
            
            # 计算风险评分
            risk_score, risk_factors = self._calculate_risk_score(parsed_responses)
            
            # 确定风险等级
            risk_level = self._determine_risk_level(risk_score)
            
            # 生成个性化建议
            recommendations = await self._generate_recommendations(
                risk_level, risk_factors, user_profile
            )
            
            # 创建风险评估结果
            assessment = RiskAssessment(
                session_id=user_profile.get('session_id', 'unknown'),
                overall_risk=risk_level,
                risk_score=risk_score,
                risk_factors=risk_factors,
                recommendations=recommendations
            )
            
            # 保存到历史记录
            self.assessment_history.append(assessment)
            
            logger.info(f"✅ {self.name} 风险评估完成: {risk_level.value} (评分: {risk_score})")
            return assessment
            
        except Exception as e:
            logger.error(f"❌ {self.name} 风险评估失败: {e}")
            # 返回默认评估结果
            return self._create_default_assessment(user_profile)
    
    def _parse_responses(self, responses: List[UserResponse]) -> Dict[str, Any]:
        """解析用户回答"""
        parsed = {}
        for response in responses:
            parsed[response.question_id] = response.answer
        return parsed
    
    def _calculate_risk_score(self, responses: Dict[str, Any]) -> Tuple[float, List[Dict[str, Any]]]:
        """计算风险评分"""
        total_score = 0.0
        risk_factors = []
        
        # 分析吸烟史
        if 'smoking' in responses:
            smoking_score, smoking_factor = self._assess_smoking_risk(responses)
            total_score += smoking_score
            risk_factors.append(smoking_factor)
        
        # 分析年龄风险
        if 'age' in responses:
            age_score, age_factor = self._assess_age_risk(responses['age'])
            total_score += age_score
            risk_factors.append(age_factor)
        
        # 分析职业暴露
        if 'occupational_exposure' in responses:
            exposure_score, exposure_factor = self._assess_occupational_risk(responses)
            total_score += exposure_score
            risk_factors.append(exposure_factor)
        
        # 分析家族史
        if 'family_history' in responses:
            family_score, family_factor = self._assess_family_risk(responses)
            total_score += family_score
            risk_factors.append(family_factor)
        
        # 分析症状
        if 'cough' in responses or 'hemoptysis' in responses or 'weight_loss' in responses:
            symptom_score, symptom_factor = self._assess_symptom_risk(responses)
            total_score += symptom_score
            risk_factors.append(symptom_factor)
        
        # 分析生活方式
        lifestyle_score, lifestyle_factor = self._assess_lifestyle_risk(responses)
        total_score += lifestyle_score
        risk_factors.append(lifestyle_factor)
        
        return total_score, risk_factors
    
    def _assess_smoking_risk(self, responses: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
        """评估吸烟风险"""
        smoking_answer = responses.get('smoking')
        smoking_years = responses.get('smoking_years', 0)
        daily_cigarettes = responses.get('daily_cigarettes', 0)
        
        if smoking_answer == "1":  # 有吸烟史
            # 计算包年数
            try:
                pack_years = (float(smoking_years) * float(daily_cigarettes)) / 20
                
                if pack_years > 30:
                    level = "heavy"
                    score = 3.0
                elif pack_years > 10:
                    level = "moderate"
                    score = 2.0
                else:
                    level = "light"
                    score = 1.0
                    
                factor_info = {
                    "factor": "smoking",
                    "name": "吸烟史",
                    "level": level,
                    "score": score,
                    "details": f"包年数: {pack_years:.1f}",
                    "description": self.risk_factors["smoking"]["levels"][level]["description"]
                }
                
                return score * self.risk_factors["smoking"]["weight"], factor_info
                
            except (ValueError, TypeError):
                # 如果计算失败，使用默认评分
                factor_info = {
                    "factor": "smoking",
                    "name": "吸烟史",
                    "level": "moderate",
                    "score": 2.0,
                    "details": "有吸烟史（具体数据缺失）",
                    "description": "吸烟增加肺癌风险"
                }
                return 2.0 * self.risk_factors["smoking"]["weight"], factor_info
        else:
            # 无吸烟史
            factor_info = {
                "factor": "smoking",
                "name": "吸烟史",
                "level": "none",
                "score": 0.0,
                "details": "无吸烟史",
                "description": "无吸烟史，降低风险"
            }
            return 0.0, factor_info
    
    def _assess_age_risk(self, age: Any) -> Tuple[float, Dict[str, Any]]:
        """评估年龄风险"""
        try:
            age_num = float(age)
            if age_num >= 60:
                level = "elderly"
                score = 2.0
            elif age_num >= 40:
                level = "middle"
                score = 1.0
            else:
                level = "young"
                score = 0.0
        except (ValueError, TypeError):
            level = "middle"
            score = 1.0
        
        factor_info = {
            "factor": "age",
            "name": "年龄",
            "level": level,
            "score": score,
            "details": f"年龄: {age}",
            "description": self.risk_factors["age"]["levels"][level]["description"]
        }
        
        return score * self.risk_factors["age"]["weight"], factor_info
    
    def _assess_occupational_risk(self, responses: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
        """评估职业暴露风险"""
        exposure_answer = responses.get('occupational_exposure')
        exposure_years = responses.get('exposure_years', 0)
        
        if exposure_answer == "1":  # 有职业暴露
            try:
                years = float(exposure_years)
                if years > 10:
                    level = "high"
                    score = 2.0
                else:
                    level = "low"
                    score = 1.0
            except (ValueError, TypeError):
                level = "moderate"
                score = 1.5
        else:
            level = "none"
            score = 0.0
        
        factor_info = {
            "factor": "occupational_exposure",
            "name": "职业暴露",
            "level": level,
            "score": score,
            "details": f"职业暴露: {'是' if exposure_answer == '1' else '否'}",
            "description": self.risk_factors["occupational_exposure"]["levels"][level]["description"]
        }
        
        return score * self.risk_factors["occupational_exposure"]["weight"], factor_info
    
    def _assess_family_risk(self, responses: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
        """评估家族史风险"""
        family_answer = responses.get('family_history')
        
        if family_answer == "1":  # 有家族史
            level = "first_degree"
            score = 2.0
        else:
            level = "none"
            score = 0.0
        
        factor_info = {
            "factor": "family_history",
            "name": "家族史",
            "level": level,
            "score": score,
            "details": f"家族史: {'有' if family_answer == '1' else '无'}",
            "description": self.risk_factors["family_history"]["levels"][level]["description"]
        }
        
        return score * self.risk_factors["family_history"]["weight"], factor_info
    
    def _assess_symptom_risk(self, responses: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
        """评估症状风险"""
        symptoms = []
        total_score = 0.0
        
        # 检查各种症状
        if responses.get('cough') == "1":
            symptoms.append("持续性干咳")
            total_score += 1.0
        
        if responses.get('hemoptysis') == "1":
            symptoms.append("痰中带血")
            total_score += 2.0
        
        if responses.get('weight_loss') == "1":
            symptoms.append("不明原因消瘦")
            total_score += 1.0
        
        if total_score == 0:
            level = "none"
            score = 0.0
        elif total_score <= 2:
            level = "mild"
            score = 1.0
        else:
            level = "severe"
            score = 3.0
        
        factor_info = {
            "factor": "symptoms",
            "name": "症状",
            "level": level,
            "score": score,
            "details": f"症状: {', '.join(symptoms) if symptoms else '无症状'}",
            "description": self.risk_factors["symptoms"]["levels"][level]["description"]
        }
        
        return score * self.risk_factors["symptoms"]["weight"], factor_info
    
    def _assess_lifestyle_risk(self, responses: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
        """评估生活方式风险"""
        # 这里可以根据更多的生活方式因素进行评估
        # 暂时使用默认评分
        level = "moderate"
        score = 1.0
        
        factor_info = {
            "factor": "lifestyle",
            "name": "生活方式",
            "level": level,
            "score": score,
            "details": "基于问卷回答评估",
            "description": self.risk_factors["lifestyle"]["levels"][level]["description"]
        }
        
        return score * self.risk_factors["lifestyle"]["weight"], factor_info
    
    def _determine_risk_level(self, risk_score: float) -> RiskLevel:
        """确定风险等级"""
        if risk_score >= 6.0:
            return RiskLevel.HIGH
        elif risk_score >= 3.0:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW
    
    async def _generate_recommendations(self, risk_level: RiskLevel, 
                                      risk_factors: List[Dict[str, Any]],
                                      user_profile: Optional[Dict[str, Any]] = None) -> List[str]:
        """生成个性化建议"""
        logger.info(f"💡 {self.name} 开始生成建议，风险等级: {risk_level.value}")
        
        try:
            assessment_payload = {
                "risk_level": risk_level.value,
                "risk_score": sum(factor.get("score", 0) for factor in risk_factors),
                "risk_factors": risk_factors
            }
            prompt = f"""You must respond in English only.
Do not output Chinese characters.

You are a preventive medicine specialist. Provide personalized lung-health recommendations based on the following assessment data and user profile.

Risk assessment summary: {assessment_payload}
User profile: {user_profile or {}}

Guidelines:
1. Tailor advice to the stated risk level.
2. Address the most significant risk factors explicitly.
3. Provide practical, evidence-based actions (screening, lifestyle, environmental precautions).
4. Keep suggestions empathetic and encouraging.

Return a numbered list of clear recommendations."""
            
            # 调用LLM生成建议
            llm_response = await self.call_llm(prompt)
            
            # 解析建议
            recommendations = self._parse_recommendations(llm_response)
            
            # 如果没有生成建议，使用默认建议
            if not recommendations:
                recommendations = self._get_default_recommendations(risk_level, risk_factors)
            
            logger.info(f"✅ {self.name} 建议生成完成: {len(recommendations)} 条")
            return recommendations
            
        except Exception as e:
            logger.error(f"❌ {self.name} 建议生成失败: {e}")
            return self._get_default_recommendations(risk_level, risk_factors)
    
    def _parse_recommendations(self, llm_response: str) -> List[str]:
        """解析LLM生成的建议"""
        recommendations = []
        
        # 简单的文本解析逻辑
        lines = llm_response.split('\n')
        for line in lines:
            line = line.strip()
            if line and (line.startswith('-') or line.startswith('•') or 
                        line.startswith('1.') or line.startswith('2.') or
                        line.startswith('3.') or line.startswith('4.') or
                        line.startswith('5.')):
                # 移除标记符号
                clean_line = line.lstrip('-•1234567890. ')
                if clean_line and len(clean_line) > 10:
                    recommendations.append(clean_line)
        
        return recommendations
    
    def _get_default_recommendations(self, risk_level: RiskLevel, 
                                   risk_factors: List[Dict[str, Any]]) -> List[str]:
        """获取默认建议"""
        recommendations = []
        
        if risk_level == RiskLevel.HIGH:
            recommendations.extend([
                "建议立即就医，进行详细检查",
                "定期进行胸部CT检查",
                "戒烟限酒，避免二手烟",
                "保持室内通风，减少油烟接触",
                "如有异常症状，及时就医"
            ])
        elif risk_level == RiskLevel.MEDIUM:
            recommendations.extend([
                "建议定期体检，关注症状变化",
                "每6-12个月进行一次胸部检查",
                "改善生活方式，戒烟限酒",
                "保持健康饮食和适量运动",
                "定期监测健康状况"
            ])
        else:
            recommendations.extend([
                "保持健康生活方式，定期体检",
                "戒烟限酒，避免二手烟",
                "保持室内通风，减少油烟接触",
                "适量运动，保持健康体重",
                "定期进行健康检查"
            ])
        
        # 根据具体风险因素添加针对性建议
        for factor in risk_factors:
            if factor.get("factor") == "smoking" and factor.get("score", 0) > 0:
                recommendations.append("强烈建议戒烟，可寻求专业戒烟帮助")
            elif factor.get("factor") == "occupational_exposure" and factor.get("score", 0) > 0:
                recommendations.append("注意职业防护，减少有害物质接触")
            elif factor.get("factor") == "symptoms" and factor.get("score", 0) > 0:
                recommendations.append("密切关注症状变化，及时就医")
        
        return recommendations
    
    def _create_default_assessment(self, user_profile: Optional[Dict[str, Any]]) -> RiskAssessment:
        """创建默认评估结果"""
        return RiskAssessment(
            session_id=user_profile.get('session_id', 'unknown') if user_profile else 'unknown',
            overall_risk=RiskLevel.MEDIUM,
            risk_score=3.0,
            risk_factors=[{
                "factor": "default",
                "name": "默认评估",
                "level": "moderate",
                "score": 3.0,
                "details": "风险评估过程中出现错误，使用默认评估",
                "description": "建议进行进一步评估"
            }],
            recommendations=[
                "建议进行专业医学评估",
                "定期进行健康检查",
                "保持健康生活方式"
            ]
        )
    
    def get_assessment_history(self) -> List[RiskAssessment]:
        """获取评估历史"""
        return self.assessment_history
    
    def get_risk_factors(self) -> Dict[str, Dict[str, Any]]:
        """获取风险因素定义"""
        return self.risk_factors
    
    def update_risk_factor(self, factor_name: str, factor_data: Dict[str, Any]):
        """更新风险因素定义"""
        if factor_name in self.risk_factors:
            self.risk_factors[factor_name].update(factor_data)
            logger.info(f"✅ 风险因素更新成功: {factor_name}")
        else:
            self.risk_factors[factor_name] = factor_data
            logger.info(f"✅ 新风险因素添加成功: {factor_name}")

if __name__ == "__main__":
    # 测试风险评估智能体
    print("=== 风险评估智能体测试 ===")
    
    # 创建智能体
    assessor = RiskAssessorAgent()
    print(f"智能体创建成功: {assessor}")
    
    # 测试风险因素获取
    risk_factors = assessor.get_risk_factors()
    print(f"风险因素数量: {len(risk_factors)}")
    
    # 测试风险评估
    from ..models.questionnaire import UserResponse
    
    # 模拟用户回答
    test_responses = [
        UserResponse("smoking", "1"),  # 有吸烟史
        UserResponse("age", "55"),     # 55岁
        UserResponse("occupational_exposure", "2"),  # 无职业暴露
        UserResponse("family_history", "2"),  # 无家族史
        UserResponse("cough", "2"),    # 无咳嗽
        UserResponse("hemoptysis", "2"),  # 无痰中带血
        UserResponse("weight_loss", "2")  # 无消瘦
    ]
    
    import asyncio
    
    async def test_assessment():
        assessment = await assessor.assess_risk(test_responses)
        print(f"风险评估完成: {assessment.overall_risk.value}")
        print(f"风险评分: {assessment.risk_score}")
        print(f"风险因素数量: {len(assessment.risk_factors)}")
        print(f"建议数量: {len(assessment.recommendations)}")
        
        print("\n风险因素详情:")
        for factor in assessment.risk_factors:
            print(f"- {factor['name']}: {factor['level']} (评分: {factor['score']})")
        
        print("\n建议:")
        for i, rec in enumerate(assessment.recommendations, 1):
            print(f"{i}. {rec}")
    
    # 运行测试
    asyncio.run(test_assessment())
    
    print("✅ 风险评估智能体测试完成")
