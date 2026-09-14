# APDC Provider Decision Boundary and Answer Quality Design

## Purpose

The schema-grounded compiler is safe, but the current planner contract gives
the provider ownership of choices that deterministic resolution has already
proved. The provider is told to repeat filters, measures, predicates,
calculations, grouping, projection, ordering, limits, and answer shape. Runtime
code then overlays those same facts and the invariant compiler validates them a
third time. Healthy but redundant provider defaults therefore become structural
conflicts rather than useful interpretation.

This design gives each semantic choice one owner. Deterministic facts own every
proved executable choice. A provider may answer only explicitly enumerated,
typed decision slots that remain unresolved. No provider response is executable
until deterministic assembly and the existing invariant chain accept it.

## Constraints

- Never execute raw or model-generated SQL.
- Never silently replace an incompatible provider choice.
- Never execute a partially understood request.
- Do not add question templates, employee-specific behavior, expected-answer
  mappings, or field-specific control-flow branches.
- Keep registries as the sole source of field, value, measure, predicate,
  calculation, grouping, ordering, ranking, limit, projection, and capability
  meaning.
- Preserve the public `fetch_context()` four-item tuple and answer APIs.
- Preserve trusted employee scope, clarification, access checks, parameterized
  PostgreSQL, Chroma domain restrictions, and read-only execution.
- Diagnostics must not retain questions, evidence text, employee identifiers,
  employee names, catalog values, raw records, reference answers, provider
  payloads, SQL parameters, credentials, or private fixture content.

## Considered approaches

### 1. Keep the full proposal and improve the prompt

This is the smallest code change, but it retains duplicate ownership. Prompt
wording cannot prove that a provider-selected measure, numerator, grouping, or
shape is equivalent to the deterministic facts. It would continue to turn
provider variation into correctness risk.

### 2. Accept a full proposal and silently normalize it

This produced stronger historical corpus scores, but formal review proved that
it replaced incompatible operations and populations. It is rejected because it
can manufacture a plausible answer for a different question.

### 3. Deterministic assembly plus typed unresolved decisions

This is the selected approach. Deterministic resolution creates a plan draft
and a finite set of unresolved slots. Fully grounded requests bypass provider
planning. When a decision is necessary, the provider can choose only candidate
identifiers emitted by code, ask for clarification, or identify a controlled
unsupported capability. Deterministic code then assembles a `PlannerProposal`
and the existing invariant compiler proves the complete result.

## Architecture

```text
Question + trusted state
  -> access and malformed-input gates
  -> registry-grounded SemanticFact values
  -> deterministic PlanningDraft
       -> complete: assemble PlannerProposal without provider
       -> unresolved: bounded PlannerDecisionRequest
            -> PlannerDecision (candidate IDs only)
  -> deterministic proposal assembly
  -> compile_proposal() invariant chain
  -> ExecutableQueryPlan + AnswerContract
  -> employee/catalog clarification when required
  -> parameterized PostgreSQL or attendance-scoped Chroma
  -> deterministic calculation/rendering or evidence-grounded narrative
```

## Typed boundaries

`PlanningDraft` contains immutable facts, deterministic choices, unresolved
slots, and controlled unsupported capabilities. It is not executable.

`PlanningNeed` has a stable request-local identifier, one decision kind, a
precise evidence occurrence, and a tuple of `PlanningCandidate` values. A
candidate contains an opaque identifier and a typed semantic choice. Candidate
identifiers are meaningful only inside that request.

`PlannerDecision` contains only status, selected `(need_id, candidate_id)`
pairs, controlled unsupported capabilities, and optional clarification need
identifiers. It has no field, value, filter, measure, predicate, calculation,
grouping, projection, order, limit, answer-contract, backend, search-query, or
SQL property.

`assemble_grounded_proposal()` converts deterministic facts plus validated
decisions into the existing strict `PlannerProposal`. It derives the answer
contract from the assembled operation. Missing, duplicate, unknown, or
incompatible decisions return typed violations; no best-effort proposal is
created.

## Provider use

The provider is skipped when facts completely determine the request. This
includes registered counts, worked/scheduled/non-attended/absent/zero-hour
semantics, explicit filters, temporal constraints, percentages, grouping,
ranking, ordering, limits, projection, and registered semantic intents.

The provider is used only when deterministic resolution emits a bounded choice
that genuinely needs language interpretation. The prompt includes the original
question, generic decision instructions, and the bounded need/candidate schema.
It does not include the full executable schema or ask the provider to repeat
already-grounded facts.

Employee and catalog ambiguity remains a deterministic clarification concern;
it is not delegated to the provider. If language cannot be represented by a
registered candidate set, it is rejected with a controlled capability rather
than broadened.

## Composition and occurrence ownership

Every semantic role retains its own evidence occurrence and precise consumed
span. Supporting evidence can overlap but cannot exclusively consume unrelated
projection, temporal, identity, grouping, or ordering occurrences. The public
regression `Show Date and Status from records for Morgan River on 2026-09-01`
must compile exactly like the equivalent date-first wording: row projection of
`Date` and `Status`, one trusted employee filter, and one date filter.

The fix must be grammar/occurrence based. It must not recognize that sentence
or those fields as a template.

## Answer-quality diagnostics

An in-memory `CaseDiagnostic` records only non-sensitive structural metadata:

- corpus index and category;
- success/failure stage;
- semantic fact kind counts and stable concept identifiers;
- whether provider decisions were required and their validation status;
- violation codes and controlled capability identifiers;
- executable mode, operation, result shape, answer-contract shape/unit;
- result kind, row/group/evidence counts, and renderer kind;
- evaluator scores and one normalized cause code.

The classifier uses deterministic precedence:

1. provider or Pydantic structural failure;
2. unsupported or rejected proposal/decision;
3. missing or excess deterministic fact;
4. unsupported plan shape;
5. answer-contract mismatch;
6. retrieval/calculation mismatch;
7. renderer incompleteness or irrelevant evidence;
8. clarification/session-state failure;
9. evaluator expectation drift requiring manual review.

Private per-case diagnostics remain local and ignored. Tracked documentation
contains only aggregates and synthetic reproductions.

## Unsupported language decisions

This implementation retains explicit rejection for nested Boolean filters,
HAVING, window calculations, cross-period comparisons, grouped percentages,
positional first/last without a grounded ordering basis, and genuine
multi-stage aggregations. These capabilities require separate end-to-end typed
designs; none is partially executed in this work.

## Evaluator contract

Malformed and unsupported cases assert typed violation codes and controlled
capability identifiers. Legacy error substrings remain only where the failure
is a pre-planning input error with no semantic violation model. No compatibility
mapping from typed violations to historical strings is added.

## Verification

Each behavior change follows RED/GREEN TDD through the public pipeline. Final
verification includes all configured tests, Ruff, formatting, compilation,
Git whitespace checks, dataset fingerprint verification, benchmark, one
non-private provider health probe, complete provider-backed behavior and answer
corpora when healthy, privacy review, handoff update, clean worktrees, commit,
and push to the configured `origin` only.
