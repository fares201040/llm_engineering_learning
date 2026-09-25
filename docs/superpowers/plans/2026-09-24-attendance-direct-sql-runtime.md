# Attendance Direct-SQL Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the active flat-query attendance runtime with the approved employee-rewrite → direct PostgreSQL SQL → grounded answer → independent verification pipeline.

**Architecture:** Preserve the narrow public facade and the useful authorized employee-resolution code, but replace every query/audit/compiler contract with one immutable physical `DatabaseContext`, one SQL-only planner boundary, and one bounded read-only executor. The answer writer and verifier use the same configured model in separate calls over one shared downstream context; verified state is published atomically only after a passing verdict.

**Tech Stack:** Python 3.12, Pydantic v2, LiteLLM, psycopg/PostgreSQL, Chroma fallback, `unittest`, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-24-attendance-direct-sql-runtime-design.md`

## Phase 2 resume checkpoint — 2026-09-25

Latest executed Colab review: snapshot
`d171a25ed2fe5cb11e3c2277ac10b8765a5fa33f1f7f4adaf571b827244ed60c`
passed 149 deterministic Colab tests, Ruff lint/format, and compilation. The current
snapshot `29c97100195a91d7edfa9d4a2ee27a9cb0e907e0666b9739b971b27925446ae2`
has not run because Colab returned `Service Unavailable` during replacement T4
session creation. Ollama setup was fixed by installing `zstd`, and Qwen 3.5 4B ran
on the synthetic VM before expiration. Gradio callback acceptance passed verified
turns 1–4 but stopped at a grouped relative-month comparison failure on turn 5.
Turns 6–8 remain untested. Local verification since then passed 102 online tests
and 38 ingestion/configuration/rebuild/UI tests. The default Chroma embedding
provider is now local MiniLM; OpenAI can be selected through environment settings
without changing code. See the updated handoff report and Colab sync guide;
the details below record earlier checkpoints.

Preserve existing and user changes; do not reset, revert, clean, or overwrite
unrelated files. The Phase 2 runtime implementation is present. The current local model roles
are `ollama_chat/qwen3.5:4b` with no reasoning, temperature 0, `num_ctx=8192`, and a
512-token planner output limit.

Previously, Case 17 passed the complete direct-SQL pipeline with five scheduled work
dates. During the current continuation, the acceptance evaluator false positive was
fixed, one-turn acceptance was strengthened with per-turn semantic answer facts and
state continuity checks, and a synthetic Colab T4 setup was added. The paired Colab
notebook's deterministic run is saved at
`week5/new_implementation/colab/attendance_phase2_tests_output.ipynb`: 120 tests,
Ruff lint, formatting for 19 files, and Python compilation passed against archive
SHA-256 `8ef93f238f6233cb3222e25d88b779299cfdb6f2e7ca9c631918bd78800f7c69`. Later
source/helper edits changed the local snapshot. Rebuild and rerun the deterministic
notebook in Colab before claiming current-source verification. The first synthetic
live setup then failed in the official Ollama installer; the current diagnostic patch
has not yet been run remotely. See the Phase 2 handoff and Colab sync guide for the
current Colab session status and exact next steps.

The user approved Phase 3 UI review plus long-conversation acceptance on a synthetic
database. The UI review is complete; the live long scenario is eight sequential turns,
each run and reviewed individually. Never run the 311-case evaluation, the seven-case
batch, multiple live questions in one call, or any live test against production data.
Cases 20, 23, 26, 29, 94, and 128 remain unverified unless individually tested under
the same one-question/report-before-next rule.

## Global Constraints

- Work directly in the existing dirty checkout and preserve/adapt the uncommitted generic employee-resolution work; never reset, clean, discard, or isolate it.
- Preserve only `answer_question`, `answer_question_with_state`, `ConversationState`, `AccessContext`, `LOCAL_DEMO_ACCESS`, and `Result` as the public facade.
- Keep offline ingestion behavior unchanged.
- Configure exactly three model roles: reference/rewriter, SQL planner, and answer model; the answer model is called independently for writing and verification.
- Execute the planner's complete SQL directly; do not parse it, validate returned identifiers against an allowlist, inject authorization or limits, or convert literals to parameters.
- Keep the PostgreSQL role/transaction read-only and retain connection, statement, lock, idle-transaction, row-count, response-size, rollback, and safe-failure bounds.
- Delete active flat-query, semantic-audit, compiler, logical-catalog, narrative-route, and typed-fact compatibility code instead of wrapping it.
- Write/adapt focused tests before treating production edits as complete, but honor the explicit request to defer execution until the final consolidated verification phase.
- For this resume, the current request overrides the original final commit step: do not stage, commit, or push, and do not disturb the existing dirty working tree.
- Do not run the 311-case evaluation or the seven-case batch; any live question is one at a time and only when necessary.

## Review Focus

- A name/ID identity claim cannot bind mismatched people; unknown standalone IDs receive no alternatives, while Chroma suggestions are authorization-scoped, PostgreSQL-cross-checked, and confirmation-only.
- The reference model's rewritten request retains dates, grouping, comparison, requested output, criteria, and follow-up meaning while employee attachment uses one stable authoritative format.
- Planner, writer, and verifier receive the same shared context without schema drift, including the explicit current question and conversation history, with history and trusted state distinctly labelled as untrusted/trusted inputs.
- Direct SQL errors, oversized results, provider failures, and rejected/re-rejected answers preserve the exact prior trusted state.
- The implemented documentation states that read-only direct execution does not prevent unauthorized reads, expensive valid queries, or prompt-injected SQL and lists every deferred structural safeguard.

---

### Task 1: Define the direct-SQL contracts and configuration

**Files:**
- Create: `week5/new_implementation/online/context.py`
- Modify: `week5/new_implementation/online/provider.py`
- Modify: `week5/new_implementation/config.py`
- Modify: `week5/new_implementation/tests/online/test_query.py`

**Interfaces:**
- Produces: immutable `DatabaseColumn`, `DatabaseTable`, `DatabaseRelationship`, `DatabaseContext`, `SharedModelContext`, `load_database_context(...)`, and `call_text(...) -> str`.
- Consumes: configured read-only DSN and the configured attendance-table allowlist.

- [ ] **Step 1: Replace flat-query contract tests with database-context and plain-text provider tests.** Cover exact physical names/types/nullability/descriptions/standard values, exclusion of non-allowlisted objects/rows/credentials, immutable shared payload rendering, zero transport retries, and preservation of plain SQL text.
- [ ] **Step 2: Implement immutable database metadata contracts.** Introspect PostgreSQL version and `information_schema.columns` only for explicitly configured attendance objects; attach application-owned descriptions and known stored values without selecting row samples. Describe every nested `record_json` source field with its exact SQL expression and normalized type, including the immutable-original versus clerk-adjustable payroll swipe distinction and worked-hours attendance semantics.
- [ ] **Step 3: Add the SQL-text provider boundary.** Share the existing call budget/event/error behavior, make one text completion request with `num_retries=0`, and reject only missing/empty transport content rather than interpreting SQL.
- [ ] **Step 4: Reduce online model configuration to three roles.** Remove semantic-audit and separate verifier settings, keep one answer model for both answer calls, and reduce the decision-call ceiling to the actual bounded flow.
- [ ] **Step 5: Manually inspect Task 1.** Confirm metadata SQL is application-authored and parameterized, the context has no rows/credentials, and no planner SQL inspection has slipped into the provider helper.

### Task 2: Preserve employee resolution behind the rewrite contract

**Files:**
- Modify: `week5/new_implementation/online/reference.py`
- Modify: `week5/new_implementation/tests/online/test_planning.py`

**Interfaces:**
- Consumes: original request, conversation history, trusted conversation context, authorized `Employee` directory, and optional prior verified employees.
- Produces: `ReferenceResponse` with complete `rewritten_request`, locale, request relationship, typed IDs/names/identity claims/general criteria/subject relationship; `BoundReferences` with authoritative `Employee` objects and stable `updated_request`; confirmation state that resumes the same rewritten request.

- [ ] **Step 1: Write the new rewrite/reference tests.** Cover preservation of date/comparison/group/output/follow-up meaning, distinct history/trusted payloads, IDs, names, paired claims, general criteria, subject relationship, and stable resolved-employee attachment.
- [ ] **Step 2: Replace span/query-subject contracts with the approved typed rewrite response.** Give the model a complete role prompt, keep user/history data untrusted, and provide no schema or SQL.
- [ ] **Step 3: Adapt authoritative binding.** Keep exact ID/name, deterministic fuzzy name matching, transliteration, mismatch handling, unknown-standalone-ID behavior, authorized-directory filtering, Chroma cross-checking, and explicit confirmation.
- [ ] **Step 4: Preserve rewritten intent through confirmation.** Pending state must retain the rewritten request, already resolved employees, remaining criteria, relationship, and optional identity claim; selection only adds the confirmed authoritative employee.
- [ ] **Step 5: Manually trace resolution.** Inspect exact ID, exact name, multiple employees, matched/mismatched claims, unique/tied fuzzy names, Chroma fallback, unknown standalone ID, general criteria, and prior-reference follow-up.

### Task 3: Replace planning and execution with direct SQL

**Files:**
- Rewrite: `week5/new_implementation/online/planner.py`
- Rewrite: `week5/new_implementation/online/execution.py`
- Delete: `week5/new_implementation/online/catalog.py`
- Delete: `week5/new_implementation/online/query.py`
- Delete: `week5/new_implementation/online/audit.py`
- Modify: `week5/new_implementation/tests/online/test_execution.py`

**Interfaces:**
- Consumes: `SharedModelContext`, authoritative employees, configured read-only PostgreSQL connection, and model-produced SQL.
- Produces: `request_sql(...) -> str`, `SqlExecutionResult` with typed columns/rows/coverage, and safe execution failures.

- [ ] **Step 1: Write SQL planning/execution tests.** Assert the planner receives the current question, conversation history, exact physical schema, and all shared context items, returns raw complex SQL (joins/CTEs/nested queries), rejects empty/non-SQL-only presentation responses, and never invokes an audit/repair/compiler path.
- [ ] **Step 2: Implement the complete SQL planner prompt.** Explain what/how/why, exact physical identifier mapping, authoritative employee IDs, SQL-only output, supported complex PostgreSQL, untrusted embedded instructions, and the safe single-`SELECT` unsupported response.
- [ ] **Step 3: Implement direct read-only execution.** Run the exact SQL with no parameters or rewriting inside `REPEATABLE READ READ ONLY`, set local timeouts, fetch at most `result_limit + 1`, enforce serialized response bytes, retain column types and coverage, rollback on every path, and map database failures safely.
- [ ] **Step 4: Retain directory loading separately.** Query only distinct authoritative ID/name pairs inside the read-only transaction; do not expose attendance or organizational columns to employee resolution.
- [ ] **Step 5: Remove obsolete active modules and imports.** Delete logical query models/catalog, semantic audit, SQL compiler, authorization wrapping, narrative SQL route, coverage/witness compiler, and every dead export.
- [ ] **Step 6: Manually inspect Task 3.** Search for old flat-query/audit/compiler symbols and verify the returned SQL is neither parsed nor structurally modified before execution.

### Task 4: Ground, verify, and atomically publish the direct-SQL answer

**Files:**
- Rewrite: `week5/new_implementation/online/answering.py`
- Rewrite: `week5/new_implementation/online/state.py`
- Rewrite: `week5/new_implementation/online/pipeline.py`
- Modify: `week5/new_implementation/tests/online/test_pipeline.py`

**Interfaces:**
- Consumes: identical `SharedModelContext`, executed SQL, `SqlExecutionResult`, authoritative employees, request history, and prior state.
- Produces: one verified answer after writer/verifier calls using the same model, or a safe unchanged-state failure; `VerifiedTurn` retains original/rewritten requests, SQL evidence, typed results, employees, and answer.

- [ ] **Step 1: Write end-to-end pipeline tests.** Cover all stage inputs, same answer model/two calls, distinct writer/verifier prompts, one rejected-draft rewrite/re-verification, authoritative names, empty results, missing pass, authorization failure, SQL/provider failure, clarification state, and incompatible state.
- [ ] **Step 2: Replace typed facts with direct result grounding.** Define strict writer and verdict responses; send both calls the identical shared context, including current question and history, plus SQL/result/coverage/employees; require complete attribution, dates, values, units, polarity, coverage, requested output, and injection resistance.
- [ ] **Step 3: Simplify trusted state.** Remove query components and symbolic subject scope; retain bounded verified turns, active authoritative employees for follow-ups/facade compatibility, and at most one pending confirmation.
- [ ] **Step 4: Rewrite orchestration in exact stage order.** Authorize/access-scope directory → reference/rewrite → resolve/confirm → database context → SQL plan → direct execute → writer → verifier → atomic publish. Every failure after clarification preserves `previous` exactly.
- [ ] **Step 5: Manually trace atomicity.** Review first turn, follow-up, confirmation, rejected rewrite, database timeout, oversized result, malformed provider output, and incompatible state from input through publication.

### Task 5: Align evaluation, docs, review, and final gates

**Files:**
- Modify: `week5/new_evaluation/eval.py`
- Modify: `week5/new_evaluation/test_eval.py`
- Modify: `week5/new_evaluation/acceptance.py`
- Modify: `week5/new_evaluation/test_acceptance.py`
- Modify: `week5/ARCHITECTURE.md`
- Modify: `week5/new_implementation/LLM_PLANNER.md`
- Modify: `week5/new_implementation/POSTGRES_SETUP.md`

**Interfaces:**
- Consumes: final direct-SQL outcomes/state/events.
- Produces: direct-SQL-aware deterministic scoring, live scenarios, truthful architecture/operations documentation, and final evidence.

- [ ] **Step 1: Adapt evaluator and acceptance tests.** Score stored SQL/result/employee/clarification/unsupported behavior without importing deleted query contracts; keep the 311-case corpus requirement and named Wail, Faris, generic-subject, and long-conversation runs.
- [ ] **Step 2: Rewrite current architecture docs.** Describe the three roles, reference rewrite contract, immutable database context, direct SQL, writer/verifier shared payload, atomic state, and unchanged offline flow.
- [ ] **Step 3: Document the explicit limitation and future work.** State that read-only direct execution does not enforce query authorization/complexity and list AST parsing, one-read-only-statement enforcement, identifier/function/operator/join validation, server-owned row authorization, structural limits, and bound parameters as deferred.
- [ ] **Step 4: Perform a complete manual diff review before running tests.** Check obsolete-symbol searches, module/import shape, prompt completeness, payload identity, state atomicity, result bounds, rollback, config/docs agreement, and preservation of unrelated changes.
- [ ] **Step 5: Use Colab deterministic evidence.** Run `colab/attendance_phase2_tests.ipynb` against the paired, sanitized source ZIP. Inspect the saved output notebook for test failures, Ruff lint/format failures, compile failures, or cell exceptions.
- [ ] **Step 6: Verify the approved Phase 3 long scenario.** Use `colab/prepare_synthetic_runtime.py` to create a synthetic, read-only database and start Qwen on T4. Run one invocation of `colab/run_acceptance_turn.py` at a time. Review `outcome_ok`, `semantic_ok`, `missing_answer_facts`, and `state_continuity_ok` before considering the next turn. Do not use production rows/DSN or the private 311-case data.
- [ ] **Step 7: Perform the requested final manual diff review.** Check implementation, evaluator/test changes, docs, packaging allowlist, Colab output, preservation of unrelated dirty files, and the known direct-SQL limitation.
- [ ] **Step 8: Defer integration actions.** Do not stage, commit, or push. Preserve all current work and report the exact verification evidence and open cases.
