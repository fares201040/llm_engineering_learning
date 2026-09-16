# APDC Independent Conversational-Intelligence Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Independently evaluate and improve the APDC attendance chatbot so realistic multilingual conversations remain correctly grounded, private, compiler-owned, parameterized, and atomic.

**Architecture:** Exercise only public conversation boundaries with synthetic data, observe typed intermediate state through redacted summaries, and repair confirmed failures in their owning layers. Provider decisions remain bounded to request-local typed choices; trusted schema and compiler code exclusively construct executable plans and SQL.

**Tech Stack:** Python 3.11+, Pydantic, `unittest`, `unittest.mock`, PostgreSQL compiler abstractions, Chroma abstractions, Ruff, Git.

**Spec:** `docs/superpowers/specs/2026-09-16-apdc-independent-conversational-intelligence-review.md`

## Global Constraints

- Start from confirmed review base `0f3d02243973d29954f6eb4b5a10e9c1fe2d686d` on `main`; the design-spec commit is `5dfec11d`.
- Use `gpt-5.6-luna` with `max` reasoning for debugging, implementation, fixes, and testing.
- Every production implementation requires a fresh independent `gpt-5.6-sol` reviewer with `medium` reasoning before acceptance.
- Do not run any pre-existing test file or suite until Task 8. Before then, run only newly added focused regressions, isolated manual probes, and necessary syntax/import checks.
- Use synthetic/public employee identifiers, names, catalog values, records, and results. Never print or commit private values.
- Do not send raw history, directories, database contents, DSNs, SQL parameters, exceptions, full physical schemas, or private identifiers to a provider.
- Never execute raw model-authored SQL. Provider output is advisory and request-local; trusted compiler code owns executable SQL and bound parameters.
- All compound units must prepare and validate before any database execution. A failure prevents partial execution and publication.
- Preserve deterministic zero-provider-call behavior for already-grounded requests.
- Do not change `week5/new_implementation/planning_decisions.py` unless a reproduced architectural defect proves its contract must change.
- Commit accepted fixes with clear messages; do not push.

## File Structure

- Create `docs/superpowers/reports/2026-09-16-apdc-conversational-intelligence-review.md`: append-only review ledger, redacted production-path map, 50-turn assessment, RED/GREEN evidence, review findings, final commands, and completion report.
- Create `week5/new_implementation/test_apdc_independent_review.py`: only focused regressions for defects reproduced during this review; no broad duplication of existing suites.
- Modify `week5/new_implementation/attendance_schema.py`: replace the SQL-shaped fallback response with a typed request-local aggregate-field decision if the public reproduction confirms the ownership defect.
- Modify `week5/new_implementation/answer.py`: request-local aggregate decision orchestration, retry context, count handling, localized scope rendering, and any separately reproduced owning-layer corrections.
- Modify `week5/new_implementation/postgres_compiler.py`: remove SQL-text validation from the provider boundary while retaining trusted compilation of validated logical aggregate choices.
- Modify evaluator or conversation modules only when a public reproduction identifies that module as the owner. Record the reason before editing.

---

### Task 1: Trace the production architecture and build the review ledger

**Files:**
- Create: `docs/superpowers/reports/2026-09-16-apdc-conversational-intelligence-review.md`
- Read: `week5/new_app.py`
- Read: `week5/new_implementation/answer.py`
- Read: `week5/new_implementation/conversation_understanding.py`
- Read: `week5/new_implementation/language_understanding.py`
- Read: `week5/new_implementation/semantic_resolution.py`
- Read: `week5/new_implementation/plan_compiler.py`
- Read: `week5/new_implementation/postgres_compiler.py`
- Read: `week5/new_implementation/planning_decisions.py`

**Interfaces:**
- Consumes: `answer_question_with_state(question, history, state, *, access_context=None)` and its preparation/execution helpers.
- Produces: a privacy-safe architectural map and review ledger used by every later task.

- [ ] **Step 1: Record repository evidence**

Run:

```powershell
git rev-parse HEAD
git branch --show-current
git status --short
git log -12 --oneline -- week5/new_implementation/planning_decisions.py
git diff 0f3d02243973d29954f6eb4b5a10e9c1fe2d686d..HEAD -- week5/new_implementation/planning_decisions.py
```

