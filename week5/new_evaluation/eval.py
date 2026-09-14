import sys
import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from litellm import completion

if __package__ and __package__.startswith("week5."):
    from .test import TestQuestion, load_tests
    from ..new_implementation.answer import (
        POSTGRES_ATTENDANCE_TABLE,
        POSTGRES_DSN,
        ConversationState,
        EmployeeClarificationRequired,
        QueryPlan,
        SemanticPlanValidationError,
        _import_psycopg,
        answer_question,
        answer_question_with_state,
        fetch_context,
        _format_aggregation_answer,
        _answer_question_with_evaluation_trace,
    )
    from ..new_implementation.config import settings
elif __package__ == "new_evaluation":
    from .test import TestQuestion, load_tests
    from new_implementation.answer import (
        POSTGRES_ATTENDANCE_TABLE,
        POSTGRES_DSN,
        ConversationState,
        EmployeeClarificationRequired,
        QueryPlan,
        SemanticPlanValidationError,
        _import_psycopg,
        answer_question,
        answer_question_with_state,
        fetch_context,
        _format_aggregation_answer,
        _answer_question_with_evaluation_trace,
    )
    from new_implementation.config import settings
else:
    # ``python eval.py`` places only this directory on sys.path. Add the
    # week5 package root so the evaluator uses the new implementation and its
    # own test set rather than the legacy evaluation package.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from new_evaluation.test import TestQuestion, load_tests
    from new_implementation.answer import (
        POSTGRES_ATTENDANCE_TABLE,
        POSTGRES_DSN,
        ConversationState,
        EmployeeClarificationRequired,
        QueryPlan,
        SemanticPlanValidationError,
        _import_psycopg,
        answer_question,
        answer_question_with_state,
        fetch_context,
        _format_aggregation_answer,
        _answer_question_with_evaluation_trace,
    )
    from new_implementation.config import settings


MODEL = settings.rag_model
DATASET_MANIFEST_PATH = Path(__file__).with_name("dataset_manifest.json")


def _retrieved_documents(context_result):
    """Normalize the shared retrieval result to a document list."""
    if isinstance(context_result, tuple):
        return context_result[0]
    return context_result


class RetrievalEval(BaseModel):
    """Evaluation metrics for retrieval performance."""

    mrr: float = Field(description="Mean Reciprocal Rank - average across all keywords")
    ndcg: float = Field(
        description="Normalized Discounted Cumulative Gain (binary relevance)"
    )
    keywords_found: int = Field(description="Number of keywords found in top-k results")
    total_keywords: int = Field(description="Total number of keywords to find")
    keyword_coverage: float = Field(description="Percentage of keywords found")


class AnswerEval(BaseModel):
    """LLM-as-a-judge evaluation of answer quality."""

    feedback: str = Field(
        description="Concise feedback on the answer quality, comparing it to the reference answer and evaluating based on the retrieved context"
    )
    accuracy: float = Field(
        description="How factually correct is the answer compared to the reference answer? 1 (wrong. any wrong answer must score 1) to 5 (ideal - perfectly accurate). An acceptable answer would score 3."
    )
    completeness: float = Field(
        description="How complete is the answer in addressing all aspects of the question? 1 (very poor - missing key information) to 5 (ideal - all the information from the reference answer is provided completely). Only answer 5 if ALL information from the reference answer is included."
    )
    relevance: float = Field(
        description="How relevant is the answer to the specific question asked? 1 (very poor - off-topic) to 5 (ideal - directly addresses question and gives no additional information). Only answer 5 if the answer is completely relevant to the question and gives no additional information."
    )


class BehaviorEval(BaseModel):
    plan_ok: bool
    employee_ids_ok: bool
    matched_count_ok: bool
    calculation_ok: bool
    clarification_ok: bool
    answer_facts_ok: bool = True
    normalized_result_ok: bool = True
    multi_turn_ok: bool = True
    expected_error_ok: bool = True
    record_ids_ok: bool = True
    group_values_ok: bool = True
    violation_codes_ok: bool = True
    answer_contract_ok: bool = True
    unsupported_capabilities_ok: bool = True


