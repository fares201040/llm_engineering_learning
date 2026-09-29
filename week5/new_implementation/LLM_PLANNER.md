# Attendance direct-SQL runtime

`attendance-online/v1` is the only online attendance runtime. It preserves the small
application facade and offline ingestion while executing one initial PostgreSQL query
per turn, with at most three eligible planner repairs.

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
| `online/planner_examples.py` | Valid SQL examples generated from discovered schema fields |
| `online/evidence.py` | Typed execution evidence supplied to the planner's answer and review calls |
| `online/state.py` | Minimal verified turns and pending confirmation |
| `online/pipeline.py` | Stage order, failure mapping, and atomic publication |
| `online/provider.py` | Structured/text calls, zero transport retries, call budget, stage events |

There is no active `online.query`, `online.catalog`, or `online.audit` module. The
runtime has no flat logical query, semantic audit call, application compiler, coverage
query, witness query, narrative query route, shadow/canary path, or old state adapter.

## Model roles and payloads

The reference and query-planning roles use `openai/gpt-4.1-mini` by default.
The planner also answers from executed rows and reviews its answer. Set
`LLM_PLANNER_MAX_OUTPUT_TOKENS=6000`.

There are two configured model roles:

1. The reference/rewriter model receives the current question, conversation history,
   trusted conversation context, and verified active employees. It returns a complete
   rewritten request plus typed IDs, names, paired identity claims, general employee
   criteria, request relationship, subject union/intersection relationship, and locale.
   Its prompt preserves dates, comparisons, grouping, requested output, and follow-up
   intent. It receives no database schema or SQL vocabulary and cannot classify a
   request as unsupported. It may flag a written employee reference as ambiguous only
   for the application's authorized identity-confirmation flow. Missing individual
   employees and unclear business or scope wording continue to the SQL planner.
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
   pass. On a retry, the planner receives `sql_execution_failure` with the prior SQL
   and the execution, validation, or answer-review issue. It corrects the query
   against the unchanged request and schema without exposing its
   reasoning. An unrepresentable request should become a safe SQL `SELECT` result
   stating that it is unsupported. If schema and verified context still leave multiple
   materially different meanings, it returns a safe `clarification_required` SQL
   result containing the question to show the user.

After SQL executes, the planner receives the original question, rewritten request,
conversation history, full schema, SQL, typed rows, result coverage, and authoritative
employee identities. It writes a focused answer. A second call to the same GPT model
independently reviews the current question, draft answer, scope, SQL, and rows. It
may correct the answer directly when the rows support it. It requests another SQL
query only when the proposed answer is materially wrong or unrelated and different
SQL against the available database can correct it. In that case the same bounded
loop replans, executes, and drafts and reviews another answer. Missing calendar
rows cannot be recovered by replanning. No draft is published before review accepts
its evidence. Both prompts include generic examples of turning rows into answers;
the review prompt also includes correction and requery examples.

The pipeline reuses one `SharedModelContext` instance. Its full schema is sent to
each planner call, including the post-query answer and review calls:

1. explicit current question;
2. updated request (the original question for a new request, or a resolved
   rewrite for a follow-up);
3. untrusted conversation history;
4. labelled trusted context;
5. database type;
6. the complete allowlisted attendance schema/tables; and
7. an explicit statement that rewriting and employee resolution are complete.

Model-facing trusted context includes the latest verified request, locale, employees,
and inherited date scope. Older conversation text is compacted only when it exceeds
the shared history budget. The full executed result is supplied for the current
answer and review calls.

The planner receives every typed relational column and all 58 described
`record_json.json_fields` entries, including exact SQL expressions, normalized types,
descriptions, and standard values. Typed relational columns remain preferred when they
represent the same concept. The schema is included once in each planner request and is
sent again only when a planner retry is required; it is never duplicated within one
request.

A narrow, generic grouped-aggregate follow-up can be planned from the previous
verified SQL without another SQL-planning model call. The planner still receives the
executed comparison rows and reviews the final answer. Requests outside that
supported shape use normal SQL planning.

