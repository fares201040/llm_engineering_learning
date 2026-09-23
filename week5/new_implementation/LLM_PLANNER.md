# Attendance online runtime

`attendance-online/v1` is the only attendance query runtime and state version. Its
purpose is deliberately narrow: interpret one attendance request, bind authoritative
employee identity and access scope, execute one bounded query or scoped narrative
retrieval, and publish state only after a grounded answer passes verification.

## Code map

| Module | Responsibility |
|---|---|
| `answer.py` | Small tuple-returning compatibility façade |
| `online/pipeline.py` | Stage order, outcome mapping, atomic publication |
| `online/state.py` | Minimal verified conversation state |
| `online/reference.py` | Authorized exact/fuzzy binding and Chroma confirmation fallback |
| `online/planner.py` | One-unit semantic writer |
| `online/query.py` | Flat query types, inheritance, bounds, validation |
| `online/catalog.py` | Logical fields, physical expressions, predicates |
| `online/audit.py` | Verdict-only semantic audit and one repair |
| `online/execution.py` | Authorization, SQL compilation/execution, coverage, witnesses, narrative scope |
| `online/answering.py` | Typed facts, grounded prose, deterministic checks, verdict |
| `online/provider.py` | Structured calls, call budget, stage events |

The query model has filters plus one output. Filters are recursive Boolean expressions:
`condition`, `predicate`, `all`, `any`, and `not`. Output is either bounded ordered
rows or aggregates with optional grouping, HAVING, ordering, and a bound. Narrative
retrieval is permitted only with the same enforceable employee scope.

Removed capabilities are intentional: multiple units, staged graphs, macros,
derivation, comparison, joins, sets, ranking, windows, and provider-authored SQL.

## Turn guarantees

1. Employee references are identified without granting authority. The PostgreSQL
   directory is filtered to the caller's row scope before exact/fuzzy matching. A
   single fuzzy candidate at or above `0.62` requires confirmation. If a written
   reference remains unresolved, Chroma may return up to five candidates; every
   candidate is scope-filtered, cross-checked against PostgreSQL, and confirmed by
   number, exact ID, or exact name before it is trusted.
2. The semantic writer receives logical catalog identifiers and trusted component IDs.
   It receives no physical column or table names.
3. The candidate must pass strict schema, exact evidence, inheritance, capability,
   type, and bound checks before audit or execution.
4. If the first audit is unavailable, only that already-valid primary candidate may
   execute. Audit rejection allows one repair, which must pass deterministic validation
   and a final audit.
5. Authorization binds employee IDs and server-owned row scope into one immutable
   object. Main, coverage, and witness SQL are compiled from it with parameters.
6. Typed result facts feed the answer writer. Deterministic validation and a separate
   verdict are required before state publication.

The hard decision-model ceiling is 11 calls: reference 2, semantic writer 2,
audit/repair 3, and answer writer/verifier 4. Each transport has zero automatic retry.
Embedding calls are separate.

## State and outcomes

`run_turn` returns `Answered`, `Clarification`, `Unsupported`, or `Failed`. Every
outcome contains a reply, evidence, and resulting state.

- `Answered` atomically appends the verified turn and updates active employee IDs.
- `Clarification` can store only one pending employee confirmation.
- `Unsupported` and `Failed` preserve the exact prior trusted state.
- State with any runtime version other than `attendance-online/v1` resets safely.

`StageEvent` is delivered through an explicit `TurnObserver`; no context-variable
tracing exists.

## Configuration

There is no planner-mode, rollout percentage, or configurable contract version.
Deployment may configure model names, timeouts, output-token bounds, read-only DSN,
table names, timezone, and result bounds. See `POSTGRES_SETUP.md`.

## Verification record

The final deterministic, evaluation, live, and static results will be recorded here
after the complete implementation has been reviewed and exercised. A partial
evaluation artifact remains `status: running` and is never reported as success.