class AnswerExecutionTrace(BaseModel):
    """Non-sensitive structure captured from the exact answer execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: Literal["executed", "controlled_nonexecution"] = "executed"
    plan: QueryPlan | None
    calculation: dict | None
    matched_count: int | None


FailureCause = Literal[
    "provider_structural_failure",
    "unsupported_provider_decision",
    "missing_deterministic_fact",
    "excess_or_ungrounded_fact",
    "unsupported_plan_shape",
    "answer_contract_mismatch",
    "retrieval_or_calculation_mismatch",
    "renderer_incomplete",
    "irrelevant_evidence",
    "session_state_failure",
    "evaluator_expectation_drift",
    "execution_plan_mismatch",
]
DiagnosticStage = Literal[
    "provider_response_validation",
    "semantic_validation",
    "result_validation",
    "answer_rendering",
    "session_state",
    "answer_judging",
]


def classify_case_diagnostic(
    *,
    stage: DiagnosticStage,
    violation_codes: tuple[str, ...] = (),
    unsupported_capabilities: tuple[str, ...] = (),
    failed_checks: tuple[str, ...] = (),
    sub_five_dimensions: tuple[str, ...] = (),
    evidence_count: int | None = None,
) -> FailureCause:
    """Classify one failure using controlled, non-sensitive structure only."""
    if stage == "provider_response_validation":
        return "provider_structural_failure"
    if unsupported_capabilities:
        return "unsupported_plan_shape"
    if "uncovered_fact" in violation_codes:
        return "missing_deterministic_fact"
    if "ungrounded_constraint" in violation_codes:
        return "excess_or_ungrounded_fact"
    if "answer_contract_mismatch" in violation_codes:
        return "answer_contract_mismatch"
    if "answer_contract_ok" in failed_checks:
        return "answer_contract_mismatch"
    if {
        "violation_codes_ok",
        "unsupported_capabilities_ok",
    }.intersection(failed_checks):
        return "unsupported_plan_shape"
    if stage == "session_state" or {
        "clarification_ok",
        "multi_turn_ok",
        "employee_ids_ok",
    }.intersection(failed_checks):
        return "session_state_failure"
    if stage == "result_validation" or {
        "matched_count_ok",
        "calculation_ok",
        "normalized_result_ok",
        "record_ids_ok",
        "group_values_ok",
    }.intersection(failed_checks):
        return "retrieval_or_calculation_mismatch"
    if "plan_ok" in failed_checks:
        return "execution_plan_mismatch"
    if stage == "answer_rendering" or "answer_facts_ok" in failed_checks:
        return "renderer_incomplete"
    if violation_codes:
        return "unsupported_provider_decision"
    return "evaluator_expectation_drift"


class CaseDiagnostic(BaseModel):
    """Privacy-safe structural classification for one corpus case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int = Field(ge=0)
    category: str = Field(min_length=1)
    stage: DiagnosticStage
    cause: FailureCause
    violation_codes: tuple[str, ...] = ()
    unsupported_capabilities: tuple[str, ...] = ()
    failed_checks: tuple[str, ...] = ()
    sub_five_dimensions: tuple[str, ...] = ()
    fact_kind_counts: dict[str, int] = Field(default_factory=dict)
    evidence_count: int | None = Field(default=None, ge=0)

    @classmethod
    def from_failure(
        cls,
        *,
        index: int,
        category: str,
        stage: DiagnosticStage,
        violation_codes: tuple[str, ...] = (),
        unsupported_capabilities: tuple[str, ...] = (),
        failed_checks: tuple[str, ...] = (),
        sub_five_dimensions: tuple[str, ...] = (),
        fact_kind_counts: dict[str, int] | None = None,
        evidence_count: int | None = None,
    ) -> "CaseDiagnostic":
        cause = classify_case_diagnostic(
            stage=stage,
            violation_codes=violation_codes,
            unsupported_capabilities=unsupported_capabilities,
            failed_checks=failed_checks,
            sub_five_dimensions=sub_five_dimensions,
            evidence_count=evidence_count,
        )
        return cls(
            index=index,
            category=category,
            stage=stage,
            cause=cause,
            violation_codes=tuple(sorted(set(violation_codes))),
            unsupported_capabilities=tuple(sorted(set(unsupported_capabilities))),
            failed_checks=tuple(sorted(set(failed_checks))),
            sub_five_dimensions=tuple(sorted(set(sub_five_dimensions))),
            fact_kind_counts=dict(sorted((fact_kind_counts or {}).items())),
            evidence_count=evidence_count,
        )


