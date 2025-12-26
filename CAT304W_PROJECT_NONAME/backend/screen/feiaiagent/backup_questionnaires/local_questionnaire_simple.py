# -*- coding: utf-8 -*-
"""
Simple local questionnaire (English version)
- Directly defines a small local questionnaire for easy control and modification
"""

import time
import logging
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

# ========== Questionnaire Question Definitions ==========

QUESTIONS = [
    # Basic Information
    {
        "id": "name",
        "text": "Full Name",
        "prompt": "May I know your full name?",
        "category": "Basic Information",
        "required": True,
        "validation": "Name cannot be empty. Please enter your real name."
    },
    {
        "id": "gender",
        "text": "Gender",
        "prompt": "What is your gender?",
        "category": "Basic Information",
        "required": True,
        "options": ["Male", "Female"],
        "validation": "Please select your gender."
    },
    {
        "id": "age",
        "text": "Age",
        "prompt": "How old are you?",
        "category": "Basic Information",
        "required": True,
        "validation": "Please enter a valid age (number)."
    },
    {
        "id": "phone",
        "text": "Contact Number",
        "prompt": "Could you please provide a contact number (optional)?",
        "category": "Basic Information",
        "required": False,
        "validation": "Please enter a valid phone number."
    },

    # Physical Measurements
    {
        "id": "height",
        "text": "Height (cm)",
        "prompt": "What is your height in centimeters?",
        "category": "Physical Measurements",
        "required": True,
        "validation": "Please enter a valid height (number)."
    },
    {
        "id": "weight",
        "text": "Weight (kg)",
        "prompt": "What is your weight in kilograms?",
        "category": "Physical Measurements",
        "required": True,
        "validation": "Please enter a valid weight (number)."
    },

    # Smoking History
    {
        "id": "smoking_history",
        "text": "Smoking History",
        "prompt": "Do you currently smoke or have you smoked before?",
        "category": "Smoking History",
        "required": True,
        "options": ["Yes", "No"],
        "validation": "Please select whether you have a smoking history."
    },
    {
        "id": "smoking_freq",
        "text": "Daily Cigarette Consumption",
        "prompt": "On average, how many cigarettes do you smoke per day?",
        "category": "Smoking History",
        "required": False,
        "depends_on": {"id": "smoking_history", "value": "Yes"},
        "validation": "Please enter the number of cigarettes you smoke per day."
    },
    {
        "id": "smoking_years",
        "text": "Total Years of Smoking",
        "prompt": "For how many years in total have you smoked?",
        "category": "Smoking History",
        "required": False,
        "depends_on": {"id": "smoking_history", "value": "Yes"},
        "validation": "Please enter the total years of smoking."
    },
    {
        "id": "smoking_quit",
        "text": "Quit Smoking",
        "prompt": "Have you already quit smoking?",
        "category": "Smoking History",
        "required": False,
        "depends_on": {"id": "smoking_history", "value": "Yes"},
        "options": ["Yes", "No"],
        "validation": "Please indicate whether you have quit smoking."
    },
    {
        "id": "smoking_quit_years",
        "text": "Years Since Quitting",
        "prompt": "How many years has it been since you quit smoking?",
        "category": "Smoking History",
        "required": False,
        "depends_on": {"id": "smoking_quit", "value": "Yes"},
        "validation": "Please enter the number of years since quitting smoking."
    },

    # Family History
    {
        "id": "family_cancer",
        "text": "Family History of Lung Cancer",
        "prompt": "Has any of your first-degree relatives been diagnosed with lung cancer?",
        "category": "Family History",
        "required": True,
        "options": ["Yes", "No"],
        "validation": "Please indicate whether you have a family history of lung cancer."
    },
    {
        "id": "family_cancer_details",
        "text": "Details of Family Lung Cancer",
        "prompt": "Please specify which relative(s) had lung cancer.",
        "category": "Family History",
        "required": False,
        "depends_on": {"id": "family_cancer", "value": "Yes"},
        "validation": "Please describe your family history of lung cancer in more detail."
    },

    # Occupation
    {
        "id": "occupation",
        "text": "Occupation",
        "prompt": "What is your current occupation?",
        "category": "Occupation Information",
        "required": True,
        "validation": "Please enter your occupation."
    },

    # Symptom Assessment
    {
        "id": "recent_symptoms",
        "text": "Recent Symptoms",
        "prompt": "Recently, have you experienced persistent cough, blood in sputum, hoarseness, or similar symptoms?",
        "category": "Symptom Assessment",
        "required": True,
        "options": ["Yes", "No"],
        "validation": "Please indicate whether you have any recent symptoms."
    },
    {
        "id": "symptoms_details",
        "text": "Details of Symptoms",
        "prompt": "Please describe your symptoms in more detail.",
        "category": "Symptom Assessment",
        "required": False,
        "depends_on": {"id": "recent_symptoms", "value": "Yes"},
        "validation": "Please provide more details about your symptoms."
    },

    # Self-rated Health
    {
        "id": "self_health",
        "text": "Self-rated Health Status",
        "prompt": "How would you rate your overall health condition?",
        "category": "Self-rated Health",
        "required": True,
        "options": ["Good", "Average", "Poor"],
        "validation": "Please select your self-rated health status."
    },
]

