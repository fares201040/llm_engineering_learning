# APDC Gradio Interpretation and Evidence Investigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make stateful identity follow-ups robust to planner interpretation-candidate variation, preserve truthful evidence through the answer and Gradio boundaries, and verify session isolation across a long live conversation.

**Architecture:** Keep the model plan advisory and normalize interpretation candidates at the deterministic plan boundary before any clarification or retrieval decision. A single valid candidate is compiled as the selected preset, two or more distinct candidates remain a clarification, and empty/invalid values fail through schema or deterministic validation. Retrieval continues to return the exact chunks used by deterministic and narrative answers, while Gradio renders only those returned chunks.

**Tech Stack:** Python 3.12, Pydantic, PostgreSQL, Chroma, LiteLLM/OpenAI, Gradio, unittest, Ruff.

**Spec:** `week5/new_implementation/APDC_Attendance_Remaining_Improvements_16_to_35.md`

## Global Constraints

- Preserve the public four-item `fetch_context()` tuple and public answer shapes.
- Do not special-case `who is this employee` or employee `A11017`.
- The LLM must not control identity, access scope, executable filters, calculations, or SQL.
- Only database-validated identities may enter `ConversationState.selected_employees`.
- Clarification must contain at least two distinct valid choices.
- Never fabricate context; render only evidence returned by the answer boundary.
- Do not commit private fixtures, credentials, generated databases, ingestion state, raw attendance data, identifiable employee facts, or the private long-run transcript.
- Preserve unrelated work and push without force only after verification and review.

---

### Task 1: Reproduce and Trace the Three-Turn Failure Boundary

**Files:**

- Inspect: `week5/new_implementation/answer.py`
- Inspect: `week5/new_app.py`
- Record locally only: private redacted trace/transcript outside tracked source paths

**Interfaces:**

- Consumes: `new_app.chat_with_state(history, state)`.
- Observes: raw `QueryPlan`, normalized plan, interpretation candidates, employee resolution, retrieval result, answer chunks, callback state, and rendered HTML.
- Produces: a root-cause statement supported by a deterministic reproduction.

- [x] **Step 1: Replay the exact three user turns through `chat_with_state()`**

Run the supplied September 2026 worked-day, scheduled-non-attendance, and identity-follow-up questions with a fresh `ConversationState` and record redacted boundary values.

- [x] **Step 2: Reproduce the planner variation deterministically**

Inject a raw identity-follow-up plan whose only interpretation candidate is a valid preset and confirm the current safe-error response occurs before employee resolution or retrieval.

- [x] **Step 3: Confirm context rendering is downstream, not independently broken**

Pass a real returned chunk to `new_app.format_context()` and confirm `<pre><code>` output; then confirm the error path receives an empty chunk list and therefore produces no evidence block.

---

### Task 2: Normalize Single, Ambiguous, and Invalid Interpretations

**Files:**

- Modify: `week5/new_implementation/test_answer.py`
- Modify: `week5/new_implementation/answer.py`

**Interfaces:**

- Consumes: `QueryPlan.interpretation_candidates` and `INTERPRETATION_PRESETS`.
- Produces: normalized executable `QueryPlan` or a controlled clarification/error.

- [x] **Step 1: Write the failing single-candidate stateful regression**

Create a stateful identity-follow-up test with a trusted selected employee and a raw plan containing `interpretation_candidates=["attendance_records"]`. Assert retrieval executes with the trusted employee filter, returns the identity evidence, preserves selected state, and does not return the “at least two” error.

- [x] **Step 2: Run the regression and verify RED**

Run:

```powershell
& '.venv\Scripts\python.exe' -m unittest `
  week5.new_implementation.test_answer.ClarificationStateTests.test_single_valid_interpretation_executes_without_clarification -v
