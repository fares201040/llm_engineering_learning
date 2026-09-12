# APDC Attendance RAG — Engineering Handoff

## Purpose and authority

This is the primary technical handoff for the current APDC attendance assistant.
It describes the code and verified runtime state as of 2026-09-12. Earlier plans
and design documents explain how the system evolved, but this handoff is the
first document a new engineer or AI agent should read.

When documents disagree, use this order of authority:

1. Current code and tests.
2. This handoff.
3. The complete documentation and current implementation plan.
4. Historical specifications and plans.

## Required reading order

1. This handoff.
2. `APDC_Attendance_Knowledge_Base_Improvements_1_to_21_Complete_Documentation.md`.
3. `APDC_Attendance_Remaining_Improvements_Implementation.md`.
4. `APDC_Answer_Quality_Next_Agent_Request.md`.
5. `docs/superpowers/plans/2026-09-12-apdc-foundation-and-answer-correctness-implementation.md`.
6. `attendance_schema.py`, especially `QueryPlan`, `FIELD_DEFINITIONS`,
   `MEASURE_DEFINITIONS`, `BUSINESS_PREDICATE_DEFINITIONS`, and
   `compile_business_intent()`.
7. `answer.py`, following `fetch_context()` and
   `answer_question_with_state()` rather than reading helpers randomly.
8. `new_app.py`, `new_evaluator.py`, `test_new_app.py`, and
   `test_new_evaluator.py`.
9. `source_ingestion.py`, `ingest.py`, `ingestion_state.py`, and
   `chroma_client.py`.
10. `new_evaluation/test.py`, `eval.py`, `benchmark.py`, `tests.jsonl`, and
    their unit tests.

## Current verified state

| Item | Verified value |
|---|---:|
| PostgreSQL attendance rows | 3,964 |
| Distinct employees | 568 |
| Attendance date range | 2026-09-01 through 2026-09-07 |
| Chroma chunks | 4,532 |
| Daily chunks | 3,964 |
| Employee-period chunks | 568 |
| Valid raw attendance rows | 3,964 |
| Local evaluation questions (private-derived, not published) | 310 |
| Implementation tests | 257 passing |
| App/dashboard/evaluation/benchmark tests | 35 passing |
| Pending ingestion generations | 0 |

The dataset fingerprint is kept in the local-only evaluation manifest and is
not published.

Repository handoff baseline: GitHub `main` commit
`7b5ef62531f384c9b453decd301613d12b406f92`. That commit was independently
reviewed with no remaining Critical or Important finding before it was pushed.
The only configured remote is
`https://github.com/fares201040/llm_engineering_learning.git`; do not add or
push to `ed-donner/llm_engineering`.

The current working tree intentionally contains the new APDC evaluation
dashboard (`week5/new_evaluator.py`) and its tests
(`week5/test_new_evaluator.py`) plus the documentation updates described here.
These changes were verified locally but were not committed or pushed at the
time of this handoff update. Preserve and inspect them; do not reset or clean
the worktree.

The private raw table also contains one historical quarantined fixture row.
That row is intentional append-only history, not an active source and not stale
data to delete.

Local verification covers employee-scoped worked days, scheduled days,
overtime, authorization status, and record identity. Employee-linked expected
values stay in the ignored private evaluation corpus and are not reproduced in
this publishable handoff. Only non-identifying aggregate acceptance results are
reported here.

## Current answer-quality issue and next-agent mission

The earlier single-interpretation/empty-evidence screenshot defect is fixed and
covered by the 257-test implementation baseline. A newer screenshot exposed a
different unresolved clarification defect.

### Reproduced user-visible sequence

1. The user asked `hi there, tell me who is faris`.
2. The assistant correctly displayed two matching employees and asked for a
   numbered clarification.
3. The first reply `1` incorrectly returned `568 distinct employees matched the
   requested criteria.`
4. A second reply `1` returned the intended employee profile.

