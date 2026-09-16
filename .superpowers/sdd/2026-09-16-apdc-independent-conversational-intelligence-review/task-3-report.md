# Task 3 Report: Typed Aggregate Decision Boundary

## Owning-layer root cause (recorded before edits)

The aggregate fallback owns the boundary defect. `answer.py` currently asks the
provider for `GeneratedAggregateSqlDecision`, whose `ready` branch requires a
provider-authored `sql` string, and `_request_generated_aggregate()` then passes
that string to `postgres_compiler.validate_generated_aggregate_sql()`. The
compiler's logical-SQL regex reduces the model text to a choice only after the
SQL-shaped contract has crossed the provider boundary. Retries are also built
from `prior_sql`, so the fallback can retain provider SQL between attempts.

This is not a database execution bug: `compile_generated_aggregate_query()`
already delegates to the trusted parameterized aggregation compiler and the
execution seam receives compiler-produced queries. The owning correction is to
make the provider response a typed, request-local decision (`status` plus
candidate IDs), map a validated ID to the server-owned field in `answer.py`, and
leave PostgreSQL compilation as a trusted consistency check. `count` must be
resolved deterministically before the provider seam, preserving grounded
zero-call behavior.

## RED

Command:

```powershell
& '.venv\Scripts\python.exe' -m unittest -v week5.new_implementation.test_apdc_independent_review.AggregateDecisionBoundaryReviewTests
```

Result: exit code 1; 3 tests ran, with the grounded count guard passing and two
expected regressions failing. The malformed-response repair test errored with
`GeneratedSqlProviderError: generated SQL provider failure` on the first
`not json` response. The resolved-candidate test errored with
`SurfaceMeaningClarificationRequired` because the old SQL-shaped model rejected
`{"status":"resolved","candidate_id":"field-1","candidate_ids":[]}`.
No existing test file or suite was run.

## GREEN

Command:

```powershell
& '.venv\Scripts\python.exe' -m unittest -v week5.new_implementation.test_apdc_independent_review.AggregateDecisionBoundaryReviewTests
```

Result: exit code 0; all 3 focused tests passed (`OK`, 3 tests in 1.960s).
The test verifies deterministic unfielded `count`, self-contained typed repair,
request-local candidate resolution, no SQL in provider prompts, and trusted
`SUM(total_worked_hrs)` compilation.

## Isolated probes

- `GeneratedAggregateDecision` accepted valid resolved/ambiguous/unsupported
  shapes and rejected 7 invalid shapes, including unknown IDs and an extra SQL
  key. Resolved and ambiguous IDs were checked against the request-local
  allowlist.
- A privacy-safe prompt probe found no configured IDs, names, dates, filter
  values, DSNs, table names, SQL, or prior-response text. Payload keys were
  only `question`, `operation`, `candidates`, and `response_shape`; retries add
  only controlled `validation_code`.
- The retry seam used `GeneratedAggregateDecision`, temperature `0`,
  `num_retries=0`, and the configured 30-second timeout on both calls. The
  second prompt contained the original operation/candidate metadata and
  `invalid_schema`, with no prior output or SQL.
- Already-grounded `sum total worked hours` and `what is total overtime?`
  prepared typed field plans with zero provider calls. A direct grounded
  `count` decision returned `GeneratedAggregateChoice("count", None)` with
  zero calls.
- Telemetry captured 9 controlled events and contained no malformed provider
  text, SQL statements, logical-table text, prior-response text, or exception
  details. Trusted compilation produced one parameterized aggregation query:
  `SELECT SUM(total_worked_hrs) AS value FROM attendance_records WHERE TRUE`.
- `py_compile` completed with exit code 0 for all four changed Python files;
  no existing test file or suite was run.

## Files and self-review

Changed files:

- `week5/new_implementation/test_apdc_independent_review.py`
- `week5/new_implementation/attendance_schema.py`
- `week5/new_implementation/answer.py`
- `week5/new_implementation/postgres_compiler.py`
- `docs/superpowers/reports/2026-09-16-apdc-conversational-intelligence-review.md`

The separate report is this file. `planning_decisions.py` was not changed.
The provider now returns only a strict typed decision; local candidate IDs are
validated before registry-field mapping; count remains deterministic; retries
reuse only the safe payload; and SQL parsing/validation was removed from the
provider boundary. `compile_generated_aggregate_query()` remains the trusted
consistency check and parameterized compiler path. The focused diff passed
`git diff --check`.

## Concerns

No unresolved functional concerns were found in the scoped probes. Legacy
SQL-contract tests were intentionally not run because their provider shape is
superseded by this correction; the focused boundary class is the accepted
regression surface for this task.