Record exact output summaries in the ledger. Do not claim the planning file is unchanged without the final command.

- [ ] **Step 2: Trace the public call path**

Document exact `file:line` transitions for app state, preflight, conversation decision, materialization, semantic facts, clarification, proposal assembly, plan compilation, query preparation, execution, successful state commit, and rendering. Mark provider inputs, trusted transitions, and possible failure exits.

- [ ] **Step 3: Define the redacted observation schema**

For each representative turn, record only:

```text
turn, route, normalized-shape, locale, referent-counts, fact-kind/origin pairs,
employee-scope cardinality, filter field/operator/type triples, date bounds,
field candidate IDs, operation, clarification kind, executable-plan shape,
compiled expression identifier, parameter count/types, result shape/count,
provider-call count, render language, and pass/fail rationale
```

Explicitly prohibit raw names, IDs, question history, parameter values, SQL text, DSNs, exceptions, and unrestricted schema dumps from the ledger.

- [ ] **Step 4: Commit the ledger skeleton**

```powershell
git add -- docs/superpowers/reports/2026-09-16-apdc-conversational-intelligence-review.md
git diff --cached --check
git commit -m "docs: start APDC independent review ledger"
```

### Task 2: Execute and assess the approximately 50-turn synthetic conversation

**Files:**
- Modify: `docs/superpowers/reports/2026-09-16-apdc-conversational-intelligence-review.md`
- Do not modify production files in this task.

**Interfaces:**
- Consumes: the public stateful answer API and the redacted observation schema from Task 1.
- Produces: one continuous synthetic conversation, a compound matrix, and a numbered defect backlog with public reproductions.

- [ ] **Step 1: Construct synthetic fixtures at runtime**

Patch the employee directory, catalog, PostgreSQL/Chroma execution seams, and provider boundary with public values such as `EMP-100`, `EMP-200`, `Alex North`, and `Sam River`. Ensure fixtures return deterministic dates, hours, statuses, departments, and locations. Do not add a fixture-driven production path.

- [ ] **Step 2: Run turns 1–15: direct context and short follow-ups**

Cover explicit employees/dates/filters/calculations; `he`, `she`, `they`, `that employee`, `the same person`; `those days`, `that period`, `the previous result`; `what about last month?`; and `and his overtime?`. Include one genuine employee ambiguity and one grounded shorthand that must not clarify.

- [ ] **Step 3: Run turns 16–30: corrections, topic changes, and resumption**

Cover “No, I meant the other employee,” “Use last week, not this week,” “I meant total worked hours, not overtime,” multi-turn clarification, a topic switch, a later return to the older employee/result, and long-distance referent recovery.

- [ ] **Step 4: Run turns 31–40: Arabic and mixed language**

Use genuine Arabic sentences, Arabic/Latin digits, diacritics, attached conjunctions, mixed Arabic/English units, spelling mistakes, abbreviations, colloquial wording, and incomplete natural phrases. Check locale and scope attribution on every answer.

- [ ] **Step 5: Run turns 41–50: compound, unsupported, and adversarial behavior**

Cover two and three calculations; multiple employees/periods; supported plus unsupported units; a valid first unit followed by an ambiguous or invalid unit; one-unit correction; reference to one prior compound result; SQL injection; prompt injection; schema extraction; employee enumeration; history extraction; diagnostic leakage; repeated paraphrases; earlier-answer comparison; and one very long noisy message.

- [ ] **Step 6: Verify compound preparation atomicity**

Instrument `execute_exact_postgres`, Chroma access, and connection acquisition. For every compound failure, assert all execution counters remain zero and the original state remains unchanged. For successful compounds, assert preparation completes for all units before the first execution and each rendered component includes its verified scope.

- [ ] **Step 7: Assess every answer manually**

For each turn, record intended meaning, actual typed plan, result fixture, rendered answer, and verdict for intent, field, employee/date scope, history use, referent preservation, completeness, locale, consistency, clarification correctness, leakage, and compound completeness. Every failure becomes `D<N>` with owning-layer hypothesis and exact public reproduction.

