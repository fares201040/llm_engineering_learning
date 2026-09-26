# Attendance direct-SQL runtime

`attendance-online/v1` is the only online attendance runtime. It preserves the small
application facade and offline ingestion while executing one initial PostgreSQL query
per turn, with at most two eligible retries after PostgreSQL programming or data errors.

## Active modules

| Module | Responsibility |
|---|---|
| `answer.py` | Tuple-returning compatibility facade |
| `embedding.py` | Shared Hugging Face/OpenAI embedding selection and provider-aware text splitting |
| `rebuild_chroma.py` | Rebuild a selected model's Chroma document collection while retaining the source for a later switch |
| `online/reference.py` | Complete request rewrite, typed employee references, authorized exact/fuzzy/Chroma-confirmed resolution |
| `online/context.py` | Immutable allowlisted physical PostgreSQL metadata and shared context with question/history |
| `online/planner.py` | Complete schema payload, SQL-only prompt, and raw text response |
| `online/comparison.py` | Schema-checked relative-month comparison of a previous verified grouped aggregate |
| `online/execution.py` | Access check, ID/name directory lookup, bounded read-only direct SQL execution |
| `online/answering.py` | Same-model answer writing and independent verification |
| `online/state.py` | Minimal verified turns and pending confirmation |
| `online/pipeline.py` | Stage order, failure mapping, and atomic publication |
| `online/provider.py` | Structured/text calls, zero transport retries, call budget, stage events |

There is no active `online.query`, `online.catalog`, or `online.audit` module. The
runtime has no flat logical query, semantic audit call, application compiler, coverage
query, witness query, narrative query route, shadow/canary path, or old state adapter.

## Model roles and payloads

For the current Phase 2 local configuration, the three roles use
`ollama_chat/qwen3.5:4b`. The provider sends local calls with
`reasoning_effort="none"` and `temperature=0`. The SQL planner uses
`num_ctx=65536`; the answer writer and verifier also use `num_ctx=65536`, while the
reference stage uses `num_ctx=8192`. Set
`LLM_PLANNER_MAX_OUTPUT_TOKENS=512` for planner output.

There are exactly three configured roles:

1. The reference/rewriter model receives the current question, conversation history,
   trusted conversation context, and verified active employees. It returns a complete
   rewritten request plus typed IDs, names, paired identity claims, general employee
   criteria, request relationship, subject union/intersection relationship, and locale.
   Its prompt preserves dates, comparisons, grouping, requested output, and follow-up
   intent. It receives no database schema or SQL vocabulary and cannot classify a
   request as unsupported. It may flag an unresolved employee reference as ambiguous
   for the application's confirmation flow.
2. The SQL planner receives a payload derived from the immutable
   `SharedModelContext` and returns plain SQL.
   Its prompt explains what query to produce, how to map every business term to exact
   supplied physical identifiers, and why physical schema alignment is its
   responsibility. Before generating SQL it must study each candidate column's exact
   name, type, nullability, authored description, and standard values. It must
   also inspect every supplied `json_fields` entry and use each
   exact SQL expression and normalized type. It must preserve authoritative employee
   IDs and return no JSON, Markdown, explanation, or mapping. Joins, nested queries,
   aggregates, and CTEs are permitted. The initial request has no extra SQL-review
   pass. Only after an eligible PostgreSQL error does the planner receive
   `sql_execution_failure`; it then reviews the failed SQL and database error against
   the unchanged request and schema and returns corrected SQL without exposing its
   reasoning. An unrepresentable request should become a safe SQL `SELECT` result
   stating that it is unsupported.
3. The answer model is invoked in two independent calls with distinct complete system
   prompts. The writer grounds a complete answer only in the executed result and must
   name authoritative employees. The verifier independently checks attribution,
   values, dates, units, polarity, coverage, omissions, unsupported claims, injection,
   and consistency. One rejected draft may be rewritten and verified once more.

The pipeline reuses one `SharedModelContext` instance. Its model payloads differ by
stage so the full attendance schema is sent only to the SQL planner:

1. explicit current question;
2. updated rewritten request;
3. untrusted conversation history;
4. labelled trusted context;
5. database type;
6. for the planner only, the complete allowlisted attendance schema/tables; and
7. an explicit statement that rewriting and employee resolution are complete.

Model-facing trusted context includes the latest verified request, locale, employees,
and inherited date scope. The complete prior answer, raw SQL result, and executed SQL
remain in application state but are omitted from later model payloads so a large
bounded answer cannot overflow the next reference or answer call.