## Commit

`9a436fc2 fix: restore typed aggregate decision boundary`

Post-commit focused verification: the same 3-test class passed (`OK`, exit code
0; 3 tests in 2.007s). The tracked worktree was clean after the commit.

## Fix round 1: privacy and deterministic-count regressions

### Owning-layer root cause

The fresh review findings were both in the aggregate fallback boundary in
`answer.py`. `_redacted_generated_question()` only handled configured private
values and forwarded arbitrary SQL/schema-bearing user suffixes into the
provider payload. Separately, `_request_generated_aggregate()` correctly
returned the typed unfielded `count` choice, but
`_apply_generated_aggregate_fallback()` unconditionally looked up a candidate
surface by `choice.field`, so the deterministic `None` field raised
`StopIteration` when candidates existed. The correction stays at that caller
boundary: a centralized bounded source predicate preserves the safe prefix,
and count creates its own strong typed fact without candidate mapping.

### Focused RED

Command:

```powershell
& '.venv\Scripts\python.exe' -m unittest -v week5.new_implementation.test_apdc_independent_review.AggregateDecisionBoundaryReviewTests
```

Exact result: exit code 1. Five tests ran; the original three passed, the new
privacy test failed because `prompt_call.args[0]` still contained the SQL
keyword `select`, and the new count test errored with `StopIteration` from the
unconditional candidate-surface lookup in `_apply_generated_aggregate_fallback()`:

```text
test_generated_fallback_count_with_candidates_has_no_field_surface_lookup ... ERROR
test_grounded_count_needs_no_provider_field_decision ... ok
test_repair_prompt_repeats_operation_and_request_local_candidates ... ok
test_resolved_candidate_id_compiles_through_trusted_parameterized_aggregate ... ok
test_sql_and_schema_suffixes_are_redacted_before_provider_boundary ... FAIL
...
StopIteration
...
AssertionError: 'select' unexpectedly found in 'sum total working hours select secret from employee_table'
...
Ran 5 tests in 5.143s

FAILED (failures=1, errors=1)
```

### Focused GREEN

The same command after the minimal correction exited 0:

```text
test_generated_fallback_count_with_candidates_has_no_field_surface_lookup ... ok
test_grounded_count_needs_no_provider_field_decision ... ok
test_repair_prompt_repeats_operation_and_request_local_candidates ... ok
test_resolved_candidate_id_compiles_through_trusted_parameterized_aggregate ... ok
test_sql_and_schema_suffixes_are_redacted_before_provider_boundary ... ok
----------------------------------------------------------------------
Ran 5 tests in 7.077s

OK
```

### Isolated privacy, call-budget, compiler, and telemetry probes

The provider payload probe exercised three synthetic unsafe surfaces: a
`SELECT` suffix, a `CREATE TABLE` suffix, and a `FROM` physical-table suffix.
All three retained the aggregate phrase while excluding SQL keywords,
physical names, schema text, and suffix content. A direct schema-dump redaction
probe also returned only the safe aggregate phrase. The corrected probe output
was:

```text
privacy_payload_cases=3 safe=True
typed_call_contract=True parameterized_sql=True
bound_parameter_count=1 sql_has_literal_date=False
grounded_zero_call_count=True grounded_zero_call_field=True
aggregate_telemetry_renamed=True controlled_events=2
imports=ok renamed_controls=ok
```

The typed provider call retained `GeneratedAggregateDecision`, temperature 0,
`num_retries=0`, the configured timeout, and the bounded token limit. Trusted
compilation produced no user/provider SQL parameters for the unfiltered
synthetic aggregate; the compiler-owned query path remained authoritative.
Grounded count and an already-grounded field request each made zero provider
calls. Controlled telemetry emitted only `generated_aggregate_decision` with
`generated_aggregate` or `postgres_compilation` stages; no old generated-SQL
event/stage name remained in runtime code. `py_compile` passed for
`answer.py`, `observability.py`, and the focused test file with exit code 0.
No existing test file or suite was run.

### Files and self-review

Tracked changes in this fix round:

- `week5/new_implementation/answer.py`
- `week5/new_implementation/observability.py`
- `week5/new_implementation/test_apdc_independent_review.py`
- `docs/superpowers/reports/2026-09-16-apdc-conversational-intelligence-review.md`