For follow-ups, both the reference and SQL models see the full available conversation
history, with older text compacted when it exceeds the shared history budget.
The current follow-up change and previous verified SQL are made explicit in the
planner request. The SQL planner receives calendar-month boundaries calculated from
the turn date. An inherited single date interval is checked against executed SQL;
grouped comparisons with conditional dates are not stored as a single inherited
date scope. The answer payload includes exact table availability overlap for the
previous calendar month when a comparison needs it.

The planner's answer and review calls receive the current question, rewritten
request, conversation history, trusted context, full schema, exact executed SQL,
typed result and execution coverage, authoritative employee IDs/names, and locale.
The review call also receives the proposed answer.

Every physical column in an allowlisted attendance object must have an authored
description. PostgreSQL column comments override compatibility descriptions in
`online/context.py`; a new column needs a database comment. Context loading fails
with the missing column names instead of sending an undescribed field to the SQL planner.

The `record_json` column carries nested descriptions for the normalized source
attendance fields. The descriptions distinguish immutable original device swipes
(`Actual_From_*`/`Actual_To_*`) from the corresponding clerk-adjustable,
effective swipe values (`From_*`/`To_*`). They also define positive
`Total_Worked_Hrs` as attendance evidence and direct the planner to leave and exception
fields when worked hours are null, empty, or zero.
The `work_location` description identifies a group within a department; one
department may contain several such groups. A department-wide request should not
inherit a work-location value from an earlier result.

The schema descriptions explain manual swipe comparisons, explicit absence, and
null-safe negation to the planner. The retired `online/semantic_contracts.py` contains
no active checks; the runtime no longer matches request words to prescribed SQL
predicates for these meanings. SQL safety, authorization, and independently verified
scope checks remain in the pipeline.
`scope_provenance` shows the current and prior original user questions, the
reference model's separately labelled interpretation, carried employees, and the
prior SQL. The planner checks the interpretation against the current question so a
new multi-clause request cannot silently import an earlier filter.

An explicitly requested single date or range is checked against contributing SQL
source paths. Several requested periods are not collapsed to the last month;
independent clauses can retain separate periods. Employee antecedents are kept only
when the prior employee ID filters a contributing source row path, rather than
appearing solely in a scalar lookup subquery.

## Where planner guidance belongs

For any new or corrected field meaning, standard value, nullable comparison, or
business interpretation, update the schema metadata in `online/context.py` or the
system prompts and schema-generated examples in `online/planner.py` and
`online/planner_examples.py`. PostgreSQL column comments are authoritative for new
columns; compatibility descriptions and JSON field descriptions support existing
data. Do not encode model-facing clarification in a request-word regex, a semantic
SQL validator, answer post-processing, or an evaluation assertion. Keep both SQL
prompt variants aligned with the same schema examples.

Examples should illustrate valid general SQL shapes. They must not contain invalid
placeholders or sample values that the planner might treat as this turn's filters.
Review an answer against the requested period, observed table dates, executed SQL,
rows, and any missing data. Literal wording checks and embedding similarity cannot
alone establish answer correctness.

The planner counts distinct attendance dates when a request asks for a number of
days, unless the request explicitly asks for records or rows. Attendance detail-row
queries include `record_id` so returned evidence retains a stable traceable identity.
Scheduled work dates use `day_type = 'Working Day'`; generic off-day requests include
both `OFF Day` and `OFF Day (ZAS)`. Positive `total_worked_hrs` proves attendance,
while zero or null alone does not prove absence. Explicit absence is
`exception = 'Absent'`. Worked-time calculations use clerk-adjustable `From_*`/`To_*`
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

General criteria remain natural language for the SQL planner. A reference-model
`missing_employee` decision without a written unresolved person is also converted to
an all-authorized planner request. This prevents a department, group, criteria, Arabic,
or indirect follow-up request from being rejected merely because no person was named.
Written names that resolve to multiple authorized people still use confirmation before
planning, and an employee inherited from state is never used after it leaves the
caller's authorized directory.

## Physical database context

`load_database_context()` queries PostgreSQL metadata only for configured attendance
objects. It records engine/dialect/version, object type, exact schema/table/column
names, physical types, nullability, application descriptions, known stored values,
optional relationship metadata, and the exact query expressions and normalized types
for nested JSONB source fields. The current allowlist contains the attendance table,
so it has no cross-table relationship metadata. It never selects application rows,
system/auth/config or private-ingestion objects, migration tables, or credentials.

