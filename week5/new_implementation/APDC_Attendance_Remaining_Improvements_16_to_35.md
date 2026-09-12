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
3. `docs/superpowers/plans/2026-09-12-apdc-foundation-and-answer-correctness-implementation.md`.
4. `attendance_schema.py`, especially `QueryPlan`, `FIELD_DEFINITIONS`,
   `MEASURE_DEFINITIONS`, `BUSINESS_PREDICATE_DEFINITIONS`, and
   `compile_business_intent()`.
5. `answer.py`, following `fetch_context()` and
   `answer_question_with_state()` rather than reading helpers randomly.
6. `source_ingestion.py`, `ingest.py`, `ingestion_state.py`, and
   `chroma_client.py`.
7. `new_evaluation/test.py`, `eval.py`, `tests.jsonl`, and their unit tests.

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
| Implementation tests | 219 passing |
| App/evaluation/benchmark tests | 27 passing |
| Pending ingestion generations | 0 |

The dataset fingerprint is kept in the local-only evaluation manifest and is
not published.

Repository handoff baseline: GitHub `main` commit
`931445979f8ef9e2909f0282d95e5ce41b4cc421`. That commit was independently
reviewed with no remaining Critical or Important finding before it was pushed.

The private raw table also contains one historical quarantined fixture row.
That row is intentional append-only history, not an active source and not stale
data to delete.

Local verification covers employee-scoped worked days, scheduled days,
overtime, authorization status, and record identity. Employee-linked expected
values stay in the ignored private evaluation corpus and are not reproduced in
this publishable handoff. Only non-identifying aggregate acceptance results are
reported here.

## New observed issue and next-agent mission

This section records the new screenshot received after the baseline above. It
is an unresolved investigation request, not a verified diagnosis.

### Sanitized observed conversation

The real employee ID and employee-linked result values remain local and are not
published in this handoff. During authorized local reproduction, replace
`E00001` below with the employee ID from the supplied screenshot.

1. The user asked how many days synthetic employee `E00001` attended during a
   month that extends beyond the loaded coverage. The assistant returned a
   worked-day result with the correct partial-coverage warning.
2. The user asked how many days the same employee did not attend. The assistant
   returned a scheduled-non-attendance result with the same warning.
3. The user asked `who is this employee`.
4. The assistant incorrectly returned:

   ```text
   I could not safely interpret that request: Interpretation clarification
   requires at least two distinct choices.
   ```

5. The Gradio **Relevant Context** panel was empty for the displayed response.

The expected third-turn behavior is to retain the validated employee selected
by the earlier turns, retrieve only evidence for that employee, answer the
identity question from trusted directory/evidence data, and render the evidence
used in the Relevant Context panel. It must not invent an identity, retrieve
another employee, or expose private fields outside the approved context schema.

### What is confirmed and what still requires tracing

Confirmed from the previous investigation:

- Gradio passes prior chat messages and a per-session `ConversationState` into
  `answer_question_with_state()`.
- Direct employee resolution is committed to `selected_employees` only after
  successful retrieval.
- Singular follow-ups such as `Who is this employee?` reuse the selected
  employee, while population/group/ranking requests do not.
- Worked-day and scheduled-non-attendance calculations for the shown employee
  were source-checked against the real local retrieval backend.

The new error text strongly suggests that a one-item interpretation candidate
set reached a validation/clarification boundary that requires at least two
choices. That is only a hypothesis until the next agent captures the actual
question, history, state before/after, raw planner output, normalized plan,
interpretation candidates, resolved employee IDs, retrieved chunks, and app
callback outputs from the failing runtime. The empty context panel may be a
downstream consequence of the safe-error path returning no chunks, or it may be
a separate rendering/data-flow defect. Trace both independently.

### Required investigation workflow

The next agent must first read this handoff and the scripts listed in the
required reading order. Then:

1. Reproduce the exact three-turn screenshot flow through `new_app.chat_with_state()`
   and through the running Gradio UI. Capture state and evidence at every
   boundary without logging private values.
2. Add a failing regression that proves the actual root cause before changing
   production code. Fix the owning schema, compiler, resolver, state, retrieval,
   or rendering boundary; do not special-case the exact user sentence.
3. Verify that a valid single interpretation is accepted directly, while true
   ambiguity still requires at least two distinct choices and unsafe/empty
   interpretations still fail closed.
4. Verify that deterministic answers and narrative answers both return the
   evidence actually used, and that `new_app._render_context()` displays it.
   If a calculation intentionally needs summarized rather than row-level
   evidence, provide a truthful structured evidence object instead of fabricating
   context.
5. Run a long, stateful Gradio conversation—at least 60 turns, plus fresh-session
   controls—covering direct IDs, names, pronouns, `this employee`, changed
   employees, unknown and ambiguous employees, worked/not-worked/absent/scheduled
   distinctions, record counts, authorization, overtime, percentages, grouping,
   ranking, dates, coverage, semantic questions, malformed input, retries, and
   clear/reset behavior. For every turn, record the expected scope/calculation,
   actual answer, selected state, pending state, and displayed evidence.
6. When a wrong answer, missing context, state leak, unsafe behavior, or unrelated
   code defect is found, stop that scenario, trace it to the first incorrect
   boundary, add a failing test, implement the native fix, rerun adjacent tests,
   restart/reload Gradio when necessary, and resume the long conversation. Do
   not accumulate unexplained failures until the end.
7. Test at least two independent Gradio sessions interleaved to prove that
   employee selection, pending clarification, and history cannot cross sessions.
8. Review risky logic step by step: model-plan trust boundaries, contradiction
   detection, single/multiple interpretation handling, pending-state mutation,
   employee follow-up scoping, exact/hybrid/semantic routing, zero-result
   behavior, coverage wording, context propagation, HTML escaping, exception
   paths, and clean-checkout behavior without private fixtures.
9. Run the complete verification commands, independently review the final diff,
   update this handoff and related documentation, commit all authorized source,
   tests, docs, and notebooks, and push to
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
rule set. The latest screenshot-specific three-turn replay passed through the
real state, normalization, employee-directory, and PostgreSQL boundaries with
only the unavailable provider calls replaced. The configured LiteLLM endpoint
was refusing connections during the final live-provider probe; this is an
environmental verification limitation, not recorded as a passing live run.

## Verification commands

```powershell
$env:ANONYMIZED_TELEMETRY='False'

& '.venv\Scripts\python.exe' -m unittest discover `
  -s week5/new_implementation -p 'test_*.py' -v

& '.venv\Scripts\python.exe' -m unittest `
  week5.test_new_app `
  week5.new_evaluation.test_eval `
  week5.new_evaluation.test_benchmark -v

& '.venv\Scripts\ruff.exe' check `
  week5/new_implementation `
  week5/new_evaluation `
  week5/new_app.py `
  week5/test_new_app.py

& '.venv\Scripts\ruff.exe' format --check `
  week5/new_implementation `
  week5/new_evaluation `
  week5/new_app.py `
  week5/test_new_app.py

& '.venv\Scripts\python.exe' -m compileall -q `
  week5/new_implementation `
  week5/new_evaluation `
  week5/new_app.py

& '.venv\Scripts\python.exe' week5/new_evaluation/eval.py --verify-dataset
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
3. Run the 219 implementation and 27 app/evaluation/benchmark tests.
4. Verify the dataset manifest before trusting expected values.
5. Reproduce every suspected defect with a failing test.
6. Fix the rule at the deterministic compiler, schema, resolver, or backend
   boundary that owns it; do not patch individual question strings.
7. Rerun adjacent categories and the full verification commands.
8. Report evidence and remaining risks before proposing broader architecture.
