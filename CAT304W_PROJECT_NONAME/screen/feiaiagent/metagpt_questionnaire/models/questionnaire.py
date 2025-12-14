# -*- coding: utf-8 -*-
"""
Questionnaire data models
Define data structures for questionnaire, questions, answers, risk assessment, and reports.
"""

from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import json


class QuestionType(Enum):
    """Question type enumeration."""
    TEXT = "text"                   # Free text input
    SINGLE_CHOICE = "single_choice"  # Single-choice
    MULTIPLE_CHOICE = "multiple_choice"  # Multiple-choice
    NUMBER = "number"               # Numeric input
    DATE = "date"                   # Date input
    SCALE = "scale"                 # Rating / scale


class RiskLevel(Enum):
    """Risk level enumeration."""
    LOW = "low"        # Low risk
    MEDIUM = "medium"  # Medium risk
    HIGH = "high"      # High risk


@dataclass
class QuestionOption:
    """Option for a question."""
    value: str
    label: str
    score: Optional[int] = None
    risk_factor: Optional[float] = None


@dataclass
class Question:
    """Question model."""
    id: str
    text: str
    type: QuestionType
    category: str
    required: bool = True
    options: Optional[List[QuestionOption]] = None
    validation_rules: Optional[Dict[str, Any]] = None
    help_text: Optional[str] = None
    risk_weight: float = 1.0
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "text": self.text,
            "type": self.type.value,
            "category": self.category,
            "required": self.required,
            "options": [opt.__dict__ for opt in (self.options or [])],
            "validation_rules": self.validation_rules,
            "help_text": self.help_text,
            "risk_weight": self.risk_weight,
            "metadata": self.metadata,
        }


@dataclass
class Questionnaire:
    """Questionnaire model."""
    id: str
    title: str
    description: str
    version: str = "1.0"
    questions: List[Question] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)
    estimated_time: str = "15–20 minutes"
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def add_question(self, question: Question):
        """Add a question to the questionnaire."""
        self.questions.append(question)
        if question.category not in self.categories:
            self.categories.append(question.category)
        self.updated_at = datetime.now()

    def get_questions_by_category(self, category: str) -> List[Question]:
        """Get questions by category."""
        return [q for q in self.questions if q.category == category]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "version": self.version,
            "questions": [q.to_dict() for q in self.questions],
            "categories": self.categories,
            "estimated_time": self.estimated_time,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def save_to_file(self, filepath: str):
        """Save questionnaire definition to a JSON file."""
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load_from_file(cls, filepath: str) -> "Questionnaire":
        """Load questionnaire definition from a JSON file."""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        questions: List[Question] = []
        for q_data in data.get("questions", []):
            q_type = QuestionType(q_data["type"])
            options = None
            if q_data.get("options"):
                options = [QuestionOption(**opt) for opt in q_data["options"]]

            question = Question(
                id=q_data["id"],
                text=q_data["text"],
                type=q_type,
                category=q_data["category"],
                required=q_data.get("required", True),
                options=options,
                validation_rules=q_data.get("validation_rules"),
                help_text=q_data.get("help_text"),
                risk_weight=q_data.get("risk_weight", 1.0),
                metadata=q_data.get("metadata"),
            )
            questions.append(question)

        return cls(
            id=data["id"],
            title=data["title"],
            description=data["description"],
            version=data.get("version", "1.0"),
            questions=questions,
            categories=data.get("categories", []),
            estimated_time=data.get("estimated_time", "15–20 minutes"),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )


@dataclass
class UserResponse:
    """User response model."""
    question_id: str
    answer: Union[str, int, float, List[str]]
    timestamp: datetime = field(default_factory=datetime.now)
    confidence: Optional[float] = None  # Confidence score for the answer

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "question_id": self.question_id,
            "answer": self.answer,
            "timestamp": self.timestamp.isoformat(),
            "confidence": self.confidence,
        }


@dataclass
class QuestionnaireSession:
    """Questionnaire session model."""
    session_id: str
    questionnaire_id: str
    user_id: Optional[str] = None
    responses: List[UserResponse] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    status: str = "in_progress"  # in_progress, completed, abandoned

    def add_response(self, response: UserResponse):
        """Add a response to the session."""
        self.responses.append(response)

    def get_response(self, question_id: str) -> Optional[UserResponse]:
        """Get a specific response by question ID."""
        for response in self.responses:
            if response.question_id == question_id:
                return response
        return None

    def is_completed(self) -> bool:
        """Check if the questionnaire has been completed."""
        return self.status == "completed"

    def complete(self):
        """Mark the questionnaire session as completed."""
        self.status = "completed"
        self.completed_at = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "session_id": self.session_id,
            "questionnaire_id": self.questionnaire_id,
            "user_id": self.user_id,
            "responses": [r.to_dict() for r in self.responses],
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
            "status": self.status,
        }


@dataclass
class RiskAssessment:
    """Risk assessment model."""
    session_id: str
    overall_risk: RiskLevel
    risk_score: float
    risk_factors: List[Dict[str, Any]] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    assessed_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "session_id": self.session_id,
            "overall_risk": self.overall_risk.value,
            "risk_score": self.risk_score,
            "risk_factors": self.risk_factors,
            "recommendations": self.recommendations,
            "assessed_at": self.assessed_at.isoformat(),
        }


