# local_questionnaire.py
# -*- coding: utf-8 -*-
"""
Local Questionnaire Module (English Version)
- Manages local questionnaire configuration, questions, and logic
- Provides questionnaire flow, answer handling, and report generation
- Supports questionnaire progress tracking and state management
"""

import time
import logging
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

# ========== Questionnaire Configuration (English Version) ==========

QUESTIONS_STRUCTURED = [
    # Basic Information
    {"id": "name", "text": "Full Name", "prompt": "May I know your full name?", "category": "Basic Information"},
    {"id": "gender", "text": "Gender", "prompt": "What is your gender?", "category": "Basic Information"},
    {"id": "birth_year", "text": "Year of Birth", "prompt": "Which year were you born?", "category": "Basic Information"},

    {"id": "height", "text": "Height (cm)", "prompt": "What is your height in centimeters?", "category": "Body Metrics"},
    {"id": "weight", "text": "Weight (kg)", "prompt": "What is your weight in kilograms?", "category": "Body Metrics"},

    # Smoking History
    {"id": "smoking_history", 
     "text": "Smoking History", 
     "prompt": "Do you currently smoke or have you smoked before?", 
     "category": "Smoking History"},

    {"id": "smoking_freq", 
     "text": "Daily Cigarette Consumption", 
     "prompt": "On average, how many cigarettes do you smoke per day?", 
     "category": "Smoking History",
     "depends_on": {
         "id": "smoking_history",
         "value": "yes",
         "values": ["yes", "smoke", "smoked", "used to smoke", "former smoker"],
         "auto_fill": "0"
     },
     "auto_fill_value": "0"
    },

    {"id": "smoking_years", 
     "text": "Years of Smoking", 
     "prompt": "How many total years have you smoked?", 
     "category": "Smoking History",
     "depends_on": {
         "id": "smoking_history",
         "value": "yes",
         "values": ["yes", "smoke", "smoked", "used to smoke", "former smoker"],
         "auto_fill": "0"
     },
     "auto_fill_value": "0"
    },

    {"id": "smoking_quit", 
     "text": "Currently Quit Smoking", 
     "prompt": "Have you already quit smoking?", 
     "category": "Smoking History",
     "depends_on": {
         "id": "smoking_history",
         "value": "yes",
         "values": ["yes", "smoke", "smoked", "used to smoke", "former smoker"],
         "auto_fill": "no"
     },
     "auto_fill_value": "no"
    },

    {"id": "smoking_quit_years", 
     "text": "Years Since Quitting", 
     "prompt": "How many years have passed since you quit smoking?", 
     "category": "Smoking History",
     "depends_on": {
         "id": "smoking_quit",
         "value": "yes",
         "values": ["yes", "quit", "stopped", "no longer smoke"],
         "auto_fill": "0"
     },
     "auto_fill_value": "0"
    },

    # Passive Smoking
    {"id": "passive_smoking", 
     "text": "Passive Smoking Exposure", 
     "prompt": "Are you frequently exposed to second-hand smoke in your living or working environment?", 
     "category": "Passive Smoking"},

    {"id": "passive_smoking_freq", 
     "text": "Daily Passive Smoking Duration", 
     "prompt": "Approximately how long each day are you exposed to second-hand smoke?", 
     "category": "Passive Smoking",
     "depends_on": {
         "id": "passive_smoking",
         "value": "yes",
         "values": ["yes", "exposed", "frequently", "second-hand"],
         "auto_fill": "0"
     },
     "auto_fill_value": "0"
    },

    {"id": "passive_smoking_years", 
     "text": "Years of Passive Smoking Exposure", 
     "prompt": "For how many years has this situation continued?", 
     "category": "Passive Smoking",
     "depends_on": {
         "id": "passive_smoking",
         "value": "yes",
         "values": ["yes", "exposed", "frequently", "second-hand"],
         "auto_fill": "0"
     },
     "auto_fill_value": "0"
    },

    # Kitchen Fumes Exposure
    {"id": "kitchen_fumes", 
     "text": "Long-term Exposure to Kitchen Fumes", 
     "prompt": "Do you cook frequently and get exposed to kitchen fumes?", 
     "category": "Kitchen Fumes"},

    {"id": "kitchen_fumes_years", 
     "text": "Years of Kitchen Fume Exposure", 
     "prompt": "For how many years have you been exposed to kitchen fumes?", 
     "category": "Kitchen Fumes",
     "depends_on": {"id": "kitchen_fumes", "value": "yes"}},

    # Social Information
    {"id": "occupation", 
     "text": "Occupation", 
     "prompt": "What is your current occupation?", 
     "category": "Social Information"},

    # Occupational Exposure
    {"id": "occupation_exposure", 
     "text": "Exposure to Occupational Carcinogens", 
     "prompt": "Does your work involve exposure to asbestos, coal tar, radiation, or other hazardous substances?", 
     "category": "Occupational Exposure"},

    {"id": "occupation_exposure_details", 
     "text": "Type of Carcinogen & Years of Exposure", 
     "prompt": "Which specific substances were you exposed to, and for how many years?", 
     "category": "Occupational Exposure",
     "depends_on": {"id": "occupation_exposure", "value": "yes"}},

    # Personal Cancer History
    {"id": "personal_tumor_history", 
     "text": "Personal Cancer History", 
     "prompt": "Have you ever been diagnosed with any type of cancer?", 
     "category": "Cancer History"},

    {"id": "personal_tumor_details", 
     "text": "Cancer Type & Diagnosis Year", 
     "prompt": "Could you specify the cancer type and the year of diagnosis?", 
     "category": "Cancer History",
     "depends_on": {"id": "personal_tumor_history", "value": "yes"}},

    # Family Cancer History
    {"id": "family_cancer_history", 
     "text": "Lung Cancer in First-Degree Relatives", 
     "prompt": "Has anyone among your parents, siblings, or children been diagnosed with lung cancer?", 
     "category": "Cancer History"},

    {"id": "family_cancer_details", 
     "text": "Relative & Cancer Type", 
     "prompt": "Which family member was affected, and what type of cancer was it?", 
     "category": "Cancer History",
     "depends_on": {"id": "family_cancer_history", "value": "yes"}},

    # Imaging
    {"id": "chest_ct_last_year", 
     "text": "Chest CT Scan in the Past Year", 
     "prompt": "Have you undergone a chest CT scan in the past year?", 
     "category": "Imaging"},

    # Respiratory Diseases
    {"id": "chronic_lung_disease", 
     "text": "Chronic Lung Disease History", 
     "prompt": "Have you been diagnosed with chronic bronchitis, emphysema, tuberculosis, or COPD?", 
     "category": "Respiratory Diseases"},

    # Recent Symptoms
    {"id": "recent_weight_loss", 
     "text": "Unexplained Weight Loss (Last 6 Months)", 
     "prompt": "In the past six months, have you experienced significant weight loss without dieting?", 
     "category": "Recent Symptoms"},

    {"id": "recent_symptoms", 
     "text": "Symptoms Such as Persistent Cough, Blood in Sputum, Hoarseness", 
     "prompt": "Have you recently experienced persistent cough, blood in sputum, or hoarseness?", 
     "category": "Recent Symptoms"},

    {"id": "recent_symptoms_details", 
     "text": "Specific Symptoms (If Any)", 
     "prompt": "Could you describe the symptoms in more detail?", 
     "category": "Recent Symptoms",
     "depends_on": {"id": "recent_symptoms", "value": "yes"}},

    # Self Feeling
    {"id": "self_feeling", 
     "text": "General Health Self-rating (1 good, 2 average, 3 poor)", 
     "prompt": "Overall, how would you describe your recent health condition?", 
     "category": "Self Evaluation"},
]