- [ ] **Step 8: Commit the evidence-only transcript update**

```powershell
git add -- docs/superpowers/reports/2026-09-16-apdc-conversational-intelligence-review.md
git diff --cached --check
git commit -m "docs: record APDC conversational evaluation"
```

### Task 3: Replace the SQL-shaped aggregate fallback with a typed decision

**Files:**
- Create: `week5/new_implementation/test_apdc_independent_review.py`
- Modify: `week5/new_implementation/attendance_schema.py`
- Modify: `week5/new_implementation/answer.py`
- Modify: `week5/new_implementation/postgres_compiler.py`
- Modify: `docs/superpowers/reports/2026-09-16-apdc-conversational-intelligence-review.md`

**Interfaces:**
- Consumes: a grounded aggregate operation and `_GeneratedAggregateCandidate(candidate_id, field, surface)` values.
- Produces: `GeneratedAggregateDecision` with `status`, one request-local `candidate_id` for a resolved field-bearing operation, bounded candidate IDs for ambiguity, or a controlled unsupported result; trusted code produces `GeneratedAggregateChoice(operation, field)`.

- [ ] **Step 1: Add focused public RED regressions**

Add tests equivalent to:

```python
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from week5.new_implementation import answer


def _response(content: str):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=content),
            )
        ]
    )


class AggregateDecisionBoundaryReviewTests(unittest.TestCase):
    def test_grounded_count_needs_no_provider_field_decision(self):
        with (
            patch.object(answer, "completion") as completion,
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
        ):
            prepared = answer._prepare_context_request("count attendance records")
        completion.assert_not_called()
        self.assertEqual(prepared.plan.aggregation, "count")
        self.assertIsNone(prepared.plan.aggregation_field)
        self.assertIn("COUNT(*)", prepared.postgres_queries.aggregation[0].sql)

    def test_repair_prompt_repeats_operation_and_request_local_candidates(self):
        prompts = []
        def respond(**kwargs):
            prompt = kwargs["messages"][0]["content"]
            prompts.append(prompt)
            if len(prompts) == 1:
                return _response("not json")
            payload = json.loads(prompt.split("\n", 1)[-1])
            self.assertEqual(payload["operation"], "sum")
            self.assertEqual(payload["candidates"][0]["candidate_id"], "field-1")
            self.assertEqual(payload["validation_code"], "invalid_schema")
            return _response(
                '{"status":"resolved","candidate_id":"field-1","candidate_ids":[]}'
            )
        with (
            patch.object(answer, "completion", side_effect=respond),
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
        ):
            prepared = answer._prepare_context_request("sum total working hours")
        self.assertEqual(prepared.plan.aggregation_field, "Total_Worked_Hrs")
        self.assertEqual(len(prompts), 2)

    def test_provider_selects_candidate_id_not_sql(self):
        response = _response(
            '{"status":"resolved","candidate_id":"field-1","candidate_ids":[]}'
        )
        with (
            patch.object(answer, "completion", return_value=response) as completion,
            patch.object(answer, "_postgres_enabled", return_value=True),
            patch.object(answer, "load_attendance_catalog_candidates", return_value={}),
        ):
            prepared = answer._prepare_context_request("sum total working hours")
        prompt = completion.call_args.kwargs["messages"][0]["content"]
        self.assertNotIn("SELECT", prompt)
        self.assertEqual(prepared.plan.aggregation_field, "Total_Worked_Hrs")
        self.assertIn("SUM(total_worked_hrs)", prepared.postgres_queries.aggregation[0].sql)
```

- [ ] **Step 2: Run only the new tests and confirm RED**

```powershell
& '.venv\Scripts\python.exe' -m unittest -v `
  week5.new_implementation.test_apdc_independent_review.AggregateDecisionBoundaryReviewTests
```

Record each failure message in the ledger.

- [ ] **Step 3: Define the strict typed provider decision**

In `attendance_schema.py`, replace `GeneratedAggregateSqlDecision` with a strict model shaped as:

```python
class GeneratedAggregateDecision(_StrictPlannerModel):
    status: Literal["resolved", "ambiguous", "unsupported"]
    candidate_id: str | None = None
    candidate_ids: list[str] = Field(default_factory=list)
