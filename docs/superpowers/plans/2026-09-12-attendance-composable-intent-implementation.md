# APDC Composable Attendance Intent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make positive, negative, absent, scheduled, row-count, and partial-period attendance questions compile into correct deterministic calculations without sentence-specific answer functions.

**Architecture:** Keep the existing typed `QueryPlan`, validation, employee resolution, and exact backends, but separate the counted measure from declarative business predicates. Compile both registries before retrieval, reject contradictory polarity, attach authoritative data coverage to deterministic results, and migrate clarification/evaluation code away from the stale monolithic calculation catalog.

**Tech Stack:** Python 3.12, Pydantic, unittest, PostgreSQL/psycopg, Chroma, LiteLLM structured output, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-12-attendance-composable-intent-design.md`

## Global Constraints

- PostgreSQL remains authoritative for exact facts when enabled.
- The LLM interprets language, but code owns executable meanings and safety validation.
- Do not add rules for individual employees or functions for individual question phrasings.
- Preserve the public answer and retrieval return shapes.
- Preserve existing employee, date, authorization, clarification, retrieval, and access-control behavior.
- Never silently answer a positive calculation when the question contains an unresolved negative or zero-valued condition.
- Remove only superseded APDC intent code, tests, imports, and documentation. Preserve unrelated dirty-worktree content and generated source data.
- Every production behavior change starts with a failing regression test.
- The user requested that Git author configuration and commits be left untouched; use test/review checkpoints instead of commits.

---

### Task 1: Declarative Measures and Business Predicates

**Files:**
- Modify: `week5/new_implementation/attendance_schema.py:27-68`
- Modify: `week5/new_implementation/attendance_schema.py:378-467`
- Test: `week5/new_implementation/test_attendance_schema.py`

**Interfaces:**
- Produces: `MeasureName`, `BusinessPredicateName`, and `InterpretationName` literal aliases.
- Produces: immutable `MEASURE_DEFINITIONS`, `BUSINESS_PREDICATE_DEFINITIONS`, and `INTERPRETATION_PRESETS` registries.
- Produces: `compile_business_intent(plan: QueryPlan) -> QueryPlan`.
- Changes: `QueryPlan` gains `measure`, `business_predicates`, and `interpretation_candidates`; later tasks remove `calculation` and `calculation_candidates` callers.

- [ ] **Step 1: Add failing measure and predicate compilation tests**

Rename the stale `CanonicalCalculationTests` class to
`ComposableIntentTests` and add these cases to it.

```python
def test_scheduled_non_attendance_compiles_compositionally():
    plan = schema.QueryPlan(
        mode="exact",
        search_query="not attended",
        measure="distinct_dates",
        business_predicates=["scheduled_working_day", "not_worked"],
    )

    compiled = schema.compile_business_intent(plan)

    assert compiled.aggregation == "distinct_count"
    assert compiled.aggregation_field == "Date"
    assert {tuple(item.model_dump().values()) for item in compiled.filters} >= {
        ("Day_Type", "eq", "Working Day"),
        ("Total_Worked_Hrs", "lte", 0.0),
    }
```

Add parallel tests for `worked`, `absent`, `scheduled_working_day`,
`authorized`, `attendance_records`, and `employees`. Add one test proving
`worked` and `not_worked` are rejected as incompatible.

- [ ] **Step 2: Run the schema tests and confirm the new API is missing**

Run:

```powershell
& '.venv\Scripts\python.exe' -m unittest `
  week5.new_implementation.test_attendance_schema.ComposableIntentTests -v
```

Expected: failure because the new measure/predicate fields and compiler do not
exist.

- [ ] **Step 3: Define the declarative registries and compiler**

Implement these shapes in `attendance_schema.py`:

```python
MeasureName = Literal["distinct_dates", "attendance_records", "employees"]
BusinessPredicateName = Literal[
    "scheduled_working_day",
    "worked",
    "not_worked",
    "absent",
    "authorized",
]
InterpretationName = Literal[
    "worked_days",
    "scheduled_working_days",
    "scheduled_non_attended_days",
    "absent_days",
    "attendance_records",
    "authorized_records",
    "employees",
]

MEASURE_DEFINITIONS = MappingProxyType({
    "distinct_dates": MeasureDefinition("distinct_count", "Date"),
    "attendance_records": MeasureDefinition("count", None),
    "employees": MeasureDefinition("distinct_count", "Employee_ID"),
})

BUSINESS_PREDICATE_DEFINITIONS = MappingProxyType({
    "scheduled_working_day": PredicateDefinition(
        "Scheduled working dates, not proof of work.",
        (RequiredFilter("Day_Type", "eq", "Working Day"),),
    ),
    "worked": PredicateDefinition(
        "Dates with positive actual worked hours.",
        (RequiredFilter("Total_Worked_Hrs", "gt", 0.0),),
    ),
    "not_worked": PredicateDefinition(
        "Dates without positive actual worked hours.",
        (RequiredFilter("Total_Worked_Hrs", "lte", 0.0),),
    ),
    "absent": PredicateDefinition(
        "Dates carrying the explicit Absent exception.",
        (RequiredFilter("Exception", "eq", "Absent"),),
    ),
    "authorized": PredicateDefinition(
        "Rows with workflow Status Authorized.",
        (RequiredFilter("Status", "eq", "Authorized"),),
    ),
})
```

