# APDC Grounded Conversational Intelligence — Tasks 9–10 Handoff

Date: 2026-09-15
Branch: `codex/apdc-conversational-intelligence`
Worktree: `D:\w\apdc`

## Completed commits

- `32ac2093` — `test: enforce multilingual conversation parity`
- `6cd22a32` — `test: verify conversational attendance safety and quality`
- The handoff is included in the final documentation commit for this task.

## Delivered scope

Task 9 completed the Arabic and mixed-language integration review. The public
paths now preserve locale through clarification, normalize Arabic presentation
forms for choices, guard contextual Arabic controls, render Arabic
interpretation and coverage messages, and refuse protected/injection routes
deterministically. Exact source spans, provider boundaries, and compound
atomicity remain owned by their existing layers.

Task 10 completed the privacy/evaluator review. Production event emission uses
an explicit key/value allowlist with recursive handling for mappings,
sequences, dataclasses, model-like payloads, objects, and exceptions.
Failure events carry controlled failure codes only. Application and evaluator
failures use fixed safe diagnostics, and aggregate result values were removed
from answer logging. Synthetic local evaluator contracts cover fast paths,
context resumption, invalid provider decisions, call counts, no-partial-result
atomicity, and analyst-visible `Question` columns.

No real employee transcript, identity, expected value, manifest, migration, or
private fixture was added.

## Verification record

Environment for deterministic local verification:

```text
POSTGRES_DSN=postgresql://local/test
OPENAI_API_KEY=test-only
PYTHONIOENCODING=utf-8
```

Commands and results:

- `python -m unittest discover -s week5 -p '*test*.py' -q` — 650 tests,
  0 failures, 6 existing skips.
- `python -m unittest week5.new_implementation.test_answer -q` — 205 tests,
  0 failures, 1 existing skip.
- Multilingual focused suites (`test_language_understanding`,
  `test_conversation_understanding`, `test_tolerant_input_robustness`,
  `test_semantic_matrix`, `test_compound_conversation`) — 104 tests,
  0 failures.
- Final Task 10 focused suites (`test_observability`, `test_new_app`,
  `test_new_evaluator`) — 27 tests, 0 failures.
- `py_compile` passed for the changed production modules.
- Scoped Ruff check and Ruff format check passed.
- `git diff --check` passed.
- Import and signature assertions for observability, application, and
  evaluator entry points passed.
- `planning_decisions.py` has no diff.

The known Google protobuf `utcfromtimestamp` deprecation warning appeared
during discovery. It is pre-existing and unrelated to this work.

## Review gate

The final diff was independently re-read against the Task 9–10 briefs. The
review checked production diagnostics for `logger.exception`, exception text,
provider payloads, plan payloads, SQL, raw question/evidence, and aggregate
answer values. It also checked the Task 9 source-span/locale paths, evaluator
column preservation, test scope, and the unchanged planning boundary. No open
Task 9 or Task 10 findings remain.

## Unrun live/private checks

The external conversation provider, private APDC dataset manifest, and any
private analyst transcript were not available or used. No claim is made about
those live checks. Verification used local fixtures and the test-only provider
key/DSN values above.

## Preserved limitations

- Conversation memory remains session-only; there is no migration or
  datastore change.
- The product remains attendance-only and fail-closed for unsupported Boolean,
  HAVING, window, comparison, grouped-percentage, and arbitrary multi-stage
  aggregation requests.
- Resource budgets require narrowing; missing coverage is disclosed.
- Semantic/hybrid execution does not gain datastore transactionality, while
  compile-first/no-partial-result behavior remains enforced.
- Existing semantic thresholds and local provider availability remain
  environment-dependent.
