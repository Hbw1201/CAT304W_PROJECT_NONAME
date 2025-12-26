# -*- coding: utf-8 -*-
"""
Enhanced local questionnaire module (English version)
- Supports conditional skip logic
- Dynamically selects questions based on user answers
- Intelligent questionnaire flow control
"""

import time
import logging
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

# ========== Questionnaire configuration with skip logic (English) ==========

QUESTIONS_STRUCTURED_ENHANCED = [
    # Basic Information - required
    {
        "id": "name",
        "text": "Full Name",
        "prompt": "May I know your full name?",
        "category": "Basic Information",
        "required": True,
    },
    {
        "id": "gender",
        "text": "Gender",
        "prompt": "What is your gender?",
        "category": "Basic Information",
        "required": True,
    },
    {
        "id": "birth_year",
        "text": "Year of Birth",
        "prompt": "Which year were you born?",
        "category": "Basic Information",
        "required": True,
    },
    {
        "id": "id_card",
        "text": "ID / Passport Number",
        "prompt": "Could you share your ID or passport number? This information will be kept strictly confidential.",
        "category": "Basic Information",
        "required": False,
    },
    {
        "id": "med_card",
        "text": "Medical Insurance Number (optional)",
        "prompt": "If you are comfortable, you may provide your medical insurance number. It is completely fine to skip this.",
        "category": "Basic Information",
        "required": False,
    },

    # Body Metrics
    {
        "id": "height",
        "text": "Height (cm)",
        "prompt": "What is your height in centimeters?",
        "category": "Body Metrics",
        "required": True,
    },
    {
        "id": "weight",
        "text": "Weight (kg)",
        "prompt": "What is your weight in kilograms?",
        "category": "Body Metrics",
        "required": True,
    },

    # Smoking History – core branching point
    {
        "id": "smoking_history",
        "text": "Smoking History (1 = Yes, 2 = No)",
        "prompt": "Do you currently smoke or have you smoked before?",
        "category": "Smoking History",
        "required": True,
    },

    # Smoking-related questions – only asked if there is smoking history
    {
        "id": "smoking_freq",
        "text": "Daily Cigarette Consumption",
        "prompt": "On average, how many cigarettes do you smoke per day?",
        "category": "Smoking History",
        "depends_on": {"id": "smoking_history", "value": "1"},
        "required": True,
    },
    {
        "id": "smoking_years",
        "text": "Total Years of Smoking",
        "prompt": "For how many years in total have you smoked?",
        "category": "Smoking History",
        "depends_on": {"id": "smoking_history", "value": "1"},
        "required": True,
    },
    {
        "id": "smoking_quit",
        "text": "Currently Quit Smoking (1 = Yes, 2 = No)",
        "prompt": "Have you already quit smoking?",
        "category": "Smoking History",
        "depends_on": {"id": "smoking_history", "value": "1"},
        "required": True,
    },
    {
        "id": "smoking_quit_years",
        "text": "Years Since Quitting",
        "prompt": "How many years has it been since you quit smoking?",
        "category": "Smoking History",
        "depends_on": {"id": "smoking_quit", "value": "1"},
        "required": True,
    },

    # Passive Smoking – focus when there is no active smoking
    {
        "id": "passive_smoking",
        "text": "Passive Smoking Exposure (1 = No, 2 = Yes)",
        "prompt": "Are you frequently exposed to second-hand smoke in your living or working environment?",
        "category": "Passive Smoking",
        "required": True,
    },
    {
        "id": "passive_smoking_freq",
        "text": "Daily Duration of Passive Smoking",
        "prompt": "Approximately how long each day are you exposed to second-hand smoke?",
        "category": "Passive Smoking",
        "depends_on": {"id": "passive_smoking", "value": "2"},
        "required": True,
    },
    {
        "id": "passive_smoking_years",
        "text": "Years of Passive Smoking Exposure",
        "prompt": "For how many years has this second-hand smoke exposure continued?",
        "category": "Passive Smoking",
        "depends_on": {"id": "passive_smoking", "value": "2"},
        "required": True,
    },

    # Kitchen Fumes – particularly important for some groups
    {
        "id": "kitchen_fumes",
        "text": "Long-term Exposure to Kitchen Fumes (1 = Yes, 2 = No)",
        "prompt": "Do you cook frequently and get exposed to kitchen fumes?",
        "category": "Kitchen Fumes",
        "required": True,
    },
    {
        "id": "kitchen_fumes_years",
        "text": "Years of Exposure to Kitchen Fumes",
        "prompt": "For how many years have you been exposed to kitchen fumes?",
        "category": "Kitchen Fumes",
        "depends_on": {"id": "kitchen_fumes", "value": "1"},
        "required": True,
    },

    # Social / Occupational Information
    {
        "id": "occupation",
        "text": "Occupation",
        "prompt": "What is your current occupation?",
        "category": "Social Information",
        "required": True,
    },

    # Occupational Exposure
    {
        "id": "occupation_exposure",
        "text": "Occupational Exposure to Carcinogens (1 = Yes, 2 = No)",
        "prompt": "In your work, are you exposed to asbestos, coal tar, radiation, or other harmful substances?",
        "category": "Occupational Exposure",
        "required": True,
    },
    {
        "id": "occupation_exposure_details",
        "text": "Type of Carcinogen and Years of Exposure (if any)",
        "prompt": "Which specific substances were you exposed to, and for how many years?",
        "category": "Occupational Exposure",
        "depends_on": {"id": "occupation_exposure", "value": "1"},
        "required": True,
    },

    # Tumour-related History
    {
        "id": "personal_tumor_history",
        "text": "Personal History of Cancer (1 = Yes, 2 = No)",
        "prompt": "Have you ever been diagnosed with any type of cancer?",
        "category": "Tumour-related History",
        "required": True,
    },
    {
        "id": "personal_tumor_details",
        "text": "Cancer Type and Year of Diagnosis (if any)",
        "prompt": "Could you describe the type of cancer and when it was diagnosed?",
        "category": "Tumour-related History",
        "depends_on": {"id": "personal_tumor_history", "value": "1"},
        "required": True,
    },
    {
        "id": "family_cancer_history",
        "text": "Family History of Lung Cancer in First- to Third-degree Relatives (1 = Yes, 2 = No)",
        "prompt": "Have any of your parents, siblings, or children been diagnosed with lung cancer?",
        "category": "Tumour-related History",
        "required": True,
    },
    {
        "id": "family_cancer_details",
        "text": "Cancer Type and Relationship (if any)",
        "prompt": "Which family member was affected, and what type of cancer was it?",
        "category": "Tumour-related History",
        "depends_on": {"id": "family_cancer_history", "value": "1"},
        "required": True,
    },

    # Imaging
    {
        "id": "chest_ct_last_year",
        "text": "Chest CT in the Past Year (1 = Yes, 2 = No)",
        "prompt": "In the past year, have you had a chest CT scan?",
        "category": "Imaging",
        "required": True,
    },
    {
        "id": "chest_ct_results",
        "text": "Chest CT Result (if done)",
        "prompt": "What were the findings of that CT scan? Was anything abnormal reported?",
        "category": "Imaging",
        "depends_on": {"id": "chest_ct_last_year", "value": "1"},
        "required": False,
    },

    # Chronic Respiratory Disease History
    {
        "id": "chronic_lung_disease",
        "text": "Chronic Lung Disease History (1 = Yes, 2 = No)",
        "prompt": "Have you been diagnosed with chronic bronchitis, emphysema, tuberculosis, COPD, or other chronic lung diseases?",
        "category": "Chronic Respiratory Disease History",
        "required": True,
    },
    {
        "id": "lung_disease_details",
        "text": "Details of Lung Disease",
        "prompt": "Which specific lung disease were you diagnosed with, and when roughly was it diagnosed?",
        "category": "Chronic Respiratory Disease History",
        "depends_on": {"id": "chronic_lung_disease", "value": "1"},
        "required": True,
    },

    # Recent Symptoms – important risk indicators
    {
        "id": "recent_weight_loss",
        "text": "Unexplained Weight Loss in the Last 6 Months (1 = Yes, 2 = No)",
        "prompt": "In the past six months, have you experienced noticeable weight loss without intentionally dieting?",
        "category": "Recent Symptoms",
        "required": True,
    },
    {
        "id": "weight_loss_amount",
        "text": "Amount of Weight Loss (kg)",
        "prompt": "Approximately how many kilograms have you lost?",
        "category": "Recent Symptoms",
        "depends_on": {"id": "recent_weight_loss", "value": "1"},
        "required": True,
    },
    {
        "id": "recent_cough",
        "text": "Recent Persistent Dry Cough (1 = Yes, 2 = No)",
        "prompt": "Have you recently had a persistent dry cough?",
        "category": "Recent Symptoms",
        "required": True,
    },
    {
        "id": "cough_duration",
        "text": "Duration of Cough",
        "prompt": "For approximately how long has this cough been ongoing?",
        "category": "Recent Symptoms",
        "depends_on": {"id": "recent_cough", "value": "1"},
        "required": True,
    },
    {
        "id": "hemoptysis",
        "text": "Blood in Sputum (1 = Yes, 2 = No)",
        "prompt": "Have you noticed any blood in your sputum (phlegm)?",
        "category": "Recent Symptoms",
        "required": True,
    },
    {
        "id": "voice_hoarse",
        "text": "Hoarseness of Voice (1 = Yes, 2 = No)",
        "prompt": "Have you recently noticed your voice becoming hoarse?",
        "category": "Recent Symptoms",
        "required": True,
    },

    # Self-rated Health
    {
        "id": "self_feeling",
        "text": "Recent Self-rated Overall Health (1 = Good, 2 = Average, 3 = Poor)",
        "prompt": "Overall, how would you describe your recent general health condition?",
        "category": "Self-rated Health",
        "required": True,
    },
]

