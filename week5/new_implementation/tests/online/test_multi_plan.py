"""Independent SQL evidence for a multi-part attendance request."""

from unittest.mock import patch

from week5.new_implementation.online.context import SharedModelContext
from week5.new_implementation.online.execution import (
    ExecutionCoverage, LOCAL_DEMO_ACCESS, SqlExecutionResult,
)
from week5.new_implementation.online.pipeline import (
    Answered, Failed, RuntimeDependencies, TurnRequest, run_turn,
)
from week5.new_implementation.online.planner import (
    ExecutedPlanStep, MultiSqlPlan, PlannerAnswer, ReplanRequest, ReviewedMultiAnswer,
    SqlPlanStep, answer_multi_result, request_sql,
)
from week5.new_implementation.online.provider import CallBudget
from week5.new_implementation.online.reference import (
    ReadyReference, ReferenceResponse, ScopeClause,
)
from week5.new_implementation.online.state import ConversationState, VerifiedTurn
from week5.new_implementation.tests.online.test_query import database_context


CLAUSES = (
    ScopeClause(request="Count Authorized attendance records.",
                current_question_basis="Count Authorized attendance records"),
    ScopeClause(request="Count all attendance records.",
                current_question_basis="count all attendance records"),
)
SQLS = (
    "SELECT COUNT(*) AS n FROM attendance_records WHERE status = 'Authorized'",
    "SELECT COUNT(*) AS n FROM attendance_records",
)


def _plan(sqls=SQLS):
    return MultiSqlPlan(steps=tuple(
        SqlPlanStep(scope_clause_index=i, subrequest=CLAUSES[i].request, sql=sql)
        for i, sql in enumerate(sqls)
    ))


def _dependencies(plan, executed):
    question = "Count Authorized attendance records and count all attendance records."
    return RuntimeDependencies(
        reference_writer=lambda *_args, **_kwargs: ReferenceResponse(decision=ReadyReference(
            rewritten_request=question, locale="en", request_relationship="new",
            subject_relationship="all_authorized", scope_clauses=CLAUSES,
        )),
        directory_loader=lambda **_kwargs: (),
        context_loader=lambda **_kwargs: database_context(),
        planner=lambda **_kwargs: plan,
        executor=lambda sql, **_kwargs: executed.append(sql) or SqlExecutionResult(
            rows=({"n": 2 if "Authorized" in sql else 5},),
            coverage=ExecutionCoverage(fetched_rows=1, result_limit=100,
                                       response_bytes=12),
        ),
    )


def test_pipeline_executes_and_publishes_separate_scoped_results():
    executed = []
    with patch("week5.new_implementation.online.pipeline.answer_multi_result",
               return_value="2 Authorized records; 5 records overall.") as answerer:
        outcome = run_turn(TurnRequest(
            question="Count Authorized attendance records and count all attendance records.",
            access_context=LOCAL_DEMO_ACCESS,
        ), dependencies=_dependencies(_plan(), executed))
    assert isinstance(outcome, Answered)
    assert executed == list(SQLS)
    assert len(outcome.evidence) == 2
    assert outcome.state.verified_turns[-1].executed_steps[1]["sql"] == SQLS[1]
    assert answerer.call_args.kwargs["steps"][0].result.rows[0]["n"] == 2
    assert answerer.call_args.kwargs["steps"][1].result.rows[0]["n"] == 5


def test_one_based_clause_indices_are_normalized():
    executed = []
    plan = MultiSqlPlan(steps=tuple(
        SqlPlanStep(scope_clause_index=i + 1, subrequest=CLAUSES[i].request,
                    sql=SQLS[i]) for i in range(2)
    ))
    with patch("week5.new_implementation.online.pipeline.answer_multi_result",
               return_value="Both counts.") as answerer:
        outcome = run_turn(TurnRequest(
            question="Count Authorized attendance records and count all attendance records.",
            access_context=LOCAL_DEMO_ACCESS,
        ), dependencies=_dependencies(plan, executed))
    assert isinstance(outcome, Answered)
    assert [step.scope_clause_index for step in answerer.call_args.kwargs["steps"]] == [0, 1]
    assert executed == list(SQLS)


