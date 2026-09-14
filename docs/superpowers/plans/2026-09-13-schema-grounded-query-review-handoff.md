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
- Hardened implementation commit before this review: `4dc150ca`
- Accepted Task 5 implementation head: `e40e7a62e0bdbd74de40a7e3d103416a5c943709`
- Review plan commit: `cf71085c`; implementation commits run from
  `3a2742a4` through `e40e7a62` on top of the original review starting point.
- The commit containing this handoff is the review starting point after pull.
- Never push to or restore content from `ed-donner/llm_engineering`.
- Preserve private attendance data, employee-linked expected values, local
  evaluation fixtures, manifests, databases, `.env` files, credentials, and
  transcripts. Do not commit or reproduce them in public documentation.

## Authoritative plan and related documents

Read these completely before changing code:

1. `docs/superpowers/plans/2026-09-13-schema-grounded-query-compilation.md`
   — the implementation plan and acceptance criteria.
2. `docs/superpowers/plans/2026-09-13-schema-grounded-query-review-remediation.md`
   — the completed five-task review/remediation plan. Its unchecked boxes are
   historical execution steps, not the current status tracker; Tasks 1–5 are
   implemented and committed through the accepted head above.
3. `docs/superpowers/plans/2026-09-13-schema-grounded-query-review-handoff.md`
   — this handoff and the active review request.
4. `docs/superpowers/plans/2026-09-12-attendance-composable-intent-implementation.md`
   — earlier composable-intent decisions to compare with the current design.
5. `docs/superpowers/plans/2026-09-12-apdc-foundation-and-answer-correctness-implementation.md`
   — foundation, trust boundaries, and answer-correctness goals.
6. `docs/superpowers/plans/2026-09-12-apdc-gradio-interpretation-and-evidence-investigation.md`
   — clarification, session state, and evidence-display behavior.
7. `week5/new_implementation/APDC_Attendance_Remaining_Improvements_Implementation.md`
   — prior root-cause and implementation notes; treat descriptions of removed
   APIs as history, not current architecture.
8. `week5/new_implementation/APDC_Attendance_Remaining_Improvements_16_to_35.md`
   — previous improvement requirements and open concerns.
9. `week5/new_implementation/APDC_Attendance_Knowledge_Base_Improvements_1_to_21_Complete_Documentation.md`
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

- Accepted implementation: `e40e7a62e0bdbd74de40a7e3d103416a5c943709`.
- Final independent acceptance review: 242 focused tests passed; no Critical or
  Important regression remained in the bounded Task 5 change.
- Full configured `week5` discovery: 430 tests passed with no skips.
- Ruff, the 42-file formatting check, Python compilation, and `git diff --check`:
  passed.
- Private dataset identity verified: 3,964 attendance rows, 568 employees,
  coverage `2026-09-01` through `2026-09-07`, fingerprint
  `6860e7657deb91023d6f199c230edf9b7d40cd3e402a1ad23ccea39f3487dde9`.
- Offline benchmark: zero failures. Median/p95 seconds were plan compilation
  0.000340/0.000414, grouped calculation 0.000016/0.000028, projection
  0.000004/0.000005, and PostgreSQL exact snapshot 0.066890/0.184476.
- A fresh non-private provider probe was healthy. The complete behavior corpus
  then passed 107/311 cases (34.4%): 107 Passed, 200 Failed, and 4 evaluation
  ValidationErrors. This is a product/proposal compatibility result, not an
  infrastructure failure.
- The complete answer-quality corpus produced 305/311 answers with 6 structural
  ValidationErrors. Scores were accuracy 2.33/5, completeness 2.49/5, and
  relevance 2.68/5.
- For comparison only, permissive commit `c7f02c51` passed 233/311 behavior cases
  and scored 3.93/3.75/4.05 with 299 completed answers. Those numbers must not be
  treated as a correctness win: formal review proved that commit silently
  overwrote incompatible provider calculations, measures, and percentage
  numerators. The accepted architecture rejects those conflicts instead.
- The three temporary ignored private fixture copies used for verification were
  hash-checked, removed, and never staged; the shared originals remain intact.