# ========== Intelligent questionnaire logic ==========


class QuestionnaireLogicManager:
    """Questionnaire logic manager – handles skip logic and question selection."""

    def __init__(self):
        self.questions = QUESTIONS_STRUCTURED_ENHANCED
        self.questions_by_id = {q["id"]: q for q in self.questions}

    def get_next_question_index(self, current_index: int, answers: Dict[str, str]) -> int:
        """
        Get the index of the next question based on current answers.
        Returns: next question index, or -1 if the questionnaire is complete.
        """
        next_index = current_index + 1

        while next_index < len(self.questions):
            question = self.questions[next_index]

            # Check dependency
            dependency = question.get("depends_on")
            if not dependency:
                # No dependency – ask this question
                return next_index

            # Dependency exists – check if it is met
            if self._is_dependency_met(dependency, answers):
                return next_index
            else:
                # Not met – skip this question
                next_index += 1

        # No more questions – questionnaire finished
        return -1

    def _is_dependency_met(self, dependency: Dict[str, str], answers: Dict[str, str]) -> bool:
        """Check whether a dependency condition is satisfied."""
        dependent_question_id = dependency.get("id")
        required_value = dependency.get("value")

        # Find the dependent question
        dependent_question = self.questions_by_id.get(dependent_question_id)
        if not dependent_question:
            return False

        dependent_question_text = dependent_question["text"]
        actual_answer = answers.get(dependent_question_text)

        return str(actual_answer) == str(required_value)

    def get_intelligent_next_question(
        self, answers: Dict[str, str], conversation_context: str = ""
    ) -> Optional[Dict[str, Any]]:
        """
        Intelligently select the next most relevant question
        based on existing answers and (optionally) conversation context.
        """
        unanswered_questions = self._get_unanswered_questions(answers)

        if not unanswered_questions:
            return None

        # Priority sorting rules
        priority_questions = self._prioritize_questions(unanswered_questions, answers)

        return priority_questions[0] if priority_questions else None

    def _get_unanswered_questions(self, answers: Dict[str, str]) -> List[Dict[str, Any]]:
        """Get all unanswered and applicable questions (dependency satisfied)."""
        unanswered = []

        for question in self.questions:
            question_text = question["text"]

            # Skip already answered questions
            if question_text in answers:
                continue

            # Check dependency
            dependency = question.get("depends_on")
            if dependency and not self._is_dependency_met(dependency, answers):
                continue

            unanswered.append(question)

        return unanswered

    def _prioritize_questions(
        self, questions: List[Dict[str, Any]], answers: Dict[str, str]
    ) -> List[Dict[str, Any]]:
        """Sort questions by priority score."""

        def get_priority_score(q: Dict[str, Any]) -> int:
            score = 0
            category = q.get("category", "")

            # Basic information first
            if category == "Basic Information":
                score += 100

            # High-risk factors get higher priority
            risk_categories = [
                "Smoking History",
                "Occupational Exposure",
                "Tumour-related History",
                "Recent Symptoms",
            ]
            if category in risk_categories:
                score += 80

            # Required questions are prioritized
            if q.get("required", False):
                score += 50

            # Questions with dependency (once met) also get some priority
            if q.get("depends_on"):
                score += 30

            return score

        return sorted(questions, key=get_priority_score, reverse=True)

    def get_questionnaire_progress(self, answers: Dict[str, str]) -> Dict[str, Any]:
        """Get questionnaire progress information."""
        total_applicable = len(self._get_all_applicable_questions(answers))
        answered = len(answers)

        return {
            "answered": answered,
            "total_applicable": total_applicable,
            "progress_percentage": (answered / total_applicable * 100)
            if total_applicable > 0
            else 0,
            "estimated_remaining": max(0, total_applicable - answered),
        }

    def _get_all_applicable_questions(self, answers: Dict[str, str]) -> List[Dict[str, Any]]:
        """Get all questions that are applicable given current answers (dependency-aware)."""
        applicable = []

        for question in self.questions:
            dependency = question.get("depends_on")
            if not dependency or self._is_dependency_met(dependency, answers):
                applicable.append(question)

        return applicable


