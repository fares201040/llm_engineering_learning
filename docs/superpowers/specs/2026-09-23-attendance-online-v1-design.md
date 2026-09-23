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
AttendanceQuery
|- filters: condition | predicate | all | any | not
`- output:
   |- Rows(fields, ordering, limit)
   `- Aggregate(measures, group_by, having, ordering, limit)
```

Supported behavior is limited to filters, projections, aggregates, grouping/HAVING,
ordering, limits, and narrative retrieval. Derive, compare, join, set, ranking,
window, macros, multi-source graphs, and multi-unit execution are unsupported.

The semantic writer emits evidence-backed filter components and exactly one output
or narrative component. A new request is complete. A modify or repeat request may
retain explicitly named opaque components from one prior verified turn. The
materializer starts empty, includes only named components, adds current components,
and validates one coherent output.

## Identity and state

Employee identity never enters the provider-authored query. Exact authoritative IDs
and names bind immediately. A single deterministic fuzzy candidate at or above the
existing 0.62 threshold requires explicit confirmation; ties or no qualifying match
require an exact ID or name. Trusted prior employee references use opaque IDs.

`ConversationState` contains only the runtime version, session ID, verified turns,
active employee IDs, and at most one pending confirmation. Incompatible state resets
safely. An answered turn publishes its verified frame and transcript atomically. A
clarification publishes only pending confirmation state. Unsupported and failed turns
leave state unchanged.

## Safety and failure policy

Application code validates the flat query against one catalog, binds authoritative
employee scope, and compiles all literals and limits as PostgreSQL parameters. Main,
coverage, and witness queries reuse the same bound query. Transactions are
repeatable-read and read-only with configured connection, statement, lock, idle, and
row limits. Narrative retrieval fails closed when identical employee scope cannot be
enforced.

An unavailable initial semantic audit may retain an already deterministically valid
candidate. A credible rejection permits one repair. The repair must pass deterministic
validation and a final audit; rejection or final-audit unavailability fails closed.
Grounded prose must pass deterministic fact checks and the answer verdict before
publication.

All structured model calls set transport retries to zero. The application-level
ceiling remains eleven calls: reference 2, semantic writer 2, audit/repair 3, and
answer writer/verifier 4. Retrieval embeddings are measured separately.

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