def diagnose_behavior_result(
    *, index: int, category: str, result: BehaviorEval
) -> CaseDiagnostic | None:
    """Convert failed deterministic checks into one privacy-safe diagnosis."""
    failed_checks = tuple(
        name for name, value in result.model_dump().items() if not value
    )
    if not failed_checks:
        return None
    if {"violation_codes_ok", "unsupported_capabilities_ok"}.intersection(
        failed_checks
    ):
        stage: DiagnosticStage = "semantic_validation"
    elif {"clarification_ok", "multi_turn_ok", "employee_ids_ok"}.intersection(
        failed_checks
    ):
        stage = "session_state"
    elif {
        "matched_count_ok",
        "calculation_ok",
        "normalized_result_ok",
        "record_ids_ok",
        "group_values_ok",
    }.intersection(failed_checks):
        stage = "result_validation"
    elif "answer_facts_ok" in failed_checks:
        stage = "answer_rendering"
    else:
        stage = "semantic_validation"
    return CaseDiagnostic.from_failure(
        index=index,
        category=category,
        stage=stage,
        failed_checks=failed_checks,
    )


def evaluate_answer_with_diagnostic(
    test: TestQuestion, *, index: int
) -> tuple[AnswerEval, CaseDiagnostic | None]:
    """Judge one answer and classify sub-perfect scores from that same run."""
    result, generated_answer, documents, trace = evaluate_answer_execution(test)
    dimensions = tuple(
        name
        for name in ("accuracy", "completeness", "relevance")
        if getattr(result, name) < 5
    )
    if not dimensions:
        return result, None
    diagnostic = None
    if trace.plan is not None:
        behavior = _evaluate_successful_behavior(
            test,
            documents,
            trace.plan,
            trace.calculation,
            trace.matched_count,
            generated_answer=generated_answer,
        )
        diagnostic = diagnose_behavior_result(
            index=index, category=test.category, result=behavior
        )
    elif not (
        test.expected_error
        or test.expected_violation_codes
        or test.expected_unsupported_capabilities
        or test.expected_clarification_ids
        or test.expected_clarification_outcome
    ):
        diagnostic = CaseDiagnostic.from_failure(
            index=index,
            category=test.category,
            stage="session_state",
            failed_checks=("clarification_ok",),
        )
    if diagnostic is None:
        missing_keywords = any(
            not any(
                keyword.casefold() in document.page_content.casefold()
                for document in documents
            )
            for keyword in test.keywords
        )
        diagnostic = CaseDiagnostic.from_failure(
            index=index,
            category=test.category,
            stage="answer_judging",
            sub_five_dimensions=dimensions,
            evidence_count=(0 if missing_keywords else None),
        )
        if missing_keywords:
            diagnostic = diagnostic.model_copy(update={"cause": "irrelevant_evidence"})
    diagnostic = diagnostic.model_copy(
        update={
            "sub_five_dimensions": tuple(sorted(set(dimensions))),
            "evidence_count": len(documents),
        }
    )
    return result, diagnostic


def _expected_subset(actual: dict | None, expected: dict | None):
    if not expected:
        return True
    if actual is None:
        return False

    def matches(key, actual_value, expected_value):
        if key == "business_predicates" and isinstance(actual_value, list):
            return set(actual_value) == set(expected_value)
        if isinstance(expected_value, float) and isinstance(actual_value, (int, float)):
            return math.isclose(actual_value, expected_value, abs_tol=0.005)
        return actual_value == expected_value

    return all(matches(key, actual.get(key), value) for key, value in expected.items())


