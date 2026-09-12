# Request for the Next AI Agent — APDC Answer Quality

Copy the message below into the next AI-agent task.

---

Please resume the APDC attendance assistant work with the eventual goal of
improving answer quality. Your first turn is onboarding and analysis only: do
not edit files, run ingestion, invoke provider-backed bulk evaluations, commit,
push, reset, clean, or otherwise mutate the repository. Read the authoritative
documents and code, inspect the existing dirty worktree without changing it,
summarize your understanding and proposed investigation order, then stop and
wait for my next request.

Do not edit files during this onboarding turn.

Repository and delivery boundary:

- Workspace: `D:\projects\llm_engineering_ed_donner\llm_engineering`
- Correct repository: `https://github.com/fares201040/llm_engineering_learning`
- Never add, restore, or push to `ed-donner/llm_engineering`.
- Current published baseline: commit
  `7b5ef62531f384c9b453decd301613d12b406f92` on `main`.
- Preserve all existing uncommitted work. In particular,
  `week5/new_evaluator.py`, `week5/test_new_evaluator.py`, and the current
  documentation updates are intentional. Do not reset or clean them.

Read these documents completely, in this order:

1. `week5/new_implementation/APDC_Attendance_Remaining_Improvements_16_to_35.md`
   — primary current handoff and highest-authority prose document.
2. `week5/new_implementation/APDC_Attendance_Knowledge_Base_Improvements_1_to_21_Complete_Documentation.md`
   — full system history, data flow, answering flow, and current architecture.
3. `week5/new_implementation/APDC_Attendance_Remaining_Improvements_Implementation.md`
   — engineering decisions, root causes, implementation phases, and remaining
   production gaps.
4. `docs/superpowers/plans/2026-09-12-apdc-foundation-and-answer-correctness-implementation.md`
   — detailed implementation plan and safety boundaries.
5. `docs/superpowers/plans/2026-09-12-apdc-gradio-interpretation-and-evidence-investigation.md`
   — the completed state/evidence investigation and acceptance workflow.

Then understand the `week5/new_*` code in this order:

1. `week5/new_implementation/attendance_schema.py`
2. `week5/new_implementation/answer.py`
3. `week5/new_app.py`
4. `week5/new_evaluation/test.py`
5. `week5/new_evaluation/eval.py`
6. `week5/new_evaluation/benchmark.py`
7. `week5/new_evaluator.py`
8. `week5/new_implementation/source_ingestion.py`
9. `week5/new_implementation/ingest.py`
10. `week5/new_implementation/ingestion_state.py`
11. `week5/new_implementation/chroma_client.py`
12. The adjacent `test_*.py` files that specify each contract.

## What the scripts do

- `attendance_schema.py` is the deterministic contract. It defines typed
  attendance fields, allowed filters/operators, measures, business predicates,
  interpretation presets, validation, and compilation into executable intent.
  The LLM may propose a plan, but this module and deterministic code own what is
  executable.
- `answer.py` owns question preflight, LLM `QueryPlan` creation, deterministic
  normalization, date/measure/predicate compilation, catalog and employee
  resolution, clarification state transitions, exact/hybrid/semantic routing,
  PostgreSQL and Chroma retrieval, calculations, evidence selection, and final
  deterministic or narrative answers. Trace public flows through
  `fetch_context()` and `answer_question_with_state()`.
- `new_app.py` is the APDC Gradio chat application. It keeps
  `ConversationState` in `gr.State`, passes chat history and state into
  `answer_question_with_state()`, displays only returned evidence, escapes
  untrusted content, and clears chat/context/trusted state together.
- `new_evaluation/test.py` loads the authorized local APDC JSONL evaluation
  corpus. The corpus and dataset manifest contain private-derived expectations
  and are intentionally ignored by Git.
- `new_evaluation/eval.py` evaluates deterministic plan behavior, employee
  scope, counts/calculations, clarifications, normalized results, retrieval MRR
  and nDCG, keyword coverage, and LLM-judged answer quality. It also verifies
  the live PostgreSQL dataset fingerprint.
- `new_evaluation/benchmark.py` measures repeatable local components and the
  optional read-only PostgreSQL exact path without pretending to benchmark
  provider stages.