The first valid choice must immediately remain scoped to the selected employee
and answer the saved identity request. It must never execute a global employee
count, require the same choice twice, or lose the validated candidate.

### Traced root cause, not yet implemented

The first numeric reply is parsed correctly and its candidate is revalidated.
`answer_question_with_state()` then builds a prepared plan with the selected
`Employee_ID`. The saved planner plan still has `measure="employees"` because
the original wording (`who is faris`) is not the narrow `who is this employee`
projection pattern. During `_fetch_context_result()`, plan normalization and
`resolve_employee_plan()` run again. `_is_employee_followup()` classifies the
plan as a population request because of that measure and removes the newly
added trusted employee filter. The exact backend consequently counts the full
568-employee population, and the resolved selection is not committed. The next
`1` is therefore handled as a new history-informed turn rather than as the
original clarification.

This diagnosis was established by tracing the current code path, but the fix
has deliberately not been implemented without a failing regression. The
approved correction is to carry an explicit trusted clarification scope (or an
equivalent typed internal contract) through retrieval so a directory-validated
choice cannot be stripped by population cleanup. Genuine population questions
must continue to discard stale conversational employee scope.

### Future implementation workflow

After the user explicitly asks the next agent to implement improvements:

1. Reproduce `who is faris` → two candidates → first reply `1` at both
   `answer_question_with_state()` and `new_app.chat_with_state()` boundaries.
2. Add a failing end-to-end regression proving that the first selection keeps
   the selected ID, answers the saved identity question, clears pending state,
   and never returns the global count.
3. Add a paired safety regression proving that a real employee-population query
   still removes stale selected-employee scope.
4. Implement the smallest typed fix at the state/retrieval/resolver boundary;
   do not special-case `Faris`, the number `1`, or the screenshot sentence.
5. Use `new_evaluator.py` to measure behavior, retrieval, and answer quality on
   a bounded sample before any full provider-backed run.
6. Expand answer-quality testing across identity, ambiguous names, follow-ups,
   deterministic calculations, semantic/hybrid questions, malformed input,
   evidence parity, reset behavior, and interleaved sessions.
7. Run the complete verification commands, independently review the final diff,
   update this handoff and related documentation, and only commit/push when the
   user asks. Push only to
   `https://github.com/fares201040/llm_engineering_learning.git` without force.

Do not commit `.env` files, access tokens, raw or identifiable employee
attendance data, `tests.jsonl`, `dataset_manifest.json`, generated Chroma or
database files, or ingestion-state artifacts. Scan the full outgoing commit for
credentials before pushing; the previous push correctly stopped on and then
removed an embedded Hugging Face token from a notebook.

## Locked architectural decisions

- Keep `attendance_records` as the typed attendance source of truth.
- Preserve every readable physical XLSX/CSV row in
  `private_ingestion.raw_source_rows` when PostgreSQL is enabled.
- Keep Chroma as a derived, attendance-only semantic index.
- Keep the SQLite ingestion ledger; do not introduce an outbox in this release.
- Classify domains deterministically from the first source folder and exact
  registered headers. Never use an LLM or fuzzy matching for ingestion routing.
- Unknown data is quarantined and never reaches attendance JSONL, typed rows,
  Chroma, prompts, or displayed context.
- Preserve the public `answer_question()` and four-item `fetch_context()`
  return shapes and attendance record IDs. Internal `_fetch_context_result()`
  returns a typed `ContextFetchResult`, which also carries the validated
  employee resolution to the session layer.
- Use trusted application access context, never model-generated domain scope.
- Defer production identity, RLS, outbox, pgvector migration, and new HR-domain
  adapters until their real schemas and policies are approved.

## End-to-end architecture

### Ingestion

```text
XLSX / CSV
  -> deterministic discovery and SHA-256 source identity
  -> source revision
  -> worksheet / CSV partition
  -> deterministic domain and schema classification
       -> valid attendance: raw envelope + typed attendance projection
       -> invalid attendance: raw envelope + publication blocked
       -> unknown: private quarantine only
  -> atomic JSONL and invalid-audit artifacts
  -> PostgreSQL raw + typed publication in one transaction
  -> Chroma attendance projection after PostgreSQL commit
  -> SQLite generation/checkpoint activation
```

