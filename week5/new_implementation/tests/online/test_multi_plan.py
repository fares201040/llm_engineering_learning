"""Independent SQL evidence for a multi-part attendance request."""

from contextlib import contextmanager
from unittest.mock import patch

from week5.new_implementation.online.context import SharedModelContext
from week5.new_implementation.online.execution import (
    ExecutionCoverage, LOCAL_DEMO_ACCESS, SqlExecutionResult,
)
from week5.new_implementation.online.pipeline import (
    Answered, Failed, RuntimeDependencies, TurnRequest,
    _explicit_date_scopes, run_turn,
)
from week5.new_implementation.online.planner import (
    ExecutedPlanStep, MultiSqlPlan, PlannerAnswer, ReplanRequest, ReviewedMultiAnswer,
    SqlPlanChoice, SqlPlanStep, answer_multi_result, request_sql,
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
SHARED_SQL = (
    "SELECT COUNT(*) FILTER (WHERE status = 'Authorized') AS authorized_count, "
    "COUNT(*) AS overall_count FROM attendance_records"
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


def test_multi_steps_share_one_snapshot_connection():
    executed = []
    deps = _dependencies(_plan(), executed)
    marker = object()
    openings = []
    connections = []

    @contextmanager
    def snapshot_factory(**kwargs):
        openings.append(kwargs)
        yield marker

    original_executor = deps.executor
    deps.snapshot_factory = snapshot_factory
    deps.executor = lambda sql, **kwargs: (
        connections.append(kwargs.get("connection"))
        or original_executor(sql, **kwargs)
    )
    with patch("week5.new_implementation.online.pipeline.answer_multi_result",
               return_value="Both counts."):
        outcome = run_turn(TurnRequest(
            question="Count Authorized attendance records and count all attendance records.",
            access_context=LOCAL_DEMO_ACCESS,
        ), dependencies=deps)
    assert isinstance(outcome, Answered)
    assert len(openings) == 1
    assert connections == [marker, marker]


def test_failed_step_is_replanned_after_snapshot_closes():
    bad_sql = "SELECT broken_column FROM attendance_records"
    plan = _plan((SQLS[0], bad_sql))
    executed = []
    deps = _dependencies(plan, executed)
    active = False
    openings = []

    @contextmanager
    def snapshot_factory(**_kwargs):
        nonlocal active
        assert not active
        active = True
        openings.append(object())
        try:
            yield openings[-1]
        finally:
            active = False

    def executor(sql, **_kwargs):
        executed.append(sql)
        if sql == bad_sql:
            raise ValueError("unknown column")
        return SqlExecutionResult(
            rows=({"n": 2},),
            coverage=ExecutionCoverage(fetched_rows=1, result_limit=100,
                                       response_bytes=12),
        )

    def corrected_step(**_kwargs):
        assert not active
        return SQLS[1]

    deps.snapshot_factory = snapshot_factory
    deps.executor = executor
    with patch("week5.new_implementation.online.pipeline.retry_plan_step",
               side_effect=corrected_step), patch(
                   "week5.new_implementation.online.pipeline.answer_multi_result",
                   return_value="Both counts."):
        outcome = run_turn(TurnRequest(
            question="Count Authorized attendance records and count all attendance records.",
            access_context=LOCAL_DEMO_ACCESS,
        ), dependencies=deps)
    assert isinstance(outcome, Answered)
    assert len(openings) == 2
    assert executed == [SQLS[0], bad_sql, SQLS[0], SQLS[1]]


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
               return_value=SqlPlanChoice(mode="multi", sql=None, steps=_plan().steps)) as call:
        result = request_sql(shared_context=shared, model="test", budget=CallBudget(),
                             timeout=2, max_output_tokens=1000)
    assert isinstance(result, MultiSqlPlan)
    assert call.call_args.kwargs["response_model"] is SqlPlanChoice
    system = call.call_args.kwargs["system"]
    assert "database_context defines available fields and business meanings" in system
    assert "Choose the SQL result shape" in system
    assert "read-only PostgreSQL" in system


def test_planner_can_choose_one_sql_for_related_clauses():
    shared = SharedModelContext(
        current_question="Related measures", updated_request="Related measures",
        scope_provenance={"reference_scope_clauses": [c.model_dump() for c in CLAUSES]},
        database_context=database_context(),
    )
    with patch("week5.new_implementation.online.planner.call_structured",
               return_value=SqlPlanChoice(mode="single", sql=SHARED_SQL, steps=())):
        result = request_sql(shared_context=shared, model="test", budget=CallBudget(),
                             timeout=2, max_output_tokens=1000)
    assert result == SHARED_SQL


def test_multi_review_can_switch_to_one_complete_query():
    executed = []
    deps = _dependencies(_plan(), executed)
    calls = []
    deps.planner = lambda **kwargs: calls.append(kwargs) or (
        _plan() if len(calls) == 1 else SHARED_SQL
    )
    deps.answerer = lambda **_kwargs: "2 Authorized records; 5 records overall."
    with patch("week5.new_implementation.online.pipeline.answer_multi_result",
               return_value=ReplanRequest(reason="Use one shared result")):
        outcome = run_turn(TurnRequest(
            question="Count Authorized attendance records and count all attendance records.",
            access_context=LOCAL_DEMO_ACCESS,
        ), dependencies=deps)
    assert isinstance(outcome, Answered)
    assert executed == [*SQLS, SHARED_SQL]
    assert len(outcome.state.verified_turns[-1].executed_steps) == 0


def test_single_choice_can_replan_when_one_clause_lacks_evidence():
    executed = []
    deps = _dependencies(_plan(), executed)
    planner_calls = []
    deps.planner = lambda **kwargs: planner_calls.append(kwargs) or (
        SQLS[1] if len(planner_calls) == 1 else SHARED_SQL
    )
    answer_calls = []

    def answerer(**_kwargs):
        answer_calls.append(None)
        if len(answer_calls) == 1:
            return ReplanRequest(reason="Authorized count is missing")
        return "2 Authorized records; 5 records overall."

    deps.answerer = answerer
    outcome = run_turn(TurnRequest(
        question="Count Authorized attendance records and count all attendance records.",
        access_context=LOCAL_DEMO_ACCESS,
    ), dependencies=deps)
    assert isinstance(outcome, Answered)
    assert executed == [SQLS[1], SHARED_SQL]
    assert planner_calls[1]["sql_execution_failure"]["error_type"] == "answer_review_requery"


def test_multi_date_clause_replans_when_steps_repeat_one_date():
    clauses = (
        ScopeClause(request="Compare counts on 2026-09-03 and 2026-09-04.",
                    current_question_basis="Compare counts on two dates"),
        ScopeClause(request="Count all records.",
                    current_question_basis="Count all records",
                    date_scope_relationship="unbounded"),
    )
    day_sql = (
        "SELECT COUNT(*) AS n FROM attendance_records "
        "WHERE attendance_date = DATE '2026-09-03'"
    )
    plan = MultiSqlPlan(steps=(
        SqlPlanStep(scope_clause_index=0, subrequest="Count 2026-09-03", sql=day_sql),
        SqlPlanStep(scope_clause_index=0, subrequest="Count 2026-09-03 again", sql=day_sql),
        SqlPlanStep(scope_clause_index=1, subrequest=clauses[1].request, sql=SQLS[1]),
    ))
    executed = []
    deps = _dependencies(plan, executed)
    question = "Compare counts on 2026-09-03 and 2026-09-04; count all records."
    deps.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
        decision=ReadyReference(
            rewritten_request=question, locale="en", request_relationship="new",
            subject_relationship="all_authorized", scope_clauses=clauses,
        )
    )
    calls = []
    deps.planner = lambda **kwargs: calls.append(kwargs) or plan
    with patch("week5.new_implementation.online.pipeline.answer_multi_result",
               return_value=ReplanRequest(reason="The second date lacks evidence")):
        outcome = run_turn(
            TurnRequest(question=question, access_context=LOCAL_DEMO_ACCESS),
            dependencies=deps,
        )
    assert isinstance(outcome, Failed)
    assert outcome.code == "replan_limit_exceeded"
    assert calls[1]["sql_execution_failure"]["error_type"] == "answer_review_requery"
    assert not outcome.state.verified_turns