- `new_evaluator.py` is the dedicated APDC Gradio evaluation dashboard. It
  wraps `new_evaluation` with Dataset, Behavior, Retrieval, and Answer Quality
  tabs. Its maximum-case control defaults to 10; `0` means all cases. It
  isolates case failures and avoids exposing exception details.
- `source_ingestion.py` discovers XLSX/CSV sources, computes stable source
  identities, parses partitions, classifies domain/schema deterministically,
  and separates valid attendance, invalid attendance, and unknown quarantine.
- `ingest.py` validates and canonicalizes records, creates stable IDs and daily
  plus employee-period chunks, publishes PostgreSQL raw/typed data, updates the
  attendance-only Chroma projection, and coordinates incremental generations.
- `ingestion_state.py` provides the SQLite generation/checkpoint ledger,
  activation/recovery rules, and run-wide advisory lock.
- `chroma_client.py` lazily owns Chroma client/collection access so imports and
  clean-checkout tests do not trigger unwanted persistent side effects.

## Architecture to understand

Ingestion flow:

```text
XLSX / CSV
  -> deterministic source discovery and SHA-256 revision identity
  -> worksheet/CSV partition parsing
  -> exact folder/header domain and schema classification
       -> valid attendance: private raw envelope + typed attendance row
       -> invalid attendance: audited but publication blocked
       -> unknown domain: private quarantine only
  -> atomic artifacts and PostgreSQL transaction
  -> attendance-only Chroma projection after PostgreSQL commit
  -> SQLite generation/checkpoint activation
```

Question-answering flow:

```text
Question + trusted AccessContext + per-session ConversationState
  -> attendance access gate and malformed-input preflight
  -> advisory LLM QueryPlan
  -> deterministic schema/measure/predicate/date compiler
  -> employee and controlled-catalog resolution
  -> clarification gate when unresolved
  -> exact PostgreSQL/Chroma, filter-first hybrid, or semantic retrieval
  -> deterministic aggregation where applicable
  -> bounded evidence selection/reranking
  -> deterministic answer or evidence-grounded narrative completion
  -> HTML-escaped evidence returned to Gradio
```

PostgreSQL `attendance_records` is the typed exact source of truth. Chroma is a
derived semantic index. The model never has final authority over domain,
identity, dates, field types, calculations, executable filters, or SQL. A
resolved employee is committed to session state only after successful
retrieval. Singular follow-ups may inherit that trusted selection; genuine
population, grouping, and ranking questions may not.

## Current verified state

- 3,964 typed attendance rows
- 568 distinct employees
- attendance coverage from 2026-09-01 through 2026-09-07
- 4,532 Chroma chunks: 3,964 daily and 568 employee-period
- 310 authorized local evaluation cases
- 257 implementation tests passing
- 35 app/dashboard/evaluation/benchmark tests passing
- Ruff lint/format, compilation, dataset verification, and browser rendering
  checks passing

Private employee-linked expected values, raw attendance data, evaluation
fixtures, manifests, generated databases, `.env` files, tokens, and transcripts
must never be committed or reproduced in public documentation.

## Known next answer-quality defect

The current unresolved defect is:

```text
User: hi there, tell me who is faris
Assistant: displays two candidate employees
User: 1
Assistant: incorrectly returns 568 distinct employees
User: 1
Assistant: then returns the intended employee profile
```

The first choice is parsed and directory-revalidated correctly. The handler
adds the selected `Employee_ID` to the saved plan, but that saved plan retains
`measure="employees"`. During the second normalization/resolution pass, the
plan is classified as a population request and the trusted employee filter is
removed. Exact aggregation then counts the global employee population and the
selection is not committed.

This is a traced diagnosis, not yet a tested fix. When I later authorize
implementation, begin with an end-to-end failing regression proving that the
first numeric choice immediately answers the saved identity request and retains
only the selected employee. Add a paired regression proving that a genuine
population query still discards stale conversational employee scope. Fix the
typed state/retrieval/resolver boundary; do not special-case a name, the number
`1`, or the screenshot wording.

For this first turn, perform read-only onboarding only. Report:

1. your understanding of the architecture and trust boundaries;
2. the exact call path responsible for the known defect;
3. which tests and evaluation categories you would run first after approval;
4. any contradictions or stale statements you find in the documents;
5. the current branch, remote, and dirty-worktree files.

Then stop and wait for my next request. Do not make changes yet.

---