### Question answering

```text
Question + AccessContext + ConversationState
  -> access/domain gate
  -> deterministic malformed-input preflight
  -> LLM QueryPlan for language interpretation
  -> composable measure, business-predicate, and constraint compiler
  -> catalog and employee resolution
  -> clarification gate when identity/value is unresolved
  -> successful resolved identity committed to per-session state
  -> exact, hybrid, or semantic backend selection
  -> deterministic aggregation when requested
  -> related-part expansion and bounded reranking
  -> schema-driven safe context rendering
  -> deterministic calculation answer or final narrative completion
```

The LLM proposes an interpretation. It does not get final authority over
identity, domain, dates, calculation meaning, field types, or executable SQL.

## Ingestion technical contract

### Source discovery and parsing

`source_ingestion.py` owns the source boundary:

- `discover_sources(root, xlsx_glob, csv_glob)` returns stable, case-folded
  relative-path order and ignores Excel lock files beginning with `~$`.
- XLSX uses `openpyxl` with `read_only=True` and `data_only=True`; every sheet
  is a separate partition.
- CSV uses `utf-8-sig`, bounded `csv.Sniffer` detection for comma, semicolon,
  tab, or pipe, and falls back to comma.
- The first non-empty row is the header. The parser does not guess title rows,
  encodings, or business meanings.
- Raw header/value arrays are preserved separately from normalized attendance
  mappings.

The only current domain registry entry is attendance. Its folder is
`attendance`, and its required normalized headers are `Employee_ID`, `Name`,
and `Date`.

Classification outcomes are valid attendance, `unrecognized_domain`,
`schema_mismatch`, `duplicate_headers`, and a row-zero `read_error` sentinel.

### Raw and typed PostgreSQL publication

`private_ingestion.raw_source_rows` is append-only. Its stable identity is:

```text
raw_row_key = SHA-256(source_path + source_sha256 + partition_name + source_row)
payload_hash = SHA-256(canonical payload JSON)
```

The JSONB payload contains only the physical `headers` and `values` arrays.
Raw JSONB is preservation data, not queryable business data, and has no GIN
index.

For a safe snapshot, `publish_postgres_snapshot()` performs one transaction:

1. Create or additively upgrade the raw and attendance tables.
2. Insert raw rows with conflict-ignore semantics.
3. Upsert canonical attendance winners, including `raw_row_key` provenance.
4. Delete typed attendance rows absent from the safe current snapshot.
5. Commit.

Chroma synchronization occurs only after this transaction succeeds. For an
unsafe snapshot, readable raw/quarantined rows are preserved in a short
transaction, typed attendance and Chroma are unchanged, and the SQLite
generation remains pending.

### Publication guards

Typed and Chroma publication is blocked when attendance validation produces
invalid rows, the valid snapshot is empty, a prior attendance source
disappears or becomes unreadable, or a prior attendance partition becomes
unknown.

A newly introduced unknown source is preserved without blocking healthy
attendance. Intentional source removal requires the one-run setting
`ALLOW_ATTENDANCE_SOURCE_REMOVAL=true`.

When PostgreSQL is disabled, compatibility-mode attendance ingestion remains
available, but detection of unknown data aborts before Chroma because the raw
rows cannot be preserved safely.

### Manifest, ledger, and Chroma rules

- Manifest version is 3 and records source hash/format, partition summaries,
  parser version, output hashes, and row counts. It never stores raw payloads.
- SQLite cache reuse requires matching path, source hash, parser version,
  artifact integrity, and—in PostgreSQL mode—expected raw-row coverage.
- Chroma metadata always includes `domain="attendance"`.
- `embedding_input_hash` includes embedding model, index schema version, and
  projected text.