def _expected_group_values_match(actual: dict | None, expected: list[dict]):
    if not expected:
        return True
    if not actual or not isinstance(actual.get("rows"), list):
        return False
    actual_by_group = {
        tuple(row.get("group", [])): row.get("value") for row in actual["rows"]
    }
    for expected_row in expected:
        actual_value = actual_by_group.get(tuple(expected_row.get("group", [])))
        expected_value = expected_row.get("value")
        if isinstance(expected_value, (int, float)) and isinstance(
            actual_value, (int, float)
        ):
            if not math.isclose(actual_value, expected_value, abs_tol=0.005):
                return False
        elif actual_value != expected_value:
            return False
    return True


def _plan_employee_ids(plan: QueryPlan):
    employee_ids = []
    for condition in plan.filters:
        if condition.field != "Employee_ID":
            continue
        if isinstance(condition.value, list):
            employee_ids.extend(str(value) for value in condition.value)
        else:
            employee_ids.append(str(condition.value))
    return employee_ids


def _evaluate_successful_behavior(
    test: TestQuestion,
    chunks: list,
    plan: QueryPlan,
    calculation: dict | None,
    matched_count: int | None,
    *,
    generated_answer: str | None = None,
) -> BehaviorEval:
    """Evaluate expectations against one already-completed public execution."""
    expected_plan = test.expected_plan or {}
    if test.category in {"semantic", "hybrid"} and "mode" not in expected_plan:
        expected_plan = {**expected_plan, "mode": test.category}
    required_filter = expected_plan.get("required_filter")
    required_filters = expected_plan.get("required_filters", [])
    expected_business_predicates = expected_plan.get("business_predicates")
    plan_values = plan.model_dump()
    plan_ok = all(
        plan_values.get(key) == value
        for key, value in expected_plan.items()
        if key not in {"required_filter", "required_filters", "business_predicates"}
    )
    if expected_business_predicates is not None:
        plan_ok = plan_ok and set(plan.business_predicates) == set(
            expected_business_predicates
        )
    actual_filters = [condition.model_dump() for condition in plan.filters]
    if required_filter:
        plan_ok = plan_ok and required_filter in actual_filters
    if required_filters:
        plan_ok = plan_ok and all(
            required in actual_filters for required in required_filters
        )

    answer_facts_ok = True
    if test.expected_answer_facts:
        if generated_answer is None and calculation is not None:
            generated_answer = _format_aggregation_answer(plan, calculation)
        elif generated_answer is None:
            generated_answer, _documents = answer_question(test.question)
        answer_facts_ok = all(
            fact.casefold() in generated_answer.casefold()
            for fact in test.expected_answer_facts
        )

    normalized_actual = {
        "plan": plan.model_dump(),
        "calculation": calculation,
        "matched_count": matched_count,
    }
    actual_record_ids = {
        str(chunk.metadata.get("record_id"))
        for chunk in chunks
        if chunk.metadata.get("record_id") is not None
    }
    return BehaviorEval(
        plan_ok=plan_ok,
        employee_ids_ok=(
            not test.expected_employee_ids
            or _plan_employee_ids(plan) == test.expected_employee_ids
        ),
        matched_count_ok=(
            test.expected_matched_count is None
            or matched_count == test.expected_matched_count
        ),
        calculation_ok=_expected_subset(calculation, test.expected_calculation),
        clarification_ok=True,
        answer_facts_ok=answer_facts_ok,
        normalized_result_ok=_expected_subset(
            normalized_actual, test.expected_normalized_result
        ),
        expected_error_ok=test.expected_error is None,
        record_ids_ok=(
            not test.expected_record_ids
            or set(test.expected_record_ids).issubset(actual_record_ids)
        ),
        group_values_ok=_expected_group_values_match(
            calculation, test.expected_group_values
        ),
        violation_codes_ok=not test.expected_violation_codes,
        answer_contract_ok=(
            test.expected_answer_contract is None
            or _expected_subset(
                (
                    getattr(plan, "answer_contract", None).model_dump()
                    if getattr(plan, "answer_contract", None) is not None
                    else None
                ),
                test.expected_answer_contract,
            )
        ),
        unsupported_capabilities_ok=not test.expected_unsupported_capabilities,
    )


