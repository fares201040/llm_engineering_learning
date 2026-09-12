# APDC Attendance RAG — Improvements 16–35 Implementation Record

## Purpose and format

This document explains the implementation from the state of the system
**before** the work. Each phase records:

1. the problem that existed;
2. the root cause;
3. the chosen solution;
4. how the implementation realizes the solution;
5. how the result is verified;
6. what remains incomplete.

It is a technical implementation record, not a claim that every roadmap item
is finished. The authoritative status, data truth, blockers, and next actions
are in
[`APDC_Attendance_Remaining_Improvements_16_to_35.md`](APDC_Attendance_Remaining_Improvements_16_to_35.md).

## Engineering decisions

- PostgreSQL is authoritative for structured facts.
- A structured request must become a fully executable typed plan before any
  retrieval call.
- The LLM interprets language; deterministic code owns filters, calculations,
  identity, and safety.
- Employee and controlled-value catalogs come from current stores, not
  question-specific constants.
- Hybrid retrieval filters before similarity ranking.
- Gradio state is isolated per browser session.
- Ingestion uses durable local state and publishes replacement data only after
  validation.
- The single JSONL artifact remains for backward compatibility.
- Existing tuple return shapes and package/script imports remain compatible.
- The implementation adds no third-party dependency.

## Phase 1 — Canonical field contract and backend parity

### Problem before implementation

The live data had 50 business fields, while the query dictionary omitted 19
date, time, remarks, and workflow fields. Field type/operator knowledge,
search text, metadata projection, SQL expressions, prompt definitions, and
context aliases were duplicated across `attendance_schema.py`, `answer.py`,
and `ingest.py`.

PostgreSQL could filter `Country`, but its returned `Result` omitted Country
while Chroma returned `Country: Yemen`. Similar parity gaps existed for
grades, actual/scheduled times, overtime, leave, and traceability.

### Root cause

The schema was descriptive, not executable. No single registry controlled how
a field flowed through normalization, filtering, SQL, metadata, and context.

### Solution

Use one immutable field registry as the executable contract. Generate
allowlisted backend behavior from the registry and reconstruct PostgreSQL
results from the complete authoritative JSON record.

### Implementation

`attendance_schema.py` declares 60 current and derived fields. Each
`FieldDefinition` records:

- storage type: text, date, time, datetime, or number;
- accepted operators;
- natural-language aliases and semantic meaning;
- controlled values when appropriate;
- an allowlisted PostgreSQL expression;
- search, metadata, context, and catalog-resolution policies.

Known typed columns map directly to PostgreSQL columns. Fields without a
dedicated column use a predefined `record_json ->> '<field>'` expression with
a type cast. Only registry keys may select expressions; user text is never an
SQL identifier.

`_postgres_row_to_result()` projects `record_json` through the registry,
making PostgreSQL and Chroma logical `Result` metadata equivalent.
`config.py` validates table settings as plain or schema-qualified SQL
identifiers. Ingestion no longer creates unused `pg_trgm` objects.

### Tests and verification

- Every live JSONL field must be declared.
- Every storage type must expose compatible operators.
- Every field must expose a safe SQL expression/projection policy.
- Full PostgreSQL `record_json` projection is asserted.
- Typed string, numeric, date, `in`, JSONB, and period filters are tested.

### Status

Implemented for the current field set. Future source fields require an
explicit registry/index-schema decision.

## Phase 2 — Executable-plan safety and broader clarification

### Problem before implementation

An empty planner plan, reversed/malformed date, malformed ID such as `A10`,
or unknown department could become unfiltered exact retrieval and return up
to 500 of all 3,964 rows. Multiple IDs collapsed to the first. Only employees
had ambiguity handling. Numeric slash dates were guessed, and retained
employees could be applied to global/group questions.

### Root cause

Validation only checked filters the planner emitted; it did not verify that
each structured intent in the question survived planning. Routing trusted the
raw plan, and clarification state was employee-specific.