## Completed review findings and native corrections

Tasks 1–5 of the remediation plan are implemented. The main correction families
were:

- compositional registry semantics for fields, concepts, measures, predicates,
  calculations, grouping, ordering, ranking, limits, projection, and unsupported
  structures;
- occurrence-bound employee, catalog, numeric, and temporal provenance, including
  complete atomic operands/lists and fail-closed unsupported relations;
- strict proposal equivalence: incompatible provider operations are rejected,
  while only absent or provably equivalent choices are completed;
- operation-complete `AnswerContract` validation for shape, unit, subject, grain,
  projection, grouped rows, and narratives;
- gated retrieval façades, parameterized PostgreSQL, explicit trusted attendance
  domain scope, read-only transactions, and consistent Chroma restrictions;
- preservation of baseline latest/earliest, ranking, percentage, employee
  clarification/follow-up, categorical operators, numeric comparisons, temporal
  scopes, and record projection through the standardized architecture.

The later Task 5 review rounds fixed confirmed wrong-result paths involving bound
superlatives, quantified subjects, collective versus distributive grouping,
catalog/operator role collisions, full operands containing conjunctions, implicit
equality suffixes, unsupported bare operators, numeric cross-field comparator
borrowing, temporal/numeric overlap, and projection loss from broad temporal
evidence spans. Each fix was preceded by a failing regression and reviewed through
the public executable-plan/result path.

## Remaining work after Task 5

Do not weaken the fail-closed boundary merely to recover provider pass rates. The
next agent should create a new written plan and address these items in order:

1. **Provider/proposal contract alignment.** The accepted strict architecture
   exposes a large mismatch between deterministic facts and provider proposals.
   The behavior run contained 102 `multi_stage_aggregation` conflicts, 106
   uncovered `filter:0` facts, 50 uncovered `measure:2` facts, 35
   `unsupported_constraint` outcomes, plus repeated predicate/calculation
   coverage failures. Redesign the planner boundary so the provider supplies only
   genuinely unresolved typed slots, or otherwise proves exact semantic
   equivalence; do not restore overwrite-based normalization.
2. **Low-score answer clusters.** Re-run the privacy-safe corpus observer and
   classify every score below 5 by plan/fact/contract/result shape. The current
   aggregate 2.33/2.49/2.68 shows that completing 305 answers is not sufficient.
   Fix shared causes in planning, evidence selection, and deterministic rendering,
   never individual questions or expected answers.
3. **Zero-pass behavior families.** On the accepted run, `worked_days`,
   `scheduled_days`, `non_attended_days`, `absent_days`, and
   `zero_worked_hours` scored 0%. `authorized_records` scored 24%,
   `exact_filter` 2.44%, and `hybrid` 6.67%. Determine whether each failure is
   semantic overproduction, provider incompatibility, evaluator contract drift,
   or a real execution defect before editing.
4. **Structural proposal failures and clarification.** Investigate the 4
   behavior-level and 6 answer-level ValidationErrors, plus failed employee
   clarification/multi-turn cases, without logging private identities or payloads.
5. **Current unsupported composition.** `Show Date and Status from records for
   <employee name> on 2026-09-01` still rejects, while date-first wording and
   count-with-identity/date work. Diagnose identity masking versus projection and
   temporal clause composition, add a failing public synthetic regression, and
   fix it natively if representable.
6. **Explicitly unsupported plan language.** Nested Boolean filters, HAVING,
   window calculations, cross-period comparisons, grouped percentages, and
   positional first/last requests remain rejected. Extend the typed plan/compiler
   contracts only with complete end-to-end support; otherwise keep rejection
   explicit and safe.
7. **Evaluator modernization.** Eleven malformed-input expectations previously
   asserted legacy error text even though the runtime rejected safely before
   retrieval. Update evaluation contracts around typed violation codes and
   capabilities only after verifying each case; do not add compatibility error
   mappings.

Task 5 is complete as an architectural hardening task, but release-quality provider
behavior and answer quality are not achieved. The next agent must report both
deterministic safety and provider effectiveness; neither substitutes for the other.

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