def evaluate_behavior(test: TestQuestion) -> BehaviorEval:
    if test.expected_clarification_ids:
        text, _chunks, state = answer_question_with_state(
            test.question, [], ConversationState()
        )
        actual_ids = [candidate.employee_id for candidate in state.pending_candidates]
        clarification_ok = actual_ids == test.expected_clarification_ids and all(
            employee_id in text for employee_id in actual_ids
        )
        multi_turn_ok = True
        for turn in test.turns:
            text, _chunks, state = answer_question_with_state(
                str(turn["user"]), [], state
            )
            selected_ids = [
                candidate.employee_id for candidate in state.selected_employees
            ]
            pending_ids = [
                candidate.employee_id for candidate in state.pending_candidates
            ]
            if "expected_employee_ids" in turn:
                multi_turn_ok = (
                    multi_turn_ok
                    and selected_ids == turn["expected_employee_ids"]
                    and not pending_ids
                    and state.pending_question is None
                    and state.pending_proposal is None
                    and state.pending_constraint is None
                )
            if "expected_pending_ids" in turn:
                multi_turn_ok = (
                    multi_turn_ok and pending_ids == turn["expected_pending_ids"]
                )
            multi_turn_ok = multi_turn_ok and all(
                str(fact).casefold() in text.casefold()
                for fact in turn.get("expected_answer_facts", [])
            )
        return BehaviorEval(
            plan_ok=True,
            employee_ids_ok=True,
            matched_count_ok=True,
            calculation_ok=True,
            clarification_ok=clarification_ok,
            answer_facts_ok=True,
            multi_turn_ok=multi_turn_ok,
        )

    try:
        _chunks, plan, calculation, matched_count = fetch_context(test.question)
    except EmployeeClarificationRequired as exc:
        actual_ids = [item.employee_id for item in exc.resolution.candidates]
        outcome_ok = test.expected_clarification_outcome == exc.resolution.outcome
        ids_ok = (
            actual_ids == test.expected_clarification_ids
            if test.expected_clarification_ids
            else True
        )
        expected = test.expected_clarification_outcome is not None
        accepted = expected and outcome_ok and ids_ok
        return BehaviorEval(
            plan_ok=accepted,
            employee_ids_ok=accepted,
            matched_count_ok=accepted,
            calculation_ok=accepted,
            clarification_ok=accepted,
            answer_facts_ok=accepted,
            normalized_result_ok=accepted,
            expected_error_ok=test.expected_error is None,
            violation_codes_ok=not test.expected_violation_codes,
            answer_contract_ok=test.expected_answer_contract is None,
            unsupported_capabilities_ok=not test.expected_unsupported_capabilities,
        )
    except SemanticPlanValidationError as exc:
        actual_codes = sorted({item.code for item in exc.violations})
        actual_capabilities = sorted(
            item.target
            for item in exc.violations
            if item.code == "unsupported_capability"
        )
        codes_ok = actual_codes == sorted(test.expected_violation_codes)
        capabilities_ok = actual_capabilities == sorted(
            test.expected_unsupported_capabilities
        )
        expected_rejection = bool(
            test.expected_violation_codes or test.expected_unsupported_capabilities
        )
        return BehaviorEval(
            plan_ok=expected_rejection and codes_ok and capabilities_ok,
            employee_ids_ok=expected_rejection,
            matched_count_ok=expected_rejection,
            calculation_ok=expected_rejection,
            clarification_ok=expected_rejection,
            answer_facts_ok=expected_rejection,
            normalized_result_ok=expected_rejection,
            expected_error_ok=test.expected_error is None,
            violation_codes_ok=codes_ok,
            answer_contract_ok=test.expected_answer_contract is None,
            unsupported_capabilities_ok=capabilities_ok,
        )
    except Exception as exc:
        expected_error_ok = bool(
            test.expected_error
            and test.expected_exception_type == type(exc).__name__
            and test.expected_error.casefold() in str(exc).casefold()
        )
        return BehaviorEval(
            plan_ok=expected_error_ok,
            employee_ids_ok=expected_error_ok,
            matched_count_ok=expected_error_ok,
            calculation_ok=expected_error_ok,
            clarification_ok=expected_error_ok,
            answer_facts_ok=expected_error_ok,
            normalized_result_ok=expected_error_ok,
            expected_error_ok=expected_error_ok,
        )

    return _evaluate_successful_behavior(
        test,
        _chunks,
        plan,
        calculation,
        matched_count,
    )


