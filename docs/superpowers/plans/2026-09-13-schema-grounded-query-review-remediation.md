# Schema-Grounded Query Review Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore and extend attendance question coverage through one registry-grounded, fail-closed query architecture so equivalent language produces equivalent executable plans and answers.

**Architecture:** Deterministic semantic resolution produces typed facts for every executable choice. An untrusted provider proposal is normalized against those facts, compiled through invariants into an `ExecutableQueryPlan`, and only then reaches parameterized retrieval. Answer shape and wording are derived from the verified plan and result rather than inferred again from the question.

**Tech Stack:** Python 3.11+, Pydantic 2, LiteLLM structured output, psycopg, Chroma, unittest, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-13-schema-grounded-query-compilation-design.md`

## Global Constraints

- Do not add or execute raw LLM-generated SQL, including an `expected_sql` or `custom_sql` escape hatch.
- Only `ExecutableQueryPlan` may cross the retrieval-orchestration gate. Low-level helpers may receive derived filters or compiled SQL only from that gated path.
- Every executable filter, measure, predicate, grouping, order, and limit must have verified provenance.
- Every strong semantic fact found in the question or trusted state must be represented or cause clarification/rejection.
- Keep one source of truth for field metadata, aliases, values, concepts, measures, predicates, operators, and SQL expressions.
- Keep prompt instructions generic; do not add question-specific rules, employee mappings, silent fallbacks, or compatibility workarounds.
- Preserve access checks, parameter binding, read-only transactions, employee revalidation, clarification flows, Chroma fallback, and the public four-item `fetch_context()` result.
- Unsupported language must be rejected before retrieval rather than partially executed.
- Add a focused failing regression before each production-code fix.
- Do not expose private attendance values in logs, reports, commits, or review prompts.

---

### Task 1: Make semantic phrases compositional and registry-driven

**Files:**
- Modify: `week5/new_implementation/attendance_schema.py`
- Modify: `week5/new_implementation/semantic_resolution.py`
- Modify: `week5/new_implementation/test_semantic_resolution.py`
- Modify: `week5/new_implementation/test_semantic_matrix.py`

**Interfaces:**
- Consumes: `FIELD_DEFINITIONS`, `VALUE_CONCEPT_DEFINITIONS`, `MEASURE_DEFINITIONS`, `BUSINESS_PREDICATE_DEFINITIONS`.
- Produces: `detect_semantic_facts(question: str, context: ResolutionContext) -> tuple[SemanticFact, ...]` with complete, non-conflicting facts.
- Preserves: `SemanticFact` as the single provenance carrier consumed by `compile_proposal()`.

- [ ] **Step 1: Add failing regressions for worked, scheduled, negated, and exclusion variants**

Add table-driven tests asserting canonical facts for `work`, `worked`, `attend`, `attended`, `positive worked hours`, `zero worked hours`, `did not attend`, `scheduled working days`, and `exclude off days`. Assert that supported negations never emit the contradictory positive predicate.

```python
def test_worked_language_emits_positive_work_predicate(self):
    facts = detect_semantic_facts(
        "How many days did employee A10017 work?",
        ResolutionContext(catalog={}),
    )
    self.assertIn("worked", {fact.concept_name for fact in facts})

def test_not_attended_is_compositional_without_positive_collision(self):
    facts = detect_semantic_facts(
        "How many scheduled days did A10017 not attend?",
        ResolutionContext(catalog={}),
    )
    predicates = {fact.concept_name for fact in facts if fact.kind == "predicate"}
    self.assertEqual(predicates, {"scheduled_working_day", "not_worked"})
