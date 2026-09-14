# APDC Provider Decision Boundary and Answer Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make deterministic semantics the sole owner of proven executable choices, restrict the provider to bounded unresolved decisions, and raise answer quality without weakening fail-closed correctness.

**Architecture:** Build an immutable deterministic planning draft from registry-grounded facts. Fully grounded questions assemble a strict `PlannerProposal` without a provider call; unresolved language is sent as finite typed decision slots whose selected candidate IDs are validated before deterministic proposal assembly and the existing invariant compiler. Add privacy-safe structural diagnostics so every sub-5 answer can be classified and corrected by shared architectural cause.

**Tech Stack:** Python 3.11+, Pydantic 2, LiteLLM structured output, psycopg, Chroma, unittest, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-14-provider-decision-boundary-answer-quality-design.md`

## Global Constraints

- Do not add raw or model-generated SQL, silent plan normalization, question templates, employee-specific behavior, expected-answer mappings, compatibility error-text mappings, or partial execution.
- Deterministic facts own all proven filters, measures, predicates, calculations, grouping, ordering, ranking, limits, projections, result shapes, and answer contracts.
- Provider output may select only request-local candidate IDs for explicitly unresolved typed needs.
- Every accepted decision and executable constraint must retain exact provenance and pass the standard invariant chain.
- Preserve public answer/retrieval shapes, trusted session scope, access checks, parameter binding, read-only PostgreSQL, and attendance-domain Chroma restrictions.
- Diagnostics and documentation must not expose private questions, identities, values, records, expected answers, provider payloads, credentials, or fixture contents.
- Add a focused failing regression before every production behavior change.
- Keep explicitly unsupported capabilities fail-closed unless this plan adds complete end-to-end support.

---

### Task 1: Add privacy-safe failure taxonomy and single-run diagnostics

**Files:**
- Modify: `week5/new_evaluation/test.py`
- Modify: `week5/new_evaluation/eval.py`
- Modify: `week5/new_evaluation/test_eval.py`
- Modify: `week5/new_evaluator.py`
- Modify: `week5/test_new_evaluator.py`

**Interfaces:**
- Produces: `FailureCause`, `CaseDiagnostic`, and `classify_case_diagnostic()`.
- Produces: one local-only diagnostic row per evaluated case using structural metadata only.
- Preserves: existing `BehaviorEval`, `AnswerEval`, CLI, and dashboard results.

- [ ] **Step 1: Write failing redaction and classification tests**

Add literal synthetic cases proving structural validation, unsupported
capability, uncovered fact, contract mismatch, retrieval mismatch, renderer
incompleteness, irrelevant evidence, session-state failure, and evaluator drift
map to distinct controlled causes. Assert serialized diagnostics exclude
`question`, `reference_answer`, `generated_answer`, evidence text, record IDs,
employee data, provider payloads, and exception messages.

```python
def test_case_diagnostic_contains_structure_without_private_text(self):
    diagnostic = CaseDiagnostic.from_failure(
        index=7,
        category="worked_days",
        stage="semantic_validation",
        violation_codes=("uncovered_fact",),
    )
    payload = diagnostic.model_dump()
    self.assertEqual(payload["cause"], "missing_deterministic_fact")
    self.assertNotIn("question", payload)
    self.assertNotIn("exception", payload)
```

- [ ] **Step 2: Run the evaluator tests and verify RED**

Run: `python -m unittest week5.new_evaluation.test_eval week5.test_new_evaluator -v`

Expected: imports fail because the diagnostic types do not exist.

- [ ] **Step 3: Implement immutable diagnostic models and precedence**

Use closed literal cause/stage enums and counts/controlled identifiers only.
Do not accept arbitrary messages on a diagnostic model. Keep diagnostic rows in
memory unless an explicit local output path is supplied.

- [ ] **Step 4: Make behavior and answer evaluation share one execution trace**

Add an evaluator-only execution adapter that captures the plan, result shape,
renderer kind, state outcome, and controlled exception type from the same
public-path run used for scoring. External provider calls may be mocked only in
unit tests; corpus runs use the configured provider.

- [ ] **Step 5: Add aggregate CLI/dashboard reporting**

Report counts by category and cause. Never render per-case private text or raw
diagnostic payloads in the dashboard.

- [ ] **Step 6: Run focused tests and commit**

Run: `python -m unittest week5.new_evaluation.test_eval week5.test_new_evaluator -v`

Commit: `feat: classify APDC answer failures safely`

---

### Task 2: Define deterministic drafts and provider decision-only models

**Files:**
- Modify: `week5/new_implementation/attendance_schema.py`
- Modify: `week5/new_implementation/semantic_resolution.py`
- Create: `week5/new_implementation/planning_decisions.py`
- Modify: `week5/new_implementation/test_attendance_schema.py`
- Modify: `week5/new_implementation/test_semantic_resolution.py`
- Create: `week5/new_implementation/test_planning_decisions.py`

**Interfaces:**
- Produces: `PlanningCandidate`, `PlanningNeed`, `PlanningDraft`,
  `PlannerDecisionSelection`, and `PlannerDecision`.
- Produces: `build_planning_draft(question, facts) -> PlanningDraft`.
- Produces: `assemble_grounded_proposal(draft, decision=None) -> PlannerProposal`.

- [ ] **Step 1: Write failing model-boundary tests**

Assert `PlannerDecision` rejects executable fields and values, unknown extra
keys, blank/duplicate need IDs, duplicate selections, selection of unknown
candidate IDs, and unsupported capabilities on a resolved status.

```python
def test_provider_decision_has_no_executable_plan_slots(self):
    with self.assertRaises(ValidationError):
        PlannerDecision.model_validate(
            {"status": "resolved", "filters": [{"field": "Status"}]}
        )