@dataclass
class AnalysisReport:
    """Analysis report model."""
    session_id: str
    title: str
    content: str
    risk_assessment: RiskAssessment
    data_insights: List[Dict[str, Any]] = field(default_factory=list)
    generated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "session_id": self.session_id,
            "title": self.title,
            "content": self.content,
            "risk_assessment": self.risk_assessment.to_dict(),
            "data_insights": self.data_insights,
            "generated_at": self.generated_at.isoformat(),
        }

    def save_to_file(self, filepath: str):
        """Save analysis report to file."""
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)


# Predefined lung cancer screening questionnaire template
def create_lung_cancer_questionnaire() -> Questionnaire:
    """Create a default lung cancer early screening questionnaire template."""
    questionnaire = Questionnaire(
        id="lung_cancer_screening_v1",
        title="Lung Cancer Risk Screening Questionnaire",
        description=(
            "A multi-factor questionnaire for lung cancer risk assessment, "
            "suitable for individuals aged 40–70."
        ),
        version="1.0",
        estimated_time="15–20 minutes",
    )

    # Basic information
    basic_questions = [
        Question(
            "name",
            "Full Name",
            QuestionType.TEXT,
            "Basic Information",
            True,
        ),
        Question(
            "gender",
            "Gender",
            QuestionType.SINGLE_CHOICE,
            "Basic Information",
            True,
            options=[
                QuestionOption("1", "Male", 0, 0.0),
                QuestionOption("2", "Female", 0, 0.0),
            ],
        ),
        Question(
            "age",
            "Age (years)",
            QuestionType.NUMBER,
            "Basic Information",
            True,
            validation_rules={"min": 40, "max": 70},
        ),
        Question(
            "height",
            "Height (cm)",
            QuestionType.NUMBER,
            "Basic Information",
            True,
            validation_rules={"min": 140, "max": 200},
        ),
        Question(
            "weight",
            "Weight (kg)",
            QuestionType.NUMBER,
            "Basic Information",
            True,
            validation_rules={"min": 40, "max": 150},
        ),
    ]

    # Smoking history
    smoking_questions = [
        Question(
            "smoking_history",
            "Do you have a history of smoking?",
            QuestionType.SINGLE_CHOICE,
            "Smoking History",
            True,
            options=[
                QuestionOption("1", "Yes", 2, 2.0),
                QuestionOption("2", "No", 0, 0.0),
            ],
        ),
        Question(
            "smoking_years",
            "Total years of smoking",
            QuestionType.NUMBER,
            "Smoking History",
            False,
            validation_rules={"min": 0, "max": 60},
        ),
        Question(
            "daily_cigarettes",
            "Average number of cigarettes per day",
            QuestionType.NUMBER,
            "Smoking History",
            False,
            validation_rules={"min": 0, "max": 100},
        ),
    ]

    # Occupational exposure
    occupational_questions = [
        Question(
            "occupational_exposure",
            "Have you been exposed to occupational carcinogens "
            "(e.g., asbestos, coal tar, radiation)?",
            QuestionType.SINGLE_CHOICE,
            "Occupational Exposure",
            True,
            options=[
                QuestionOption("1", "Yes", 2, 2.0),
                QuestionOption("2", "No", 0, 0.0),
            ],
        ),
        Question(
            "exposure_years",
            "Total years of occupational exposure",
            QuestionType.NUMBER,
            "Occupational Exposure",
            False,
            validation_rules={"min": 0, "max": 50},
        ),
    ]

    # Family history
    family_questions = [
        Question(
            "family_history",
            "Do any first-degree relatives (parents, siblings, children) "
            "have a history of lung cancer?",
            QuestionType.SINGLE_CHOICE,
            "Family History",
            True,
            options=[
                QuestionOption("1", "Yes", 2, 2.0),
                QuestionOption("2", "No", 0, 0.0),
            ],
        )
    ]

    # Symptoms
    symptom_questions = [
        Question(
            "cough",
            "Do you have a persistent dry cough?",
            QuestionType.SINGLE_CHOICE,
            "Symptoms",
            True,
            options=[
                QuestionOption("1", "Yes", 3, 3.0),
                QuestionOption("2", "No", 0, 0.0),
            ],
        ),
        Question(
            "hemoptysis",
            "Do you have blood in your sputum (hemoptysis)?",
            QuestionType.SINGLE_CHOICE,
            "Symptoms",
            True,
            options=[
                QuestionOption("1", "Yes", 3, 3.0),
                QuestionOption("2", "No", 0, 0.0),
            ],
        ),
        Question(
            "weight_loss",
            "In the past six months, have you experienced unexplained "
            "weight loss?",
            QuestionType.SINGLE_CHOICE,
            "Symptoms",
            True,
            options=[
                QuestionOption("1", "Yes", 2, 2.0),
                QuestionOption("2", "No", 0, 0.0),
            ],
        ),
    ]

    # Add all questions to the questionnaire
    all_questions = (
        basic_questions
        + smoking_questions
        + occupational_questions
        + family_questions
        + symptom_questions
    )

    for question in all_questions:
        questionnaire.add_question(question)

    return questionnaire


if __name__ == "__main__":
    # Test questionnaire creation
    questionnaire = create_lung_cancer_questionnaire()
    print(f"Questionnaire created: {questionnaire.title}")
    print(f"Total questions: {len(questionnaire.questions)}")
    print(f"Categories: {questionnaire.categories}")

    # Save to file
    questionnaire.save_to_file("lung_cancer_questionnaire.json")
    print("Questionnaire has been saved to file.")