`compile_business_intent()` copies the plan, applies the selected measure,
rejects incompatible predicates, rejects conflicting filters on predicate-owned
fields, and appends each predicate's required filters. It must preserve filters
on unrelated fields.

- [ ] **Step 4: Run the focused schema tests**

Run the command from Step 2. Expected: all canonical intent tests pass.

- [ ] **Step 5: Refactor the schema while green**

Remove duplicated descriptions and keep each business meaning in exactly one
registry. Do not remove the legacy fields until Task 3 has migrated all
callers.

---

### Task 2: Planner Output and Polarity Safety

**Files:**
- Modify: `week5/new_implementation/answer.py:560-656`
- Modify: `week5/new_implementation/answer.py:1490-1810`
- Modify: `week5/new_implementation/attendance_schema.py`
- Test: `week5/new_implementation/test_answer.py:92-390`
- Test: `week5/new_implementation/test_answer.py:1410-1840`

**Interfaces:**
- Consumes: `MEASURE_DEFINITIONS`, `BUSINESS_PREDICATE_DEFINITIONS`, and `compile_business_intent()` from Task 1.
- Produces: `_explicit_attendance_contract(question: str) -> set[BusinessPredicateName]` for high-confidence declarative constraints.
- Produces: `_validate_attendance_polarity(question: str, plan: QueryPlan) -> None`.
- Changes: `plan_query()` asks for a measure and business predicates independently.

- [ ] **Step 1: Add failing planner and normalization regressions**

Add table-driven mocked-provider tests for these normalized expectations:

```python
cases = {
    "How many days did E00001 attend?":
        ("distinct_dates", {"worked"}),
    "How many days did E00001 not attend?":
        ("distinct_dates", {"scheduled_working_day", "not_worked"}),
    "How many days was E00001 absent?":
        ("distinct_dates", {"absent"}),
    "How many scheduled working days did E00001 not work?":
        ("distinct_dates", {"scheduled_working_day", "not_worked"}),
    "How many dates had zero worked hours?":
        ("distinct_dates", {"not_worked"}),
    "How many attendance records did A11017 have?":
        ("attendance_records", set()),
}
```

Add a test where mocked planner output selects `worked` for a negated question.
Assert `PlanValidationError` occurs before employee directory, PostgreSQL,
Chroma, aggregation, reranking, or final-answer calls.

- [ ] **Step 2: Run the new answer tests and confirm incorrect positive compilation**

Run:

```powershell
& '.venv\Scripts\python.exe' -m unittest `
  week5.new_implementation.test_answer.QueryPlannerSchemaTests `
  week5.new_implementation.test_answer.PlanNormalizationTests `
  week5.new_implementation.test_answer.ExecutablePlanSafetyTests -v
```

Expected: the negative cases fail because `worked_days` is still selected and
compiled.

- [ ] **Step 3: Expose measures and predicates in the planner prompt**

Replace `_calculation_definition_context_text()` with one renderer for measure
definitions and one for predicate definitions. Replace the direct
`attended -> worked_days` instruction with examples showing orthogonal output:

```text
"worked days" -> measure=distinct_dates, predicates=[worked]
"did not attend" -> measure=distinct_dates,
                    predicates=[scheduled_working_day, not_worked]