# ========== Global instance ==========

questionnaire_logic = QuestionnaireLogicManager()

# ========== Compatibility wrappers ==========


def get_next_question_index(current_index: int, answers: Dict[str, str]) -> int:
    """Compatibility wrapper for original interface."""
    return questionnaire_logic.get_next_question_index(current_index, answers)


def get_intelligent_next_question(answers: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Return the next intelligently recommended question."""
    return questionnaire_logic.get_intelligent_next_question(answers)


# ========== Enhanced report generation (English) ==========


def generate_enhanced_assessment_report(answers: Dict[str, str]) -> str:
    """
    Generate a detailed risk assessment report based on the enhanced questionnaire.
    """
    report = "Intelligent Lung Cancer Risk Assessment Report\n\n" + "=" * 60 + "\n\n"

    def get_answer_by_id(question_id: str) -> Optional[str]:
        """Get answer by question ID."""
        question = questionnaire_logic.questions_by_id.get(question_id)
        if not question:
            return None
        return answers.get(question["text"])

    # Basic Information
    report += "[Basic Information]\n"
    name = get_answer_by_id("name")
    if name:
        report += f"Name: {name}\n"

    gender_ans = get_answer_by_id("gender")
    if gender_ans:
        # Be tolerant: support both coded values and plain text
        if gender_ans in ["1", "2"]:
            gender_text = "Male" if gender_ans == "1" else "Female"
        else:
            gender_text = gender_ans
        report += f"Gender: {gender_text}\n"

    birth_year = get_answer_by_id("birth_year")
    if birth_year:
        try:
            # If a specific year is needed for logic, adjust here
            age = 2024 - int(birth_year)
            report += f"Year of Birth: {birth_year} (Age: {age} years)\n"
        except Exception:
            report += f"Year of Birth: {birth_year}\n"

    # BMI calculation
    height_ans = get_answer_by_id("height")
    weight_ans = get_answer_by_id("weight")
    if height_ans and weight_ans:
        try:
            height = float(height_ans)
            weight = float(weight_ans)
            bmi = weight / ((height / 100) ** 2)
            if 18.5 <= bmi <= 24.9:
                bmi_status = "Normal"
            elif bmi < 18.5:
                bmi_status = "Underweight"
            else:
                bmi_status = "Overweight"
            report += (
                f"Height: {height:.1f} cm, Weight: {weight:.1f} kg, "
                f"BMI: {bmi:.1f} ({bmi_status})\n"
            )
        except Exception:
            report += f"Height: {height_ans} cm, Weight: {weight_ans} kg\n"

    # Risk evaluation
    report += "\n[Intelligent Risk Evaluation]\n"
    risk_score = 0
    risk_factors: List[str] = []

    # Smoking history
    smoking_history = get_answer_by_id("smoking_history")
    if smoking_history == "1":
        smoking_years = get_answer_by_id("smoking_years")
        smoking_freq = get_answer_by_id("smoking_freq")
        smoking_quit = get_answer_by_id("smoking_quit")

        try:
            years = float(smoking_years or 0)
            daily = float(smoking_freq or 0)
            pack_years = (years * daily) / 20.0

            if pack_years > 30:
                risk_score += 4
                risk_level_local = "very high"
            elif pack_years > 20:
                risk_score += 3
                risk_level_local = "high"
            elif pack_years > 10:
                risk_score += 2
                risk_level_local = "moderate"
            else:
                risk_score += 1
                risk_level_local = "low"

            status = "former smoker" if smoking_quit == "1" else "current smoker"
            report += (
                f"🚭 Smoking history: {status}, smoking index {pack_years:.1f} pack-years "
                f"({risk_level_local} risk contribution).\n"
            )
            risk_factors.append(f"Smoking history ({pack_years:.1f} pack-years)")
        except Exception:
            risk_score += 2
            report += "🚭 Smoking history: smoker (insufficient data to calculate pack-years).\n"
            risk_factors.append("Smoking history")

    # Passive smoking
    passive_smoking = get_answer_by_id("passive_smoking")
    if passive_smoking == "2":
        passive_years = get_answer_by_id("passive_smoking_years")
        risk_score += 1
        report += "💨 Passive smoking: long-term exposure to second-hand smoke"
        if passive_years:
            report += f" (for about {passive_years} years)"
        report += ".\n"
        risk_factors.append("Long-term passive smoking")

    # Occupational exposure
    occupation_exposure = get_answer_by_id("occupation_exposure")
    if occupation_exposure == "1":
        exposure_details = get_answer_by_id("occupation_exposure_details")
        risk_score += 2
        report += "⚠ Occupational exposure: exposure to carcinogenic substances"
        if exposure_details:
            report += f" ({exposure_details})"
        report += ".\n"
        risk_factors.append("Occupational exposure to carcinogens")

    # Family history
    family_history = get_answer_by_id("family_cancer_history")
    if family_history == "1":
        family_details = get_answer_by_id("family_cancer_details")
        risk_score += 2
        report += "👨‍👩‍👧‍👦 Family history: lung cancer in close relatives"
        if family_details:
            report += f" ({family_details})"
        report += ".\n"
        risk_factors.append("Family history of lung cancer")

    # Personal tumour history
    personal_tumor = get_answer_by_id("personal_tumor_history")
    if personal_tumor == "1":
        tumor_details = get_answer_by_id("personal_tumor_details")
        risk_score += 3
        report += "🏥 Personal history: previous cancer"
        if tumor_details:
            report += f" ({tumor_details})"
        report += ".\n"
        risk_factors.append("Personal history of cancer")

    # Recent symptoms
    symptoms: List[str] = []

    if get_answer_by_id("recent_cough") == "1":
        duration = get_answer_by_id("cough_duration")
        desc = "persistent dry cough"
        if duration:
            desc += f" (duration: {duration})"
        symptoms.append(desc)
        risk_score += 2

    if get_answer_by_id("hemoptysis") == "1":
        symptoms.append("blood in sputum")
        risk_score += 3

    if get_answer_by_id("voice_hoarse") == "1":
        symptoms.append("hoarseness of voice")
        risk_score += 2

    if get_answer_by_id("recent_weight_loss") == "1":
        weight_loss = get_answer_by_id("weight_loss_amount")
        desc = "unexplained weight loss"
        if weight_loss:
            desc += f" (about {weight_loss} kg)"
        symptoms.append(desc)
        risk_score += 2

    if symptoms:
        report += "🔴 Significant symptoms: " + " | ".join(symptoms) + "\n"
        risk_factors.extend(symptoms)

    # Chronic lung disease
    chronic_lung = get_answer_by_id("chronic_lung_disease")
    if chronic_lung == "1":
        lung_details = get_answer_by_id("lung_disease_details")
        risk_score += 1
        report += "🫁 Chronic lung disease history"
        if lung_details:
            report += f" ({lung_details})"
        report += ".\n"
        risk_factors.append("Chronic lung disease")

    # Overall risk evaluation
    report += "\n[Overall Risk Assessment]\n"

    if risk_score >= 8:
        overall_risk_level = "Very High Risk"
        risk_color = "🔴🔴🔴"
        recommendation = (
            "It is strongly recommended that you seek medical attention as soon as possible. "
            "Please consult a respiratory or thoracic specialist, and discuss detailed investigations "
            "such as low-dose CT scans and tumour marker tests."
        )
    elif risk_score >= 5:
        overall_risk_level = "High Risk"
        risk_color = "🔴🔴"
        recommendation = (
            "You are at a relatively high risk. Please consult a specialist doctor promptly and "
            "consider chest CT imaging and related screening tests."
        )
    elif risk_score >= 3:
        overall_risk_level = "Moderate Risk"
        risk_color = "🟡"
        recommendation = (
            "Your risk is at a moderate level. It is advisable to have regular health check-ups, "
            "including chest imaging at suitable intervals, and to discuss your risk with a doctor."
        )
    elif risk_score >= 1:
        overall_risk_level = "Low-to-Moderate Risk"
        risk_color = "🟢🟡"
        recommendation = (
            "Your risk appears to be slightly elevated. Maintaining a healthy lifestyle, "
            "avoiding smoking, and monitoring for new or persistent symptoms is recommended."
        )
    else:
        overall_risk_level = "Low Risk"
        risk_color = "🟢"
        recommendation = (
            "Your current risk appears relatively low. Continue to maintain healthy habits and "
            "consider regular general health check-ups."
        )

    report += f"{risk_color} Risk Level: {overall_risk_level} (Score: {risk_score})\n\n"

    if risk_factors:
        report += "Key contributing risk factors: " + " | ".join(risk_factors) + "\n\n"

    report += f"📋 Professional Recommendation: {recommendation}\n\n"

    # Lifestyle advice
    report += "[General Health & Lifestyle Advice]\n"
    report += "• Avoid smoking and second-hand smoke; seek support for smoking cessation if needed.\n"
    report += "• Keep indoor air well ventilated and reduce exposure to heavy kitchen fumes.\n"
    report += "• Engage in regular physical activity suitable for your condition.\n"
    report += "• Maintain a balanced diet rich in vegetables and fruits.\n"
    report += "• Use appropriate occupational protection and attend regular health check-ups if exposed to hazards.\n"
    report += "• Pay attention to any new or persistent respiratory symptoms and seek medical care promptly.\n\n"

    # Follow-up suggestions
    if risk_score >= 3:
        report += "[Follow-up Suggestions]\n"
        if risk_score >= 5:
            report += "• Consider repeating chest CT every 3–6 months under medical guidance.\n"
            report += "• Discuss regular monitoring of tumour markers with your doctor.\n"
        else:
            report += "• Consider repeating chest imaging every 6–12 months, as advised by your doctor.\n"
        report += "• Seek medical review promptly if new symptoms appear or existing symptoms worsen.\n"
        report += "• Maintain regular communication with your healthcare providers.\n\n"

    report += "=" * 60 + "\n"
    report += f"Report generated at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
    report += (
        "Note: This report is for preliminary risk assessment only and cannot replace a formal "
        "diagnosis by a qualified doctor. If you have any concerns, please seek medical advice promptly.\n"
    )

    return report


# ========== Exported interfaces ==========

questions_structured = QUESTIONS_STRUCTURED_ENHANCED
# For compatibility with older code that expects a list of question texts
questions = [q["text"] for q in QUESTIONS_STRUCTURED_ENHANCED]


def get_questionnaire_summary() -> Dict[str, Any]:
    """Return basic metadata/summary of the enhanced questionnaire."""
    return {
        "title": "Intelligent Lung Cancer Risk Screening Questionnaire",
        "description": (
            "An AI-assisted lung cancer early screening questionnaire with intelligent skip logic "
            "and personalized question recommendation."
        ),
        "version": "2.0",
        "total_questions": len(QUESTIONS_STRUCTURED_ENHANCED),
        "categories": list(
            set(q.get("category", "Other") for q in QUESTIONS_STRUCTURED_ENHANCED)
        ),
        "estimated_time": "10–20 minutes (dynamically adjusted based on individual responses)",
        "features": [
            "Intelligent skip logic",
            "Personalized question recommendation",
            "Real-time risk scoring",
            "Detailed explanatory report",
        ],
    }


if __name__ == "__main__":
    # Basic sanity test of the enhanced logic (English version)
    print("=== Enhanced Questionnaire Logic Test (English) ===")

    # Example partial answers (keys must match question['text'])
    test_answers = {
        "Full Name": "Test User",
        "Gender": "1",  # 1 = Male, 2 = Female (compatible with old coding)
        "Year of Birth": "1970",
        "Height (cm)": "175",
        "Weight (kg)": "70",
        "Smoking History (1 = Yes, 2 = No)": "1",  # has smoking history
    }

    # Test intelligent next question
    next_question = get_intelligent_next_question(test_answers)
    if next_question:
        print(f"Intelligently recommended next question: {next_question['prompt']}")

    # Test progress
    progress = questionnaire_logic.get_questionnaire_progress(test_answers)
    print(f"Questionnaire progress: {progress}")

    print("✅ Enhanced questionnaire logic test completed.")