### Solution

Compile an attendance-specific plan before retrieval. Independently detect
structured intent, validate type/operator/value completeness, resolve
identities and catalogs from data, and stop when anything is unresolved.

### Implementation

`normalize_query_plan()` and `_compile_required_constraints()` now:

- validate every explicit employee-ID token;
- preserve multiple IDs with a typed `in` filter;
- compile relative dates, explicit ranges, and single month-name dates;
- reject invalid, reversed, omitted/ambiguous, and slash-numeric dates;
- reject non-finite numerics and invalid scalar/list shapes;
- enforce field/operator compatibility;
- detect a comparison whose operand disappeared from the plan;
- detect explicit catalog values that conflict with planner output;
- validate grouping, percentage, ordering, and limits;
- distinguish an output projection from a required filter;
- treat grouping as a structured use of the grouped field;
- force structured questions to exact or filter-first hybrid mode.

`load_employee_directory()` loads `(employee_id, name)` pairs from
PostgreSQL, with Chroma fallback. Resolution order is explicit ID, exact
normalized name, partial name, then conservative token-aware fuzzy matching.
Duplicate names keep separate IDs.

`load_attendance_catalog()` resolves Department, Work Location, Shift,
Status, Exception, and Leave Type. Exact values canonicalize; strong unique
matches may resolve; weaker/multiple matches become bounded clarification;
unknown values stop.

`ConversationState` stores selected employees, saved question/plan, displayed
employee candidates, and one `PendingConstraint`. Replies select only
displayed candidates by number, exact value/name, ID, `both`, or `all`.
Candidates are revalidated and the saved plan resumes without replanning.
Multiple ambiguities resolve sequentially.

`answer_question_with_state()` returns expected validation/clarification
messages with no evidence. `new_app.py` carries state through `gr.State`.
`answer_question()` remains a fresh-state compatibility wrapper.

Follow-up intent logic reuses a selected employee for employee-scoped metric
questions such as “What about overtime?” but not explicit population/group
requests.

The screenshot follow-up “Who is this employee?” exposed two related defects.
Successful direct ID/name resolution was used for retrieval but discarded from
the return path, so only clarification-based selection populated
`ConversationState.selected_employees`. In addition, the population guard used
`employees?`, which incorrectly classified singular “this employee” as global.

The internal `_fetch_context_result()` boundary now returns a typed
`ContextFetchResult` containing the validated resolved candidates, while the
public `fetch_context()` API keeps its original four-item return contract.
`answer_question_with_state()` persists candidates only after successful
retrieval. The population classifier recognizes population nouns, ranking
questions, and grouping dimensions, while singular identity follow-ups retain
the trusted selected ID. Failed new identities preserve the last successful
selection.

### Tests and verification

Boundary tests patch exact retrieval, embedding, semantic retrieval,
aggregation, reranking, and final completion and assert zero calls for invalid,
unknown, or ambiguous requests. Other tests cover:

- duplicate names and fuzzy `muhtar`;
- conflicting name/ID and multiple IDs;
- number/name/ID/`both`/`all` replies;
- invalid and stale replies;
- non-employee ambiguity;
- saved-plan execution without replanning;
- follow-up scoping;
- direct-ID success followed by “Who is this employee?”;
- population requests remaining unscoped;
- failed new identities preserving the prior selection;
- independent state objects passed through the Gradio handler.

### Status

The deterministic safety and clarification layer is implemented. Live
planner-language coverage awaits provider recovery.

## Phase 3 — Grouped, percentage, and ranked calculations

### Problem before implementation

`QueryPlan` modeled one scalar. Percentage, grouping, ordering, and top-N
were absent. Rendering inferred labels from question wording, producing
“3,964 days” for a count of attendance records. Duplicate employee names
could merge, and percentage denominator semantics were implicit.

### Root cause