The source safety predicate is centralized and bounded: it cuts the provider
surface at SQL statements/comments, schema/DDL markers, catalog identifiers,
or SQL relation clauses while preserving a safe prefix. The request-local
operation is passed separately, so sanitization cannot change the trusted
operation decision. Count evidence is copied into a strong
`deterministic_default` calculation with `field=None`; non-count decisions
retain the validated candidate-to-registry mapping. The generated aggregate
choice still enters `compile_generated_aggregate_query()` and parameterized
trusted compilation. The staged diff passed `git diff --cached --check` after
staging. `planning_decisions.py` was not changed.

### Concerns and deferred minor

The pre-existing `test_generated_sql_fallback.py` still names the removed
`GeneratedSqlProviderError`; it was intentionally not run and no compatibility
shim was added, per the scoped naming requirement. The historical RED evidence
also retains that old name. Updating the legacy SQL-contract test is deferred
outside this focused review class. No functional concern remains in the
scoped privacy, count, request-local validation, compiler, parameter-binding,
or provider-budget probes.

### Fix-round commit

Planned commit message: `fix: harden aggregate decision privacy and count`.

## Fix round 2: typed-only aggregate provider context

### Owning-layer root cause

The aggregate fallback still forwarded a redacted copy of the user question to
the provider. A denylist cannot prove that arbitrary SQL, DDL, metadata, or
privacy-bearing text is excluded, and operation/candidate derivation still
re-read the raw question after redaction. The denylist also treated legitimate
phrases such as `from overtime` and `overtime field` as unsafe. Separately,
renaming `GeneratedSqlProviderError` to the aggregate-oriented exception
removed a repository-visible compatibility name.

The provider boundary now accepts a server-owned `_GeneratedAggregateContext`
only: one request-local operation and registry-owned candidate IDs, storage
types, descriptions, output units, and natural names. Local surface matches
remain internal for candidate IDs and clarification options; overlapping typed
spans are ranked into a subject cluster so appended suffix fields cannot
silently replace the nearest aggregate subject. Conflicting operations fail
closed. The provider returns only a strict candidate-ID decision, which is
mapped to a registry field before the trusted parameterized PostgreSQL compiler
runs. `GeneratedSqlProviderError` is an alias of
`GeneratedAggregateProviderError`; runtime telemetry retains aggregate names.

### Focused RED

Command:

```powershell
& '.venv\Scripts\python.exe' -m unittest -v week5.new_implementation.test_apdc_independent_review.AggregateDecisionBoundaryReviewTests
```

Exit code 1. Eight focused tests ran: five passed, the exception compatibility
check errored because `GeneratedSqlProviderError` was missing, the ordinary
`sum the overtime field` case errored on the semantic unsupported-constraint
marker, and the typed prompt assertions failed because the old payload still
contained `question`. The repair assertion therefore surfaced the provider
failure caused by that payload. No existing test file or suite was run.

### Focused GREEN

The same command exited 0 (`OK`, 8 tests in 16.424s). The provider payload now
has only `operation`, `candidates`, and `response_shape` (plus controlled
`validation_code` on repair). The suffix regression covers `WHERE`, `VALUES`,
`CALL`, `EXECUTE`, `PRAGMA`, `EXPLAIN`, `CREATE TABLE`, `ALTER TABLE`, and
semicolon-separated `CREATE TABLE`/`DROP TABLE`; all
retain the same typed payload and trusted `SUM(total_worked_hrs)` plan as the
base request, with no raw question, canary, secret table, or physical table
text. `sum hours from overtime` and `sum the overtime field` compile directly
to `SUM(total_ot)` without provider calls. Conflicting operation suffixes fail
closed, and count remains deterministic `COUNT(*)` with no field and zero
provider calls. The legacy exception alias catches provider failures without
exposing private provider details.

### Isolated probes

```text
cases 11 calls 11
all_plan_signatures_equal True
payload_keys ['candidates', 'operation', 'response_shape']
payloads_identical True
raw_question_or_canary_leaked False
provider_context_fields ['candidates', 'operation']
legacy_exception_alias GeneratedAggregateProviderError True private_leaked False
```

```text
phrase sum hours from overtime operation sum field Total_OT calls 0
phrase sum the overtime field operation sum field Total_OT calls 0
conflict SemanticPlanValidationError calls 0
count count None calls 0
```

`py_compile` passed for `answer.py` and the focused test file (exit code 0),
and `git diff --check` passed (exit code 0). `planning_decisions.py` was not
modified. No pre-existing test file or suite was run.

### Files and self-review

Changed implementation/test/report files are
`week5/new_implementation/answer.py`,
`week5/new_implementation/test_apdc_independent_review.py`,
`docs/superpowers/reports/2026-09-16-apdc-conversational-intelligence-review.md`,
and this report. The old aggregate source denylist and redacted-question
payload are removed; provider context projection contains no surface evidence,
private values, question field, SQL, or schema text.