```

- [ ] **Step 2: Write failing deterministic assembly matrix**

Generate cases for all registered measures, predicates, calculations, value
concepts, filter resolution kinds, grouping, ordering, ranking, limit,
projection, percentage numerator, narrative intent, trusted employee scope, and
supported temporal operators. Assert fully grounded cases have no needs and
assemble the exact expected proposal/contract without provider data.

- [ ] **Step 3: Run focused tests and verify RED**

Run: `python -m unittest week5.new_implementation.test_attendance_schema week5.new_implementation.test_semantic_resolution week5.new_implementation.test_planning_decisions -v`

- [ ] **Step 4: Implement draft construction from occurrence-bound facts**

Draft construction must reject multiple independent operations as
`multi_stage_aggregation`, retain all strong unsupported facts, and emit a need
only when a finite registry-derived candidate set exists. Employee and catalog
candidate ambiguity remains owned by the existing resolvers.

- [ ] **Step 5: Implement deterministic proposal assembly**

Build every `Proposed*` choice from facts and provenance, derive the complete
answer contract, and preserve exact consumed/evidence spans. Missing or invalid
decisions return controlled violations rather than a partial proposal.

- [ ] **Step 6: Run focused suites and commit**

Run: `python -m unittest week5.new_implementation.test_attendance_schema week5.new_implementation.test_semantic_resolution week5.new_implementation.test_planning_decisions week5.new_implementation.test_plan_compiler -v`

Commit: `feat: assemble plans from deterministic facts`

---

### Task 3: Replace full provider proposals with bounded decisions

**Files:**
- Modify: `week5/new_implementation/answer.py`
- Modify: `week5/new_implementation/query.py`
- Modify: `week5/new_implementation/test_answer.py`
- Modify: `week5/new_implementation/test_query.py`
- Modify: `week5/new_implementation/test_observability.py`

**Interfaces:**
- Replaces provider use of `propose_query()` with
  `decide_planning_needs(question, draft, history, trusted_employees) -> PlannerDecision`.
- Keeps `PlannerProposal` as the internal compiler input, never provider output.
- Skips provider planning when `PlanningDraft.needs` is empty.

- [ ] **Step 1: Write failing zero-provider-call regressions**

Through `_fetch_context_result()`, assert registered worked, scheduled,
scheduled-non-attended, absent, zero-worked-hours, Authorized-record, exact
filter, date, percentage, grouping, ranking, projection, and trusted follow-up
requests compile and execute while the provider planning mock remains uncalled.

- [ ] **Step 2: Write failing bounded-decision prompt tests**

For a synthetic ambiguous phrase, capture the provider prompt and response
schema. Assert it contains only opaque need/candidate IDs, generic decision
instructions, bounded trusted context, and the question; it must not contain the
full semantic registry or any executable plan slot.

- [ ] **Step 3: Write failing invalid-decision stop tests**

Unknown need IDs, unknown candidate IDs, duplicate decisions, missing required
decisions, and structurally invalid provider output must stop before employee
directory expansion, retrieval, calculation, reranking, and final completion.

- [ ] **Step 4: Run the focused tests and verify RED**

Run: `python -m unittest week5.new_implementation.test_answer week5.new_implementation.test_query week5.new_implementation.test_observability -v`

- [ ] **Step 5: Implement the decision-only provider call**

Send only unresolved slots. Parse directly through strict Pydantic structured
output, validate every selection against its request-local candidate set, then
call `assemble_grounded_proposal()`.

- [ ] **Step 6: Remove full-proposal overlay from the live path**

Delete `_overlay_authoritative_facts()` only after repository search proves no
live caller. Do not retain a compatibility wrapper. Keep `compile_proposal()` as
the mandatory proposal-to-executable boundary.

- [ ] **Step 7: Emit privacy-safe decision lifecycle events**

Emit need count, decision status, selection count, and controlled failure codes;
never emit need evidence, candidates, question text, provider content, or
trusted employee data.

- [ ] **Step 8: Run focused suites and commit**

Run: `python -m unittest week5.new_implementation.test_answer week5.new_implementation.test_query week5.new_implementation.test_plan_compiler week5.new_implementation.test_observability -v`

Commit: `refactor: limit provider to unresolved planning decisions`

---

### Task 4: Fix projection, identity, and temporal clause composition

**Files:**
- Modify: `week5/new_implementation/semantic_resolution.py`
- Modify: `week5/new_implementation/plan_compiler.py`
- Modify: `week5/new_implementation/test_semantic_resolution.py`
- Modify: `week5/new_implementation/test_plan_compiler.py`
- Modify: `week5/new_implementation/test_answer.py`
- Modify: `week5/new_evaluation/test_eval.py`

**Interfaces:**
- Preserves: occurrence-bound identity, projection, and temporal facts.
- Produces: an executable rows plan for projection + identity + date in either
  clause order.

- [ ] **Step 1: Add the failing synthetic public-pipeline regression**

Use synthetic employee `A10018 / Morgan River` and assert:

```python
question = "Show Date and Status from records for Morgan River on 2026-09-01"
self.assertEqual(plan.projection, ["Date", "Status"])
self.assertIn(("Employee_ID", "eq", "A10018"), filters)
self.assertIn(("Date", "eq", "2026-09-01"), filters)
```

Assert the equivalent date-first wording produces the same executable plan and
that count-with-identity/date behavior is unchanged.

- [ ] **Step 2: Run the regression and record the exact failing invariant/fact**

Run the single test with `-v`. Confirm whether identity masking consumes the
projection delimiter, the projection span consumes the temporal relation, or
coverage sees an uncovered field before proposing a fix.

- [ ] **Step 3: Compare working and failing occurrence graphs**

Inspect exact `evidence_span` and `consumed_span` values for identity,
projection, result-shape, and temporal facts. State one root-cause hypothesis
and test it with the smallest detector-only case.

- [ ] **Step 4: Implement the generic span-ownership correction**

Change only the grammar/span rule that owns the defect. Do not add field lists,
sentence templates, or special handling for the synthetic identity.

- [ ] **Step 5: Run detector, compiler, runtime, and parity suites**

Run: `python -m unittest week5.new_implementation.test_semantic_resolution week5.new_implementation.test_plan_compiler week5.new_implementation.test_answer week5.new_evaluation.test_eval -v`

- [ ] **Step 6: Commit**

Commit: `fix: compose projected employee date records`

---

### Task 5: Modernize unsupported and malformed evaluator expectations

**Files:**
- Modify: `week5/new_evaluation/test.py`
- Modify: `week5/new_evaluation/eval.py`
- Modify: `week5/new_evaluation/test_eval.py`
- Modify locally only when present: `week5/new_evaluation/tests.jsonl`

**Interfaces:**
- Uses: `expected_violation_codes` and `expected_unsupported_capabilities` for
  semantic/compiler rejections.
- Retains: `expected_error` only for access and malformed preflight errors that
  occur before a semantic violation exists.

- [ ] **Step 1: Add failing evaluator-boundary tests**

Assert typed semantic rejections cannot pass by substring matching an exception
message. Assert genuine preflight errors still use their typed exception class
and controlled public response.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m unittest week5.new_evaluation.test_eval -v`