The plan/result contract did not represent analytical shape, and the renderer
did not consume calculation semantics.

### Solution

Extend the plan additively, introduce typed scalar/grouped/percentage results,
separate percentage scope from numerator condition, and implement equivalent
SQL and Python calculations with deterministic rendering.

### Implementation

`QueryPlan` gained optional `group_by`, `percentage_condition`,
`order_by`, `order_direction`, and `limit`, plus `percentage`
aggregation. The schema adds:

- `ScalarCalculationResult`;
- `GroupedCalculationRow` and `GroupedCalculationResult`;
- `PercentageCalculationResult`.

One or two allowlisted grouping fields are accepted, with 50 groups by
default. PostgreSQL composes only registry expressions and parameters. The
Chroma fallback calculates over filtered metadata.

For percentages, plan filters define the denominator scope and
`percentage_condition` defines the numerator. Zero denominator returns an
undefined result rather than dividing by zero. Employee grouping expands to
ID plus display name. Ordering, null groups, limits, total-group count, and
truncation are deterministic.

Canonical concepts compile to executable operations:

- worked days → distinct Date + `Total_Worked_Hrs > 0`;
- scheduled days → distinct Date + `Day_Type = "Working Day"`;
- authorized records → count + `Status = "Authorized"`;
- employees → distinct Employee_ID.

`_format_aggregation_answer()` renders scalar sentences, percentage
summaries, and Markdown tables without calling the final LLM.

Exact evidence, count, and calculation share one PostgreSQL `REPEATABLE READ
READ ONLY` transaction.

### Tests and verification

Tests cover grouped average, employee overtime, duplicate names, percentage
numerator/denominator, zero denominator, null groups, order/limit/truncation,
rendering without LLM, and the record-count label regression. Employee-linked
manual SQL results remain in the ignored private evaluation corpus.

### Status

Implemented for the declared calculation model. Ambiguous percentage
populations intentionally stop instead of guessing.

## Phase 4 — Evaluation truth, routing, reranking, and context

### Problem before implementation

Expected answer facts were not enforced, ambiguity cases were repeated first
turns, and no dataset fingerprint blocked stale expectations. One
employee-scoped overtime expectation was incorrectly zero. Seven semantic
examples were forced into exact mode by a narrow control-flow regex. Reranking
priorities existed only in a prompt, and retrieval quality relied mainly on
substring keywords.

### Root cause

Evaluation metadata was disconnected from behavior assertions; routing
vocabulary lived in control flow; relevance did not identify expected records.

### Solution

Version evaluation against PostgreSQL truth, make deterministic checks
mandatory, represent multi-turn behavior, move semantic concepts to schema
data, score evidence before optional reranking, and project context from
resolved fields.

### Implementation

`TestQuestion` now supports expected record IDs, normalized result,
multi-turn turns, and expected error. `eval.py`:

- checks answer facts against rendered answer text;
- asserts normalized plan/identity/calculation/results;
- executes multi-turn clarification;
- enforces semantic category routing;
- computes record-ID MRR and nDCG where labels exist;
- runs deterministic failures before qualitative LLM judging.

The local-only `dataset_manifest.json` stores record/employee counts, date
bounds, and a canonical PostgreSQL content fingerprint. `--verify-dataset`
fails clearly when any value drifts. The manifest and private-derived
`tests.jsonl` corpus are ignored by Git and are not published.
In a clean checkout, the unit suite explicitly skips the private corpus
integration checks; direct corpus or manifest commands explain which
authorized local fixture is missing.

The active local 310-row corpus is APDC-only and covers exact, semantic,
hybrid, clarification, calculation, and safety behavior. It remains a local
acceptance artifact because it contains private-derived expected results.

Semantic intent aliases live in `attendance_schema.py`.
`_deterministic_evidence_score()` places exact identity/field evidence ahead
of generic summaries before optional LLM reranking. Exact/hybrid contexts
retain required fields and omit unrelated content; semantic contexts stay
rich.