The planner receives every typed relational column and all 58 described
`record_json.json_fields` entries, including exact SQL expressions, normalized types,
descriptions, and standard values. Typed relational columns remain preferred when they
represent the same concept. The schema is included once in each planner request and is
sent again only when a planner retry is required; it is never duplicated within one
request.

A narrow, generic grouped-aggregate follow-up is planned from the previous verified
SQL without another model rewrite. The prior query must be a single grouped SUM/COUNT
over an allowlisted table, with one group column, one aggregate, and no prior date
scope. The comparison keeps the prior query as the eligibility CTE and calculates
the previous and current calendar-month values from PostgreSQL rows. Its deterministic
answer checks each group, numeric difference, result count, and available date range.
Requests outside that supported shape use the normal model path.

For follow-ups, the model sees the latest verified turn and the previous user question.
The current follow-up change and previous verified SQL are made explicit in the
planner request. The SQL planner receives calendar-month boundaries calculated from
the turn date. An inherited single date interval is checked against executed SQL;
grouped comparisons with conditional dates are not stored as a single inherited
date scope. The answer payload includes exact table availability overlap for the
previous calendar month when a comparison needs it.

The writer and verifier receive the current question, rewritten request, conversation
history, trusted context, database type, resolution statement, database date-coverage
summary, exact executed SQL, typed result and execution coverage, authoritative
employee IDs/names, and locale. They do not receive the attendance schema. The verifier
also receives the proposed answer.
The writer's coverage-bound check accepts exact ISO dates and equivalent spelled-out
English dates; the verifier still judges whether the coverage statement is accurate.

Every physical column in an allowlisted attendance object must have an authored
description in `online/context.py`. Context loading fails with the missing column names
instead of sending a generic or undescribed field to the SQL planner.

The `record_json` column carries nested descriptions for the normalized source
attendance fields. The descriptions distinguish immutable original device swipes
(`Actual_From_*`/`Actual_To_*`) from the corresponding clerk-adjustable,
payroll-effective swipe values (`From_*`/`To_*`). They also define positive
`Total_Worked_Hrs` as attendance evidence and direct the planner to leave and exception
fields when worked hours are null, empty, or zero.

For manual-swipe requests, the runtime parses the proposed SQL and requires one filter
whose four effective-versus-device comparisons are joined with `OR`. Comparisons
joined only with `AND`, emitted outside a filter, or missing a pair trigger a planner
retry. Detection uses both the original and self-contained rewritten request, including
supported Arabic manual-swipe wording and inherited follow-up intent.

The planner counts distinct attendance dates when a request asks for a number of
days, unless the request explicitly asks for records or rows. Attendance detail-row
queries include `record_id` so returned evidence retains a stable traceable identity.
Scheduled work dates use `day_type = 'Working Day'`; generic off-day requests include
both `OFF Day` and `OFF Day (ZAS)`. Positive `total_worked_hrs` proves attendance,
while zero or null alone does not prove absence. Explicit absence is
`exception = 'Absent'`. Payroll calculations use clerk-adjustable `From_*`/`To_*`
values; immutable `Actual_From_*`/`Actual_To_*` values are for device-swipe questions.

## Employee authority

The runtime first filters the PostgreSQL employee directory to the caller's
`AccessContext`. Directory SQL selects only distinct `employee_id` and `name`.
Exact ID/name matches bind immediately. PostgreSQL first searches whole-name trigram
similarity, then first-name token similarity when the whole-name search is empty.
Both fuzzy paths return confirmation-only candidates; they never bind an ID from
similarity alone. Otherwise Chroma may supply at most five candidates, but each
candidate must still match the current authorized PostgreSQL ID/name pair and must be
selected explicitly. Unknown standalone IDs receive no alternatives.
The Chroma fallback embeds names with the configured embedding provider and
validates every result against the current authorized directory. Semantic
similarity supplies confirmation choices; it never binds an employee by itself.

Resolved employees are attached in one stable form:

```text
Resolved employees:
- Faris Hassan (A10114)

Request:
Show total worked hours during September 2026.
```

General criteria remain natural language for the SQL planner.

## Physical database context

`load_database_context()` queries PostgreSQL metadata only for configured attendance
objects. It records engine/dialect/version, object type, exact schema/table/column
names, physical types, nullability, application descriptions, known stored values,
optional relationship metadata, and the exact query expressions and normalized types
for nested JSONB source fields. The current allowlist contains the attendance table,
so it has no cross-table relationship metadata. It never selects application rows,
system/auth/config or private-ingestion objects, migration tables, or credentials.

## Direct execution and bounds