- [ ] **Step 3: Audit each legacy malformed expectation locally**

For every case, execute the real public path and classify it as access preflight,
literal preflight, semantic violation, controlled unsupported capability, or
evaluator drift. Do not print question or expected-answer text.

- [ ] **Step 4: Migrate valid local expectations**

Write only typed codes/capabilities for compiler-owned rejections. Do not add a
runtime compatibility map and do not commit private corpus content.

- [ ] **Step 5: Lock explicit unsupported capability decisions**

Add synthetic public tests proving nested Boolean filters, HAVING, windows,
cross-period comparison, grouped percentage, ungrounded positional first/last,
and genuine multi-stage aggregation reject before retrieval.

- [ ] **Step 6: Run evaluator and safety suites and commit**

Run: `python -m unittest week5.new_evaluation.test_eval week5.new_implementation.test_answer week5.new_implementation.test_plan_compiler -v`

Commit: `test: modernize typed APDC rejection expectations`

---

### Task 6: Run and classify the complete provider-backed corpora

**Files:**
- Modify only after a reproduced shared defect: files named in Tasks 1-5.
- Modify locally only: ignored privacy-safe diagnostic report.
- Modify: `docs/superpowers/plans/2026-09-13-schema-grounded-query-review-handoff.md`

**Interfaces:**
- Produces: aggregate cause/category matrix for every score below 5.
- Produces: synthetic regression for each confirmed shared product defect.