def test_more_than_four_clauses_do_not_fail_before_planning():
    shared = SharedModelContext(
        current_question="Five independent measures", updated_request="Five independent measures",
        scope_provenance={"reference_scope_clauses": [
            {"request": f"Count group {i}", "current_question_basis": f"group {i}"}
            for i in range(5)
        ]},
        database_context=database_context(),
    )
    steps = tuple(SqlPlanStep(scope_clause_index=i, subrequest=f"Count group {i}",
                              sql=SQLS[1]) for i in range(5))
    with patch("week5.new_implementation.online.planner.call_structured",
               return_value=SqlPlanChoice(mode="multi", sql=None, steps=steps)):
        result = request_sql(shared_context=shared, model="test", budget=CallBudget(),
                             timeout=2, max_output_tokens=1000)
    assert isinstance(result, MultiSqlPlan)
    assert len(result.steps) == 5


def test_five_clause_plan_executes_and_publishes():
    clauses = tuple(ScopeClause(request=f"Count group {i}.",
                                current_question_basis=f"group {i}") for i in range(5))
    plan = MultiSqlPlan(steps=tuple(
        SqlPlanStep(scope_clause_index=i, subrequest=clause.request, sql=SQLS[1])
        for i, clause in enumerate(clauses)
    ))
    executed = []
    deps = _dependencies(plan, executed)
    deps.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
        decision=ReadyReference(
            rewritten_request="Count five groups", locale="en",
            request_relationship="new", subject_relationship="all_authorized",
            scope_clauses=clauses,
        )
    )
    with patch("week5.new_implementation.online.pipeline.answer_multi_result",
               return_value="Five counts."):
        outcome = run_turn(TurnRequest(
            question="Count five groups", access_context=LOCAL_DEMO_ACCESS,
        ), dependencies=deps)
    assert isinstance(outcome, Answered)
    assert len(executed) == 5
    assert len(outcome.state.verified_turns[-1].executed_steps) == 5


