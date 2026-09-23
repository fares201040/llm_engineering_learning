# Attendance Online v1 Design

## Purpose

Replace the mixed legacy and semantic-query-v4 attendance runtime with one small,
explicit `attendance-online/v1` pipeline. The redesign keeps the app-facing API,
deterministic authorization, parameterized PostgreSQL, semantic audit, grounded
answer verification, conversational follow-ups, and atomic state publication.

Offline ingestion remains behaviorally unchanged.

## Principles

1. There is one production runtime and no legacy, shadow, canary, or fallback path.
2. One user message produces one structured or narrative result.
3. The model interprets language; application code owns identity, authorization,
   query validity, physical schema, SQL, execution, and state publication.
4. Contracts live beside the boundary that owns them.
5. Every stage returns typed values or typed failures to one orchestrator.
6. Unsupported requests are reported precisely and never approximated.

## Architecture

The app calls the narrow compatibility facade in `answer.py`. The facade creates a
`TurnRequest` and calls `online.pipeline.run_turn()`:

```text
guard -> trusted context -> reference -> plan -> materialize/validate -> audit
      -> authorize/execute -> grounded answer -> atomic publish
```

The `online` package contains focused modules for pipeline, state, reference,
planning, query, catalog, audit, execution, answering, and shared provider support.
The root facade continues to expose `answer_question`, `answer_question_with_state`,
`ConversationState`, `AccessContext`, `LOCAL_DEMO_ACCESS`, and `Result`.

## Canonical query

The staged logical graph is replaced by one flat attendance query:

```text
BoundSubject
|- mode: employees | criteria | union | intersection | all_authorized
|- employee_ids: authoritative application-owned IDs
`- criteria_filters: condition | predicate | all | any | not

AttendanceQuery
|- filters: condition | predicate | all | any | not
`- output: Rows(...) | Aggregate(...)
```

Supported behavior is limited to filters, projections, aggregates, grouping/HAVING,
ordering, limits, and narrative retrieval. Derive, compare, join, set, ranking,
window, macros, multi-source graphs, and multi-unit execution are unsupported.

The semantic writer converts subject-criteria spans into separate evidence-backed
criteria filters, emits result filters, and emits exactly one output or narrative
component. A new request is complete. A modify or repeat request may
retain explicitly named opaque components from one prior verified turn. The
materializer starts empty, includes only named components, adds current components,
and validates one coherent output.

## Generic subject resolution

The reference provider receives the current message, exact-offset guide, and verified
active subject. It receives no physical schema and no fixed list of department,
attendance, or hours columns. It returns:

