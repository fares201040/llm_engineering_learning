# APDC Tasks 9–10 Independent Architectural Review

Date: 2026-09-15
Branch: `codex/apdc-conversational-intelligence`
Reviewed implementation head: `a4edc028`
Review range: `7634bfe..a4edc028`
Review fix commit: `cdb8fe3f`

## Scope and method

This review independently compared Tasks 9 and 10 with the grounded
conversational-intelligence plan, its progress ledger and task briefs/reports,
the four authoritative documents named by the plan, and the implementation diff.
The review traced the provider gateway, multilingual routing and rendering,
typed clarification state, preparation/execution atomicity, compiler and
retrieval boundaries, production diagnostics, and the local evaluator.

Every production correction below was preceded by a failing public-boundary
regression and an observed RED result. Fixes were made only in the owning layer.
`planning_decisions.py` was not changed.

## Findings and corrections

### Important — allowlisted telemetry identifiers accepted content-shaped values

`observability.py` treated any short opaque-looking token as a query
fingerprint and accepted arbitrary text after the `request-` prefix. Employee
identifiers or other private single-token values could therefore be emitted
under allowlisted keys.

The redaction boundary now accepts only the native shapes actually produced by
the system: a 32-character lowercase hexadecimal UUID or a numeric
`request-<n>` test identifier for request IDs, and a 64-character lowercase
SHA-256 value for PostgreSQL query fingerprints.

RED: the new request-ID and fingerprint regressions emitted
`request-private-employee` and `EMPLOYEE_SECRET`.
GREEN: both keys are dropped, while a native SHA-256 fingerprint remains
visible.

### Important — generic plan-validation details reached the public answer

`answer.py` interpolated arbitrary `PlanValidationError` text into the
user-facing response. That could expose internal validation or provider details
and was not reliably localized.

The public boundary now renders a fixed bilingual interpretation failure for
generic plan validation. Typed `SemanticPlanValidationError` remains separate:
its controlled English violation taxonomy stays actionable, Arabic receives the
fixed localized response, and pending state is cleared on both paths.

RED: a public `answer_question_with_state()` regression returned a synthetic
private validation detail.
GREEN: the detail is absent, the Arabic response is localized, no context is
published, and state is unchanged.

### Important — Arabic contextual routing bypassed canonical normalization

`conversation_understanding.py` matched Arabic context controls against raw
literal spellings. Harmless hamza and diacritic variants could skip the bounded
conversation decision path.

The control vocabulary and input are now compared in the same canonical
presentation-normalized form. Source text and spans are not rewritten; the
change affects only the routing predicate.

RED: a diacritized spelling of the Arabic ordinal control was not contextual.
GREEN: it enters the conversation-decision path.

### Important — recursive set sanitization invoked untrusted `repr`

The recursive privacy sanitizer sorted arbitrary set members by `repr` before
checking that they were controlled strings. A malformed diagnostic payload
could therefore raise inside observability instead of failing closed.

The sanitizer now filters against the controlled string vocabulary first and
sorts only validated strings.

RED: a set member whose `repr` raised escaped the sanitizer.
GREEN: the member is dropped and the controlled violation code is retained.

### Important — allowlisted telemetry keys could bypass their value schema

The recursive sanitizer dispatched on the runtime value shape before applying
the allowlisted key's scalar contract. A mapping under `request_id`, or a
Boolean under a fingerprint/count key, could therefore survive through a more
permissive recursive branch.

Key-specific validation now runs before generic container traversal. RED
regressions demonstrated all three bypasses; GREEN drops them while preserving
valid nested diagnostic structures.

### Important — contextual routing and decision validation used divergent vocabularies

Arabic prior references and coordinators were recognized inconsistently across
routing and residue validation. This let normalized spellings such as
`نفس الموظف`, `أيضا`, `أيضاً`, vocalized `أَيْضًا`, and `ثم` avoid the
conversation-decision boundary. A provider could also cover a prior reference
inside a whole unit span, mark that unit `new`, and bypass the residue-only
grounding check.

The owning language layer now exposes one normalized, bounded control
vocabulary used by both routing and validation. Validation checks prior
references in each unit source span as well as omitted residue, and requires the
corresponding attendance unit to carry a grounded non-new relation or a
validated employee mention. Public RED/GREEN cases cover both rejection and the
legitimate mention path.

