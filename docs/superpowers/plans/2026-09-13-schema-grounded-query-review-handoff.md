# APDC Schema-Grounded Query Review Handoff

## Purpose

This handoff transfers the APDC attendance assistant after the schema-grounded
query compilation implementation and hardening review. The next task is not a
small bug fix and is not specific to `Day_Type`, off days, or any other single
column. It is an architectural answer-quality review across every supported
field, operation, question shape, backend, and clarification path.

The final target is an assistant that gives correct, relevant, and complete
answers because the architecture, contracts, and code design are consistent,
pattern-based, predictable, and extensible—not because individual questions
are patched with prompt rules or hardcoded special cases.

## Repository and comparison boundary

- Workspace: `D:\projects\llm_engineering_ed_donner\llm_engineering`
- Branch: `main`
- Correct remote: `https://github.com/fares201040/llm_engineering_learning.git`
- Manual before/after comparison baseline: `44b8413a`
- Hardened implementation commit before this handoff: `4dc150ca`
- The commit containing this handoff is the review starting point after pull.
- Never push to or restore content from `ed-donner/llm_engineering`.
- Preserve private attendance data, employee-linked expected values, local
  evaluation fixtures, manifests, databases, `.env` files, credentials, and
  transcripts. Do not commit or reproduce them in public documentation.

## Authoritative plan and related documents

Read these completely before changing code:

1. `docs/superpowers/plans/2026-09-13-schema-grounded-query-compilation.md`
   — the implementation plan and acceptance criteria.
2. `docs/superpowers/plans/2026-09-13-schema-grounded-query-review-handoff.md`
   — this handoff and the active review request.
3. `docs/superpowers/plans/2026-09-12-attendance-composable-intent-implementation.md`
   — earlier composable-intent decisions to compare with the current design.
4. `docs/superpowers/plans/2026-09-12-apdc-foundation-and-answer-correctness-implementation.md`
   — foundation, trust boundaries, and answer-correctness goals.
5. `docs/superpowers/plans/2026-09-12-apdc-gradio-interpretation-and-evidence-investigation.md`
   — clarification, session state, and evidence-display behavior.
6. `week5/new_implementation/APDC_Attendance_Remaining_Improvements_Implementation.md`
   — prior root-cause and implementation notes; treat descriptions of removed
   APIs as history, not current architecture.
7. `week5/new_implementation/APDC_Attendance_Remaining_Improvements_16_to_35.md`
   — previous improvement requirements and open concerns.
8. `week5/new_implementation/APDC_Attendance_Knowledge_Base_Improvements_1_to_21_Complete_Documentation.md`
   — historical system documentation; verify every architectural statement
   against current code before relying on it.

`week5/new_implementation/APDC_Answer_Quality_Next_Agent_Request.md` is an older
handoff. It contains stale commit counts and pre-migration architecture, so it
must not override this document or the current implementation plan.

## Current architecture to trace manually

The intended question-answering boundary is:

```text
Question + trusted access/session state
  -> deterministic access and malformed-input checks
  -> registry-derived semantic facts and bounded candidate context
  -> LLM PlannerProposal (untrusted, no SQL and no backend authority)
  -> semantic compiler and invariant chain
  -> ExecutableQueryPlan with constraint provenance and AnswerContract
  -> employee/catalog clarification when required
  -> parameterized PostgreSQL compiler or controlled Chroma retrieval
  -> deterministic calculation where representable
  -> bounded evidence and deterministic/evidence-grounded answer
```

The main code review order is:

1. `week5/new_implementation/attendance_schema.py`
2. `week5/new_implementation/semantic_resolution.py`
3. `week5/new_implementation/plan_compiler.py`
4. `week5/new_implementation/postgres_compiler.py`
5. `week5/new_implementation/answer.py`
6. `week5/new_implementation/query.py`
7. `week5/new_implementation/retrieval.py`
8. `week5/new_implementation/calculations.py`
9. `week5/new_implementation/resolution.py`
10. `week5/new_app.py`
11. `week5/new_evaluation/test.py`
12. `week5/new_evaluation/eval.py`
13. `week5/new_evaluation/benchmark.py`
14. All adjacent `test_*.py` contract and regression tests.

## What was implemented and hardened

- The LLM returns a strict `PlannerProposal`, not an executable query plan.
- Field definitions, natural names, operators, storage types, roles, value
  concepts, measures, predicates, and calculations come from registries.
- Resolver strategies are selected by resolution kind through an abstract
  resolver interface rather than column-specific planner branches.
- The semantic compiler canonicalizes values, records provenance, evaluates a
  standard invariant chain, and produces `ExecutableQueryPlan` only when safe.
- Percentage numerator conditions are independently canonicalized and are not
  copied into denominator filters.
- Calculation operation/subject and grouping are independently detected so an
  LLM cannot silently substitute a different calculation or omit grouping.
- Numeric comparator semantics are preserved (`at least` becomes `gte`, etc.).
- Numeric detection does not treat digits inside employee IDs as numeric field
  filters. Direct numeric equality requires explicit comparison language or
  direct field/value adjacency.
- Temporal and numeric values share schema-level canonicalization. PostgreSQL
  compilation validates them again at the database boundary, rejecting invalid
  dates and non-finite numbers.
- PostgreSQL queries are compiled from trusted identifiers and bound values.
- PostgreSQL catalog candidate searches filter and limit in SQL.
- Grouping is limited to two unique fields at the typed model boundary.
- Row-based percentages consistently allow `field=None` through proposal,
  executable calculation, and result contracts.
- Stale prompt rules, the old plan normalizer, duplicate intent compiler,
  obsolete state fields, prepared-plan bypasses, and raw PostgreSQL helper
  paths were removed. Public façade modules now expose the verified pipeline.
- Package/script imports in `new_app.py` were made deterministic to avoid two
  incompatible `ConversationState` class identities during test discovery.

## Verification evidence at handoff

- Ruff check: passed.
- Python compilation: passed.
- Full `week5` test discovery: 229 tests passed.
- App/evaluator/evaluation/benchmark wiring: 35 tests passed.
- Private dataset identity verified: 3,964 attendance rows, 568 employees,
  coverage `2026-09-01` through `2026-09-07`, fingerprint
  `6860e7657deb91023d6f199c230edf9b7d40cd3e402a1ad23ccea39f3487dde9`.
- The complete provider-backed behavior corpus was attempted but became trapped
  in repeated LiteLLM provider retries and was stopped. A later single-case run
  for the off-day regression failed for the same provider reason. Therefore,
  live behavior accuracy is **not claimed as passed** and must be rerun when the
  provider is healthy.

## Review risks that require special attention

Do not assume passing unit tests prove broad language coverage. Manually inspect
at least these architectural risks:

- whether every advertised question shape has independent semantic facts and
  can pass grounding/coverage invariants;
- ordering, ranking, limit, record-projection, grouped calculation, percentage,
  and multi-stage question expressiveness;
- ambiguity when the same phrase maps to multiple fields or concepts;
- collisions between employee IDs, dates, periods, and numeric values;
- calculation phrases embedded in field names, such as “total worked hours”;
- whether `AnswerContract` validation is strong enough to prevent a correct SQL
  result from being presented with the wrong unit, subject, grain, or shape;
- catalog and employee resolution behavior across PostgreSQL and Chroma;
- trusted session-state inheritance versus genuine population/group questions;
- PostgreSQL/Chroma semantic parity, null behavior, ordering, grouping, and
  coverage metadata;
- fail-closed behavior for requests the plan language cannot represent;
- stale documentation, exports, tests, compatibility paths, or duplicate
  sources of business meaning.