```text
SubjectReference
|- mode: employees | criteria | union | intersection | all_authorized
|- employees: explicit ID/name/identity-claim values with exact spans
|- criteria: exact natural-language spans describing other employees
`- scope_cue: exact span required for all_authorized
```

Examples of criteria spans include `Engineering employees`, `employees absent in
September`, and `employees who worked more than 8 hours`. The reference provider does
not translate those phrases into fields, predicates, or SQL. The semantic writer owns
that translation and may use any compatible field or predicate in the single logical
catalog. No question phrase, department, status, exception, or hours field receives a
special code path.

The application requires criteria filters for `criteria`, `union`, and `intersection`;
forbids them for `employees` and `all_authorized`; and verifies that their evidence is
contained in the returned criteria spans. It also requires both explicit employees and
criteria for union/intersection. The audit receives the immutable subject reference and
checks that the criteria meaning was neither omitted nor duplicated as an ordinary
result filter. A criterion that cannot be represented by the logical catalog returns a
precise unsupported outcome; an unclear criterion returns clarification.

Execution always applies authorization as an outer conjunction. It then compiles the
requested subject as:

```text
employees:      authorized AND employee_id IN (...)
criteria:       authorized AND criteria_filters
union:          authorized AND (employee_id IN (...) OR criteria_filters)
intersection:   authorized AND (employee_id IN (...) AND criteria_filters)
all_authorized: authorized
```

All values remain PostgreSQL parameters. Main, coverage, witness, and narrative
retrieval compile the same bound subject expression.

## Identity and state

Employee IDs never enter the provider-authored query. Exact authoritative IDs and
names bind immediately. A single deterministic fuzzy candidate at or above the
existing 0.62 threshold requires explicit confirmation. When a written reference is
still unresolved, Chroma semantic search is a fallback, not an identity authority. It
searches only the caller's allowed employee scope and returns at most five options;
each option must match the current PostgreSQL directory and the user must select it by
number, exact ID, or exact name. No result falls back to requesting an exact ID or
name. Trusted prior subjects use their verified bound form.

The reference provider returns the explicit employee value and exact evidence span,
not offsets alone. A message containing both a name and ID produces one identity
claim. The ID is the lookup key and PostgreSQL verifies the name. A matching ID binds
without confirmation; a mismatch or unknown ID never reaches attendance planning.
Identity-verification questions receive a deterministic grounded yes/no response.
Criteria remain symbolic rather than expanding potentially large employee groups into
conversation-state ID lists. A pending fuzzy employee confirmation retains the subject
mode and criteria spans, so confirming one employee cannot discard the rest of the
subject. Authorization is applied independently.

`ConversationState` contains only the runtime version, session ID, verified turns,
the active subject, façade-compatible active employee IDs, and at most one pending set
of employee options. Incompatible state resets safely. An answered turn publishes its
verified frame and transcript atomically. A
clarification publishes only pending confirmation state. Unsupported and failed turns
leave state unchanged.

## Safety and failure policy

Application code validates the flat query against one catalog, binds authoritative
subject scope, and compiles all literals and limits as PostgreSQL parameters. Employee
plus criteria wording is compiled as an explicit union or intersection. Main,
coverage, witness, and narrative queries reuse the same bound scope. Transactions are
repeatable-read and read-only with configured connection, statement, lock, idle, and
row limits. Narrative retrieval fails closed when identical employee scope cannot be
enforced.
Grounded answer facts include the authoritative PostgreSQL employee name whenever an
employee ID is bound, and answer validation requires that name in the final response.

An unavailable initial semantic audit may retain an already deterministically valid
candidate. A credible rejection permits one repair. The repair must pass deterministic
validation and a final audit; rejection or final-audit unavailability fails closed.
Grounded prose must pass deterministic fact checks and the answer verdict before
publication.

All structured model calls set transport retries to zero. The application-level
ceiling remains eleven calls: reference 2, semantic writer 2, audit/repair 3, and
answer writer/verifier 4. Retrieval embeddings are measured separately.

The flat runtime intentionally does not turn an aggregate result into a new employee
set for another query. Direct requests such as grouping employees and applying HAVING
remain supported. Combining an explicit employee with a cohort defined by an
aggregate from a different period requires a set/subquery stage and returns a precise
unsupported result instead of silently changing meaning.

The generic criteria-span reference contract remains valid if a later approved design
replaces the structured semantic writer with SQL output. That future boundary change
must still preserve server-owned identity, authorization, parameter validation, and
read-only execution; it is outside this design change.

## Observability and evaluation

All stages emit one `StageEvent(stage, status, code, duration, details)` shape through
an optional observer. Production uses a logging observer; evaluation uses a recording
observer. The evaluator consumes typed queries, results, outcomes, and events rather
than context-variable interception or raw diagnostic printing.

The 311 behavior cases remain. Removed capabilities and compound requests become
precise unsupported expectations. Completion requires deterministic suites, static
checks, a complete behavior run, Wail with injected initial-audit unavailability,
the Faris four-turn flow, and a long-conversation run.

## Documentation and size constraints

`week5/ARCHITECTURE.md` is the canonical overview with separate compact online and
offline Mermaid diagrams. `LLM_PLANNER.md` is the concise operational contract.
Superseded planner specs, plans, checkpoints, diagnostics, and stale partial results
are removed only after their valid requirements become tests or current docs.

The facade should remain under roughly 150 lines, orchestration near 500 lines, and
other control-flow modules near 800 lines. These are review guardrails, not reasons to
split cohesive catalog data.
