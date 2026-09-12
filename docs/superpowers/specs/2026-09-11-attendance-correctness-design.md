# APDC Attendance Correctness and Clarification Design

## Purpose

Correct the confirmed APDC attendance answer-quality defects at their source.
The design keeps the LLM for language interpretation while ensuring that
attendance semantics, employee identity, executable filters, calculations,
clarification, and session state are deterministic and database-backed.

This work includes Improvements 18, 20, 21, 22, 26, and 33 where they are
needed for the confirmed defects. Improvement 24 is already implemented. The
unrelated roadmap items remain outside this implementation cycle.

## Constraints

- PostgreSQL attendance records are authoritative for exact facts and
  calculations when PostgreSQL is enabled.
- Do not silently combine employees or semantically broaden an unresolved
  structured identity search.
- An explicit valid employee ID has priority over a conflicting name.
- Preserve the public `answer_question(question, history)` interface.
- Use one hidden Gradio state value per browser session. Do not add global
  conversation storage.
- Do not add dependencies or a generalized entity-resolution framework.
- Add a failing regression test before every behavioral production change.
- Keep changes focused and readable. Remove superseded helpers, stale imports,
  obsolete comments, duplicate logic, and temporary diagnostics before final
  verification.
- Remove the old Insurellm evaluation data and replace it with APDC attendance
  cases. Do not preserve it as an active or legacy evaluation corpus.

## Confirmed Root Causes

### Attendance semantics are not executable rules

The planner prompt has field names but no canonical data dictionary. It may
therefore count every record for “worked days,” omit `Day_Type = "Working
Day"` for scheduled-day questions, or omit `Status = "Authorized"` for
authorized-record questions. `_format_aggregation_answer()` changes only the
label and cannot repair an incorrect SQL calculation.

### Raw LLM plans are executed with insufficient normalization

Apart from relative dates and limited name handling, the system executes the
planner's raw mode, filters, and aggregation. Repeated calls can classify the
same name as `Name`, `Employee_ID`, or no identity; select different modes;
and emit invalid date or aggregation values. Typed validation occurs only in
parts of the PostgreSQL SQL builder, after routing has already happened, and
is not consistently applied to Chroma paths.

### Employee resolution discards identity

The current resolver returns distinct names rather than `(employee_id, name)`
candidates. Duplicate names lose their IDs, and multiple matches become a
single `Name IN (...)` filter. No-match exact plans are broadened to hybrid
retrieval. The PostgreSQL fuzzy query compares a short misspelling against the
entire full name, which gives `muhtar` a score below the configured threshold.

### Clarification has no state or execution gate

Every message is replanned from chat text. There is no saved query plan,
candidate list, selected employee, or validation of `1`, a name, an ID,
`both`, or `all`. Consequently, a clarification-looking reply can work only
when the LLM happens to reconstruct the original request. Nothing prevents
retrieval and answer generation while identity is ambiguous.

### The evaluation corpus measures the wrong product

`week5/new_evaluation/tests.jsonl` contains 150 Insurellm company/product
questions. It does not measure APDC attendance planning, retrieval,
calculation, identity resolution, clarification, or final answers.

### Split embedding parts are never reunited

Ingestion records `record_id`, `embedding_part`, and
`embedding_parts_total`, but semantic retrieval returns only the individually
matched parts. A future oversized attendance record can therefore reach the
final answer without its sibling parts.

## Architecture

The request path becomes:

```text
Gradio message + per-session ConversationState
    -> clarification reply handling, when pending
    -> LLM QueryPlan for a new request
    -> deterministic plan normalization and validation
    -> database-backed employee resolution
    -> clarification/no-match response OR executable plan
    -> exact or filter-first semantic retrieval
    -> related split-part expansion for semantic results
    -> deterministic aggregation
    -> reranking only when needed
    -> question-specific final context
    -> deterministic answer or final-answer LLM
    -> updated ConversationState
```

Only an executable plan may cross the retrieval boundary.

## Canonical Attendance Data Dictionary

Create `week5/new_implementation/attendance_schema.py`. It contains immutable,
declarative definitions for attendance fields and calculation concepts. It is
not a collection of employee-specific cases or canned answers.

Each field definition records:

- canonical field name;
- storage type (`text`, `date`, or `number`);
- accepted operators;
- user-language aliases used for relevant-context selection;
- concise domain meaning;
- known closed values where the dataset has a controlled vocabulary.

The module defines reusable calculation concepts:

- `worked_days`: `COUNT(DISTINCT Date)` with `Total_Worked_Hrs > 0`;
- `scheduled_working_days`: `COUNT(DISTINCT Date)` with
  `Day_Type = "Working Day"`;
- `attendance_records`: `COUNT(*)`;
- `authorized_records`: `COUNT(*)` with `Status = "Authorized"`;
- `employees`: `COUNT(DISTINCT Employee_ID)`;
- numeric sum, average, minimum, and maximum over an allowed numeric field.

The planner and final-answer prompts receive only the concise definitions
relevant to the current question. SQL/Python, not prompt wording, applies the
calculation definitions. Improvement 24's field aliases move into this module
so field meaning and context selection have one source of truth.

## Query Plan and Deterministic Compilation

Keep `QueryPlan` backward compatible while adding optional structured fields
needed for safe compilation: a canonical calculation concept and an explicit
clarification signal. Existing callers that construct the current fields
continue to work.

After the LLM returns a plan, `normalize_query_plan(question, plan)` performs
the following operations in a fixed order:

1. Retain only supported fields and operators.
2. Normalize and validate every value using the field's declared type for all
   retrieval backends.
3. Apply deterministic relative and explicit date ranges.
4. Detect explicit employee-ID tokens in the question and validate them
   against the employee directory.
5. Convert planner-created `Name` conditions, `name_hint`, and name-like
   `Employee_ID` values into one employee reference for the resolver.
6. Compile recognized attendance calculations through the canonical data
   dictionary, replacing conflicting planner aggregation/filter output.
7. Reject missing comparison operands, unsupported aggregation fields,
   conflicting structured constraints, and invalid dates/numbers with a
   user-facing clarification result.
8. Canonicalize routing: structured questions use exact retrieval; only a
   genuine fuzzy semantic intent may use semantic or hybrid retrieval.

Planner sampling will use deterministic settings where the provider supports
them, but correctness must not depend on identical raw LLM text. Tests compare
normalized executable plans.

## Employee Resolution

Introduce these minimal models in the answer path:

```text
EmployeeCandidate
    employee_id: str
    name: str

EmployeeResolution
    outcome: unique | confirmation | ambiguous | none
    candidates: list[EmployeeCandidate]
    reference: str
```

The employee directory comes from distinct PostgreSQL `(employee_id, name)`
rows. Chroma metadata supplies the same pair only when PostgreSQL is disabled.

Resolution order:

1. Explicit employee ID, validated by exact lookup.
2. Exact normalized full name.
3. Normalized partial name.
4. Token-aware fuzzy matching using Python's existing standard-library
   similarity support.

Token-aware matching compares the reference with both the normalized full
name and individual name tokens. This makes a short typo such as `muhtar`
comparable to `Example` without lowering a global full-name threshold enough
to admit unrelated employees.

A single very strong candidate proceeds automatically. A weaker single
candidate requires confirmation. Multiple viable candidates produce a
bounded, stable list ordered by match quality, normalized name, and employee
ID. Exact duplicate names remain separate candidates because IDs are never
discarded.

A valid explicit ID removes conflicting name filters. An unknown ID or name
returns a correction request and never becomes semantic retrieval.

## Conversation State and Clarification

Add the minimal per-session model documented by the roadmap:

```text
ConversationState
    selected_employees: list[EmployeeCandidate]
    pending_question: str | None
    pending_plan: QueryPlan | None
    pending_candidates: list[EmployeeCandidate]
```

`answer_question_with_state(question, history, state)` returns:

```text
(answer_text, retrieved_chunks, updated_state)
```

When clarification is pending, the function validates the reply without
calling the planner. Valid choices are:

- a displayed one-based number;
- a displayed full name when it identifies one displayed candidate;
- a displayed employee ID;
- `both` or `all`, selecting exactly the displayed candidates.

Invalid choices retain the pending state and repeat the bounded options.
Before resuming retrieval, selected candidates are revalidated against the
current employee directory. The saved normalized plan is copied, receives
authoritative `Employee_ID` filters, and executes without replanning.

After a successful request, the selection remains active for identity-free
follow-ups. A new explicit ID or name replaces it. State contains no retrieved
attendance records and no global or cross-session storage.

`answer_question(question, history)` constructs a fresh state, delegates to
the stateful function, and returns its existing two-item result. It therefore
remains compatible and safe, although only the Gradio/stateful interface can
resume a later clarification reply.

## Retrieval Safety Gate

Employee resolution and plan validation occur before `fetch_context()` may
call any backend. The unresolved path must not call:

- exact PostgreSQL or Chroma retrieval;
- semantic embeddings or vector retrieval;
- aggregation;
- reranking;
- final-answer completion.

Tests patch every boundary and assert zero calls for ambiguous, unknown,
invalid, and invalid-clarification requests. This is the primary correctness
invariant of the design.

Structured no-match is a valid zero-result outcome only after all supplied
entities have been resolved and validated. It never changes the plan to
semantic or hybrid mode.

## Gradio Integration and Error Handling

Keep the existing stateless `chat(history)` compatibility helper. Add a
state-aware handler used by the UI and one hidden `gr.State` initialized with
a fresh `ConversationState` per browser session.

The submit chain passes and returns the hidden state. Two state objects share
no mutable defaults. Tests exercise two simulated sessions and prove that one
session's selection cannot alter the other.

Expected user-input failures are rendered as assistant clarification/error
messages. Unexpected operational failures are logged with a stack trace and
rendered as a concise retry message; raw exceptions do not escape through the
Gradio event handler.

## Filter-First Retrieval and Reranking

Retain the existing filter-first hybrid architecture and add behavioral tests
using controlled candidates:

- candidates outside structured filters never reach similarity ranking;
- exact employee/date facts remain ahead of generic summaries;
- result sets at or below `FINAL_K` never call the reranker;
- ambiguous or invalid plans never reach either path.

Do not test prompt source text. Tests assert observable selected records and
downstream calls. Live scenarios provide the final reranking check.

## Related Split Parts

After semantic or hybrid retrieval and before reranking, inspect retrieved
metadata for `embedding_parts_total > 1`. Fetch sibling chunks that share the
same `record_id` from the active semantic store, merge them with retrieved
results, deduplicate by `part_id`, and order siblings by `embedding_part`.

Exact attendance-row retrieval is unchanged. Expansion is bounded to record
IDs already returned by semantic retrieval. PostgreSQL uses one `record_id =
ANY(...)` query; Chroma uses metadata filtering. The current store contains no
split records, so permanent synthetic tests verify the behavior without
mutating live data.

## APDC Evaluation Corpus

Replace every row in `week5/new_evaluation/tests.jsonl` with APDC attendance
questions. No Insurellm questions remain anywhere in the active evaluation
data.

Extend the evaluation case schema with optional structured expectations:

- expected normalized mode;
- expected filters;
- expected employee IDs;
- expected matched count or deterministic calculation;
- expected answer facts;
- whether clarification and zero downstream calls are required.

The corpus covers positive and negative cases for explicit IDs, conflicting
names and IDs, exact/partial/fuzzy/duplicate names, unknown names, invalid
values, dates and ranges, departments, shifts, exceptions, leave, overtime,
worked/scheduled/authorized semantics, clarification replies, and retained
selection.

Expected deterministic values are calculated directly from the current APDC
PostgreSQL dataset and stored as versioned benchmark expectations. A
read-only verification utility compares those expectations with direct SQL so
dataset drift is visible instead of silently changing the benchmark.

Clean up stale evaluation code while making this change: remove unused
`db_name`, use the configured model rather than a duplicate model constant,
correct obsolete async comments/docstrings, and remove compatibility branches
that are no longer exercised only when tests prove the supported package and
script entry points still work.

## Test Strategy

Every behavioral task follows red, green, refactor:

1. Add one minimal failing regression test.
2. Run it and confirm the expected behavioral failure.
3. Implement the smallest production change.
4. Run the focused test and its containing module.
5. Refactor only while green.
6. Run the complete Week 5 suite before the next task.

Permanent coverage includes all 20 employee-clarification acceptance
scenarios in the roadmap plus canonical attendance semantics, malformed plan
values, deterministic normalized plans, Gradio isolation/error paths,
filter-first retrieval, related split parts, and APDC evaluation loading.

## Verification

Before completion, run and record:

- all Week 5 automated tests;
- every focused regression test added during the work;
- Ruff check and formatting verification;
- Python compilation;
- `git diff --check`;
- direct read-only PostgreSQL truth queries;
- positive and negative end-to-end planner/retrieval/answer requests;
- ambiguity probes proving zero downstream retrieval/aggregation/reranking/
  final-answer calls;
- Gradio construction and launch smoke testing;
- two simulated Gradio sessions proving state isolation;
- final review for stale imports, dead code, duplicate logic, temporary
  diagnostics, outdated Insurellm data, and unrelated modifications.

The work is complete only when automated results, live LLM responses, and
direct PostgreSQL results agree.