```

Its model validator must enforce exactly one nonblank `candidate_id` for `resolved`, one or more unique IDs for `ambiguous`, and no IDs for `unsupported`. A validation function must reject any resolved or ambiguous ID outside the request-local allowlist.

- [ ] **Step 4: Make count deterministic and field decisions request-local**

In `answer.py`, bypass the provider for grounded `count` and construct `GeneratedAggregateChoice("count", None)` directly. For `distinct_count`, `sum`, `average`, `min`, and `max`, send only redacted question surface, grounded operation, candidate IDs, safe descriptions/types/units/natural names, and response-shape instructions. Convert a validated candidate ID to its registry field locally.

- [ ] **Step 5: Make every retry self-contained**

Build each retry by adding a controlled `validation_code` to the original safe decision payload. Do not include prior raw provider output or SQL. Preserve the provider-call budget, `num_retries=0`, timeout, deterministic temperature, and controlled observability fields.

- [ ] **Step 6: Remove SQL-text provider validation**

Delete `validate_generated_aggregate_sql()` and the logical SQL regex from `postgres_compiler.py`. Retain `compile_generated_aggregate_query(choice, plan)` as a trusted consistency check that delegates to `compile_aggregation_queries()` and returns exactly one parameterized aggregate query.

- [ ] **Step 7: Run focused GREEN and isolated privacy probes**

Run the Task 3 test class only. Then inspect captured provider payloads and assert absence of synthetic names, IDs, raw history, DSNs, parameters, physical table names, SQL, and exceptions. Assert grounded count and already-grounded field requests make zero provider calls.

- [ ] **Step 8: Obtain fresh Sol-medium review and resolve findings**

Review the Task 3 diff for typed decision ownership, request-local validation, privacy, compiler ownership, parameter binding, zero-call behavior, call budget, and compound compatibility. Correct Critical/Important findings with Luna-max, rerun only the new focused class, and request a fresh scoped Sol-medium review.

- [ ] **Step 9: Commit the accepted correction**

```powershell
git add -- week5/new_implementation/attendance_schema.py `
  week5/new_implementation/answer.py `
  week5/new_implementation/postgres_compiler.py `
  week5/new_implementation/test_apdc_independent_review.py `
  docs/superpowers/reports/2026-09-16-apdc-conversational-intelligence-review.md
git diff --cached --check
git commit -m "fix: restore typed aggregate decision boundary"
```

### Task 4: Preserve verified scope in Arabic scalar answers

**Files:**
- Modify: `week5/new_implementation/test_apdc_independent_review.py`
- Modify: `week5/new_implementation/answer.py`
- Modify: `docs/superpowers/reports/2026-09-16-apdc-conversational-intelligence-review.md`

**Interfaces:**
- Consumes: `_format_aggregation_answer(plan, aggregation, locale="ar")` and `_verified_scope_suffix(plan)`.
- Produces: deterministic Arabic scalar text that identifies the same verified employee/date/filter scope as English without leaking hidden values.

- [ ] **Step 1: Add and run focused RED regressions**

Add one direct Arabic scalar test and one Arabic compound test with two synthetic employees/periods. Assert each paragraph contains the correct public fixture scope and cannot be confused with the other result. Run only the new `ArabicAggregationScopeReviewTests` class and record RED.

- [ ] **Step 2: Implement the smallest localized rendering correction**

Compute a locale-appropriate scope suffix once and append it to every Arabic scalar aggregation branch, including count, distinct count, percentage, sum, average, minimum, and maximum. Reuse verified plan filters; do not derive scope from the original question or provider text.

- [ ] **Step 3: Confirm focused GREEN and rerun affected manual turns**

Run only `ArabicAggregationScopeReviewTests`, then rerun the Arabic scalar and compound turns from Task 2. Record exact outcomes.

- [ ] **Step 4: Obtain fresh Sol-medium review and commit**

Review locale, grammar, verified-plan agreement, compound attribution, privacy, and unchanged English output. Resolve Critical/Important findings, obtain a fresh scoped review, then commit:

```powershell
git add -- week5/new_implementation/answer.py `
  week5/new_implementation/test_apdc_independent_review.py `
  docs/superpowers/reports/2026-09-16-apdc-conversational-intelligence-review.md