## Direct execution and bounds

The planner string is parsed as one read-only SQL query, checked against the
allowlisted attendance tables and request-scope guards, and then executed. When
employee row scope applies, each base table is wrapped with the authoritative
employee-ID filter before execution. Execution starts
`REPEATABLE READ READ ONLY`, applies local
statement/lock/idle-transaction timeouts, fetches at most `result_limit + 1`, bounds
the serialized response size, records column type codes and row coverage, and rolls
back on both success and failure. Database/provider/size/timeout failures return a safe
failed outcome and preserve the exact prior trusted state.

A PostgreSQL `ProgrammingError`, `DataError`, invalid SQL syntax, semantic scope
failure, result bound, or tentative unsupported-schema decision can trigger a
planner retry: no more than four planning attempts. Each retry receives
the failed SQL, error type, database error text capped at 4,000 characters, retry
number, and the same `SharedModelContext` with the same complete schema. An
unavailable table stops as unsupported before a retry can silently drop the requested
relation. Connection, timeout, authorization, provider, and answer failures do not
trigger SQL repair. A fourth eligible rejection fails safely without publishing
conversation state. The maximum provider-call budget remains 8.

Malformed employee identifier shapes and recognized impossible dates or
non-finite/malformed numeric comparisons are rejected before planning. An unknown
standalone ID receives no candidate alternatives. Unresolved names can produce
confirmation-only candidates after authorized-directory checks. Unsupported concepts,
including non-attendance requests, use the planner's `unsupported_capability` SQL-result
protocol and return an explicit unsupported outcome. Genuine business ambiguity uses
the parallel `clarification_required` protocol. Both control protocols accept only a
single literal text expression with no table reference. The same planner answer and
review calls present the request-specific explanation or question; no verified turn
is published for a control result.
Empty/malformed provider responses and planner Markdown fences fail safely.
Read-only database permissions remain a separate defense if a validator misses a
query form.

## Security limitation and deferred safeguards

The runtime checks that the query is a single read-only statement over exposed
tables and applies authoritative employee row scope. It also bounds execution time,
returned rows, and response bytes. These controls do not yet allowlist every SQL
function, operator, or column, so the database role should expose only approved data.

Deferred work:

1. expand the AST checks to allowlist columns, functions, operators, and joins;
2. add a database-side query complexity budget; and
3. use bound parameters for model-authored literal values where practical.

## State and outcomes

Only a completed planner answer review publishes a `VerifiedTurn` containing original and rewritten
requests, answer, locale, authoritative employees, exact executed SQL, and typed
result. A clarification may store one pending employee confirmation. All provider,
SQL, bound, and verification failures preserve prior state. State from another runtime
version resets safely.

## Gradio publication

`week5/new_app.py` renders reviewed answers as sanitized Markdown. The question
appears immediately and a verification status appears below the textbox. Once the
pipeline returns a reviewed final answer, the UI reveals it in cumulative chunks;
this is progressive display after review, not live provider-token streaming. Clear
and Submit sequence numbers prevent obsolete work from repopulating a cleared chat.

## Current local verification (2026-09-29)

At commit `e5e9dadc`, local discovery in `week5/new_implementation` passed 254
tests, `week5/test_new_app.py` passed 20 tests, and Ruff lint/format and staged
diff checks passed. A live model/database question with Authorized counts by
country and all-HR people by work location produced independent SQL branches and
the checked 770/4/1 country counts plus 9 HR people. This is local evidence, not
a Colab or private-evaluation pass for the current commit. The exact manual prompts
are listed in `docs/superpowers/reports/2026-09-29-attendance-manual-conversation-reference.md`.

## Historical verification record (2026-09-28)

The then-current sanitized source archive had SHA-256
`b7c9afd1cc0a520a1e8fbc1ea461f7439f6136936e4a9822d715d229cec72ffc`.
The Colab CPU notebook validated its 55 allowlisted source files, passed 266
deterministic tests, and passed Ruff lint, formatting, and Python compilation.
The archive contains a generated 311-line placeholder manifest; the private
evaluation corpus, credentials, and attendance exports are supplied separately
in the authorized Colab runtime. Prompt comparisons and the remaining private
cases are evaluated separately from this deterministic gate.
