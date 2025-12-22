from metagpt_questionnaire.simple_questionnaire_manager import SimpleQuestionnaireManager
from metagpt_questionnaire.models.questionnaire import Questionnaire, Question, QuestionType


def _build_questionnaire() -> Questionnaire:
    questions = [
        Question(
            id="smoking_history",
            text="Smoking History",
            type=QuestionType.TEXT,
            category="Smoking History",
        ),
        Question(
            id="smoking_quit",
            text="Currently Quit Smoking",
            type=QuestionType.TEXT,
            category="Smoking History",
        ),
        Question(
            id="smoking_freq",
            text="Daily Cigarette Consumption",
            type=QuestionType.NUMBER,
            category="Smoking History",
        ),
        Question(
            id="smoking_years",
            text="Years of Smoking",
            type=QuestionType.NUMBER,
            category="Smoking History",
        ),
        Question(
            id="smoking_quit_years",
            text="Years Since Quitting",
            type=QuestionType.NUMBER,
            category="Smoking History",
        ),
    ]
    return Questionnaire(
        id="test",
        title="Smoking Test",
        description="Test questionnaire for smoking branch",
        questions=questions,
    )


def test_pack_years_from_pack_per_day() -> None:
    manager = SimpleQuestionnaireManager()
    details = manager._normalize_smoking_response("yes, 1 pack/day for 20 years")
    manager._merge_smoking_details(details)
    smoking = manager.structured_answers["smoking"]
    assert smoking["status"] in {"current", "former"}
    assert smoking["pack_years"] == 20.0


def test_former_smoker_pack_years() -> None:
    manager = SimpleQuestionnaireManager()
    details = manager._normalize_smoking_response(
        "former smoker, 10 cigarettes/day for 30 years, quit 2015"
    )
    manager._merge_smoking_details(details)
    smoking = manager.structured_answers["smoking"]
    assert smoking["status"] == "former"
    assert smoking["quit_year"] == 2015
    assert smoking["pack_years"] == 15.0


def test_no_smoking_no_followup() -> None:
    manager = SimpleQuestionnaireManager()
    manager.initialize_questionnaire(_build_questionnaire())
    details = manager._normalize_smoking_response("no")
    manager._merge_smoking_details(details)
    manager._update_smoking_followup_queue()
    smoking = manager.structured_answers["smoking"]
    assert smoking["status"] == "never"
    assert manager.pending_followup_ids == []