```

- [ ] **Step 2: Run the focused tests and record the expected missing/contradictory facts**

Run: `python -m unittest week5.new_implementation.test_semantic_resolution week5.new_implementation.test_semantic_matrix -v`

- [ ] **Step 3: Separate field aliases from value and business-concept phrases**

Keep raw column naming in each `FieldDefinition`. Put business meanings and supported complements in the appropriate value-concept or predicate definition. Add registry metadata needed for phrase priority and intentional composed meanings instead of branching on complete questions.

- [ ] **Step 4: Implement one longest-supported-match policy**

Resolve normalized phrases by semantic span and role, suppress only a shorter conflicting meaning, and retain intentional compatible facts. Ensure negative phrases are processed before positive substrings.

- [ ] **Step 5: Expand the generated matrix**

Generate positive cases for every field alias, closed value, catalog alias, value concept, measure, predicate, and retrieval intent. Generate negative mutation and collision cases for unsupported operators, malformed values, and overlapping phrases.

- [ ] **Step 6: Run focused suites and commit**

Run: `python -m unittest week5.new_implementation.test_attendance_schema week5.new_implementation.test_semantic_resolution week5.new_implementation.test_semantic_matrix -v`

Commit: `fix: make attendance semantics compositional`

---

### Task 2: Ground entities and temporal constraints before provider planning

**Files:**
- Modify: `week5/new_implementation/semantic_resolution.py`
- Modify: `week5/new_implementation/answer.py`
- Modify: `week5/new_implementation/test_semantic_resolution.py`
- Modify: `week5/new_implementation/test_answer.py`

**Interfaces:**
- Produces: generic entity-reference facts for identifier tokens and possessive/name spans.
- Produces: canonical date filter facts with `eq`, `lt`, `lte`, `gt`, or `gte` operators.
- Consumes: authoritative employee directory resolution and `ResolutionContext.employees`.
- Preserves: trusted employee choices with `origin="trusted_state"`.

- [ ] **Step 1: Add failing entity and date regressions**

Cover valid IDs, malformed ID-like tokens, full-name possessives, ISO dates, month-name dates, before/after comparisons, and ambiguous slash dates. Assert malformed or ambiguous literals reject before retrieval.

```python
def test_named_employee_reference_survives_authorized_count_planning(self):
    context = ResolutionContext(
        catalog={},
        employees=(EmployeeReference(employee_id="A10017", name="Selected Employee"),),
    )
    facts = detect_semantic_facts(
        "Count Selected Employee's Authorized records.", context
    )
    self.assertIn(
        ("entity", ("A10017",), "Selected Employee"),
        {(fact.kind, fact.values, fact.evidence_text) for fact in facts},
    )

def test_before_date_emits_temporal_comparison_fact(self):
    facts = detect_semantic_facts(
        "Count records before 2026-09-03",
        ResolutionContext(catalog={}),
    )
    self.assertIn(
        ("Date", "lt", ("2026-09-03",)),
        {(fact.field, fact.operator, fact.values) for fact in facts},
    )
```

- [ ] **Step 2: Run focused tests and verify failures occur before retrieval**

Run: `python -m unittest week5.new_implementation.test_semantic_resolution week5.new_implementation.test_answer -v`

- [ ] **Step 3: Add generic entity-reference detection and directory resolution**

Represent valid identifiers and name spans as candidate facts without embedding names in code. Resolve names only through the bounded directory. Preserve directory-validated choices as trusted state; reject unresolved or ambiguous candidates explicitly.

- [ ] **Step 4: Add schema-level temporal literal detection**

Canonicalize ISO and unambiguous month-name literals. Derive comparison operators from generic temporal language. Reject ambiguous numeric dates and temporal constraints with no representable target field.

- [ ] **Step 5: Make employee cleanup provenance-aware**

Retain a turn-local trusted employee scope during clarification and follow-ups. Remove prior scope only for an explicit new population/grouping request.

- [ ] **Step 6: Run focused suites and commit**

Run: `python -m unittest week5.new_implementation.test_semantic_resolution week5.new_implementation.test_answer -v`

Commit: `fix: ground employee and temporal constraints`

---

### Task 3: Normalize proposal structure and cover every executable choice

**Files:**
- Modify: `week5/new_implementation/attendance_schema.py`
- Modify: `week5/new_implementation/answer.py`
- Modify: `week5/new_implementation/plan_compiler.py`
- Modify: `week5/new_implementation/test_attendance_schema.py`
- Modify: `week5/new_implementation/test_answer.py`
- Modify: `week5/new_implementation/test_plan_compiler.py`

**Interfaces:**
- Consumes: strong and trusted `SemanticFact` instances.
- Produces: a structurally valid `PlannerProposal` or an explicit unsupported/ambiguous outcome.
- Produces: provenance for calculations, grouping, ordering direction, ranking, limits, and record projection.
- Preserves: `compile_proposal(proposal, context) -> CompilationOutcome` as the only proposal-to-executable boundary.

- [ ] **Step 1: Add failing regressions for the reproduced validation failures**

Test deterministic overlay with raw proposals containing both a named measure and equivalent calculation. Cover Authorized record count and percentage of records with a controlled status. Assert normalization produces one authoritative operation and preserves the percentage numerator condition.

```python
def test_authoritative_measure_removes_redundant_calculation(self):
    raw = {
        "status": "ready",
        "measure": {"name": "attendance_records", "evidence_text": "records"},
        "calculation": {"operation": "count", "evidence_text": "Count"},
        "answer_contract": {"shape": "scalar", "unit": "records"},
    }
    facts = (
        SemanticFact(
            kind="measure",
            concept_name="attendance_records",
            evidence_text="records",
            origin="question",
            strength="strong",
        ),
    )
    prepared = _overlay_authoritative_facts(raw, facts)
    self.assertIsNotNone(prepared["measure"])
    self.assertIsNone(prepared["calculation"])