- A text/model/schema change re-embeds; a metadata-only change calls metadata
  update without embedding; identical hashes produce no write.
- Obsolete Chroma IDs are deleted only after replacement writes succeed.
- The last no-change ingestion produced zero PostgreSQL upserts, zero embedding
  calls, zero Chroma writes, and left all 4,532 chunks unchanged.

Chroma telemetry is configured natively through `chroma_client.py` and is
disabled by default with `CHROMA_ANONYMIZED_TELEMETRY=false`.

## Question flow and correctness rules

### 1. Trusted access gate

`fetch_context()` resolves `AccessContext` first. The local demo defaults to
`LOCAL_DEMO_ACCESS`, which permits attendance only. Unsupported domains are
rejected before planner, directory lookup, embeddings, retrieval, reranking,
or completion with this non-enumerating message:

```text
This demo supports authorized attendance questions only.
```

`domain` is deliberately absent from model-generated `QueryPlan`.

### 2. Preflight and plan compilation

The compiler rejects malformed employee IDs, ambiguous slash dates, invalid or
reversed calendar ranges, missing/non-finite numeric operands, unsupported
fields/operators, unsafe scalar/list shapes, invalid grouping, and unresolved
structured constraints.

Explicit question facts override conflicting planner values. Important rules:

- a question date replaces a conflicting planner date but preserves `before`,
  `after`, inclusive comparison, or equality meaning;
- `September 2026` remains a month/year range and is not parsed as September 20;
- named fields such as `Actual_From_Date` do not receive an extra daily `Date`
  constraint;
- a confirmed `Employee_ID` prevents old `name_hint` reinjection;
- unrequested planner group limits are removed, while explicit top/bottom N is
  preserved;
- list requests remain non-aggregate requests.

### 3. Composable business calculations

Measures and business predicates are independent, declarative registries:

- `distinct_dates` + `worked`: distinct `Date` with
  `Total_Worked_Hrs > 0`;
- `distinct_dates` + `scheduled_working_day`: distinct `Date` with
  `Day_Type = "Working Day"`;
- `distinct_dates` + `scheduled_working_day` + `not_worked`: scheduled dates
  with `Total_Worked_Hrs <= 0`;
- `distinct_dates` + `absent`: dates with `Exception = "Absent"`;
- `attendance_records`: `COUNT(*)`, only for explicit row-count intent;
- `attendance_records` + `authorized`: `COUNT(*)` with
  `Status = "Authorized"`;
- `employees`: distinct `Employee_ID`.

Explicit count nouns make the measure authoritative, while positive/negative
attendance language makes the predicate polarity authoritative. Contradictory
plans stop before retrieval. Requested date ranges are compared with source
coverage, and incomplete periods receive a deterministic warning.

Authorized attendance means workflow `Status`, not `Exception`,
`OT_Authorized`, or `OT_Not_Authorized`. “Count distinct employees with
Authorized attendance records” therefore remains a distinct employee count
and deterministically uses `Status = "Authorized"`.

Percentage plans keep numerator and denominator scopes separate. Grouped
averages follow PostgreSQL semantics: null numeric values are ignored rather
than treated as zero.

### 4. Identity resolution and clarification

Employee resolution uses database-backed `(employee_id, name)` candidates.
Explicit IDs win. Exact, partial, and conservative fuzzy name matching are
supported; duplicate names remain separate candidates.

If the reference is ambiguous, execution stops and saves the plan in
`ConversationState`. A reply may select a number, full name, ID, `both`, or
`all` from the displayed candidates. The chosen candidate is revalidated, its
trusted ID is injected, and pending candidates, plan, and constraint are
cleared after successful execution. The planner is not called again.

A successful direct ID or name resolution follows the same state transition:
`_fetch_context_result()` returns the validated candidates in
`ContextFetchResult`, and `answer_question_with_state()` commits them only after
retrieval succeeds. The public `fetch_context()` wrapper remains backward
compatible with its original four-item tuple.
Identity-free turns such as “Who is this employee?” therefore reuse the exact
trusted ID. Explicit plural/quantified population and grouping questions do
not inherit the selected employee. A failed new identity does not replace the
last successful selection.