### Tests and verification

- Corpus size/content replacement is asserted.
- Answer facts are checked against rendered output.
- Semantic categories must route semantically.
- Multi-turn cases assert selected employee.
- Dataset verification matches the authorized local-only fingerprint without
  publishing it.
- Exact, broad exact, and semantic context policies are tested.

### Status

Partial. Only five rows have expected record IDs. Broad semantic/hybrid labels,
recall@6 = 1.0, aggregate MRR/nDCG@6 ≥ 0.8, and live provider behavior remain.

## Phase 5 — Incremental JSONL, checkpointing, and recovery

### Problem before implementation

Conversion opened live outputs with `w` before success. Manifest v1 held only
source hashes, so a crash could leave truncated output declared current.
Embeddings accumulated in memory before any upsert. One business hash could
not distinguish text/model/schema versus metadata changes. There was no
cross-stage journal or run-wide writer guard.

### Root cause

Source detection, output integrity, index identity, and sink progress were
independent ad hoc mechanisms.

### Solution

Use a standard-library SQLite ledger, atomic materialization, separate hash
domains, streamed durable Chroma batches, and delayed deletion while retaining
the single JSONL contract.

### Implementation

`ingestion_state.py` uses SQLite WAL tables for:

- pending/active/archived generations;
- normalized records and source locators;
- business, embedding-input, and metadata hashes;
- per-source normalized valid/invalid snapshots;
- stage/batch checkpoints with payload hashes;
- active-generation state.

Pending generations resume only for the same source hash. An OS advisory lock
surrounds the complete run because a database transaction cannot safely remain
open during external API/store calls. A second run fails clearly; process
failure releases the lock.

`convert_excel_to_jsonl()` hashes source workbooks. Unchanged files reuse
ledger rows without opening Excel. Changed files are validated, then JSONL and
invalid audit are written to same-directory temporary files, flushed, fsynced,
and atomically replaced. The manifest is written last.

`_journal_documents()` separates:

- business record hash;
- embedding-input hash including model, searchable text, and index schema;
- metadata hash.

Model/text/schema changes re-embed; metadata-only changes reuse vectors.

`sync_embeddings_to_chroma()` processes one bounded batch at a time:
embedding API → Chroma upsert → ledger checkpoint. It verifies stored hashes
before honoring a checkpoint and delays stale/superseded deletion until all
replacement upserts succeed.

Empty and partially invalid snapshots are rejected before sink writes unless
explicitly allowed. Canonical duplicate selection runs once before daily and
period chunks.

### Tests and verification

Tests cover generation rollback/resume, source-hash isolation, run-lock
contention, unchanged source reuse, atomic conversion failure, non-finite
numeric rejection, canonical daily/period consistency, empty/partial snapshot
guards, embedding failure before deletion, and checkpoint hash verification.

### Status and remaining gap

Partial. PostgreSQL sinks are transactionally idempotent but not checkpointed,
and readers do not share an active generation across stores.

Code can write manifest v2 with output hashes/counts, but the live manifest is
still v1. Matching v1 is accepted without output validation. Automatic
no-re-embedding v1→v2 bootstrap/migration is not implemented. Logical changes
also still rematerialize the required single JSONL file.

## Phase 6 — Observability and benchmarking

### Problem before implementation

Timeouts, candidate/group limits, table names, and benchmark counts were
scattered. Logs could expose queries, names, IDs, and DSNs. Timings combined
several stages, and no repeatable percentile benchmark existed.

### Root cause

Configuration, privacy, and measurement developed independently in each
module.

### Solution

Centralize typed settings, provide a privacy-safe event contract, and run real
read-only components with warmups and median/p95 reporting.

### Implementation

`Settings.from_environment()` validates operational paths, booleans, limits,
timeouts, models, logging options, benchmark defaults, and SQL identifiers.
Legacy module-level constants remain compatible.