"absent days" -> measure=distinct_dates, predicates=[absent]
"attendance records" -> measure=attendance_records, predicates=[]
```

Keep numeric sum/average/min/max in the existing aggregation fields when no
named measure applies.

- [ ] **Step 4: Add the declarative explicit-contract and polarity gate**

Represent high-confidence aliases beside their business definitions, not in
employee-specific code. The contract must distinguish:

```python
"did not attend" -> {"scheduled_working_day", "not_worked"}
"not present" -> {"scheduled_working_day", "not_worked"}
"zero worked hours" -> {"not_worked"}
"absent" -> {"absent"}
```

Merge required contract predicates only when they are unambiguous. If planner
output contains `worked` while the explicit contract requires `not_worked`,
raise `PlanValidationError` rather than replacing the plan silently. This gate
must run before backend selection.

- [ ] **Step 5: Make filter conflicts fail closed**

Before appending predicate filters, compare constraints on the same field.
Allow identical constraints and independent fields. Reject examples such as:

```text
Total_Worked_Hrs > 0 AND Total_Worked_Hrs <= 0
Exception = Absent AND business predicate worked
```

The second contradiction follows from the attendance contract even though it
uses different physical fields.

- [ ] **Step 6: Run focused tests and the prior planner/normalization tests**

Run:

```powershell
& '.venv\Scripts\python.exe' -m unittest `
  week5.new_implementation.test_answer.QueryPlannerSchemaTests `
  week5.new_implementation.test_answer.DateRangeResolutionTests `
  week5.new_implementation.test_answer.EmployeeResolutionTests -v
```

Expected: all new negative cases and existing positive/date/employee cases
pass.

---

### Task 3: Clarification Migration and Stale Intent Removal

**Files:**
- Modify: `week5/new_implementation/attendance_schema.py`
- Modify: `week5/new_implementation/answer.py:203-236`
- Modify: `week5/new_implementation/answer.py:3062-3323`
- Test: `week5/new_implementation/test_attendance_schema.py`
- Test: `week5/new_implementation/test_answer.py:1280-1840`
- Test: `week5/test_new_app.py`

**Interfaces:**
- Consumes: `INTERPRETATION_PRESETS` from Task 1.
- Produces: `ConversationState.pending_interpretations: list[InterpretationName]`.
- Produces: `InterpretationClarificationRequired` and `_select_pending_interpretation()`.
- Removes: `CalculationName`, `CALCULATION_DEFINITIONS`, `compile_calculation_semantics()`, `apply_calculation_semantics()`, `pending_calculations`, and their stale prompt/format helpers.

- [ ] **Step 1: Add failing clarification migration tests**

Test an ambiguous `days` request that offers at least two interpretation
presets. Select `scheduled_non_attended_days` by number and assert the resumed
plan contains:

```python
assert resumed.measure == "distinct_dates"
assert set(resumed.business_predicates) == {
    "scheduled_working_day",
    "not_worked",
}
assert resumed_state.pending_interpretations == []
assert resumed_state.pending_plan is None
assert resumed_state.pending_question is None
```

Retain the chained interpretation-to-employee clarification test and assert it
executes the original question after both selections.

- [ ] **Step 2: Run clarification and app-state tests to verify failure**

Run:

```powershell
& '.venv\Scripts\python.exe' -m unittest `
  week5.new_implementation.test_answer.ClarificationStateTests `
  week5.test_new_app -v
```

Expected: failure because state and exception classes still use calculation
names.

- [ ] **Step 3: Migrate state and clarification rendering**

Render choices from `INTERPRETATION_PRESETS`, including the measure,
predicates, and business description. Selecting a preset copies its measure
and predicates into the saved plan and resumes without replanning. Preserve
trusted selected employees and all pending-state clearing behavior.

- [ ] **Step 4: Remove the superseded monolithic API**

Use:

```powershell
rg -n "CalculationName|CALCULATION_DEFINITIONS|compile_calculation_semantics|apply_calculation_semantics|pending_calculations" `
  week5/new_implementation week5/new_evaluation week5/new_app.py week5/test_new_app.py
```

Migrate every real caller, then delete the definitions and compatibility
wrapper. Expected after cleanup: no production match and no test relying on
the old representation.

- [ ] **Step 5: Run schema, answer, and app tests**

Run:

```powershell
& '.venv\Scripts\python.exe' -m unittest discover `
  -s week5/new_implementation -p 'test_*.py'
& '.venv\Scripts\python.exe' -m unittest week5.test_new_app
```

Expected: all tests pass before coverage behavior is added.

---

### Task 4: Authoritative Partial-Period Coverage

**Files:**
- Modify: `week5/new_implementation/attendance_schema.py`
- Modify: `week5/new_implementation/answer.py:1975-2200`
- Modify: `week5/new_implementation/answer.py:2459-2690`
- Test: `week5/new_implementation/test_answer.py:1830-2210`