### 5. Backend selection

- Exact: structured filters, rows, and calculations; PostgreSQL when enabled,
  otherwise Chroma compatibility behavior.
- Hybrid: semantic meaning plus structured filters; filters are applied before
  similarity ranking.
- Semantic: fuzzy meaning without structured constraints.

If employee resolution adds an ID to a semantic plan, the plan is promoted to
hybrid so identity scope cannot be lost.

PostgreSQL `IN` filters use storage-matched arrays: `date[]`, `time[]`,
`timestamp[]`, `double precision[]`, or `text[]`. SQL identifiers and field
expressions come only from allowlisted configuration/schema registries.

### 6. Evidence and answer rendering

Exact PostgreSQL evidence and calculations use a read-only repeatable-read
snapshot. Split semantic chunks are reunited by logical record ID before
reranking. Small result sets skip the LLM reranker. Large sets receive
deterministic evidence scoring before bounded reranking.

Scalar, grouped, and percentage calculations are rendered deterministically.
Narrative answers receive retrieved content as delimited untrusted evidence.
The Gradio renderer HTML-escapes source names and page content and displays
evidence inside `<pre><code>`.

## Critical defects fixed

1. Record counts could be described as days.
2. Worked days and scheduled days could be confused.
3. Authorized workflow status could be confused with overtime authorization.
4. Percentages could count distinct null fields instead of physical records.
5. Planner-created filters could conflict with explicit question values.
6. Malformed IDs, dates, and numeric comparisons could reach providers.
7. A semantic employee-name query could lose its resolved employee scope.
8. A clarification selection could repeat because pending state or name hints
   survived successful selection.
9. Non-`Date` temporal `IN` filters used incompatible PostgreSQL `text[]` casts.
10. “List attendance records” and “List Authorized records” could become counts.
11. Unrequested model-generated limits could truncate grouped output.
12. Evaluation checked group count but not group values.
13. Evaluation declared clarification success without proving pending state was
    cleared.
14. Expected record IDs existed in the corpus but were not enforced.
15. Direct `python eval.py --verify-dataset` failed due package-relative import.
16. Explicit date correction forced equality, corrupted month/year ranges, and
    added an unrelated daily Date constraint.
17. Distinct employee questions containing “attendance records” could be
    overwritten by record-count rules.
18. Negative attendance phrasing could be compiled as positive worked days;
    composable predicates and fail-closed polarity checks now own this rule.
19. A directly resolved employee was not written back to conversation state,
    and singular “this employee” was incorrectly classified as a population
    request. Follow-up retrieval could therefore contain unrelated employees.

Every fix was introduced with a reproducing test, corrected at the owning
normalization/business-rule boundary, and checked for adjacent regressions.

## Evaluation state

The 310-question local corpus is APDC-only. Because its expected results are
derived from private attendance data, `tests.jsonl` and `dataset_manifest.json`
are intentionally ignored by Git and are not part of the published repository:

The publishable unit suite detects whether those authorized local fixtures are
available and explicitly skips only the private corpus integration checks when
they are absent. Direct evaluator commands instead return an actionable
missing-private-fixture error.

| Category | Questions |
|---|---:|
| Exact filter | 41 |
| Malformed input | 34 |
| Field filter | 31 |
| Worked days | 26 |
| Scheduled days | 25 |
| Authorized records | 25 |
| Employee ambiguity | 20 |
| Multi-employee | 20 |
| Semantic | 15 |
| Hybrid | 15 |
| Attendance records | 16 |
| Date filter | 16 |
| Grouped aggregate | 12 |
| Percentage | 10 |
| Scheduled non-attendance | 2 |
| Explicit absence | 1 |
| Zero worked hours | 1 |