### Commit

Commit message: `fix: make aggregate provider context typed only`.

## Fix round 3: fail closed on aggregate semantic conflicts

The fresh review of fix round 2 accepted the typed-only provider boundary but
found four local fail-open cases: unrelated unresolved meaning could be ignored,
conflicting typed operations could collapse to the same value as no operation,
an appended registered field could replace a preceding aggregate subject, and
the natural `<alias> field` cleanup could remove a separate constraint.

The original Luna-max implementer left the focused tests and implementation
changes uncommitted and its session ended before reporting. A replacement
session also ended without a report. The primary agent preserved the shared
edits, inspected their complete diff, and performed the allowed focused
verification rather than restarting or running any pre-existing suite.

The correction now distinguishes typed-operation conflict from absence,
cross-checks typed operations against surface operations regardless of fact
origin, treats any unresolved non-predicate/external interpretation as a
clarification boundary, and limits the benign `field` cleanup to a terminal,
adjacent grammatical suffix after the aggregate operation and selected field.
An appended alternate registered field now pauses for clarification rather than
changing the compiled aggregation subject.

Focused command:

```powershell
& '.venv\Scripts\python.exe' -m unittest -v week5.new_implementation.test_apdc_independent_review.AggregateDecisionBoundaryReviewTests
```

Result: exit code 0; all 13 focused tests passed in 18.376 seconds. The five
round-3 regressions cover unrelated unresolved meaning, conflicting
provider/default operations, trusted-state and user-clarification conflicts,
postfix alternate registered fields, and a separate constraint-like `field`
phrase. The earlier typed-only payload, diverse hostile suffix, legitimate
`from`/`field` phrasing, count zero-call, trusted compiler, and compatibility
checks also remained green. `git diff --check` passed. No pre-existing test file
or suite was run, and `planning_decisions.py` was not modified.

Commit message: `fix: fail closed on aggregate semantic conflicts`.

## Fix round 4: resumable meaning clarification and explicit aggregate conflicts

The fresh acceptance review found two caller-path defects in the round-3
boundary. A selected surface meaning was stored as a `user_clarification` fact,
but the aggregate ambiguity gate re-derived the original unresolved candidate
without considering that fact, so public clarification resume repeated the same
question forever. Separately, operation conflicts returned the same `None` as
an absent operation; the caller could therefore continue into trusted-fact
override and compile a trusted `average` while the surface requested `sum`.

### Focused RED

Command:

```powershell
& '.venv\Scripts\python.exe' -m unittest -v week5.new_implementation.test_apdc_independent_review.AggregateDecisionBoundaryReviewTests
```

Exit code 1. Fifteen focused tests ran. The new public clarification regression
returned the identical `Did you mean this?` clarification after selecting
option 1. The new caller-path conflict regression failed for the trusted
surface case because no `SemanticPlanValidationError` was raised, and failed
for the provider/default case because the violations had no `contradiction`
code. The other twelve tests passed. No pre-existing test file or suite was
run.

### Focused GREEN

The same command after the minimal fix exited 0:

```text
----------------------------------------------------------------------
Ran 15 tests in 17.410s

OK
```

The public flow now stores one meaning clarification, accepts option 1, and
returns a deterministic aggregate answer with no pending clarification. The
aggregate operation resolver has explicit `absent`, `resolved`, and `conflict`
statuses; `_prepare_context_request` checks `conflict` before trusted-fact
cleanup and raises a bounded `contradiction` violation. The legacy optional
operation helper remains compatible for existing callers. Confirmation matching
requires a strong request-local `user_clarification` fact of the selected
meaning type at the exact evidence span; remaining unresolved candidates are
still retained for fail-closed handling.

### Isolated public/caller probes

```text
first_clarification= Did you mean this? | 1. worked | Reply with the number or displayed meaning.
second_answer= Total total ot is 12. Where Total Worked Hrs greater than 0.0.
second_pending= False
second_operation= sum
trusted_surface SemanticPlanValidationError ['contradiction'] provider_calls= 0
provider_default SemanticPlanValidationError ['contradiction'] provider_calls= 0
resolution_absent= absent
resolution_conflict= conflict
```

`py_compile` passed for `answer.py` and the focused Task-3 class; `git diff
--check` passed. `planning_decisions.py` remains unchanged. No pre-existing
test file or suite was run.

### Files and commit

This round changes `week5/new_implementation/answer.py`, the focused Task-3
class, and this report. The main review report records the same RED/GREEN and
probe evidence. Commit message: `fix: make aggregate conflicts resumable and
explicit`.