git diff --cached --check
git commit -m "fix: retain scope in Arabic aggregate answers"
```

### Task 5: Escape deterministic grouped-result cells

**Files:**
- Modify: `week5/new_implementation/test_apdc_independent_review.py`
- Modify: `week5/new_implementation/answer.py`
- Modify: `docs/superpowers/reports/2026-09-16-apdc-conversational-intelligence-review.md`

**Interfaces:**
- Consumes: trusted grouped result values that may nevertheless contain Markdown control characters.
- Produces: stable Markdown cells with pipes escaped and CR/LF collapsed without changing semantic values.

- [ ] **Step 1: Add and run focused RED regressions**

Add English and Arabic grouped-result tests using synthetic group values `Ops | North` and `Remote\nAnnex`. Assert one logical row per result, escaped `\|`, and no injected table row. Run only `GroupedRenderingSafetyReviewTests` and record RED.

- [ ] **Step 2: Add one shared deterministic cell formatter**

Add a private formatter in `answer.py` that converts a value to display text, replaces CR/LF runs with one space, and escapes `|` as `\|`. Use it for every group label in both English and Arabic table branches. Do not HTML-escape whole Markdown answers or alter numeric formatting.

- [ ] **Step 3: Confirm GREEN, review, and commit**

Run only `GroupedRenderingSafetyReviewTests`. Obtain a fresh Sol-medium review of Markdown safety, localization parity, and deterministic rendering. Resolve Critical/Important findings and commit:

```powershell
git add -- week5/new_implementation/answer.py `
  week5/new_implementation/test_apdc_independent_review.py `
  docs/superpowers/reports/2026-09-16-apdc-conversational-intelligence-review.md
