import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field

TEST_FILE = str(Path(__file__).parent / "tests.jsonl")
ExpectedErrorType = Literal["DomainAccessDeniedError", "PlanValidationError"]


class TestQuestion(BaseModel):
    """An APDC attendance case with optional deterministic expectations."""

    question: str = Field(description="The question to ask the RAG system")
    keywords: list[str] = Field(
        description="Keywords that must appear in retrieved context"
    )
    reference_answer: str = Field(description="The reference answer for this question")
    category: str = Field(description="Attendance behavior under evaluation")
    expected_plan: dict | None = None
    expected_employee_ids: list[str] = Field(default_factory=list)
    expected_matched_count: int | None = None
    expected_calculation: dict | None = None
    expected_answer_facts: list[str] = Field(default_factory=list)
    expected_clarification_ids: list[str] = Field(default_factory=list)
    expected_clarification_outcome: Literal["none", "ambiguous"] | None = None
    expected_record_ids: list[str] = Field(default_factory=list)
    expected_group_values: list[dict] = Field(default_factory=list)
    expected_normalized_result: dict | None = None
    expected_violation_codes: list[str] = Field(default_factory=list)
    expected_answer_contract: dict | None = None
    expected_unsupported_capabilities: list[str] = Field(default_factory=list)
    turns: list[dict] = Field(default_factory=list)
    expected_error: str | None = None
    expected_exception_type: ExpectedErrorType | None = None


def load_tests(loader=None, tests=None, pattern=None):
    """Load test questions, while remaining compatible with unittest discovery.

    ``unittest`` reserves the module-level ``load_tests`` name and calls it
    with loader arguments during discovery. The evaluator calls it with no
    arguments, in which case it returns the JSONL question list.
    """
    if loader is not None or tests is not None or pattern is not None:
        import unittest

        return unittest.TestSuite()

    if not Path(TEST_FILE).is_file():
        raise FileNotFoundError(
            "The private evaluation corpus is not available in this checkout. "
            "Provide week5/new_evaluation/tests.jsonl from an authorized local "
            "APDC evaluation environment."
        )

    tests = []
    with open(TEST_FILE, "r", encoding="utf-8") as f:
        for line in f:
            data = json.loads(line.strip())
            tests.append(TestQuestion(**data))
    return tests