def test_pipeline_rejects_missing_clause_before_execution():
    executed = []
    wrong = MultiSqlPlan(steps=(
        SqlPlanStep(scope_clause_index=0, subrequest=CLAUSES[0].request, sql=SQLS[0]),
        SqlPlanStep(scope_clause_index=0, subrequest=CLAUSES[1].request, sql=SQLS[1]),
    ))
    deps = _dependencies(wrong, executed)
    calls = []
    deps.planner = lambda **kwargs: calls.append(kwargs) or wrong
    outcome = run_turn(TurnRequest(
        question="Count Authorized attendance records and count all attendance records.",
        access_context=LOCAL_DEMO_ACCESS,
    ), dependencies=deps)
    assert isinstance(outcome, Failed)
    assert outcome.code == "incomplete_multi_plan"
    assert executed == []
    assert len(calls) == 2
    assert calls[1]["sql_execution_failure"]["error_type"] == "plan_structure"


def test_invalid_clause_indices_get_one_corrective_replan():
    executed = []
    wrong = MultiSqlPlan(steps=(
        SqlPlanStep(scope_clause_index=0, subrequest=CLAUSES[0].request, sql=SQLS[0]),
        SqlPlanStep(scope_clause_index=0, subrequest=CLAUSES[1].request, sql=SQLS[1]),
    ))
    deps = _dependencies(wrong, executed)
    calls = []
    deps.planner = lambda **kwargs: calls.append(kwargs) or (wrong if len(calls) == 1 else _plan())
    with patch("week5.new_implementation.online.pipeline.answer_multi_result",
               return_value="Both counts."):
        outcome = run_turn(TurnRequest(
            question="Count Authorized attendance records and count all attendance records.",
            access_context=LOCAL_DEMO_ACCESS,
        ), dependencies=deps)
    assert isinstance(outcome, Answered)
    assert len(calls) == 2
    assert calls[1]["sql_execution_failure"]["error_type"] == "plan_structure"
    assert executed == list(SQLS)


def test_request_sql_uses_structured_plan_for_independent_clauses():
    shared = SharedModelContext(
        current_question="Two counts", updated_request="Two counts",
        scope_provenance={"reference_scope_clauses": [c.model_dump() for c in CLAUSES]},
        database_context=database_context(),
    )
    with patch("week5.new_implementation.online.planner.call_structured",
               return_value=_plan()) as call:
        result = request_sql(shared_context=shared, model="test", budget=CallBudget(),
                             timeout=2, max_output_tokens=1000)
    assert isinstance(result, MultiSqlPlan)
    assert call.call_args.kwargs["response_model"] is MultiSqlPlan
    system = call.call_args.kwargs["system"]
    assert "database_context defines available fields and business meanings" in system
    assert "Return a structured plan" in system
    assert "read-only PostgreSQL" in system


def test_count_reconciliation_uses_single_sql_contract_even_with_two_clauses():
    shared = SharedModelContext(
        current_question="Explain the count gap", updated_request="Explain the count gap",
        count_reconciliation=True,
        scope_provenance={"reference_scope_clauses": [c.model_dump() for c in CLAUSES]},
        database_context=database_context(),
    )
    with patch("week5.new_implementation.online.planner.call_text",
               return_value=SQLS[1]) as call:
        result = request_sql(shared_context=shared, model="test", budget=CallBudget(),
                             timeout=2, max_output_tokens=1000)
    assert result == SQLS[1]
    assert "one executable PostgreSQL SELECT" in call.call_args.kwargs["system"]


def test_second_step_write_query_is_rejected_without_publication():
    executed = []
    bad = _plan((SQLS[0], "DELETE FROM attendance_records"))
    with patch("week5.new_implementation.online.pipeline.retry_plan_step",
               return_value="DELETE FROM attendance_records"):
        outcome = run_turn(TurnRequest(
            question="Count Authorized attendance records and count all attendance records.",
            access_context=LOCAL_DEMO_ACCESS,
        ), dependencies=_dependencies(bad, executed))
    assert isinstance(outcome, Failed)
    assert outcome.code == "multi_step_failed"
    assert executed == [SQLS[0]]
    assert not outcome.state.verified_turns


def test_global_date_is_enforced_when_reference_clauses_omit_it():
    executed = []
    deps = _dependencies(_plan(), executed)
    question = "On 2026-09-03, count Authorized attendance records and all attendance records."
    deps.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(decision=ReadyReference(
        rewritten_request=question, locale="en", request_relationship="new",
        subject_relationship="all_authorized", scope_clauses=CLAUSES,
    ))
    with patch("week5.new_implementation.online.pipeline.retry_plan_step",
               return_value=SQLS[0]):
        outcome = run_turn(TurnRequest(question=question, access_context=LOCAL_DEMO_ACCESS),
                           dependencies=deps)
    assert isinstance(outcome, Failed)
    assert outcome.code == "multi_step_failed"
    assert executed == []