# ========== Questionnaire Configuration ==========

QUESTIONNAIRE_CONFIG = {
    "title": "Lung Cancer Risk Screening Questionnaire (Simple Version)",
    "description": "A simple lung cancer risk assessment questionnaire based on basic clinical risk factors.",
    "version": "1.0",
    "estimated_time": "10–15 minutes",
    "total_questions": len(QUESTIONS),
}

# ========== Helper Functions ==========


def get_question_by_id(question_id: str) -> dict:
    """Get a question by its ID."""
    for question in QUESTIONS:
        if question["id"] == question_id:
            return question
    return None


def get_questions_by_category(category: str) -> list:
    """Get all questions under a specific category."""
    return [q for q in QUESTIONS if q["category"] == category]


def get_next_question_index(current_index: int, answers: dict) -> int:
    """
    Get the index of the next question, taking dependency conditions into account.
    Returns:
        - next_index (int): index of the next question, or -1 if no more questions.
    """
    next_index = current_index + 1

    while next_index < len(QUESTIONS):
        question = QUESTIONS[next_index]

        # Check dependency condition
        if "depends_on" in question:
            depends_on = question["depends_on"]
            dependent_question_id = depends_on["id"]
            required_value = depends_on["value"]

            # Look up the answer to the dependent question
            dependent_answer = answers.get(dependent_question_id, "")
            if dependent_answer != required_value:
                # Dependency not satisfied, skip this question
                next_index += 1
                continue

        # Dependency satisfied or no dependency, return this question index
        return next_index

    # No more questions
    return -1


def validate_answer(question_id: str, answer: str) -> Tuple[bool, str]:
    """Validate a user's answer to a given question."""
    question = get_question_by_id(question_id)
    if not question:
        return False, "Question does not exist."

    # Required check
    if (not answer) or answer.strip() == "":
        if question.get("required", False):
            return False, question.get("validation", "This question is required.")
        return True, "Valid answer."

    # Basic length check
    if len(answer.strip()) < 1:
        return False, "The answer is too short. Please provide more information."

    # Option check
    if "options" in question:
        if answer not in question["options"]:
            return False, "Please choose from the following options: " + ", ".join(
                question["options"]
            )

    return True, "Valid answer."


def generate_simple_report(answers: dict) -> str:
    """
    Generate a simple textual report (basic version).
    Note: Detailed occupational carcinogen risk analysis is left to the AI agent.
    """
    report = "Lung Cancer Risk Screening Report (Simple Version)\n\n" + "=" * 50 + "\n\n"

    # Basic Information
    report += "[Basic Information]\n"
    report += f"Name: {answers.get('name', 'Not provided')}\n"
    report += f"Gender: {answers.get('gender', 'Not provided')}\n"
    report += f"Age: {answers.get('age', 'Not provided')}\n"
    report += f"Contact Number: {answers.get('phone', 'Not provided')}\n"
    report += f"Occupation: {answers.get('occupation', 'Not provided')}\n\n"

    # Physical Measurements
    height = answers.get("height", "")
    weight = answers.get("weight", "")
    if height and weight:
        try:
            h = float(height)
            w = float(weight)
            bmi = w / ((h / 100) ** 2)
            report += f"Height: {h:.1f} cm, Weight: {w:.1f} kg, BMI: {bmi:.1f}\n\n"
        except Exception:
            report += f"Height: {height} cm, Weight: {weight} kg\n\n"

    # Basic Risk Assessment (does NOT include occupational carcinogen exposure;
    # that part is delegated to the AI agent)
    report += "[Basic Risk Assessment]\n"
    basic_risk_factors = []

    if answers.get("smoking_history") == "Yes":
        basic_risk_factors.append("Smoking history")

    if answers.get("family_cancer") == "Yes":
        basic_risk_factors.append("Family history of lung cancer")

    if answers.get("recent_symptoms") == "Yes":
        basic_risk_factors.append("Recent respiratory-related symptoms")

    if basic_risk_factors:
        report += "Identified basic risk factors: " + ", ".join(basic_risk_factors) + "\n"
    else:
        report += "No obvious basic risk factors were identified based on the provided answers.\n"

    # Occupational carcinogen exposure risk will be assessed by the AI agent
    report += "\n[AI-based Occupational Risk Analysis]\n"
    report += (
        "The risk related to occupational exposure to carcinogens will be assessed "
        "by the AI agent based on your occupation and other relevant information.\n"
        "Please refer to the full AI-generated report for a detailed evaluation.\n"
    )

    # Basic Recommendations
    report += "\n[Basic Medical Recommendations]\n"
    if len(basic_risk_factors) >= 2:
        report += "1. It is recommended to have regular health check-ups.\n"
        report += "2. Please consider improving lifestyle habits (e.g., smoking cessation, exercise).\n"
        report += "3. If you experience persistent or worsening symptoms, seek medical attention promptly.\n"
    else:
        report += "1. Maintain a healthy lifestyle and avoid risk factors such as smoking.\n"
        report += "2. Have periodic health check-ups.\n"
        report += "3. Pay attention to any new or persistent respiratory symptoms.\n"

    report += (
        "\nNote: This is a simplified screening report. A complete risk assessment, "
        "including occupational carcinogen exposure, will be generated by the AI agent.\n"
    )

    return report