Expected numeric facts are derived from the locked attendance JSONL and checked
against its fingerprint. Evaluation asserts plan fields, one or many required
filters, employee IDs, exact record IDs, matched counts, normalized results,
calculation values, every expected grouped value, rendered facts, expected
errors, and cleared multi-turn state.

One uninterrupted sequential provider/database run passed 300/300 cases with
no retries. The affected date and Authorized-record categories were rerun after
their corrections with zero failures. Each later regression case, including
the final distinct-employee wording, then passed individually against the final
rule set. An earlier screenshot-specific replay passed the real state,
normalization, employee-directory, and PostgreSQL boundaries while the provider
was temporarily unavailable. The later final replay and long acceptance run
both passed through the configured live provider.

## 2026-09-12 Gradio interpretation and evidence investigation

The supplied three-turn Gradio failure was reproduced at the plan-normalization
boundary. A valid, single-item `interpretation_candidates` list was being sent
to clarification validation, which requires at least two distinct choices. The
request therefore stopped before employee resolution and retrieval, so the
answer correctly carried no chunks and Relevant Context had nothing to render.
The rendering component itself was not the original fault.

Interpretation candidates are now stably deduplicated. One compatible valid
candidate compiles directly, multiple distinct candidates remain a genuine
clarification, and invalid enum values fail schema validation. Explicit
quantitative, projection, identity, semantic, ranking, percentage, and limited
record intent takes precedence over contradictory advisory planner labels.

The same investigation exposed and fixed adjacent deterministic-boundary
defects:

- reset now clears chat history, Relevant Context, selected employees, and all
  pending clarification state;
- employee anaphora without a trusted selection stops before retrieval;
- population questions cannot inherit or accept planner-invented employee
  scope;
- numeric calculations, scheduled-attendance percentages, grouped rankings,
  and ordered record projections compile from explicit user wording;
- positive worked-day wording does not override an explicit worked-hours
  threshold;
- case-only controlled-value differences do not create false contradictions;
- narrative field, identity, and semantic projections cannot become record
  counts merely because of planner candidates;
- identity answers use the current trusted selection and current retrieval,
  rather than stale conversation claims;
- broad exact record requests report the authoritative match count and label
  the displayed chunks as an evidence sample;
- punctuation-only, malformed comparison, unsupported-domain, and creative
  requests fail before unsafe or irrelevant retrieval.

Both deterministic and model-generated answer paths now have explicit parity
tests proving that the answer boundary returns the exact retrieved evidence.
The Gradio callback has a matching test proving that this evidence is escaped
and rendered under Relevant Context.

An uninterrupted provider/database acceptance run completed 60 turns in the
primary session and nine turns in an independently interleaved control session.
It covered identities and anaphora, employee replacement, unknown and ambiguous
identity, attendance distinctions, status and record counts, numeric and
percentage calculations, grouping and ranking, time coverage, semantic
questions, invalid input, clarification/retry, and reset. Assertions recorded
scope, calculation, answer, selected and pending state, evidence count, and the
final executable plan for every turn in a private temporary transcript. The two
sessions remained isolated, and reset cleared the second session before its
fresh-session controls. The transcript and private-derived facts are not
tracked.

The restarted Gradio application also passed the supplied three-turn browser
replay. The identity follow-up returned the trusted employee identity with real
retrieved evidence. Clear removed both chat and context, and the same anaphoric
question in a fresh state returned a controlled request for an employee name or
ID with no evidence. A final ordered-record browser check returned and displayed
exactly the explicitly requested two most recent evidence rows.

Final automated verification passed 257 implementation tests and 35
app/dashboard/evaluation/benchmark tests. Ruff check and format check, Python compilation,
private dataset integrity verification, five APDC-adjacent notebook schema
validations, and `git diff --check` passed. The only warning remained the known
third-party protobuf deprecation warning. Independent final re-review reported
no Critical or Important findings.

## APDC evaluation dashboard