### Important — Arabic multiple-selection controls used unsafe/incomplete boundaries

The original substring test could interpret employee-name fragments as
multi-select controls, while common attached conjunction/preposition forms were
missed. The parser now recognizes only bounded controls, with optional Arabic
`و`/`ل` clitics. Regressions prove `لكلاهما`, `وكلاهما`, and `ولكلاهما`
are accepted, while synthetic names containing `معاذ`/`الجميعي`-like fragments
do not widen employee scope.

### Important — semantic compiler guidance and application fallbacks were not safely localized

The generic validation correction also revealed that controlled semantic-plan
violations and the application fallback could render English-only or
uncontrolled text on Arabic paths. Semantic violations now render from the
controlled violation-code taxonomy, generic details stay hidden, and the app
fallback follows the detected reply locale. Pending state remains cleared on
both compiler-validation paths.

### Important — evaluator exceptions guessed a provider failure stage

The dashboard labeled arbitrary runtime, Pydantic, and timeout exceptions as
provider structural failures despite having no stage evidence. All ambiguous
exceptions caught at the dashboard boundary now use the fixed
`evaluation_runtime_failure` cause; typed diagnostics returned by the evaluator
remain authoritative when available. `Question` remains present in analyst
detail tables and exception details remain absent.

### Architectural cleanup — circular synthetic evaluator harness

Task 10's synthetic dashboard helper accepted caller-supplied call counts and
then asserted those same counts, while its declared turns were unused and the
helper was not connected to the UI. It was a test-only, self-reporting path
rather than an evaluator of production behavior. The dead helper and circular
tests were removed. Existing public answer, compound-conversation, provider,
and atomicity suites remain the native executable owners of these assertions.

### Privacy hygiene — analyst-only identifier copied into public tests

Task 10's new redaction tests copied the analyst-only employee identifier named
by the plan's private-check section. The committed test additions were scrubbed
to use explicit synthetic sensitive tokens without weakening coverage. No
production behavior change was required for this artifact-only correction.

## Confirmed boundaries

- The conversation provider still makes at most one actual completion attempt,
  sets `num_retries=0`, receives no raw history, and can select only bounded
  request-local choices.
- Invalid conversation decisions return controlled localized help before
  planning or retrieval and do not mutate the original state.
- Attendance units still prepare and compile before compound execution;
  PostgreSQL connection reuse, Chroma snapshot reuse, and no-partial-result
  publication remain unchanged.
- Employee resolution remains in `answer.py`; Chroma execution remains there;
  facades remain thin.
- No compiler, retrieval, answer-contract, typed-state, or clarification
  lifecycle bypass was introduced by Tasks 9–10 or the review fixes.
- Deterministic aggregate/profile/projection/multi-employee rendering remains
  provider-free.
- The evaluator continues to expose `Question` in analyst detail tables while
  production telemetry excludes question/history/evidence/employee/provider/
  plan/SQL content.
- No compatibility shim, duplicated business-semantic layer, test-only
  evaluator path, migration, raw SQL provider field, or question-specific
  answer map remains or was added.
- `planning_decisions.py` is unchanged in the reviewed range and review diff.

## Verification

Final verification completed with local test-only environment values:

- Independent focused re-review: 89 tests passed; no remaining Critical or
  Important architectural/public-behavior finding.
- Focused conversation/language/answer/app/evaluator batch: 286 tests passed
  with 1 existing skip.
- Unified Task 3–10 suite: 606 tests passed with 6 existing skips.
- Full `week5` discovery: 662 tests passed with 6 existing skips.
- Ruff check and Ruff format check passed across implementation, evaluation,
  application, and test scopes.
- Python compilation, imports, and public-signature assertions passed.
- `git diff --check`, staged whitespace, planning-boundary, diff, and status
  checks passed.
- The known protobuf `utcfromtimestamp` deprecation warning and expected test
  log output remain.

## Unavailable checks and remaining risks

The external conversation provider, private APDC manifest/dataset, and private
analyst transcript were not used. No live-provider, private-corpus, dataset
fingerprint, or benchmark claim is made by this review.

Preserved limitations remain: session-only memory; attendance-only scope;
fail-closed unsupported Boolean/HAVING/window/comparison/grouped-percentage and
multi-stage calculations; bounded resource narrowing; disclosed missing
coverage; and compile-first/no-partial semantics without cross-backend datastore
transactionality.