def test_shared_date_is_enforced_when_only_first_clause_names_it():
    clauses = (
        ScopeClause(request="Count Authorized records on 2026-09-03.",
                    current_question_basis="Count Authorized records",
                    date_scope_relationship="shared"),
        ScopeClause(request="Count all records.",
                    current_question_basis="count all records",
                    date_scope_relationship="shared"),
    )
    sqls = (
        "SELECT COUNT(*) AS n FROM attendance_records "
        "WHERE status = 'Authorized' AND attendance_date = DATE '2026-09-03'",
        SQLS[1],
    )
    plan = MultiSqlPlan(steps=tuple(SqlPlanStep(
        scope_clause_index=i, subrequest=clauses[i].request, sql=sqls[i]
    ) for i in range(2)))
    executed = []
    deps = _dependencies(plan, executed)
    question = "On 2026-09-03, count Authorized records and all records."
    deps.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(decision=ReadyReference(
        rewritten_request=question, locale="en", request_relationship="new",
        subject_relationship="all_authorized", scope_clauses=clauses,
    ))
    with patch("week5.new_implementation.online.pipeline.retry_plan_step",
               return_value=SQLS[1]):
        outcome = run_turn(TurnRequest(question=question, access_context=LOCAL_DEMO_ACCESS),
                           dependencies=deps)
    assert isinstance(outcome, Failed)
    assert outcome.code == "multi_step_failed"
    assert executed == [sqls[0]]


def test_planner_subrequest_cannot_drop_clause_status():
    executed = []
    plan = MultiSqlPlan(steps=(
        SqlPlanStep(scope_clause_index=0, subrequest="Count records.", sql=SQLS[1]),
        SqlPlanStep(scope_clause_index=1, subrequest=CLAUSES[1].request, sql=SQLS[1]),
    ))
    with patch("week5.new_implementation.online.pipeline.retry_plan_step",
               return_value=SQLS[1]):
        outcome = run_turn(TurnRequest(
            question="Count Authorized attendance records and count all attendance records.",
            access_context=LOCAL_DEMO_ACCESS,
        ), dependencies=_dependencies(plan, executed))
    assert isinstance(outcome, Failed)
    assert outcome.code == "multi_step_failed"
    assert executed == []


def test_multi_turn_persists_distinct_clause_date_scopes():
    clauses = (
        ScopeClause(request="Count records on 2026-09-03.",
                    current_question_basis="Count records on 2026-09-03"),
        ScopeClause(request="Count all records across available dates.",
                    current_question_basis="across available dates",
                    date_scope_relationship="unbounded"),
    )
    sqls = (
        "SELECT COUNT(*) AS n FROM attendance_records "
        "WHERE attendance_date = DATE '2026-09-03'",
        SQLS[1],
    )
    plan = MultiSqlPlan(steps=tuple(SqlPlanStep(
        scope_clause_index=i, subrequest=clauses[i].request, sql=sqls[i]
    ) for i in range(2)))
    executed = []
    deps = _dependencies(plan, executed)
    question = "Count records on 2026-09-03 and separately count all records across available dates."
    deps.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(decision=ReadyReference(
        rewritten_request=question, locale="en", request_relationship="new",
        subject_relationship="all_authorized", scope_clauses=clauses,
    ))
    with patch("week5.new_implementation.online.pipeline.answer_multi_result",
               return_value="Both counts."):
        outcome = run_turn(TurnRequest(question=question, access_context=LOCAL_DEMO_ACCESS),
                           dependencies=deps)
    assert isinstance(outcome, Answered)
    steps = outcome.state.verified_turns[-1].executed_steps
    assert steps[0]["requested_date_scope"] == ["2026-09-03", "2026-09-03"]
    assert steps[1]["requested_date_scope"] is None
    assert outcome.state.verified_turns[-1].requested_date_scope is None


