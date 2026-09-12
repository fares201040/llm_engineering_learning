# APDC Attendance Correctness and Clarification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make APDC attendance planning, identity resolution, clarification, calculations, retrieval, and evaluation deterministic and database-backed while preserving the existing stateless API.

**Architecture:** Retain the LLM as a structured language interpreter, then compile its output through a canonical attendance schema and database-backed employee resolver before any retrieval can run. Add a minimal per-session clarification state to Gradio, reunite retrieved split parts, and replace the unrelated evaluation corpus with APDC attendance cases.

**Tech Stack:** Python 3.11+, Pydantic, PostgreSQL/psycopg, Chroma, OpenAI/LiteLLM, Gradio, unittest, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-11-attendance-correctness-design.md`

## Global Constraints

- PostgreSQL attendance rows are authoritative for exact facts and calculations.
- Never retrieve while employee identity or a structured constraint is unresolved.
- An explicit valid employee ID overrides a conflicting employee name.
- Preserve `answer_question(question, history) -> tuple[str, list[Result]]`.
- Use one hidden Gradio state per browser session; never use module-global conversation state.
- Add no dependency and no generalized entity-resolution framework.
- Every behavioral production change starts with a focused failing test.
- Do not commit automatically from the dirty `main` checkout. Preserve all unrelated user-owned changes.
- Replace the Insurellm evaluation rows; do not archive or retain them in the active evaluation data.
- Remove stale imports, constants, comments, duplicate helpers, and temporary diagnostics before completion.

---

### Task 1: Canonical Attendance Schema and Deterministic Metrics

**Files:**
- Create: `week5/new_implementation/attendance_schema.py`
- Create: `week5/new_implementation/test_attendance_schema.py`
- Modify: `week5/new_implementation/answer.py`
- Modify: `week5/new_implementation/test_answer.py`

**Interfaces:**
- Produces: `FIELD_DEFINITIONS`, `CALCULATION_DEFINITIONS`, `relevant_field_definitions(question)`, and `apply_calculation_semantics(question, plan)`.
- Consumes: the existing `QueryPlan` and `FilterCondition` shapes without importing `answer.py` from the schema module.

- [ ] **Step 1: Add a failing worked-days semantics test**

  Create a plan for “How many days did A10029 work?” and assert that applying canonical semantics produces `distinct_count(Date)` plus `Total_Worked_Hrs > 0`.

- [ ] **Step 2: Run the focused test and confirm it fails because no canonical semantics compiler exists**

  Run: `python -m unittest week5.new_implementation.test_attendance_schema.CanonicalCalculationTests.test_worked_days_require_positive_worked_hours -v`

- [ ] **Step 3: Implement immutable field/calculation definitions and the minimal compiler**

  Use declarative definitions for worked days, scheduled working days, attendance records, authorized records, employees, and allowed numeric aggregations. The compiler mutates a copied plan, replaces only conflicting calculation filters, and remains independent of employee identity.

- [ ] **Step 4: Add and run separate red–green tests for scheduled working days and authorized records**

  Assert `Day_Type = "Working Day"` and `Status = "Authorized"` respectively, with direct-count semantics.

- [ ] **Step 5: Move Improvement 24 field aliases into the schema module**

  Keep the three existing context-selection tests green. Delete the superseded alias constant from `answer.py`.

- [ ] **Step 6: Add relevant field definitions to planner/final context without relying on them for execution**

  Test the returned definition set as data and test calculation behavior, not exact prompt source text.

- [ ] **Step 7: Run schema tests, answer tests, Ruff, and the complete Week 5 suite**

---

### Task 2: Cross-Backend Plan Normalization and User-Input Validation

**Files:**
- Modify: `week5/new_implementation/attendance_schema.py`
- Modify: `week5/new_implementation/answer.py`
- Modify: `week5/new_implementation/test_answer.py`

**Interfaces:**
- Produces: `PlanValidationError`, `normalize_query_plan(question, plan, employee_directory=None) -> QueryPlan`.
- Consumes: field definitions and calculation compiler from Task 1.

- [ ] **Step 1: Add a failing test proving an invalid numeric/date plan is rejected before retrieval routing**

  Use a numeric `Date` value and an invalid `Lateness_Hrs` operand. Assert `PlanValidationError` before any backend mock is called.

- [ ] **Step 2: Implement type/operator normalization for every backend**

  Reuse one validator for PostgreSQL and Chroma. Normalize dates to ISO strings, numerics to finite floats, `in` values item-by-item, and reject unsupported aggregation fields.

- [ ] **Step 3: Add a failing test for a missing comparative operand**

  Use “late more than banana hours” with a planner plan that omitted the numeric condition. Assert a user clarification result rather than semantic retrieval.

- [ ] **Step 4: Add declarative comparison-intent validation**

  Detect that a comparison phrase requires a numeric filter and reject a plan that dropped or malformed it. Do not encode employee- or answer-specific cases.

- [ ] **Step 5: Canonicalize structured routing and planner output**

  Exact filters/calculations stay exact unless the question contains genuine semantic intent. Set deterministic provider sampling where supported, while testing normalized plans rather than raw response text.

- [ ] **Step 6: Convert expected validation failures into concise user-facing messages**

  Unexpected operational failures remain exceptions below the UI boundary and are logged there later.

- [ ] **Step 7: Run focused tests, all answer tests, Ruff, and the complete suite**

---

### Task 3: Database-Backed Employee Candidates and Resolution

**Files:**
- Modify: `week5/new_implementation/answer.py`
- Modify: `week5/new_implementation/test_answer.py`

**Interfaces:**
- Produces: `EmployeeCandidate`, `EmployeeResolution`, `load_employee_directory()`, `resolve_employee_reference(reference, candidates)`.
- Replaces: name-only `resolve_employee_names_*` behavior after compatibility tests are updated.

- [ ] **Step 1: Add a failing test that duplicate full names retain separate employee IDs**

  Use two `Duplicate Example Person` candidates and assert an ambiguous result listing A10439 and A10663 separately.

- [ ] **Step 2: Implement PostgreSQL and Chroma employee-directory loaders**

  Return sorted unique `(employee_id, name)` pairs. PostgreSQL is authoritative when enabled; Chroma is the fallback.

- [ ] **Step 3: Add and pass tests for exact full names and unique partial names**

  A unique match returns `unique`; several matches return `ambiguous` rather than a combined filter.

- [ ] **Step 4: Add a failing `muhtar` fuzzy-resolution test**

  Assert bounded example candidates derived from the directory. Verify that unrelated names remain below the threshold.

- [ ] **Step 5: Implement token-aware fuzzy scoring with stable ordering**

  Use standard-library similarity on normalized full names and tokens. Distinguish strong unique, weaker confirmation, ambiguous, and none outcomes.

- [ ] **Step 6: Add red–green tests for explicit ID priority and unknown IDs**

  A valid ID removes conflicting name constraints; an unknown ID yields `none` and cannot execute retrieval.

- [ ] **Step 7: Normalize planner identity output**

  Merge `name_hint`, `Name` conditions, and name-like `Employee_ID` values into one reference. Extract explicit ID tokens from the question and give them priority.

- [ ] **Step 8: Remove superseded name-only resolver code and rerun focused/full checks**

---

### Task 4: Stateful Clarification and the Retrieval Safety Gate

**Files:**
- Modify: `week5/new_implementation/answer.py`
- Modify: `week5/new_implementation/test_answer.py`

**Interfaces:**
- Produces: `ConversationState`, `answer_question_with_state(question, history, state) -> tuple[str, list[Result], ConversationState]`.
- Preserves: `answer_question(question, history) -> tuple[str, list[Result]]` as a fresh-state wrapper.

- [ ] **Step 1: Add a failing ambiguity-gate test**

  Patch exact retrieval, embeddings/semantic retrieval, aggregation, reranking, and final completion. Assert all remain uncalled for an ambiguous employee reference.

- [ ] **Step 2: Implement clarification rendering and pending state**

  Save the original question, normalized plan, and bounded candidates. Return candidate number, full name, and employee ID.

- [ ] **Step 3: Add red–green tests for number, full-name, and employee-ID replies**

  Each valid reply revalidates the chosen employee, injects an authoritative ID filter, resumes the saved plan, and does not call the planner again.

- [ ] **Step 4: Add red–green tests for `both` and `all`**

  Select exactly the displayed candidates. Never expand the selection from a new fuzzy lookup.

- [ ] **Step 5: Add a failing invalid-choice retention test**

  Invalid numbers, names, and IDs keep pending state and perform zero downstream calls.

- [ ] **Step 6: Add stale-candidate revalidation**

  If a displayed employee disappeared or changed identity, retain clarification and refresh the safe candidate response without retrieval.

- [ ] **Step 7: Add persistent-selection and replacement tests**

  An identity-free follow-up uses the selected IDs. A newly supplied ID or name replaces the old selection.

- [ ] **Step 8: Preserve the compatibility wrapper and run all 20 roadmap acceptance scenarios**

- [ ] **Step 9: Run focused tests, all answer tests, Ruff, and the complete suite**

---

### Task 5: Gradio Session State and Safe Error Rendering

**Files:**
- Modify: `week5/new_app.py`
- Modify: `week5/test_new_app.py`

**Interfaces:**
- Produces: `chat_with_state(history, state)` for Gradio.
- Preserves: `chat(history)` and `format_context(context)`.

- [ ] **Step 1: Add a failing two-session isolation test**

  Create two independent `ConversationState` values, select an employee in one, and assert the other remains empty.

- [ ] **Step 2: Implement the hidden `gr.State` event path**

  Pass state into and out of `chat_with_state`; keep `chat` as the stateless compatibility wrapper.

- [ ] **Step 3: Add a failing expected-input-error rendering test**

  Assert clarification/validation results appear as assistant messages without escaping as Gradio exceptions.

- [ ] **Step 4: Add a failing unexpected-error rendering/logging test**

  Patch the answer boundary to raise, assert `logger.exception` behavior, and return a concise retry response with empty context.

- [ ] **Step 5: Run app tests, construct the Blocks UI with launch patched, then run full checks**

---

### Task 6: Filter-First Hybrid Verification and Related Split Parts

**Files:**
- Modify: `week5/new_implementation/answer.py`
- Modify: `week5/new_implementation/test_answer.py`

**Interfaces:**
- Produces: `expand_related_split_parts(chunks, backend) -> list[Result]` plus store-specific sibling fetchers.
- Consumes: resolved executable plans only.

- [ ] **Step 1: Add behavioral tests for Chroma filter-first semantic ranking**

  Use controlled embeddings and metadata; assert out-of-filter records never appear in results regardless of similarity.

- [ ] **Step 2: Add behavioral tests for PostgreSQL filtered-vector SQL parameters**

  Assert structured filter parameters precede ordering/limit correctly and invalid plans never reach SQL.

- [ ] **Step 3: Add a failing synthetic split-part expansion test**

  Retrieve part 2, provide stored parts 1–3, and assert the merged result contains all three exactly once in part order.

- [ ] **Step 4: Implement bounded Chroma sibling retrieval**

  Fetch only record IDs already present in semantic results and preserve unsplit results.

- [ ] **Step 5: Add a failing PostgreSQL sibling-query test and implement one `ANY(record_ids)` query**

- [ ] **Step 6: Insert expansion before reranking for semantic/hybrid modes only**

  Keep exact daily-record retrieval unchanged and keep the `FINAL_K` rerank gate.

- [ ] **Step 7: Run focused tests, all answer tests, Ruff, and the complete suite**

---

### Task 7: Replace Evaluation Corpus with APDC Attendance Cases

**Files:**
- Modify: `week5/new_evaluation/test.py`
- Modify: `week5/new_evaluation/eval.py`
- Replace contents: `week5/new_evaluation/tests.jsonl`
- Modify: `week5/new_evaluation/test_eval.py`

**Interfaces:**
- Extends: `TestQuestion` with optional normalized-plan, employee-ID, matched-count/calculation, answer-fact, and clarification expectations.
- Produces: evaluation functions for planner/retrieval/answer/clarification behavior.

- [ ] **Step 1: Add a failing corpus test asserting every case is APDC attendance-specific**

  Assert the corpus is non-empty, contains required attendance categories, and contains no `Insurellm`, product-contract, salary, or insurance-product questions.

- [ ] **Step 2: Replace all 150 rows with APDC attendance scenarios**

  Include the 20 clarification acceptance cases plus exact IDs, dates, departments, shifts, exceptions, leave, overtime, worked days, scheduled days, authorized records, malformed values, and no-match cases.

- [ ] **Step 3: Calculate fixed expectations with read-only PostgreSQL queries**

  Record dataset bounds and deterministic expected values in the cases. Add a read-only consistency check that reports dataset drift.

- [ ] **Step 4: Add planner/retrieval/answer/clarification evaluators**

  Evaluate normalized behavior rather than raw LLM plan formatting. Preserve existing MRR/nDCG functions where they remain meaningful.

- [ ] **Step 5: Remove stale evaluation code**

  Delete unused `db_name`, use shared configured model settings, correct obsolete async comments/docstrings, and retain only import paths covered by package/script smoke tests.

- [ ] **Step 6: Run evaluation unit tests and a bounded live positive/negative evaluation sample**

---

### Task 8: Final Cleanup and Verification

**Files:**
- Review all files changed in Tasks 1–7.
- Update: `week5/new_implementation/APDC_Attendance_Remaining_Improvements_16_to_35.md` only with verified completion status.

**Interfaces:**
- No new interfaces.

- [ ] **Step 1: Search for stale and duplicated code**

  Inspect imports, old resolver helpers, duplicate field sets, old prompt rules, unused constants, temporary diagnostics, `Insurellm`, `TODO`, and `TBD`.

- [ ] **Step 2: Run every focused regression class added by this plan**

- [ ] **Step 3: Run the complete automated suite**

  Run: `.venv\Scripts\python.exe -m unittest discover -s week5 -p 'test*.py' -v`

- [ ] **Step 4: Run static verification**

  Run Ruff check, Ruff format check, Python compilation, and `git diff --check` using the commands from the handoff.

- [ ] **Step 5: Run direct PostgreSQL truth comparisons**

  Verify worked/scheduled/authorized counts, explicit IDs, duplicate names, example candidates, numeric/date filters, and zero-result cases.

- [ ] **Step 6: Run live end-to-end LLM scenarios**

  Cover positive exact facts, negative/no-match input, malformed input, ambiguity, each clarification reply form, selection persistence/replacement, semantic/hybrid retrieval, and deterministic calculations.

- [ ] **Step 7: Prove the ambiguity gate**

  Capture zero calls to retrieval, aggregation, reranking, and final-answer generation until a valid choice is supplied.

- [ ] **Step 8: Build and start Gradio**

  Verify safe context rendering, hidden session state, two-session isolation, and user-facing error paths.

- [ ] **Step 9: Review the final workspace scope and update the roadmap**

  Confirm no unrelated files changed and document completed versus remaining improvements without overstating unverified behavior.