`observability.py` provides text/JSON events and a timed stage context
manager. Sensitive keys are recursively redacted, and employee IDs/PostgreSQL
URIs are sanitized in free text.

`benchmark.py` measures actual plan normalization, Python grouped
calculation, context projection, and a read-only PostgreSQL exact snapshot.
Warmups are excluded. Reports include dataset fingerprint, cache state,
backend flags, model names, failures, and skipped provider stages. It cannot
write live sinks.
When the private manifest is absent, the benchmark reports
`private_fixture_unavailable` with no fingerprint instead of failing.

### Tests and verification

Tests cover redaction, duration/state fields, JSON output, percentiles,
failures/skips, real component names, and dataset/backend identification. The
last 2-warmup/10-run execution had zero failures.

### Status

Partial. Full stage/failure event coverage, persisted benchmark baseline, and
automatic >20% local regression enforcement remain.

## Phase 7 — Focused module split

### Problem before implementation

`answer.py` and `ingest.py` accumulated planning, resolution, retrieval,
calculation, context, normalization, embedding, and sink work. This made
review and safe modification difficult. External clients also risked
initialization during import.

### Root cause

Consumers imported directly from two original scripts, so responsibilities
could not move safely without first creating stable boundaries.

### Solution

Create responsibility-oriented façades and lazy external access first, then
move implementations incrementally behind those interfaces.

### Implementation

```text
query.py              planner/compiler façade
resolution.py         identity/catalog/state façade
retrieval.py          backend-neutral retrieval façade
calculations.py       calculation façade and typed models
context.py            evidence/context façade
attendance_records.py normalization/chunk façade
ingestion_sinks.py    Chroma/PostgreSQL sink façade
ingestion_state.py    concrete ledger implementation
```

OpenAI and Chroma access use lazy wrappers. `answer.py` and `ingest.py`
retain supported symbols and script/package paths.

### Tests and verification

The suite imports package/script paths, exercises legacy callers, and
constructs Gradio with launch mocked without initializing a live provider.

### Status

Partial. Most façade modules currently re-export bodies still located in
`answer.py` (~2,800 lines) or `ingest.py` (~2,000 lines). Future extraction
must move one responsibility at a time with characterization tests before and
after.

## Significant defects fixed

| Previous defect | Native correction |
|---|---|
| Dropped planner constraints caused broad retrieval | Executable-plan compiler and hard pre-retrieval gate |
| Multiple IDs collapsed | Validate/preserve all IDs with typed `in` |
| Duplicate names merged | Candidate identity is ID + name |
| Unknown structured values used semantic fallback | Data-backed resolution and zero-call stop paths |
| Retained employee contaminated global questions | Employee-follow-up intent check |
| Directly resolved employee was lost before the next turn | Carry validated resolution through internal `ContextFetchResult` and commit it after success |
| Singular “this employee” was treated as a population query | Structurally separate singular follow-ups from population, ranking, and grouping requests |
| Numeric dates were guessed | Reject and request ISO/month-name |
| PostgreSQL omitted fields present in Chroma | Registry projection from `record_json` |
| Python `ne` disagreed with SQL NULL behavior | Matching SQL NULL predicate semantics |
| Record count rendered as days | Operation/field-based formatter |
| Exact subqueries saw different snapshots | One repeatable-read transaction |
| Duplicate records fed inconsistent summaries | Canonicalize once before all projections |
| NaN/Infinity entered numeric fields | Reject non-finite values |
| Failed conversion could truncate outputs | Temp + fsync + atomic replace |
| Embedding work had no durable progress | Streamed batch upsert/checkpoint |
| Deletion preceded replacement | Delete only after all new writes |
| Stale checkpoint skipped bad sink state | Verify stored hashes |
| Concurrent runs interleaved sinks | Run-wide OS advisory lock |
| Answer facts checked dict text | Check rendered answer |
| Semantic category did not enforce route | Normalized mode assertion |
| Dataset drift was silent | PostgreSQL fingerprint gate |
| Logs exposed PII/DSNs | Default structured redaction |
| Benchmark measured placeholders | Real local/read-only component calls |