`week5/new_evaluator.py` is the Gradio UI for the current APDC evaluator. It
imports `week5.new_evaluation`, never the legacy Insurellm evaluator. Its four
tabs expose dataset verification, deterministic behavior checks, retrieval
metrics, and provider-backed answer-quality scoring. The shared maximum-cases
control defaults to 10; `0` means the full private corpus. Per-case exceptions
are isolated and rendered by exception type without exposing exception details.

Run it from the repository root:

```powershell
& '.venv\Scripts\python.exe' 'week5\new_evaluator.py'
```

The legacy `week5/evaluator.py` remains unchanged and must not be used to judge
the APDC `new_implementation`.

## Verification commands

```powershell
$env:ANONYMIZED_TELEMETRY='False'

& '.venv\Scripts\python.exe' -m unittest discover `
  -s week5/new_implementation -p 'test_*.py' -v

& '.venv\Scripts\python.exe' -m unittest `
  week5.test_new_evaluator `
  week5.test_new_app `
  week5.new_evaluation.test_eval `
  week5.new_evaluation.test_benchmark -v

& '.venv\Scripts\ruff.exe' check `
  week5/new_implementation `
  week5/new_evaluation `
  week5/new_evaluator.py `
  week5/test_new_evaluator.py `
  week5/new_app.py `
  week5/test_new_app.py

& '.venv\Scripts\ruff.exe' format --check `
  week5/new_implementation `
  week5/new_evaluation `
  week5/new_evaluator.py `
  week5/test_new_evaluator.py `
  week5/new_app.py `
  week5/test_new_app.py

& '.venv\Scripts\python.exe' -m compileall -q `
  week5/new_implementation `
  week5/new_evaluation `
  week5/new_evaluator.py `
  week5/test_new_evaluator.py `
  week5/new_app.py `
  week5/test_new_app.py

& '.venv\Scripts\python.exe' week5/new_evaluation/eval.py --verify-dataset

& '.venv\Scripts\python.exe' -c `
  "import glob,nbformat; paths=glob.glob('week5/day*.ipynb'); [nbformat.validate(nbformat.read(p, as_version=4)) for p in paths]; print(f'Validated {len(paths)} APDC-adjacent notebooks')"

git diff --check
```

The only observed warning is a third-party protobuf deprecation warning from
Chroma dependencies.

## Operational cautions

- Stop the Gradio app before migration ingestion because the current design has
  no cross-store transactional outbox.
- Never restart the app while the SQLite generation is pending.
- Never delete raw history during rollback.
- Never project unknown/private raw payloads into prompts or semantic search.
- Do not silently approve source removal; use the explicit one-run override.
- Do not broaden a failed structured query into semantic search.
- The workspace contains unrelated user-owned changes. Do not reset, clean,
  bulk-stage, or commit them.

## Deferred production work

- Real authentication and employee identity mapping.
- PostgreSQL RLS and field/row authorization.
- Separate ingestion and retrieval database roles/DSNs.
- Transactional outbox or shared active-generation publication.
- pgvector migration and live pgvector acceptance testing.
- Domain adapters for payroll, loans, repayments, leave, or benefits.
- Approved cross-domain joins and aggregations.
- Raw-history retention and purge policy.
- Persisted benchmark baseline and automated regression gate.
- Gradual physical extraction of the large `answer.py` and `ingest.py` bodies
  behind the existing façade modules.

## Resume checklist

Before changing code, a new agent should:

1. Confirm branch and inspect the dirty worktree without modifying it.
2. Read the required documents and trace one exact, one hybrid, one semantic,
   one ambiguity, and one malformed-input question through the code.
3. Run the 257 implementation and 35 app/dashboard/evaluation/benchmark tests.
4. Verify the dataset manifest before trusting expected values.
5. Reproduce every suspected defect with a failing test.
6. Fix the rule at the deterministic compiler, schema, resolver, or backend
   boundary that owns it; do not patch individual question strings.
7. Rerun adjacent categories and the full verification commands.
8. Report evidence and remaining risks before proposing broader architecture.