def test_review_replans_complete_multi_evidence_before_publication():
    executed = []
    deps = _dependencies(_plan(), executed)
    planner_calls = []
    deps.planner = lambda **kwargs: planner_calls.append(kwargs) or _plan()
    with patch("week5.new_implementation.online.pipeline.answer_multi_result",
               side_effect=[ReplanRequest(reason="Missing overall count"),
                            "2 Authorized records; 5 records overall."]):
        outcome = run_turn(TurnRequest(
            question="Count Authorized attendance records and count all attendance records.",
            access_context=LOCAL_DEMO_ACCESS,
        ), dependencies=deps)
    assert isinstance(outcome, Answered)
    assert len(planner_calls) == 2
    assert planner_calls[1]["sql_execution_failure"]["error_type"] == "answer_review_requery"
    assert executed == list(SQLS) * 2
    assert len(outcome.state.verified_turns) == 1


def test_follow_up_receives_prior_steps_without_a_misleading_single_sql():
    prior = VerifiedTurn(
        turn_id="prior", original_question="Two counts", rewritten_request="Two counts",
        answer="Two counts", locale="en", executed_sql=SQLS[0],
        executed_steps=tuple({"scope_clause_index": i, "subrequest": CLAUSES[i].request,
                              "sql": SQLS[i], "result": {"rows": [{"n": i + 1}]}}
                             for i in range(2)),
    )
    seen = []
    deps = RuntimeDependencies(
        reference_writer=lambda *_args, **_kwargs: ReferenceResponse(decision=ReadyReference(
            rewritten_request="Count by status now", locale="en",
            request_relationship="follow_up", subject_relationship="all_authorized",
        )),
        directory_loader=lambda **_kwargs: (),
        context_loader=lambda **_kwargs: database_context(),
        planner=lambda **kwargs: seen.append(kwargs["shared_context"]) or SQLS[1],
        executor=lambda *_args, **_kwargs: SqlExecutionResult(
            rows=({"n": 5},), coverage=ExecutionCoverage(
                fetched_rows=1, result_limit=100, response_bytes=12)),
        answerer=lambda **_kwargs: "Five records.",
    )
    outcome = run_turn(TurnRequest(
        question="How many in all?", state=ConversationState(verified_turns=(prior,)),
        access_context=LOCAL_DEMO_ACCESS,
    ), dependencies=deps)
    assert isinstance(outcome, Answered)
    assert "sql" not in seen[0].previous_verified_turn
    assert len(seen[0].previous_verified_turn["executed_steps"]) == 2
    assert "result" not in seen[0].previous_verified_turn["executed_steps"][0]
    assert "result" not in seen[0].scope_provenance["carried_filter_source"]["previous_executed_steps"][0]


def test_multi_answer_review_must_cover_every_step():
    shared = SharedModelContext(current_question="Two counts", updated_request="Two counts",
                                database_context=database_context())
    result = SqlExecutionResult(rows=({"n": 2},), coverage=ExecutionCoverage(
        fetched_rows=1, result_limit=100, response_bytes=12))
    steps = tuple(ExecutedPlanStep(scope_clause_index=i, subrequest=CLAUSES[i].request,
                                   sql=SQLS[i], result=result) for i in range(2))
    with patch("week5.new_implementation.online.planner.call_structured",
               side_effect=[PlannerAnswer(answer="Both counts"),
                            ReviewedMultiAnswer(answer="Both counts",
                                                covered_step_indices=(0,))]):
        outcome = answer_multi_result(shared_context=shared, steps=steps, employees=(),
                                      locale="en", model="test", budget=CallBudget(),
                                      timeout=2, max_output_tokens=1000)
    assert outcome.reason == "The final answer review did not verify every requested part"


def test_multi_answer_review_replans_an_extra_filter():
    shared = SharedModelContext(current_question="Two counts", updated_request="Two counts",
                                database_context=database_context())
    result = SqlExecutionResult(rows=({"n": 2},), coverage=ExecutionCoverage(
        fetched_rows=1, result_limit=100, response_bytes=12))
    steps = tuple(ExecutedPlanStep(scope_clause_index=i, subrequest=CLAUSES[i].request,
                                   sql=SQLS[i], result=result) for i in range(2))
    with patch("week5.new_implementation.online.planner.call_structured",
               side_effect=[PlannerAnswer(answer="Both counts"),
                            ReviewedMultiAnswer(answer="Both counts",
                                                covered_step_indices=(0, 1),
                                                scope_valid_step_indices=(0,))]):
        outcome = answer_multi_result(shared_context=shared, steps=steps, employees=(),
                                      locale="en", model="test", budget=CallBudget(),
                                      timeout=2, max_output_tokens=1000)
    assert "incorrect scope" in outcome.reason
