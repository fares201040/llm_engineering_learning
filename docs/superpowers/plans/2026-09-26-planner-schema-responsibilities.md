# Planner Schema Responsibilities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move schema/domain judgment to the SQL planner, give it the complete schema at 64K context, and make manual-swipe questions reliably produce the correct SQL.

**Architecture:** Keep the reference boundary focused on request rewriting and employee identity extraction without schema input or an unsupported veto. Serialize the complete shared database context for the planner, preserve typed-column preference in its prompt, and enforce the four-pair manual-swipe meaning at the SQL semantic boundary.

**Tech Stack:** Python 3.12, Pydantic, LiteLLM/Ollama, PostgreSQL, unittest, Colab A100

**Spec:** `docs/superpowers/specs/2026-09-26-planner-schema-responsibilities.md`

## Global Constraints

- Avoid unrelated refactors and preserve existing confirmation and authorization behavior.
- Use test-driven development for every production behavior change.
- Run model-dependent tests only on Colab A100.
- Commit all relevant tracked local changes after verification.

## Review Focus

- A general employee criterion with no explicit identity must reach the planner without clarification.
- An explicit employee plus an additional criterion must preserve both parts of the request.
- Confirmation must attach the selected authoritative employee and then answer the original request.
- A multi-request message must preserve every compatible clause.
- An unrelated request must be rejected through the planner protocol rather than the reference model.

---

### Task 1: Narrow the reference boundary

**Files:**
- Modify: `week5/new_implementation/online/reference.py`
- Modify: `week5/new_implementation/online/pipeline.py`
- Test: `week5/new_implementation/tests/online/test_planning.py`
- Test: `week5/new_implementation/tests/online/test_pipeline.py`

**Interfaces:**
- Produces: reference payload without database schema; reference decisions cannot stop the planner as unsupported.
- Consumes: existing deterministic employee binding and confirmation flow.

- [ ] Write tests proving the reference receives no schema and an unrelated request reaches the planner.
- [ ] Run the focused tests and verify they fail for the existing schema/unsupported behavior.
- [ ] Remove the schema payload and unsupported veto from the reference path; simplify the system prompt consistently.
- [ ] Run focused tests and the online unit suite.

### Task 2: Send the complete planner schema and guard manual-swipe semantics

**Files:**
- Modify: `week5/new_implementation/online/planner.py`
- Modify: `week5/new_implementation/online/pipeline.py`
- Test: `week5/new_implementation/tests/online/test_planning.py`
- Test: `week5/new_implementation/tests/online/test_pipeline.py`

**Interfaces:**
- Consumes: `SharedModelContext.model_payload()`.
- Produces: planner payload containing every PostgreSQL column and every nested `record_json.json_fields` entry; semantic retry signal for incomplete manual-swipe SQL.

- [ ] Write tests asserting all nested fields are present and incomplete manual-swipe comparisons are rejected.
- [ ] Run focused tests and verify expected failures.
- [ ] Remove request-based schema projection and add the minimal four-pair semantic check and prompt meaning.
- [ ] Run focused tests and the online unit suite.

### Task 3: Give only the SQL planner a 64K local context

**Files:**
- Modify: `week5/new_implementation/online/provider.py`
- Modify: `week5/new_implementation/tests/online/test_query.py`
- Modify: `week5/new_implementation/LLM_PLANNER.md`
- Modify: `week5/new_implementation/POSTGRES_SETUP.md`

**Interfaces:**
- Produces: local `sql_planner` calls use `num_ctx=65536`; reference and answer calls remain `8192`.

- [ ] Write provider tests for planner and non-planner context sizes.
- [ ] Run them and verify the planner case fails at 8192.
- [ ] Implement stage-specific local model options and update runtime documentation.
- [ ] Run provider/config tests and the online unit suite.

### Task 4: Verify behavior and model quality

**Files:**
- Modify only if failures demonstrate a root cause in the scoped behavior.

**Interfaces:**
- Consumes: completed reference, planner-schema, semantic, and context changes.
- Produces: local deterministic evidence plus Colab A100 model/evaluator evidence.

- [ ] Run the complete local unit suite and inspect every failure.
- [ ] Run the exact manual-swipe conversation on Colab A100, including any confirmation turn, and record generated SQL attempts.
- [ ] Run evaluator cases 100-149 on Colab A100; investigate failures by root cause and add RED-to-GREEN regression tests before fixes.
- [ ] Re-run affected cases and the full local unit suite, review the system prompts for responsibility consistency, then commit all scoped files.

