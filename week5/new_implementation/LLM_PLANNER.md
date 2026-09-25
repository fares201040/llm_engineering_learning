# Attendance direct-SQL runtime

`attendance-online/v1` is the only online attendance runtime. It preserves the small
application facade and offline ingestion while executing one initial PostgreSQL query
per turn, with at most one eligible retry after a PostgreSQL programming or data error.

## Active modules

| Module | Responsibility |
|---|---|
| `answer.py` | Tuple-returning compatibility facade |
| `embedding.py` | Shared Hugging Face/OpenAI embedding selection and provider-aware text splitting |
| `rebuild_chroma.py` | Rebuild a selected model's Chroma document collection while retaining the source for a later switch |
| `online/reference.py` | Complete request rewrite, typed employee references, authorized exact/fuzzy/Chroma-confirmed resolution |
| `online/context.py` | Immutable allowlisted physical PostgreSQL metadata and shared context with question/history |
| `online/planner.py` | Request-specific schema projection, SQL-only prompt, and raw text response |
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
`reasoning_effort="none"`, `temperature=0`, and `num_ctx=8192`. Set
`LLM_PLANNER_MAX_OUTPUT_TOKENS=512` for the planner.

There are exactly three configured roles:

1. The reference/rewriter model receives the current question, conversation history,
   trusted conversation context, and verified active employees. It returns a complete
   rewritten request plus typed IDs, names, paired identity claims, general employee
   criteria, request relationship, subject union/intersection relationship, and locale.
   Its prompt preserves dates, comparisons, grouping, requested output, and follow-up
   intent. It receives no database schema or SQL vocabulary.
2. The SQL planner receives a request-specific payload derived from the immutable
   `SharedModelContext` and returns plain SQL.
   Its prompt explains what query to produce, how to map every business term to exact
   supplied physical identifiers, and why physical schema alignment is its
   responsibility. Before generating SQL it must study each candidate column's exact
   name, type, nullability, authored description, and standard values. It must
   also inspect the supplied request-relevant `json_fields` entries and use each
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
6. for the planner only, projected allowlisted attendance schema/tables; and
7. an explicit statement that rewriting and employee resolution are complete.

The planner projection always keeps every typed relational column and its description.
It omits table date-coverage metadata so the planner cannot turn observed availability
into a permanent SQL filter. The writer and verifier still receive coverage bounds.
It removes JSON fallback entries duplicated by typed columns and retains only
request-relevant JSON-only fields (or the JSON-only catalog when the request explicitly
asks for it). The verified case 17 planner payload was 63.2% smaller after projection.
The schema is normally sent once per question and is sent again only after an eligible
SQL retry.

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

Every physical column in an allowlisted attendance object must have an authored
description in `online/context.py`. Context loading fails with the missing column names
instead of sending a generic or undescribed field to the SQL planner.

The `record_json` column carries nested descriptions for the normalized source
attendance fields. The descriptions distinguish immutable original device swipes
(`Actual_From_*`/`Actual_To_*`) from the corresponding clerk-adjustable,
payroll-effective swipe values (`From_*`/`To_*`). They also define positive
`Total_Worked_Hrs` as attendance evidence and direct the planner to leave and exception
fields when worked hours are null, empty, or zero.

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

The exact planner string is passed to `connection.execute(sql)` without parameters,
AST parsing, returned-identifier validation, authorization wrapping, limit injection,
or literal conversion. Execution starts `REPEATABLE READ READ ONLY`, applies local
statement/lock/idle-transaction timeouts, fetches at most `result_limit + 1`, bounds
the serialized response size, records column type codes and row coverage, and rolls
back on both success and failure. Database/provider/size/timeout failures return a safe
failed outcome and preserve the exact prior trusted state.

A PostgreSQL `ProgrammingError` or `DataError` triggers at most one repair retry after
the initial query: no more than two database execution attempts. The retry receives
the failed SQL, error type, database error text capped at 4,000 characters, retry
number, and the same `SharedModelContext` with the same projected schema. Connection,
timeout, result-bound, authorization, provider, and answer failures do not trigger SQL
repair. A second eligible rejection fails safely without publishing conversation
state. The maximum provider-call budget is 8: up to two reference attempts, two
planner attempts, and four answer writer/verifier attempts.

Malformed employee identifier shapes and recognized impossible dates or
non-finite/malformed numeric comparisons are rejected before planning. An unknown
standalone ID receives no candidate alternatives. Unresolved names can produce
confirmation-only candidates after authorized-directory checks. Unsupported
non-attendance requests stop before SQL; unsupported schema concepts use the
`unsupported_capability` SQL-result protocol and return an explicit unsupported
outcome. Empty/malformed provider responses and planner Markdown fences fail safely.
The runtime does not structurally parse SQL before execution.

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

## Verification record (2026-09-25)

The previously verified sanitized archive SHA-256 was
`fccad7a9e9ea23cb3e0c70c80e4734258a9072f0811584b269606adfe1f1a19d`.
The [executed Colab notebook](colab/attendance_phase2_tests_output.ipynb) validated
that hash and passed **127 deterministic tests**, Ruff lint, formatting for 19 files,
and Python compilation in a T4 session; it has no error cells. The ZIP contains 32
allowlisted source/helper files and a generated 311-line placeholder manifest. It
excludes credentials, environment files, attendance exports, previous results, and
the private evaluation corpus. Deterministic checks mock model/database boundaries.

The Ollama setup failure was caused by missing `zstd`; the setup helper now installs
it, starts Qwen 3.5 4B, and verifies the temporary read-only PostgreSQL database with
16 synthetic rows and three employees. The one-turn runner now launches a fresh Python
process per invocation so Colab's persistent kernel cannot reuse stale imported code.
The approved UI was reviewed without adding features.

The long synthetic scenario was run one turn per CLI invocation. Turns 1 and 2 passed:
five September worked dates for A11017 and the explicit September 6 absence. Turn 3
failed semantic acceptance. With the latest source, SQL correctly used
`exception IS DISTINCT FROM 'Absent'`, but omitted the inherited September interval
and returned two August dates alongside September 1–5. The exact five-row oracle
rejected the result. The [saved synthetic checkpoint](colab/attendance_phase3_long_synthetic_negation.json)
contains the answer, SQL, result, and state checks. Turns 4–8 were not run. The T4
session was stopped after the failed turn.

Later synthetic Gradio callback acceptance passed verified turns 1–4. Turn 5 exposed
inaccurate relative-month comparison SQL and answer wording, so the sequence again
stopped before turns 6–8. The current code includes further context, planner, and
answer-grounding changes that have not yet run in Colab: the runtime expired and
replacement T4 creation returned `Service Unavailable`. See
[the sync guide](colab/LOCAL_COLAB_SYNC_GUIDE.md) for tested and pending snapshot
hashes. The grouped comparison remains unverified.

The model can still publish a plausible answer after misinterpreting follow-up scope;
the model verifier did not catch this case. Structural SQL authorization and semantic
validation remain deferred as described above. Do not treat passing deterministic
tests as evidence of long-conversation correctness. Do not run the private 311-case
evaluation or the seven-case batch. Cases 20, 23, 26, 29, 94, and 128 remain
unverified; Wail, Faris, and generic-subject live scenarios still lack factual oracles.