## Post-baseline addition — APDC evaluation dashboard

`week5/new_evaluator.py` adds a Gradio dashboard over the current
`week5/new_evaluation` package. It intentionally does not import the legacy
Insurellm evaluator. The page exposes four independently runnable workflows:

1. private dataset fingerprint verification;
2. deterministic behavior-contract evaluation;
3. retrieval MRR, nDCG, and keyword-coverage evaluation;
4. provider-backed answer accuracy, completeness, and relevance evaluation.

A shared maximum-case control defaults to 10 and treats `0` as an explicit full
corpus request. Per-case errors are retained as typed failure rows without
rendering sensitive exception details. `week5/test_new_evaluator.py` verifies
case limiting, aggregation, failure reporting, and manifest summaries.

The dashboard component tree and all four tabs were rendered in a live browser
smoke test. The refreshed local baseline is 257 implementation tests and 35
app/dashboard/evaluation/benchmark tests. These dashboard and documentation
changes remain uncommitted until the user explicitly requests delivery.

### Newly diagnosed answer-quality defect

The first numeric reply to an ambiguous `who is faris` request can return the
global 568-employee count. The candidate is initially selected correctly, but
the saved `measure="employees"` makes a second resolver pass classify the plan
as a population query and remove the trusted `Employee_ID` filter. This is a
traced diagnosis, not a completed correction. The next implementation must
start with a failing regression, preserve validated clarification scope through
retrieval, and prove that genuine population queries still discard stale
employee scope.

## Verification procedure

```powershell
$env:ANONYMIZED_TELEMETRY='False'
$env:POSTHOG_DISABLED='true'
& '.venv\Scripts\python.exe' -m unittest discover -s week5 -p '*test*.py' -v
& '.venv\Scripts\ruff.exe' check week5/new_implementation week5/new_evaluation week5/new_evaluator.py week5/test_new_evaluator.py week5/new_app.py week5/test_new_app.py
& '.venv\Scripts\ruff.exe' format --check week5/new_implementation week5/new_evaluation week5/new_evaluator.py week5/test_new_evaluator.py week5/new_app.py week5/test_new_app.py
& '.venv\Scripts\python.exe' -m compileall -q week5/new_implementation week5/new_evaluation week5/new_evaluator.py week5/test_new_evaluator.py week5/new_app.py week5/test_new_app.py
git diff --check
& '.venv\Scripts\python.exe' -m week5.new_evaluation.eval --verify-dataset
& '.venv\Scripts\python.exe' -m week5.new_evaluation.benchmark --warmups 2 --runs 10
```

Only after a provider health probe succeeds:

```powershell
& '.venv\Scripts\python.exe' -m week5.new_evaluation.eval --behavior --all
```

Manual scenarios must cover current PostgreSQL truth, scalar/grouped/
percentage/top-N calculations, all invalid date/ID/value gates, employee and
catalog clarification replies, two Gradio sessions, filter-first semantic
retrieval, failure/resume boundaries, and Gradio construction/live response.

## Final outcome and remaining work

The implementation now prevents unsafe broadening, makes attendance semantics
executable, preserves identity, performs deterministic analytics, aligns
PostgreSQL and Chroma evidence, makes Chroma ingestion resumable, and provides
reproducible evaluation and performance tools.

It remains operationally incomplete in seven areas:

1. provider-dependent planner/reranker/final-answer verification;
2. live pgvector verification;
3. PostgreSQL sink checkpoints and shared generation publication;
4. live manifest v1→v2 integrity migration;
5. semantic/hybrid identity labels and enforced quality thresholds;
6. persisted performance gates and physical module extraction.
7. trusted clarification scope surviving the second resolver pass without
   contaminating genuine population queries.