```

Expected: FAIL because `normalize_query_plan()` raises `PlanValidationError` before employee resolution and retrieval.

- [x] **Step 3: Add focused normalization cases**

Assert one valid candidate applies its preset and clears candidates, duplicate candidates collapse before cardinality decisions, two distinct candidates remain pending clarification, and Pydantic rejects unknown candidate identifiers.

- [x] **Step 4: Implement the minimal deterministic normalization**

Deduplicate candidates in stable order. If there is one valid candidate, apply the corresponding preset only when no selected measure/predicates already own intent; if there are at least two, retain them for clarification. Continue to let schema validation reject invalid identifiers.

- [x] **Step 5: Run focused and adjacent tests to verify GREEN**

Run the new tests plus `PlanNormalizationTests`, `ClarificationStateTests`, `EmployeeResolutionTests`, `QuestionSpecificContextTests`, and `week5.test_new_app`.

---

### Task 3: Prove Evidence Propagation and App Rendering

**Files:**

- Modify: `week5/new_implementation/test_answer.py`
- Modify: `week5/test_new_app.py`
- Modify only if a failing test proves ownership: `week5/new_implementation/answer.py`
- Modify only if a failing test proves ownership: `week5/new_app.py`

**Interfaces:**

- Consumes: `ContextFetchResult.chunks` and `_answer_from_context()`.
- Produces: the same evidence list from deterministic/narrative answers and escaped Relevant Context HTML from `chat_with_state()`.

- [x] **Step 1: Add a deterministic-answer evidence assertion**

Return a concrete `Result` from `_fetch_context_result()` and assert `answer_question_with_state()` returns that exact evidence with its deterministic aggregation answer.

- [x] **Step 2: Add a narrative-answer evidence assertion**

Return a concrete `Result`, stub only the final completion, and assert the narrative answer returns the exact retrieved evidence.

- [x] **Step 3: Add an app callback rendering assertion**

Stub `answer_question_with_state()` with a concrete `Result`, call `chat_with_state()`, and assert the source/content are escaped and present under `<pre><code>`.

- [x] **Step 4: Run the tests and modify production only if RED identifies a defect**

Do not create summary evidence unless calculation retrieval genuinely has no row evidence. Any summary object must be truthful, typed, and derived from the executed calculation.

---

### Task 4: Live Gradio and 60-Turn Stateful Acceptance

**Files:**

- Verify: `week5/new_app.py`
- Record locally only: private redacted 60-turn transcript with expected scope/calculation, actual answer, selected state, pending state, and displayed evidence.

**Interfaces:**

- Consumes: running Gradio UI and two independent browser sessions.
- Produces: a redacted acceptance matrix covering at least 60 turns and fresh-session controls.

- [x] **Step 1: Start Gradio against the verified local stores**

Launch `week5/new_app.py`, wait for the HTTP endpoint, and use the browser UI for the exact three-turn replay.

- [x] **Step 2: Execute at least 60 turns**

Cover direct IDs/names, anaphora, employee replacement, unknown/ambiguous identities, worked/not-worked/absent/scheduled distinctions, record counts, status, overtime, percentages, grouping/ranking, dates/coverage, semantic questions, malformed/out-of-scope input, clarification/retry, and clear/reset behavior.

- [x] **Step 3: Interleave two sessions**

Alternate messages between two independent sessions and prove their history, selected employee, and pending clarification never cross.

- [x] **Step 4: Stop on each defect**

For every incorrect boundary, begin a new RED/GREEN cycle before continuing the scenario. Keep private names, IDs, and employee-linked facts out of tracked artifacts.

---

### Task 5: Risk Review, Verification, Documentation, and Delivery

**Files:**

- Modify: `week5/new_implementation/APDC_Attendance_Remaining_Improvements_16_to_35.md`
- Modify: `week5/new_implementation/APDC_Attendance_Knowledge_Base_Improvements_1_to_21_Complete_Documentation.md`
- Verify: all APDC implementation, app, evaluation, benchmark, and notebook files.

**Interfaces:**

- Produces: reviewed, verified, documented, credential-scanned commit on `main`.

- [x] **Step 1: Review each risky boundary**

Trace model-plan trust, contradictions, interpretation cardinality, pending-state mutation, follow-up scoping, retrieval routing, zero results, coverage wording, evidence propagation, HTML escaping, exception paths, ingestion publication guards, and clean-checkout fixture behavior.

- [x] **Step 2: Run complete verification**

Run 219+ implementation tests, 27+ app/evaluation/benchmark tests, Ruff check, Ruff format check, compileall, dataset verification, notebook validation, and `git diff --check`.

- [x] **Step 3: Request independent final review**

Require no Critical or Important findings. If findings exist, reproduce and fix each through a focused RED/GREEN cycle, then repeat review.

- [x] **Step 4: Update both authoritative documents**

Record the evidenced root cause, fix semantics, test counts, live UI/60-turn results, session isolation, and any environmental limitation without publishing private-derived facts.

- [x] **Step 5: Scan and deliver**

Inspect the complete outgoing diff and staged file list, scan for tokens/credentials/private fixtures, configure the requested Git author, commit only authorized files, and push `main` without force.