**Interfaces:**
- Produces: `CoverageWindow(date_min: date, date_max: date)`.
- Produces: `fetch_postgres_coverage(connection=None) -> CoverageWindow | None`.
- Produces: `requested_date_window(plan: QueryPlan) -> CoverageWindow | None`.
- Produces: `attach_coverage_metadata(plan, aggregation, available) -> dict`.
- Changes: deterministic aggregation dictionaries may include a `coverage`
  object; public tuple shapes do not change.

- [ ] **Step 1: Add failing coverage tests**

Create `CoverageMetadataTests` in `test_answer.py` for these cases.

Mock PostgreSQL coverage as 2026-09-01 through 2026-09-07 and a requested
range through 2026-09-30. Assert aggregation metadata contains:

```python
{
    "available_start": "2026-09-01",
    "available_end": "2026-09-07",
    "requested_start": "2026-09-01",
    "requested_end": "2026-09-30",
    "complete": False,
}
```

Assert `_format_aggregation_answer()` includes both the deterministic count
and `available attendance data covers September 1-7, 2026, not the full requested period`.
Add a complete-range case proving no warning is appended.

- [ ] **Step 2: Run the coverage tests and verify missing metadata**

Run:

```powershell
& '.venv\Scripts\python.exe' -m unittest `
  week5.new_implementation.test_answer.CoverageMetadataTests -v
```

Expected: failure because aggregation results do not yet carry coverage.

- [ ] **Step 3: Query coverage in the same PostgreSQL snapshot**

Execute this parameter-free query through the existing repeatable-read
connection:

```sql
SELECT MIN(attendance_date) AS date_min,
       MAX(attendance_date) AS date_max
FROM attendance_records
```

Attach coverage after calculation but before returning from
`execute_exact_postgres()`. Do not change the aggregation's numeric value.

- [ ] **Step 4: Implement fallback coverage**

For Chroma exact fallback, derive the minimum and maximum `Date` from trusted
attendance-record metadata without using semantic similarity. Return `None`
when no trustworthy coverage is available; never invent a complete range.

- [ ] **Step 5: Add intent-aware deterministic wording**

Use measure and predicates to label scalar date results:

```text
[worked] -> worked days
[scheduled_working_day] -> scheduled working days
[scheduled_working_day, not_worked] -> scheduled working days not attended
[absent] -> recorded absent days
```

Append the coverage warning after this sentence. Keep record, employee,
percentage, grouped, and numeric formatting behavior unchanged.

- [ ] **Step 6: Run PostgreSQL, Chroma, and formatting tests**

Run:

```powershell
& '.venv\Scripts\python.exe' -m unittest `
  week5.new_implementation.test_answer.CoverageMetadataTests `
  week5.new_implementation.test_answer.DeterministicAggregationAnswerTests `
  week5.new_implementation.test_answer.PostgresResultParityTests `
  week5.new_implementation.test_answer.PostgresResultTests -v
```

Expected: all coverage, backend parity, and prior formatting tests pass.

---

### Task 5: Source-Verified Evaluation Cases and Documentation

**Files:**
- Modify: `week5/new_evaluation/tests.jsonl`
- Modify: `week5/new_evaluation/test.py`
- Modify: `week5/new_evaluation/eval.py`
- Modify: `week5/new_evaluation/test_eval.py`
- Modify: `week5/new_evaluation/test_benchmark.py`
- Modify: `week5/new_implementation/APDC_Attendance_Knowledge_Base_Improvements_1_to_21_Complete_Documentation.md`
- Modify: `week5/new_implementation/APDC_Attendance_Remaining_Improvements_16_to_35.md`
- Modify: `week5/new_implementation/APDC_Attendance_Remaining_Improvements_Implementation.md`

**Interfaces:**
- Changes: evaluation expectations use `measure` and
  `business_predicates` instead of `calculation`.
- Produces: permanent synthetic-fixture positive, negative, absence, schedule,
  and coverage cases.

- [ ] **Step 1: Add failing evaluation-loader and behavior assertions**

Add source-verified cases for worked dates, scheduled working dates, scheduled
non-attended dates, explicit absence, zero-hour dates, attendance records, and
partial coverage. Keep employee-linked expected values only in the ignored
private evaluation corpus.

Update behavior evaluation to assert the complete predicate set and coverage
metadata rather than checking only aggregation/value.

- [ ] **Step 2: Run evaluation unit tests and verify the old schema fails**

Run:

```powershell
& '.venv\Scripts\python.exe' -m unittest `
  week5.new_evaluation.test_eval `
  week5.new_evaluation.test_benchmark -v
