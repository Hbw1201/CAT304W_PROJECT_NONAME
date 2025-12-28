# -*- coding: utf-8 -*-
"""
Questionnaire data models for MetaGPT questionnaire system.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime


class QuestionType(Enum):
    """Question type enumeration."""
    TEXT = "text"
    SINGLE_CHOICE = "single_choice"
    MULTIPLE_CHOICE = "multiple_choice"
    NUMBER = "number"


class RiskLevel(Enum):
    """Risk level enumeration."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class QuestionOption:
    """Question option data class."""
    value: str
    label: str
    
    def to_dict(self) -> Dict[str, str]:
        """Convert to dictionary."""
        return {"value": self.value, "label": self.label}


@dataclass
class Question:
    """Question data class."""
    id: str
    text: str
    type: QuestionType
    category: Optional[str] = None
    required: bool = True
    options: Optional[List[QuestionOption]] = None
    depends_on: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    help_text: Optional[str] = None
    validation_rules: Optional[Dict[str, Any]] = None
    risk_weight: float = 1.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "id": self.id,
            "text": self.text,
            "type": self.type.value if isinstance(self.type, QuestionType) else str(self.type),
            "required": self.required,
            "risk_weight": self.risk_weight,
        }
        if self.category:
            result["category"] = self.category
        if self.options:
            result["options"] = [opt.to_dict() for opt in self.options]
        if self.depends_on:
            result["depends_on"] = self.depends_on
        if self.metadata:
            result["metadata"] = self.metadata
        if self.help_text:
            result["help_text"] = self.help_text
        if self.validation_rules:
            result["validation_rules"] = self.validation_rules
        return result


@dataclass
class Questionnaire:
    """Questionnaire data class."""
    id: str
    title: str
    description: str
    questions: List[Question] = field(default_factory=list)
    language: str = "en"
    version: str = "1.0"
    estimated_time: Optional[str] = None
    
    def add_question(self, question: Question) -> None:
        """Add a question to the questionnaire."""
        self.questions.append(question)
    
    def get_question_by_id(self, question_id: str) -> Optional[Question]:
        """Get a question by its ID."""
        for question in self.questions:
            if question.id == question_id:
                return question
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "language": self.language,
            "version": self.version,
            "questions": [q.to_dict() for q in self.questions],
        }
        if self.estimated_time:
            result["estimated_time"] = self.estimated_time
        return result


@dataclass
class UserResponse:
    """User response data class."""
    question_id: str
    answer: str
    raw_answer: Optional[str] = None
    timestamp: Optional[datetime] = None
    confidence: Optional[float] = None
    
    def __post_init__(self):
        """Set default timestamp if not provided."""
        if self.timestamp is None:
            self.timestamp = datetime.now()
        if self.raw_answer is None:
            self.raw_answer = self.answer

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary without raising exceptions."""
        try:
            data: Dict[str, Any] = {
                "question_id": getattr(self, "question_id", None)
                or getattr(self, "id", None)
                or getattr(self, "key", None),
                "answer": getattr(self, "answer", None) or getattr(self, "value", None),
                "confidence": getattr(self, "confidence", None)
                or getattr(self, "score", None)
                or getattr(self, "prob", None),
            }
            for key in ("category", "timestamp", "source", "raw", "notes", "reasoning", "raw_answer"):
                if hasattr(self, key):
                    data[key] = getattr(self, key)
            return data
        except Exception:
            return {"question_id": None, "answer": None, "confidence": None}


@dataclass
class RiskAssessment:
    """Risk assessment data class."""
    session_id: str
    overall_risk: RiskLevel
    risk_score: Optional[float] = None
    risk_factors: Optional[List[Dict[str, Any]]] = None
    recommendations: Optional[List[str]] = None
    reasoning: Optional[str] = None
    assessed_at: Optional[datetime] = None
    
    def __post_init__(self):
        """Set default values."""
        if self.assessed_at is None:
            self.assessed_at = datetime.now()
        if self.risk_factors is None:
            self.risk_factors = []
        if self.recommendations is None:
            self.recommendations = []


@dataclass
class AnalysisReport:
    """Analysis report data class."""
    summary: str
    risk_assessment: Optional[RiskAssessment] = None
    risk_factors: Optional[List[Dict[str, Any]]] = None
    recommendations: Optional[List[str]] = None
    generated_at: Optional[datetime] = None
    
    def __post_init__(self):
        """Set default values."""
        if self.generated_at is None:
            self.generated_at = datetime.now()
        if self.risk_factors is None:
            self.risk_factors = []
        if self.recommendations is None:
            self.recommendations = []


def create_lung_cancer_questionnaire() -> Questionnaire:
    """
    Create a basic lung cancer questionnaire.
    Returns a minimal Questionnaire object that can be populated later by designers/managers.
    """
    return Questionnaire(
        id="lung_cancer_questionnaire",
        title="Lung Cancer Risk Assessment Questionnaire",
        description="A comprehensive questionnaire for assessing lung cancer risk factors",
        language="en",
        version="1.0",
        estimated_time="15-20 minutes",
        questions=[]  # Empty list - will be populated by questionnaire designer
    )