def test_multi_plan_caps_combined_rows_before_answering():
    clauses = tuple(ScopeClause(request=f"Count group {i}.",
                                current_question_basis=f"group {i}") for i in range(11))
    plan = MultiSqlPlan(steps=tuple(
        SqlPlanStep(scope_clause_index=i, subrequest=clause.request, sql=SQLS[1])
        for i, clause in enumerate(clauses)
    ))
    executed = []
    deps = _dependencies(plan, executed)
    deps.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
        decision=ReadyReference(
            rewritten_request="Count eleven groups", locale="en",
            request_relationship="new", subject_relationship="all_authorized",
            scope_clauses=clauses,
        )
    )
    deps.executor = lambda sql, **_kwargs: executed.append(sql) or SqlExecutionResult(
        rows=tuple({"n": i} for i in range(100)),
        coverage=ExecutionCoverage(fetched_rows=100, result_limit=100,
                                   response_bytes=1000),
    )
    outcome = run_turn(TurnRequest(
        question="Count eleven groups", access_context=LOCAL_DEMO_ACCESS,
    ), dependencies=deps)
    assert isinstance(outcome, Failed)
    assert outcome.code == "multi_result_bound"
    assert len(executed) == 11
    assert not outcome.state.verified_turns


def test_month_range_and_month_comparison_have_distinct_scopes():
    assert _explicit_date_scopes("March to May 2026") == {
        ("2026-03-01", "2026-05-31")
    }
    assert _explicit_date_scopes("Compare March and May 2026") == {
        ("2026-03-01", "2026-03-31"),
        ("2026-05-01", "2026-05-31"),
    }