# Mapping ID → question
QUESTIONS_BY_ID = {q['id']: q for q in QUESTIONS_STRUCTURED}

# Legacy compatibility
questions = [q['text'] for q in QUESTIONS_STRUCTURED]
questionnaire_reference = {}

# ========== Report Generation (Refactored English Version) ==========

def generate_assessment_report(answers: Dict[str, str]) -> str:
    """
    Generate lung cancer early screening risk assessment report (English version).
    Uses DeepSeek-based report agent to determine risk level.
    """
    report = "Lung Cancer Risk Screening Report\n\n" + "=" * 50 + "\n\n"

    def get_answer(question_id: str) -> Optional[str]:
        """Safely get answer by question ID"""
        question_text = QUESTIONS_BY_ID.get(question_id, {}).get('text')
        if not question_text:
            return None
        return answers.get(question_text)

    # Basic Information
    report += "[Basic Information]\n"
    name = get_answer('name')
    if name:
        report += f"Name: {name}\n"

    gender_ans = get_answer('gender')
    if gender_ans:
        # 兼容原来用 1/2 存性别的情况，如果已经是英文文本也能正常显示
        if gender_ans in ["1", "2"]:
            gender_text = "Male" if gender_ans == "1" else "Female"
        else:
            gender_text = gender_ans
        report += f"Gender: {gender_text}\n"

    birth_year = get_answer('birth_year')
    if birth_year:
        report += f"Year of Birth: {birth_year}\n"

    height_ans = get_answer('height')
    weight_ans = get_answer('weight')
    if height_ans and weight_ans:
        try:
            height = float(height_ans)
            weight = float(weight_ans)
            bmi = weight / ((height / 100) ** 2)
            report += f"Height: {height:.1f} cm, Weight: {weight:.1f} kg, BMI: {bmi:.1f}\n"
        except (ValueError, TypeError):
            report += f"Height: {height_ans} cm, Weight: {weight_ans} kg\n"

    # Risk Evaluation
    report += "\n[Risk Evaluation]\n"
    risk_score = 0

    # Smoking
    smoking_history = get_answer('smoking_history')
    if smoking_history and smoking_history.lower() in ["yes", "smoke", "smoked", "used to smoke", "former smoker", "1"]:
        report += "⚠ Smoking history: The patient has a history of smoking, which increases lung cancer risk.\n"
        try:
            years = float(get_answer('smoking_years') or 0)
            daily = float(get_answer('smoking_freq') or 0)
            pack_years = (years * daily) / 20
            if pack_years > 30:
                risk_score += 3
            elif pack_years > 20:
                risk_score += 2
            else:
                risk_score += 1
            report += f"   Smoking index (pack-years): {pack_years:.1f}\n"
        except (ValueError, TypeError):
            risk_score += 2

    # Passive smoking
    passive_smoking = get_answer('passive_smoking')
    if passive_smoking and passive_smoking.lower() in ["yes", "exposed", "frequently", "second-hand", "1"]:
        report += "⚠ Passive smoking: The patient is exposed to second-hand smoke.\n"
        risk_score += 1

    # Occupational exposure
    if get_answer('occupation_exposure') and get_answer('occupation_exposure').lower() in ["yes", "1"]:
        report += "⚠ Occupational exposure: The patient has exposure to occupational carcinogens.\n"
        risk_score += 2

    # Family history
    if get_answer('family_cancer_history') and get_answer('family_cancer_history').lower() in ["yes", "1"]:
        report += "⚠ Family history: There is a family history of lung cancer, which may increase hereditary risk.\n"
        risk_score += 2

    # Symptoms
    if get_answer('recent_symptoms') and get_answer('recent_symptoms').lower() in ["yes", "1"]:
        report += "⚠ Symptoms: The patient presents with suspicious symptoms. It is recommended to seek medical attention promptly.\n"
        risk_score += 3

    # Imaging
    chest_ct_last_year = get_answer('chest_ct_last_year')
    if chest_ct_last_year and chest_ct_last_year.lower() in ["no", "2"]:
        report += "📋 Recommendation: No chest CT was performed in the past year. Please consult a doctor regarding the need for low-dose CT screening based on the risk assessment.\n"

    # Use DeepSeek-based agent to determine risk level
    risk_level, risk_analysis = _get_risk_level_from_deepseek(answers, risk_score)

    # Overall Assessment
    report += "\n[Overall Assessment]\n"
    if risk_level == "High":
        report += (
            "🔴 High risk: Based on the comprehensive assessment, you are at a higher risk for lung cancer.\n"
            "It is strongly recommended to consult a respiratory or thoracic specialist as soon as possible\n"
            "and discuss the need for low-dose CT screening or further investigations.\n"
        )
    elif risk_level == "Medium":
        report += (
            "🟡 Medium risk: Based on the comprehensive assessment, your risk is at a moderate level.\n"
            "It is recommended to have regular health check-ups and to discuss with a doctor whether\n"
            "lung cancer screening is appropriate for you.\n"
        )
    else:
        report += (
            "🟢 Low risk: Based on the comprehensive assessment, your current risk is relatively low.\n"
            "However, it is still important to maintain a healthy lifestyle, avoid smoking and second-hand smoke,\n"
            "and pay attention to any new or persistent symptoms.\n"
        )

    # Add AI analysis
    if risk_analysis:
        report += f"\n[AI-generated Medical Commentary]\n{risk_analysis}\n"

    report += "\n" + "=" * 50 + "\n"
    report += f"Report generated at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
    return report