- [ ] **Step 1: Run one non-private provider health probe**

Use a synthetic/global attendance record-count request. Record only responsive,
infrastructure failure, or invalid structured response. Do not print provider
payloads.

- [ ] **Step 2: Run the complete behavior corpus when healthy**

Record totals, pass rate, category rates, violation/capability counts, and
structural errors. Provider infrastructure failures are inconclusive.

- [ ] **Step 3: Run the complete answer-quality corpus when healthy**

Record completion count and aggregate accuracy/completeness/relevance. Store
only privacy-safe per-case structural diagnostics locally.

- [ ] **Step 4: Classify every sub-5 case**

Require the sub-5 count to equal the sum of cause buckets. Manually inspect
locally when the automatic cause is `evaluator_expectation_drift`; tracked notes
contain only aggregate categories and synthetic reproductions.

- [ ] **Step 5: Fix confirmed shared defects one at a time**

For each shared product cause: reproduce through the public path with synthetic
data, write and verify a failing regression, implement the owning architectural
fix, run focused/adjacent tests, and rerun affected corpus categories. Do not
change code solely to satisfy judge wording.

- [ ] **Step 6: Compare accepted strict baseline, permissive historical result,
and current result**

Report safety separately from provider completion and answer scores. Never call
a higher score a correctness improvement unless executable plans and results
are semantically equivalent.

---

### Task 7: Complete verification, documentation, and delivery

**Files:**
- Modify: `docs/superpowers/plans/2026-09-13-schema-grounded-query-review-handoff.md`
- Modify: `week5/new_implementation/APDC_Attendance_Remaining_Improvements_16_to_35.md`
- Modify: `week5/new_implementation/APDC_Attendance_Knowledge_Base_Improvements_1_to_21_Complete_Documentation.md`

**Interfaces:**
- Produces: final verified `main`, pushed only to the configured `origin`.

- [ ] **Step 1: Run complete deterministic discovery**

Run: `python -m unittest discover -s week5 -p '*test*.py' -v`

- [ ] **Step 2: Run static and compilation checks**

Run Ruff check, Ruff format check over the 42-file scope, Python compilation,
and `git diff --check`. Record any pre-existing formatter drift separately and
ensure no new drift remains.

- [ ] **Step 3: Verify private dataset and benchmark**

Run dataset verification and the 2-warmup/10-run benchmark. Confirm row count,
employee count, coverage, fingerprint, zero benchmark failures, and compare
median/p95 timing with baseline.

- [ ] **Step 4: Review privacy and forbidden architecture**

Inspect the full diff and staged files. Search for credentials, private fixture
paths/content, raw SQL/provider SQL fields, old full-proposal provider calls,
question-specific mappings, silent fallbacks, and compatibility error mappings.

- [ ] **Step 5: Update handoffs with evidenced results**

Document manual runtime paths, low-score clusters, root causes, RED regressions,
architectural fixes, unsupported capabilities, deterministic verification,
provider health/corpora, dataset/benchmark, and baseline comparison without
private content.

- [ ] **Step 6: Commit coherent changes and push**

Inspect `git diff --cached` and `git diff --cached --check`, commit only scoped
files, push `main` to the configured `origin` without force, and verify
`HEAD == origin/main`.

- [ ] **Step 7: Prove clean worktrees**

Run status in the primary and review worktrees and report the final commit hash,
remote synchronization, and clean state.

## Acceptance Criteria

- Fully grounded supported questions do not call the provider planner.
- Provider output cannot contain or alter executable fields, values, operations,
  grouping, ordering, limits, projections, answer contracts, backend choices, or
  SQL.
- Every accepted provider decision selects a bounded request-local candidate and
  has exact provenance.
- Every strong semantic fact is represented, clarified, or rejected before
  retrieval.
- Zero-pass attendance calculation families execute through deterministic
  assembly when their meaning is complete.
- Projection + employee + date composition works independent of clause order.
- All sub-5 answer cases are accounted for by privacy-safe cause aggregates.
- Explicit unsupported structures remain typed, controlled, and fail-closed.
- Malformed evaluator expectations use typed violations where appropriate.
- Complete deterministic verification, dataset verification, and benchmark pass.
- Healthy-provider behavior and answer results are reported separately from
  deterministic safety.
- No private APDC data or credentials enter commits, logs, or documentation.
- `main` is pushed only to the configured origin and all worktrees are clean.