def calculate_mrr(keyword: str, retrieved_docs: list) -> float:
    """Calculate reciprocal rank for a single keyword (case-insensitive)."""
    keyword_lower = keyword.lower()
    for rank, doc in enumerate(retrieved_docs, start=1):
        if keyword_lower in doc.page_content.lower():
            return 1.0 / rank
    return 0.0


def calculate_dcg(relevances: list[int], k: int) -> float:
    """Calculate Discounted Cumulative Gain."""
    dcg = 0.0
    for i in range(min(k, len(relevances))):
        dcg += relevances[i] / math.log2(i + 2)  # i+2 because rank starts at 1
    return dcg


def calculate_ndcg(keyword: str, retrieved_docs: list, k: int = 10) -> float:
    """Calculate nDCG for a single keyword (binary relevance, case-insensitive)."""
    keyword_lower = keyword.lower()

    # Binary relevance: 1 if keyword found, 0 otherwise
    relevances = [
        1 if keyword_lower in doc.page_content.lower() else 0
        for doc in retrieved_docs[:k]
    ]

    # DCG
    dcg = calculate_dcg(relevances, k)

    # Ideal DCG (best case: keyword in first position)
    ideal_relevances = sorted(relevances, reverse=True)
    idcg = calculate_dcg(ideal_relevances, k)

    return dcg / idcg if idcg > 0 else 0.0


def calculate_record_id_mrr(expected_ids: list[str], retrieved_docs: list) -> float:
    if not expected_ids:
        return 0.0
    expected = set(expected_ids)
    for rank, document in enumerate(retrieved_docs, start=1):
        if document.metadata.get("record_id") in expected:
            return 1.0 / rank
    return 0.0


def calculate_record_id_ndcg(
    expected_ids: list[str], retrieved_docs: list, k=6
) -> float:
    expected = set(expected_ids)
    relevances = [
        int(document.metadata.get("record_id") in expected)
        for document in retrieved_docs[:k]
    ]
    ideal = [1] * min(len(expected), k)
    denominator = calculate_dcg(ideal, k)
    return calculate_dcg(relevances, k) / denominator if denominator else 0.0


def evaluate_retrieval(test: TestQuestion, k: int = 10) -> RetrievalEval:
    """
    Evaluate retrieval performance for a test question.

    Args:
        test: TestQuestion object containing question and keywords
        k: Number of top documents to retrieve (default 10)

    Returns:
        RetrievalEval object with MRR, nDCG, and keyword coverage metrics
    """
    # Retrieve documents using shared answer module
    retrieved_docs = _retrieved_documents(fetch_context(test.question))

    if test.expected_record_ids:
        mrr = calculate_record_id_mrr(test.expected_record_ids, retrieved_docs)
        ndcg = calculate_record_id_ndcg(test.expected_record_ids, retrieved_docs, k)
        found = {document.metadata.get("record_id") for document in retrieved_docs[:k]}
        keywords_found = len(set(test.expected_record_ids) & found)
        total_keywords = len(test.expected_record_ids)
        return RetrievalEval(
            mrr=mrr,
            ndcg=ndcg,
            keywords_found=keywords_found,
            total_keywords=total_keywords,
            keyword_coverage=(keywords_found / total_keywords * 100),
        )

    # Calculate MRR (average across all keywords)
    mrr_scores = [calculate_mrr(keyword, retrieved_docs) for keyword in test.keywords]
    avg_mrr = sum(mrr_scores) / len(mrr_scores) if mrr_scores else 0.0

    # Calculate nDCG (average across all keywords)
    ndcg_scores = [
        calculate_ndcg(keyword, retrieved_docs, k) for keyword in test.keywords
    ]
    avg_ndcg = sum(ndcg_scores) / len(ndcg_scores) if ndcg_scores else 0.0

    # Calculate keyword coverage
    keywords_found = sum(1 for score in mrr_scores if score > 0)
    total_keywords = len(test.keywords)
    keyword_coverage = (
        (keywords_found / total_keywords * 100) if total_keywords > 0 else 0.0
    )

    return RetrievalEval(
        mrr=avg_mrr,
        ndcg=avg_ndcg,
        keywords_found=keywords_found,
        total_keywords=total_keywords,
        keyword_coverage=keyword_coverage,
    )