def test_conditional_aggregate_can_cover_two_months_in_one_step():
    clauses = (
        ScopeClause(request="Compare March and May 2026 attendance counts.",
                    current_question_basis="Compare March and May"),
        ScopeClause(request="Count all records across all dates.",
                    current_question_basis="Count all records",
                    date_scope_relationship="unbounded"),
    )
    comparison_sql = (
        "SELECT COUNT(*) FILTER (WHERE attendance_date BETWEEN DATE '2026-03-01' "
        "AND DATE '2026-03-31') AS march_count, "
        "COUNT(*) FILTER (WHERE attendance_date BETWEEN DATE '2026-05-01' "
        "AND DATE '2026-05-31') AS may_count FROM attendance_records"
    )
    plan = MultiSqlPlan(steps=(
        SqlPlanStep(scope_clause_index=0, subrequest=clauses[0].request,
                    sql=comparison_sql),
        SqlPlanStep(scope_clause_index=1, subrequest=clauses[1].request,
                    sql=SQLS[1]),
    ))
    executed = []
    deps = _dependencies(plan, executed)
    question = "Compare March and May 2026 attendance counts; separately count all dates."
    deps.reference_writer = lambda *_args, **_kwargs: ReferenceResponse(
        decision=ReadyReference(
            rewritten_request=question, locale="en", request_relationship="new",
            subject_relationship="all_authorized", scope_clauses=clauses,
        )
    )
    with patch("week5.new_implementation.online.pipeline.answer_multi_result",
               return_value="Both month counts and the total."):
        outcome = run_turn(TurnRequest(
            question=question, access_context=LOCAL_DEMO_ACCESS,
        ), dependencies=deps)
    assert isinstance(outcome, Answered)
    assert executed == [comparison_sql, SQLS[1]]


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
    assert executed == [SQLS[0]] * 4
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
    assert executed == [sqls[0]] * 4


def test_review_can_reject_a_step_that_drops_clause_status():
    executed = []
    plan = MultiSqlPlan(steps=(
        SqlPlanStep(scope_clause_index=0, subrequest="Count records.", sql=SQLS[1]),
        SqlPlanStep(scope_clause_index=1, subrequest=CLAUSES[1].request, sql=SQLS[1]),
    ))
    with patch("week5.new_implementation.online.pipeline.answer_multi_result",
               return_value=ReplanRequest(reason="Status scope omitted")):
        outcome = run_turn(TurnRequest(
            question="Count Authorized attendance records and count all attendance records.",
            access_context=LOCAL_DEMO_ACCESS,
        ), dependencies=_dependencies(plan, executed))
    assert isinstance(outcome, Failed)
    assert outcome.code == "replan_limit_exceeded"
    assert executed == [SQLS[1], SQLS[1]] * 2


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
                                                covered_step_indices=(0,),
                                                covered_clause_indices=(0, 1))]) as call:
        outcome = answer_multi_result(shared_context=shared, steps=steps, employees=(),
                                      locale="en", model="test", budget=CallBudget(),
                                      timeout=2, max_output_tokens=1000)
    assert outcome.reason == "The final answer review did not verify every requested part"
    assert "A date literal alone is not evidence" in call.call_args.kwargs["system"]


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
                                                scope_valid_step_indices=(0,),
                                                covered_clause_indices=(0, 1))]):
        outcome = answer_multi_result(shared_context=shared, steps=steps, employees=(),
                                      locale="en", model="test", budget=CallBudget(),
                                      timeout=2, max_output_tokens=1000)
    assert "incorrect scope" in outcome.reason


def test_multi_answer_review_must_verify_every_clause():
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
                                                scope_valid_step_indices=(0, 1),
                                                covered_clause_indices=(0,))]):
        outcome = answer_multi_result(shared_context=shared, steps=steps, employees=(),
                                      locale="en", model="test", budget=CallBudget(),
                                      timeout=2, max_output_tokens=1000)
    assert "every requested clause" in outcome.reason