The planner string is checked for an unrequested attendance-date predicate, then
passed to `connection.execute(sql)` without parameters, general structural
authorization, limit injection, or literal conversion. Execution starts
`REPEATABLE READ READ ONLY`, applies local
statement/lock/idle-transaction timeouts, fetches at most `result_limit + 1`, bounds
the serialized response size, records column type codes and row coverage, and rolls
back on both success and failure. Database/provider/size/timeout failures return a safe
failed outcome and preserve the exact prior trusted state.

A PostgreSQL `ProgrammingError`, `DataError`, or an unrequested date filter triggers
at most two planner retries after the initial query: no more than three planning
attempts. Each retry receives
the failed SQL, error type, database error text capped at 4,000 characters, retry
number, and the same `SharedModelContext` with the same complete schema. Connection,
timeout, result-bound, authorization, provider, and answer failures do not trigger SQL
repair. A third eligible rejection fails safely without publishing conversation
state. The maximum provider-call budget remains 8; a third planner call consumes one
of the calls otherwise available for answer writing or verification.

Malformed employee identifier shapes and recognized impossible dates or
non-finite/malformed numeric comparisons are rejected before planning. An unknown
standalone ID receives no candidate alternatives. Unresolved names can produce
confirmation-only candidates after authorized-directory checks. Unsupported concepts,
including non-attendance requests, use the planner's `unsupported_capability` SQL-result
protocol and return an explicit unsupported outcome. Empty/malformed provider responses
and planner Markdown fences fail safely.
The narrow date-predicate check uses SQL parsing; it does not provide general SQL
authorization.

## Security limitation and deferred safeguards

This implementation is intentionally not secure against unauthorized reads available
to the database role, expensive valid SQL, or prompt-injected SQL. Read-only database
permissions prevent writes but do not provide structural query authorization.

Deferred work:

1. parse PostgreSQL SQL into an AST;
2. require exactly one read-only statement;
3. allowlist every table, column, function, operator, and join;
4. inject authoritative row authorization outside model control;
5. enforce result/complexity limits structurally in SQL; and
6. replace model-authored literals with bound parameters.

## State and outcomes

Only a passing verifier publishes a `VerifiedTurn` containing original and rewritten
requests, answer, locale, authoritative employees, exact executed SQL, and typed
result. A clarification may store one pending employee confirmation. All provider,
SQL, bound, and verification failures preserve prior state. State from another runtime
version resets safely.

## Verification record (2026-09-27)

The current sanitized archive SHA-256 is
`f04377f8bf8ec1884f845e591b64d4ee7cc8f6a31cb6fd42b7fe20e495ef8c3c`.
The local deterministic suite passed **248 tests and 10 subtests** after the
manual-swipe validation and context-bound fixes. The archive has a generated 311-line
placeholder manifest, but excludes the private evaluation corpus and all credentials
and attendance exports. The source [Colab notebook](colab/attendance_phase2_tests.ipynb)
contains the current hash and is ready for a new Colab execution.

The last [executed Colab notebook](colab/attendance_phase2_tests_output.ipynb) remains
the A100 record for the preceding archive
`c77544ccf51f9805267dd568b03c5a4478f9eb0a1beeab238895dc4afd7b36c1`; it validated
48 allowlisted files and passed **245 deterministic tests**, Ruff lint and formatting
for 29 files, and Python compilation. The synthetic database has 16 rows and three
employees; the temporary model is Qwen 3.5 4B.

On that preceding snapshot, the private A100 evaluator passed cases 100–149 (50/50), the 25-case fixed-case
conversation replay including employee confirmations (25/25), and cases 150–199
(50/50). The exact manual-swipe question produced all 65 returned employees from one
planner attempt, with no answer truncation.

The synthetic UI conversation passed turns 1–7 on the preceding source snapshot.
Turn 8 was rerun against that A100 archive from its verified seven-turn checkpoint;
the running total, result arithmetic, answer facts, and state checks passed. The
checkpoint reports eight completed turns. The native grouped relative-month plan
corrected turn 5's eligibility and date-coverage errors. The native running-total
plan derives its metric from the previous verified grouped SQL.

The live synthetic evaluator completed four cases with no failed flags on that
archive. A prior run found that the planner invented September/August filters for a
date-unbounded department ranking. The SQL date-predicate guard now rejects that
plan before execution and retries against the unchanged request. See
[the sync guide](colab/LOCAL_COLAB_SYNC_GUIDE.md) and
[the phase handoff](../../docs/superpowers/reports/2026-09-26-attendance-colab-evaluation-handoff.md)
for issue classes and run details. The private 311 cases have not been run in Colab.