def evaluate_answer_execution(
    test: TestQuestion,
) -> tuple[AnswerEval, str, list, AnswerExecutionTrace]:
    """Judge an answer and retain non-sensitive structure from that exact run."""
    generated_answer, retrieved_docs, _state, context = (
        _answer_question_with_evaluation_trace(test.question)
    )
    trace = AnswerExecutionTrace(
        outcome="executed" if context is not None else "controlled_nonexecution",
        plan=context.plan if context is not None else None,
        calculation=context.aggregation if context is not None else None,
        matched_count=context.matched_count if context is not None else None,
    )

    # LLM judge prompt
    judge_messages = [
        {
            "role": "system",
            "content": "You are an expert evaluator assessing the quality of answers. Evaluate the generated answer by comparing it to the reference answer. Only give 5/5 scores for perfect answers.",
        },
        {
            "role": "user",
            "content": f"""Question:
{test.question}

Generated Answer:
{generated_answer}

Reference Answer:
{test.reference_answer}

Please evaluate the generated answer on three dimensions:
1. Accuracy: How factually correct is it compared to the reference answer? Only give 5/5 scores for perfect answers.
2. Completeness: How thoroughly does it address all aspects of the question, covering all the information from the reference answer?
3. Relevance: How well does it directly answer the specific question asked, giving no additional information?

Provide detailed feedback and scores from 1 (very poor) to 5 (ideal) for each dimension. If the answer is wrong, then the accuracy score must be 1.""",
        },
    ]

    # Call the LLM judge with structured output.
    judge_response = completion(
        model=MODEL, messages=judge_messages, response_format=AnswerEval
    )

    answer_eval = AnswerEval.model_validate_json(
        judge_response.choices[0].message.content
    )

    return answer_eval, generated_answer, retrieved_docs, trace


def evaluate_answer(test: TestQuestion) -> tuple[AnswerEval, str, list]:
    """Evaluate answer quality while preserving the historical public tuple."""
    result, generated_answer, retrieved_docs, _trace = evaluate_answer_execution(test)
    return result, generated_answer, retrieved_docs


def evaluate_all_retrieval():
    """Evaluate all retrieval tests."""
    tests = load_tests()
    total_tests = len(tests)
    for index, test in enumerate(tests):
        result = evaluate_retrieval(test)
        progress = (index + 1) / total_tests
        yield test, result, progress


def evaluate_all_answers():
    """Evaluate all answers to tests using batched async execution."""
    tests = load_tests()
    total_tests = len(tests)
    for index, test in enumerate(tests):
        result = evaluate_answer(test)[0]
        progress = (index + 1) / total_tests
        yield test, result, progress