def test_authoritative_percentage_removes_conflicting_measure(self):
    condition = {
        "field": "Status",
        "operator": "eq",
        "value": "Pending For Authorization",
        "evidence_text": "Status equal to Pending For Authorization",
    }
    raw = {
        "status": "ready",
        "measure": {"name": "attendance_records", "evidence_text": "records"},
        "calculation": {
            "operation": "percentage",
            "percentage_condition": condition,
            "evidence_text": "percentage",
        },
        "answer_contract": {"shape": "scalar", "unit": "percentage"},
    }
    facts = (
        SemanticFact(
            kind="calculation",
            concept_name="percentage",
            evidence_text="percentage",
            origin="question",
            strength="strong",
        ),
        SemanticFact(
            kind="filter",
            field="Status",
            operator="eq",
            values=("Pending For Authorization",),
            evidence_text="Status equal to Pending For Authorization",
            origin="question",
            strength="strong",
        ),
    )
    prepared = _overlay_authoritative_facts(raw, facts)
    self.assertIsNone(prepared["measure"])
    self.assertEqual(prepared["calculation"]["operation"], "percentage")
```

- [ ] **Step 2: Run focused tests and verify the Pydantic mutual-exclusion failure**

Run: `python -m unittest week5.new_implementation.test_attendance_schema week5.new_implementation.test_answer week5.new_implementation.test_plan_compiler -v`

- [ ] **Step 3: Normalize mutually exclusive choices from authoritative facts**

Prefer a registry measure when the strong facts identify that measure. Prefer an explicit strong calculation when no named measure represents it. Remove only demonstrably redundant provider choices; reject genuine conflicts.

- [ ] **Step 4: Extend fact and provenance coverage**

Detect and verify projection, grouping, ordering field and direction, ranking, limit, and percentage numerator/denominator choices. Register a typed derived aggregate result as the ordering target for grouped rankings; never expose it as raw SQL.

- [ ] **Step 5: Reject partially understood requests**

Require every strong fact to be covered and every executable proposal choice to have matching evidence or trusted provenance. Return controlled unsupported capabilities for unrepresentable Boolean structure, calculations, or narrative requests.

- [ ] **Step 6: Run focused suites and commit**

Run: `python -m unittest week5.new_implementation.test_attendance_schema week5.new_implementation.test_answer week5.new_implementation.test_plan_compiler week5.new_implementation.test_postgres_compiler -v`

Commit: `fix: normalize grounded planner operations`

---

### Task 4: Make answer contracts and rendering operation-complete

**Files:**
- Modify: `week5/new_implementation/attendance_schema.py`
- Modify: `week5/new_implementation/plan_compiler.py`
- Modify: `week5/new_implementation/answer.py`
- Modify: `week5/new_implementation/test_plan_compiler.py`
- Modify: `week5/new_implementation/test_answer.py`

**Interfaces:**
- Produces: deterministic expected answer contract derived from the compiled operation.
- Validates: `shape`, `unit`, `subject_field`, `grain`, grouping, aggregation, and row/projection compatibility.
- Consumes: verified plan metadata for deterministic answer wording.

- [ ] **Step 1: Add failing contract-shape and rendering regressions**

Cover scalar count, scalar percentage, grouped aggregate, projected rows, and semantic narrative. Assert grouped headers include both operation and field, and percentage answers identify the verified numerator and denominator meaning.

- [ ] **Step 2: Run focused tests and record current weak validations and generic wording**

Run: `python -m unittest week5.new_implementation.test_plan_compiler week5.new_implementation.test_answer -v`

- [ ] **Step 3: Derive and validate the complete answer contract**

Add output-unit metadata to field definitions where needed. Derive the expected contract from measure/calculation, grouping, projection, and retrieval mode, then compare the provider contract against it before execution.

- [ ] **Step 4: Render answers from verified semantics**

Use registry natural labels and compiled predicates/conditions to describe counts, grouped values, percentages, employee scope, temporal scope, and coverage windows. Do not ask the model to redo arithmetic.

- [ ] **Step 5: Run focused suites and commit**

Run: `python -m unittest week5.new_implementation.test_plan_compiler week5.new_implementation.test_answer -v`

Commit: `fix: enforce complete answer contracts`

---

### Task 5: Close retrieval boundaries and prove baseline-to-current capability parity

**Files:**
- Modify: `week5/new_implementation/answer.py`
- Modify: `week5/new_implementation/retrieval.py`
- Modify: `week5/new_implementation/postgres_compiler.py`
- Modify: `week5/new_implementation/test_answer.py`
- Modify: `week5/new_implementation/test_postgres_compiler.py`
- Modify: `week5/new_evaluation/test_eval.py`

**Interfaces:**
- Consumes: only `ExecutableQueryPlan` at public retrieval orchestration.
- Produces: PostgreSQL and Chroma constraints containing the trusted attendance domain.
- Preserves: parameterized SQL, read-only transactions, and the public `fetch_context()` tuple.

- [ ] **Step 1: Add failing gate and domain-scope regressions**

Assert low-level retrieval helpers are not exported from the public façade, semantic PostgreSQL includes a parameterized trusted-domain predicate, and no retrieval begins for rejected proposals.

- [ ] **Step 2: Add baseline capability regressions through the current architecture**

Cover worked and not-worked distinct dates, scheduled non-attendance, record count, latest records, grouped highest aggregate, percentage, single date, temporal comparison, employee clarification, and follow-up scope. Assert executable plans rather than legacy normalization internals.

- [ ] **Step 3: Run focused tests and verify each regression fails for the expected architectural reason**

Run: `python -m unittest week5.new_implementation.test_answer week5.new_implementation.test_postgres_compiler week5.new_evaluation.test_eval -v`

- [ ] **Step 4: Narrow retrieval entry points and enforce domain scope**

Expose only gated orchestration publicly. Derive low-level filters and compiled SQL inside that boundary. Apply trusted domain restrictions consistently to PostgreSQL and Chroma semantic retrieval.

- [ ] **Step 5: Run all deterministic verification**

Run from the repository root, using the existing shared virtual environment:

```powershell
$env:ANONYMIZED_TELEMETRY='False'
$env:POSTHOG_DISABLED='true'
& 'D:\projects\llm_engineering_ed_donner\llm_engineering\.venv\Scripts\python.exe' -m unittest discover -s week5 -p '*test*.py' -v
& 'D:\projects\llm_engineering_ed_donner\llm_engineering\.venv\Scripts\ruff.exe' check week5/new_implementation week5/new_evaluation week5/new_evaluator.py week5/test_new_evaluator.py week5/new_app.py week5/test_new_app.py
& 'D:\projects\llm_engineering_ed_donner\llm_engineering\.venv\Scripts\ruff.exe' format --check week5/new_implementation week5/new_evaluation week5/new_evaluator.py week5/test_new_evaluator.py week5/new_app.py week5/test_new_app.py
& 'D:\projects\llm_engineering_ed_donner\llm_engineering\.venv\Scripts\python.exe' -m compileall -q week5/new_implementation week5/new_evaluation week5/new_evaluator.py week5/test_new_evaluator.py week5/new_app.py week5/test_new_app.py
git diff --check
& 'D:\projects\llm_engineering_ed_donner\llm_engineering\.venv\Scripts\python.exe' -m week5.new_evaluation.eval --verify-dataset
& 'D:\projects\llm_engineering_ed_donner\llm_engineering\.venv\Scripts\python.exe' -m week5.new_evaluation.benchmark --warmups 2 --runs 10
```

Search with `rg` for raw model SQL, duplicated semantic sources, question-specific prompt rules, and obsolete state boundaries, and record each match classification in the task report.

- [ ] **Step 6: Run provider-backed evaluation conditionally**

Probe provider health first with one non-private record-count case. If healthy, run:

```powershell
& 'D:\projects\llm_engineering_ed_donner\llm_engineering\.venv\Scripts\python.exe' -m week5.new_evaluation.eval --behavior --all
```

Run the answer-quality action through its existing application/evaluator entry point for all cases. Treat provider failures as inconclusive and report them separately from deterministic product failures.

- [ ] **Step 7: Commit final boundary changes**

Commit: `fix: enforce grounded retrieval boundaries`

## Acceptance Criteria

- Equivalent supported wording produces the same verified executable plan.
- Worked, scheduled, non-attended, Authorized, date, percentage, grouping, ordering, ranking, limit, projection, and follow-up examples have regression coverage.
- No proposal with ungrounded executable choices reaches retrieval.
- Provider output cannot trigger redundant measure/calculation validation failures for deterministically grounded requests.
- `AnswerContract` matches actual operation shape, unit, subject, grain, and projection.
- PostgreSQL and Chroma retrieval remain explicitly attendance-domain scoped.
- The complete deterministic suite, Ruff, compilation, and dataset verification pass.
- Provider-backed results are reported only when provider access is healthy.
- The final branch contains coherent commits and no private attendance data.
