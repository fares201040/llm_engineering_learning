# Attendance Online v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mixed attendance runtime with one small, secure, testable `attendance-online/v1` pipeline.

**Architecture:** A narrow compatibility facade delegates to a typed one-unit pipeline. A flat attendance query is deterministically materialized, audited, authorized, compiled, executed, grounded, verified, and atomically published.

**Tech Stack:** Python 3.12, Pydantic v2, LiteLLM structured outputs, psycopg/PostgreSQL, Chroma, `unittest`, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-23-attendance-online-v1-design.md`

## Global Constraints

- Work in the current dirty checkout; never reset, clean, or overwrite unrelated changes.
- Use RED/GREEN TDD for every production behavior.
- Preserve only the narrow app-facing API.
- Keep offline ingestion behavior unchanged.
- Use `attendance-online/v1` as the only runtime/state version.
- Use one semantic unit and one attendance source per turn.
- Bind identity and authorization deterministically; all SQL literals remain parameters.
- Preserve the audit asymmetry, grounded-answer verdict, atomic state, and eleven-call ceiling.
- Stage exact files, inspect every staged diff, and create the requested staged commits.

## Review Focus

- A unique fuzzy candidate must require confirmation; unresolved written references
  may use access-scoped Chroma options, but no Chroma candidate may bind without user
  selection and authoritative PostgreSQL cross-checking.
- A supplied name and ID must be one identity claim. The ID is primary, the name is
  checked against PostgreSQL, and mismatched pairs never reach query planning.
- The first reference boundary owns employees, criteria spans, union/intersection, explicit
  all-authorized intent, and reuse of a verified prior subject. Authorization remains
  a separate server-owned intersection.
- Nested `not`/`any` filters must retain meaning through validation, SQL, and evaluation.
- Empty or missing employee scope must never compile to broader access.
- Initial-audit unavailability and final-audit unavailability must have different outcomes.
- Any answer-verdict failure must preserve the exact pre-turn trusted state.

---

### Task 1: Persist design and characterize retained behavior

**Produces:** Approved spec/plan files and baseline behavior/safety evidence.

- [ ] Record status, HEAD, and targeted baseline results without mutating or cleaning user files.
- [ ] Persist the approved design and implementation plan.
- [ ] Add characterization tests only where retained behavior lacks focused coverage.
- [ ] Run the characterization tests and inspect all failures.
- [ ] Commit `docs: specify attendance online v1 architecture`.

### Task 2: Build the flat query core

**Produces:** `online.catalog`, `online.query`, and the single enforced `QueryLimits`.

- [ ] Write failing strict-contract, provenance, inheritance, typing, bounds, and unsupported-operation tests.
- [ ] Run them and confirm failures are caused by the missing flat query API.
- [ ] Implement shared attendance field metadata, provider-safe catalog rendering, flat query models, include-only materialization, and validation.
- [ ] Re-run focused and affected ingestion-schema tests.
- [ ] Commit `refactor: add flat attendance query core`.

### Task 3: Replace provider planning boundaries

**Produces:** `online.provider`, `online.reference`, `online.planner`, and `online.audit`.

- [ ] Write failing tests for exact/prior identity, fuzzy confirmation, scoped Chroma
  fallback options, one-unit responses, explicit zero retries, bounded repairs, audit
  policy, and call accounting.
- [ ] Run them and confirm expected RED failures.
- [ ] Implement the shared provider helper and the reference, planner, and audit boundaries.
- [ ] Give every system prompt a concise role/task statement; return explicit employee
  and generic criteria spans, preserve subject union/intersection, and validate
  name-and-ID claims deterministically.
- [ ] Re-run focused suites and the provider-budget suite.
- [ ] Commit `refactor: replace attendance planning boundaries`.

### Task 4: Implement secure execution and grounded answers

**Produces:** `online.execution` and `online.answering`.

- [ ] Write failing authorization, parameterization, Boolean SQL, grouping/HAVING, coverage/witness, narrative-scope, grounded-fact, and verdict tests.
- [ ] Run them and confirm expected RED failures.
- [ ] Implement binding, compilation, bounded read-only execution, typed results/facts, answer generation, validation, and verdict.
- [ ] Re-run focused security, compiler, execution, and answer suites.
- [ ] Commit `refactor: add authorized attendance execution`.

### Task 5: Switch the app to the atomic pipeline

**Produces:** `online.state`, `online.pipeline`, the narrow `answer.py` facade, and app/config integration.

- [ ] Write failing outcome, state publication, pending confirmation, failure mapping, event, facade, and app tests.
- [ ] Run them and confirm expected RED failures.
- [ ] Implement minimal state, typed outcomes, stage orchestration, centralized error mapping, atomic publication, facade adaptation, and active-only configuration.
- [ ] Re-run pipeline, state, facade, app, and affected configuration tests.
- [ ] Commit `refactor: switch app to attendance online v1`.

### Task 6: Consolidate evaluation and acceptance

**Produces:** Typed observer-based evaluation, complete behavior scoring, and one acceptance CLI.

- [ ] Write failing evaluator tests for nested Boolean logic, grouped values, record IDs, clarification, unsupported codes, and complete-run status.
- [ ] Run them and confirm the current evaluator fails for the intended reasons.
- [ ] Replace trace interception, consolidate acceptance scripts, and update all 311 expectations for the reduced capability set.
- [ ] Run evaluation unit tests and representative offline cases.
- [ ] Commit `test: consolidate attendance online evaluation`.

### Task 7: Remove obsolete architecture

**Produces:** Zero active legacy/shadow/canary, v1/v3, graph/macro, duplicate-state, or direct-script compatibility code.

- [ ] Add stale-symbol/import assertions and watch them fail.
- [ ] Delete obsolete production modules, facades, tests, diagnostics, partial results, checkpoint, and superseded planner documents.
- [ ] Update remaining imports and run the deterministic suite.
- [ ] Inspect every stale-search hit and staged deletion.
- [ ] Commit `refactor: remove legacy attendance architecture`.

### Task 8: Publish docs and final evidence

**Produces:** Canonical architecture/operations docs and complete verification evidence.

- [ ] Replace the architecture page with compact online/offline Mermaid diagrams and rewrite `LLM_PLANNER.md`.
- [ ] Run package tests, evaluation/app tests, compileall, Ruff check/format-check, and `git diff --check`.
- [ ] Run the complete 311-case behavior evaluation, Wail unavailable-audit scenario, Faris four-turn scenario, and long conversation.
- [ ] Inspect final status, diff, file sizes, stale searches, and staged content.
- [ ] Commit `docs: publish simplified attendance architecture`.

## Required Commands

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s week5/new_implementation/tests -t . -p "test_*.py" -v
.\.venv\Scripts\python.exe -m unittest week5.new_evaluation.test_eval week5.new_evaluation.test_acceptance week5.test_new_app -v
.\.venv\Scripts\python.exe -m compileall -q week5/new_implementation week5/new_evaluation week5/new_app.py
.\.venv\Scripts\ruff.exe check week5/new_implementation week5/new_evaluation week5/new_app.py
.\.venv\Scripts\ruff.exe format --check week5/new_implementation week5/new_evaluation week5/new_app.py
git diff --check
```