def current_dataset_manifest():
    psycopg, dict_row = _import_psycopg()
    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as connection:
        rows = connection.execute(
            f"SELECT record_json FROM {POSTGRES_ATTENDANCE_TABLE} ORDER BY record_id"
        ).fetchall()
        summary = connection.execute(
            f"SELECT COUNT(DISTINCT employee_id) AS employees, "
            f"MIN(attendance_date) AS date_min, MAX(attendance_date) AS date_max "
            f"FROM {POSTGRES_ATTENDANCE_TABLE}"
        ).fetchone()
    canonical = "\n".join(
        json.dumps(row["record_json"], sort_keys=True, separators=(",", ":"))
        for row in rows
    )
    return {
        "record_count": len(rows),
        "employee_count": int(summary["employees"]),
        "date_min": summary["date_min"].isoformat(),
        "date_max": summary["date_max"].isoformat(),
        "fingerprint": hashlib.sha256(canonical.encode()).hexdigest(),
    }


def verify_dataset():
    if not DATASET_MANIFEST_PATH.is_file():
        raise FileNotFoundError(
            "The private dataset manifest is not available in this checkout. "
            "Provide week5/new_evaluation/dataset_manifest.json from an "
            "authorized local APDC evaluation environment."
        )
    expected = json.loads(DATASET_MANIFEST_PATH.read_text(encoding="utf-8"))
    actual = current_dataset_manifest()
    mismatches = {
        key: {"expected": expected.get(key), "actual": actual.get(key)}
        for key in actual
        if expected.get(key) != actual.get(key)
    }
    if mismatches:
        raise RuntimeError(
            "Evaluation dataset drift requires explicit review: "
            + json.dumps(mismatches, sort_keys=True)
        )
    return actual


def run_cli_evaluation(test_number: int):
    """Run evaluation for one corpus row."""
    # Load tests
    tests = load_tests()

    if test_number < 0 or test_number >= len(tests):
        print(f"Error: test_row_number must be between 0 and {len(tests) - 1}")
        sys.exit(1)

    # Get the test
    test = tests[test_number]

    # Print test info
    print(f"\n{'=' * 80}")
    print(f"Test #{test_number}")
    print(f"{'=' * 80}")
    print(f"Question: {test.question}")
    print(f"Keywords: {test.keywords}")
    print(f"Category: {test.category}")
    print(f"Reference Answer: {test.reference_answer}")

    # Retrieval Evaluation
    print(f"\n{'=' * 80}")
    print("Retrieval Evaluation")
    print(f"{'=' * 80}")

    retrieval_result = evaluate_retrieval(test)

    print(f"MRR: {retrieval_result.mrr:.4f}")
    print(f"nDCG: {retrieval_result.ndcg:.4f}")
    print(
        f"Keywords Found: {retrieval_result.keywords_found}/{retrieval_result.total_keywords}"
    )
    print(f"Keyword Coverage: {retrieval_result.keyword_coverage:.1f}%")

    # Answer Evaluation
    print(f"\n{'=' * 80}")
    print("Answer Evaluation")
    print(f"{'=' * 80}")

    answer_result, generated_answer, retrieved_docs = evaluate_answer(test)

    print(f"\nGenerated Answer:\n{generated_answer}")
    print(f"\nFeedback:\n{answer_result.feedback}")
    print("\nScores:")
    print(f"  Accuracy: {answer_result.accuracy:.2f}/5")
    print(f"  Completeness: {answer_result.completeness:.2f}/5")
    print(f"  Relevance: {answer_result.relevance:.2f}/5")
    print(f"\n{'=' * 80}\n")


def main(argv=None):
    """CLI for dataset verification, deterministic behavior, or one row."""
    parser = argparse.ArgumentParser()
    parser.add_argument("test_number", nargs="?", type=int)
    parser.add_argument("--verify-dataset", action="store_true")
    parser.add_argument("--behavior", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)
    if args.verify_dataset:
        print(json.dumps(verify_dataset(), indent=2))
        return 0
    if args.behavior:
        tests = load_tests() if args.all else load_tests()[:1]
        failures = []
        for index, test in enumerate(tests):
            result = evaluate_behavior(test)
            if not all(result.model_dump().values()):
                failures.append({"index": index, "result": result.model_dump()})
        print(json.dumps({"tests": len(tests), "failures": failures}, indent=2))
        return 1 if failures else 0
    if args.test_number is None:
        parser.error("provide a test number or an evaluation mode")
    run_cli_evaluation(args.test_number)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
