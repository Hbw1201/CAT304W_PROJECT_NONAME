import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = PROJECT_ROOT / "feiaiagent"
for path in (PROJECT_ROOT, APP_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from feiaiagent.metagpt_questionnaire.models.questionnaire import (  # noqa: E402
    Questionnaire,
    Question,
    QuestionType,
    UserResponse,
)
from feiaiagent.metagpt_questionnaire.simple_questionnaire_manager import (  # noqa: E402
    SimpleQuestionnaireManager,
)


def _build_questionnaire(questions):
    return Questionnaire(
        id="test",
        title="Test Questionnaire",
        description="Testing candidate pool",
        questions=questions,
    )


def test_smoking_followups_removed_when_not_smoker():
    questionnaire = _build_questionnaire(
        [
            Question(
                id="smoking_history",
                text="Do you smoke?",
                type=QuestionType.SINGLE_CHOICE,
                category="Smoking History",
            ),
            Question(
                id="smoking_freq",
                text="How often do you smoke?",
                type=QuestionType.TEXT,
                category="Smoking History",
            ),
            Question(
                id="general_health",
                text="How do you feel today?",
                type=QuestionType.TEXT,
                category="General",
            ),
        ]
    )

    manager = SimpleQuestionnaireManager()
    manager.initialize_questionnaire(questionnaire)
    manager.answered_questions = [
        UserResponse(question_id="smoking_history", answer="2")
    ]

    candidates, _, _ = manager._build_candidate_context()
    candidate_ids = {question.id for question in candidates}

    assert "smoking_freq" not in candidate_ids
    assert "general_health" in candidate_ids


def test_dependencies_block_until_requirement_met():
    questionnaire = _build_questionnaire(
        [
            Question(
                id="q1",
                text="Have you had imaging done?",
                type=QuestionType.SINGLE_CHOICE,
                category="History",
            ),
            Question(
                id="q2",
                text="What were the imaging results?",
                type=QuestionType.TEXT,
                category="History",
                validation_rules={"depends_on": {"id": "q1", "value": "1"}},
            ),
        ]
    )

    manager = SimpleQuestionnaireManager()
    manager.initialize_questionnaire(questionnaire)

    manager.answered_questions = [UserResponse(question_id="q1", answer="2")]
    candidates, _, _ = manager._build_candidate_context()
    candidate_ids = {question.id for question in candidates}
    assert "q2" not in candidate_ids

    manager.answered_questions = [UserResponse(question_id="q1", answer="1")]
    candidates, _, _ = manager._build_candidate_context()
    candidate_ids = {question.id for question in candidates}
    assert "q2" in candidate_ids