def _get_risk_level_from_deepseek(answers: Dict[str, str], risk_score: int) -> Tuple[str, str]:
    """
    Use DeepSeek-based agent to determine risk level.
    Returns (risk_level, detailed_analysis) where risk_level in {"Low", "Medium", "High"}.
    """
    try:
        # Build Q&A data
        qa_data = []
        for question_id, question_data in QUESTIONS_BY_ID.items():
            question_text = question_data['text']
            answer = answers.get(question_text, 'Not answered')
            qa_data.append(f"Question: {question_text}\nAnswer: {answer}")

        qa_text = "\n\n".join(qa_data)

        # Build English prompt
        prompt = f"""
You are a professional medical expert. Based on the patient's questionnaire responses, 
you need to assess the risk level for lung cancer screening.

Patient responses:
{qa_text}

Current risk score (traditional rule-based scoring): {risk_score}

Please determine the overall risk level based on the following criteria:
- Low risk: No obvious major risk factors. General health advice and routine check-ups are sufficient.
- Medium risk: Some meaningful risk factors are present. Consider regular follow-up and discuss screening with a doctor.
- High risk: Multiple significant risk factors or concerning symptoms. Strongly recommend seeking medical assessment as soon as possible.

Please respond strictly in the following format:
Risk Level: [Low/Medium/High]
Detailed Analysis: [a detailed explanation based on medical reasoning, including key risk factors and practical suggestions.]

Requirements:
- Base your reasoning on medical knowledge and the given questionnaire data.
- Consider the combined effect of all risk factors (smoking, family history, occupational exposure, symptoms, etc.).
- Provide concrete, practical suggestions, but do NOT make a definitive diagnosis.
- Use clear and patient-friendly English.
- Do NOT output in any language other than English.

Please ONLY output the content in the exact format above, without any extra commentary.
"""

        import requests
        import os

        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            logger.warning("DEEPSEEK_API_KEY is not set. Falling back to default rule-based risk level.")
            return _get_default_risk_level(risk_score), "AI analysis is not available. A traditional score-based assessment is used instead."

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        data = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3,
            "max_tokens": 1000
        }

        response = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=30
        )

        if response.status_code == 200:
            result = response.json()
            content = result['choices'][0]['message']['content'].strip()

            # Default values
            risk_level = "Low"
            analysis = content

            # Parse lines
            for line in content.split('\n'):
                line = line.strip()
                if line.lower().startswith("risk level:"):
                    value = line.split(":", 1)[1].strip()
                    if "high" in value.lower():
                        risk_level = "High"
                    elif "medium" in value.lower():
                        risk_level = "Medium"
                    elif "low" in value.lower():
                        risk_level = "Low"
                elif line.lower().startswith("detailed analysis:"):
                    analysis = line.split(":", 1)[1].strip()

            logger.info(f"DeepSeek risk level: {risk_level}")
            return risk_level, analysis
        else:
            logger.error(f"DeepSeek API call failed: {response.status_code}")
            return _get_default_risk_level(risk_score), "AI analysis failed. A traditional score-based assessment is used instead."

    except Exception as e:
        logger.error(f"DeepSeek risk assessment failed: {e}")
        return _get_default_risk_level(risk_score), f"AI analysis error: {str(e)}"


def _get_default_risk_level(risk_score: int) -> str:
    """Get default risk level (rule-based) as Low / Medium / High."""
    if risk_score >= 6:
        return "High"
    elif risk_score >= 3:
        return "Medium"
    else:
        return "Low"

# ========== Utility Functions (English Version) ==========

def get_question_info(question_index: int) -> Optional[Dict[str, Any]]:
    """
    Return formatted question info for the frontend:
    - category
    - prompt question (the text spoken to the user)
    - original question text (label)
    - question index
    - total questions
    """
    if not 0 <= question_index < len(QUESTIONS_STRUCTURED):
        return None

    q_data = QUESTIONS_STRUCTURED[question_index]
    return {
        "category": q_data.get("category", "Other"),
        "question": q_data['prompt'],
        "original_question": q_data['text'],
        "question_index": question_index + 1,
        "total_questions": len(QUESTIONS_STRUCTURED)
    }

# ========== Export Configuration ==========
questions_structured = QUESTIONS_STRUCTURED
