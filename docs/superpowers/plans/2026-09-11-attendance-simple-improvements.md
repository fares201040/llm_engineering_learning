# APDC Attendance Simple Improvements Implementation Plan

> **For agentic workers:** This plan is executed inline in the current session with test-first checkpoints.

**Goal:** Reorder the remaining attendance roadmap from low-risk improvements to high-complexity architectural work, then implement and verify the low-risk improvements without pre-empting the roadmap-owned identity, retrieval, or modularization work.

**Architecture:** Keep the current `answer.py` and `ingest.py` public constants/functions compatible while centralizing environment parsing in a small shared config module. Add deterministic date-range parsing, safe source-change detection, explicit rerank skipping, structured logging, and evaluation wiring fixes. Leave identity clarification, numeric SQL operator semantics, incremental processing, recovery, and module splitting for their reordered roadmap positions.

**Tech Stack:** Python 3.11+, Pydantic, Chroma, PostgreSQL/psycopg, OpenAI/LiteLLM, openpyxl, unittest.

**Spec:** `week5/new_implementation/APDC_Attendance_Remaining_Improvements_16_to_35.md`

## Global Constraints

- Preserve existing uncommitted user changes outside the week 5 new implementation.
- Do not silently select among ambiguous employee identities; that remains Improvement 20.
- Do not change numeric/date `contains` SQL behavior; that remains Improvements 16/17.
- Keep `uv run new_app.py` from `week5` working.
- Every production behavior change gets a regression test before implementation.

### Task 1: Reorder the roadmap

**Files:**
- Modify: `week5/new_implementation/APDC_Attendance_Remaining_Improvements_16_to_35.md`

- [ ] Replace the recommended order and priority groups with a complexity-first order: source detection/config/logging/tests/date parsing/evaluation first; retrieval-quality and planner work next; identity, recovery, and modularization last.
- [ ] State explicitly which known defects remain deferred to their owning improvement.

### Task 2: Centralized configuration and structured logging

**Files:**
- Create: `week5/new_implementation/config.py`
- Modify: `week5/new_implementation/answer.py`
- Modify: `week5/new_implementation/ingest.py`
- Test: `week5/new_implementation/test_answer.py`
- Test: `week5/new_implementation/test_ingest_postgres.py`

- [ ] Add environment-backed parsing helpers and use them for operational constants while preserving module-level names used by existing callers/tests.
- [ ] Replace runtime `print()` diagnostics with module loggers and configure CLI ingestion logging in `main()`.
- [ ] Test invalid/default environment parsing and log-call behavior without making network calls.

### Task 3: Natural date ranges and rerank skipping

**Files:**
- Modify: `week5/new_implementation/answer.py`
- Test: `week5/new_implementation/test_answer.py`

- [ ] Add deterministic parsing for `between <date> and <date>` and `from <date> to <date>`, including month names and omitted years.
- [ ] Add a rerank gate that does not invoke the LLM reranker when at most `FINAL_K` chunks are available.
- [ ] Test positive, malformed, reversed, and boundary-date cases.

### Task 4: Source change detection and evaluation wiring

**Files:**
- Modify: `week5/new_implementation/ingest.py`
- Modify: `week5/new_evaluation/eval.py`
- Test: `week5/new_implementation/test_ingest_postgres.py`

- [ ] Persist a sidecar source signature after successful Excel conversion and regenerate JSONL when source files are added, removed, or changed.
- [ ] Make the evaluator consume the tuple returned by `fetch_context` and load its own `new_evaluation/tests.jsonl` module reliably from either package or script execution.
- [ ] Test unchanged/changed source decisions and tuple extraction without external API calls.

### Task 5: Full scenario verification

**Files:**
- No production changes expected.

- [ ] Run focused unit tests from repository root.
- [ ] Run compile checks and direct script import/startup smoke checks.
- [ ] Run read-only PostgreSQL/Chroma positive and negative scenarios where configured.
- [ ] Record deferred roadmap-owned defects separately from regressions introduced by these changes.