```

Expected: failures until evaluator models and assertions use the composable
representation.

- [ ] **Step 3: Migrate active evaluation expectations**

Replace active `expected_plan.calculation` checks with `measure` and exact
predicate-set checks. Remove compatibility branches that recognize only the
deleted calculation catalog. Preserve numeric, record-ID, grouped-value,
clarification, and matched-count assertions.

- [ ] **Step 4: Update APDC documentation**

Document the business distinction between worked, non-worked, scheduled
non-attendance, explicit absence, off-days, leave, and record count. Record the
partial source range and deterministic warning behavior. Remove statements
that describe the deleted calculation compiler as current architecture.

- [ ] **Step 5: Run dataset verification and evaluation tests**

Run:

```powershell
& '.venv\Scripts\python.exe' -m week5.new_evaluation.eval --verify-dataset
& '.venv\Scripts\python.exe' -m unittest `
  week5.new_evaluation.test_eval `
  week5.new_evaluation.test_benchmark -v
```

Expected dataset truth remains 3,964 records, 568 employees, and coverage
2026-09-01 through 2026-09-07; every evaluation unit test passes.

---

### Task 6: Full Regression, Live Acceptance, and Final Cleanup

**Files:**
- Verify: `week5/new_implementation/*`
- Verify: `week5/new_evaluation/*`
- Verify: `week5/new_app.py`
- Verify: `week5/test_new_app.py`
- Verify: `docs/superpowers/specs/2026-09-12-attendance-composable-intent-design.md`
- Verify: `docs/superpowers/plans/2026-09-12-attendance-composable-intent-implementation.md`

**Interfaces:**
- Consumes: all preceding tasks.
- Produces: verified implementation with no stale monolithic calculation code
  and no unrelated file mutation.

- [ ] **Step 1: Run complete local tests**

```powershell
& '.venv\Scripts\python.exe' -m unittest discover `
  -s week5/new_implementation -p 'test_*.py'
& '.venv\Scripts\python.exe' -m unittest `
  week5.test_new_app `
  week5.new_evaluation.test_eval `
  week5.new_evaluation.test_benchmark
```

Expected: all implementation, app, evaluation, and benchmark tests pass.

- [ ] **Step 2: Run static verification**

```powershell
& '.venv\Scripts\ruff.exe' check `
  week5/new_implementation week5/new_evaluation `
  week5/new_app.py week5/test_new_app.py
& '.venv\Scripts\ruff.exe' format --check `
  week5/new_implementation week5/new_evaluation `
  week5/new_app.py week5/test_new_app.py
& '.venv\Scripts\python.exe' -m compileall -q `
  week5/new_implementation week5/new_evaluation week5/new_app.py
git diff --check
```

Expected: Ruff and compilation succeed. `git diff --check` reports no new
whitespace errors; pre-existing line-ending warnings may remain.

- [ ] **Step 3: Verify direct PostgreSQL truth**

Run parameterized read-only queries for A11017 and confirm 7 records, 5
scheduled working dates, 4 worked dates, 1 scheduled non-worked date, 1
explicit Absent date, and coverage through September 7.

- [ ] **Step 4: Run live planner and end-to-end controls sequentially**

Verify normalized plans, calculations, and final answers for:

```text
How many days did synthetic employee E00001 attend during a sample month?
How many days did synthetic employee E00001 not attend during that month?
How many days was synthetic employee E00001 absent during that month?
How many scheduled working days did synthetic employee E00001 not work?
How many days did synthetic employee E00001 have zero worked hours?
How many attendance records does synthetic employee E00001 have?
```

Validate each answer against the authorized local expectations and require a
coverage caveat whenever the requested period extends beyond loaded data.

- [ ] **Step 5: Prove fail-closed behavior**

Inject contradictory planner proposals and assert zero calls to retrieval,
aggregation, reranking, and final-answer generation. Verify clarification
state remains resumable and isolated between app sessions.

- [ ] **Step 6: Audit stale and unrelated changes**

Run:

```powershell
rg -n "CalculationName|CALCULATION_DEFINITIONS|compile_calculation_semantics|apply_calculation_semantics|pending_calculations" `
  week5/new_implementation week5/new_evaluation week5/new_app.py week5/test_new_app.py
git status --short
git diff --stat
```

Expected: no stale production API matches, no temporary diagnostics, and only
the explicitly scoped APDC files differ from the pre-task status. Do not stage,
commit, reset, delete, or reformat unrelated files.

- [ ] **Step 7: Request independent review and address only verified findings**

Provide the reviewer the spec, plan, relevant diff, focused tests, full test
results, direct SQL results, and live query matrix. Resolve every critical or
important finding with a new failing test before changing production code,
then repeat Steps 1-6.