git diff --cached --check
git commit -m "fix: escape grouped attendance result cells"
```

### Task 6: Resolve additional defects found by the manual conversation

**Files:**
- Modify only the owning production module for each confirmed `D<N>`.
- Modify: `week5/new_implementation/test_apdc_independent_review.py`
- Modify: `docs/superpowers/reports/2026-09-16-apdc-conversational-intelligence-review.md`

**Interfaces:**
- Consumes: numbered public reproductions and owning-layer diagnoses from Task 2.
- Produces: one independently reviewable commit per confirmed defect or tightly coupled root cause.

- [ ] **Step 1: Adjudicate every backlog item before editing**

For each `D<N>`, record one of: confirmed defect with owner; intentionally unsupported with cited contract; fixture/probe error; unavailable live/private check. Do not silently discard imperfect responses.

- [ ] **Step 2: Create one focused RED test per confirmed root cause**

Name each test after behavior, not wording. Drive the public API when possible. Assert typed plan/state and execution counters in addition to output text. Run only the newly added test method and record RED.

- [ ] **Step 3: Apply the smallest native correction**

Edit the owner identified in Step 1. Semantic vocabulary changes must be registry-wide and multilingual rather than question-specific. State changes must preserve immutable/revalidated referents. Compiler changes must accept only typed verified plans. Rendering changes must derive from plan/result metadata.

- [ ] **Step 4: Confirm GREEN and rerun the affected conversation segment**

Run only the new regression, then the minimum manual sequence needed to prove correction, resumption, long-distance context, or compound behavior. Recheck provider calls, privacy, parameter shapes, state mutation, and atomicity.

- [ ] **Step 5: Obtain fresh Sol-medium review and commit**

Review each implementation independently. Resolve Critical/Important findings and obtain a fresh scoped review before committing. Use a message naming the native behavior, not the evaluator case.

### Task 7: Run offline answer-quality and BIRD/Contract EX evaluation

**Files:**
- Modify: `docs/superpowers/reports/2026-09-16-apdc-conversational-intelligence-review.md`
- Modify production/tests only if a newly reproduced genuine defect enters the Task 6 workflow.

**Interfaces:**
- Consumes: the completed implementation and the repository's offline evaluation entry points.
- Produces: exact aggregate metrics plus a manual disposition for every low-scoring/non-passing case.

- [ ] **Step 1: Identify the offline-only commands without executing existing suites**

Inspect `week5/new_evaluation/BIRD_METRIC.md`, evaluator entry points, and CLI help/source. Record the exact commands, case counts, environment assumptions, and whether private fixtures are available. Do not invent scores when assets are absent.

- [ ] **Step 2: Run representative offline answer-quality and BIRD/Contract EX evaluation once**

Use only authorized local fixtures. Capture total cases, pass/fail counts, accuracy/completeness/relevance/consistency/groundedness measures, and execution/contract metrics. Do not invoke live/private evaluation without available authorization and data.

- [ ] **Step 3: Inspect every weak case manually**

Record intended meaning, typed plan, actual result, rendered answer, owning layer, and disposition. Route genuine defects through Task 6 with new RED/GREEN evidence and a fresh review. Document unsupported cases and unavailable checks.

### Task 8: Run the one-time final regression and review gate

**Files:**
- Modify: `docs/superpowers/reports/2026-09-16-apdc-conversational-intelligence-review.md`
- Do not modify expected answers merely to make failures pass.

**Interfaces:**
- Consumes: all accepted reviewed commits.
- Produces: final verification evidence, whole-branch review, final HEAD, and completion report.

- [ ] **Step 1: Run the relevant existing focused suites exactly once**

Use the focused APDC commands documented by the repository, including conversation understanding, semantic/language resolution, plan/PostgreSQL compiler, answer, generated fallback, compound conversation, application/evaluator, and BIRD/Contract EX tests. Record commands, test counts, skips, failures, and elapsed results. Do not rerun a pre-existing suite after a failure; diagnose from this single gate and use only new focused regressions for any subsequent correction.

- [ ] **Step 2: Run static, compilation, import, and whitespace checks**

```powershell
& '.venv\Scripts\ruff.exe' check week5/new_implementation week5/new_evaluation week5/new_evaluator.py week5/new_app.py
& '.venv\Scripts\ruff.exe' format --check week5/new_implementation week5/new_evaluation week5/new_evaluator.py week5/new_app.py
& '.venv\Scripts\python.exe' -m compileall -q week5/new_implementation week5/new_evaluation week5/new_evaluator.py week5/new_app.py
& '.venv\Scripts\python.exe' -c "from week5.new_implementation import answer, attendance_schema, conversation_understanding, plan_compiler, postgres_compiler; print('imports ok')"
git diff --check 0f3d02243973d29954f6eb4b5a10e9c1fe2d686d..HEAD
git diff --stat 0f3d02243973d29954f6eb4b5a10e9c1fe2d686d..HEAD
git diff 0f3d02243973d29954f6eb4b5a10e9c1fe2d686d..HEAD -- week5/new_implementation/planning_decisions.py
git status --short
```

- [ ] **Step 3: Request a final whole-branch Sol-medium review**

Review the complete range from `0f3d0224` to `HEAD` against the approved spec. Require explicit findings for conversational correctness, privacy, provider payloads, zero-call paths, state/referents, multilingual behavior, compiler ownership, parameter binding, compound preparation/execution atomicity, deterministic rendering, evaluator integrity, and the `planning_decisions.py` comparison.

- [ ] **Step 4: Resolve final Critical/Important findings**

Use Luna-max for any fix, add a new focused RED regression, run only that regression through GREEN, rerun the affected manual probe, and obtain a fresh scoped Sol-medium review. Do not rerun the existing suites; report the final-gate result and the post-gate focused evidence separately.

- [ ] **Step 5: Complete and commit the review report**

Include confirmed starting/final HEADs, every scenario, important turn-by-turn failures and corrections, owning layers, RED/GREEN commands/results, safe state/provider representations, semantic changes, evaluation metrics, remaining weak cases, final existing-test/static results, independent review findings, unavailable checks, preserved limitations, changed files, and commits.

```powershell
git add -- docs/superpowers/reports/2026-09-16-apdc-conversational-intelligence-review.md
git diff --cached --check
git commit -m "docs: complete APDC conversational intelligence review"
git rev-parse HEAD
git status --short
```
