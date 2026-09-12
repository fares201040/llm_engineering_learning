# Schema-Grounded Query Compilation Design

## Purpose

Prevent a technically valid but semantically wrong LLM plan from reaching
PostgreSQL. The design applies one pattern to every attendance column rather
than adding question-specific prompt rules or Python branches.

## Existing foundation

The existing architecture remains valid:

1. An LLM proposes a structured plan.
2. Python normalizes and validates it.
3. Python compiles parameterized PostgreSQL.
4. PostgreSQL returns authoritative data.
5. Python formats deterministic calculations.

The gap is that current validation proves type and SQL safety but does not
fully prove semantic faithfulness. A plan can select a valid field and valid
value that the user never requested.

## Single source of truth

`FIELD_DEFINITIONS`, measure definitions, predicate definitions, and value
concepts form one semantic registry. The registry supplies:

- canonical field names and storage types;
- natural-language field aliases;
- allowed operators;
- SQL expressions;
- value resolution strategy;
- closed values or catalog source;
- aliases and families for business values;
- allowed filtering, grouping, ordering, and aggregation roles;
- whether a field is visible to the planner or reserved for deterministic application scoping;
- named measure and predicate compilation.

The planner prompt, deterministic resolvers, compiler, SQL allowlist, and
generated tests all consume this same registry. Business mappings are not
duplicated in prompt prose.

## Untrusted and trusted plan types

The model returns `PlannerProposal`, never the executable plan. It is a
separate model rather than a wrapper around `QueryPlan`, and its
`ProposedFilter` is not an executable `FilterCondition`. It contains:

- proposed filters whose evidence is embedded beside each filter;
- evidence-bearing measure, predicate, grouping, ordering, and limit choices;
- a typed calculation intent rather than duplicate low-level aggregation
  fields;
- an `AnswerContract` describing the intended result shape, unit, subject,
  and grain;
- a planning status of `ready`, `ambiguous`, or `unsupported`;
- typed capability identifiers when the current plan language cannot express
  the request.

Planner-provided models reject unknown keys and blank evidence. One model-level
validator owns context-free status and shape rules for direct construction,
JSON parsing, and clarification-state restoration. Checks that require the
question, registry, or current catalog remain in the semantic compiler; the
runtime does not duplicate either set of rules.

Python first builds a plain `QueryPlan` candidate and runs every invariant over
it. Only a violation-free candidate is constructed as `ExecutableQueryPlan`,
and only that type can cross the PostgreSQL execution boundary.

Python derives the legacy aggregation fields only while creating
`ExecutableQueryPlan`. Existing result serialization remains compatible, but
the optional plain-`QueryPlan` prepared-plan bypass is removed after its
test-only callers migrate.

Backend mode and semantic search text are also application-owned. Python
derives exact, semantic, or hybrid mode from detected semantic intent plus the
compiled structured choices; semantic and hybrid plans use the original user
question. `PlannerProposal` contains neither a model-selected backend nor a
second free-form paraphrase that could introduce unsupported entities or
business meaning.

## Why the LLM must not supply executable SQL

Raw model SQL can bypass field allowlists, row access rules, parameterization,
query-cost limits, and semantic validation. It can also hallucinate schema or
silently answer a different question.

The useful requirement behind an expected-SQL field is handled safely in two
ways:

1. `AnswerContract` gives an independent semantic cross-check. For example,
   a request for a number of days must compile to a date-grain calculation,
   not an unrelated record count.
2. `unsupported_capabilities` records why a proposal cannot be represented.
   The runtime fails closed, while evaluation reports guide deliberate
   extensions to the typed plan language.

Python produces a `CompiledPostgresQuery(sql, params)` object from the trusted
plan. The SQL may be logged by fingerprint or exposed in developer diagnostics,
but it is never accepted from the LLM.

## Generic resolver pattern

Use an abstract `FieldResolver` interface for field-value interpretation.
Concrete strategies cover:

- identifiers and trusted entities;
- dates, times, and datetimes;
- finite numeric values and comparison phrases;
- closed categorical values;
- database-catalog categorical values;
- free text that belongs in semantic retrieval rather than equality filters.

Every filterable field declares a resolution strategy. A startup contract test
fails if any registered field lacks a compatible resolver.

Every field also receives at least one normalized natural name generated from
its canonical column name. This gives new columns baseline deterministic
coverage even before extra approved aliases are added.

Natural-name indexes preserve all matches rather than overwriting duplicate
phrases. A collision produces ambiguity unless other verified evidence
disambiguates it. Value and alias resolution is then scoped to the chosen
field, so a valid value from another column cannot justify changing columns.

Catalog values are canonicalized against current PostgreSQL data. Large or
sensitive catalogs are not copied wholesale into the prompt; only compact
schema metadata and question-relevant candidates are supplied.

Internal fields such as deterministic document/row scope remain in the
application registry but are not planner-visible. The prompt renderer also
omits SQL expressions, so model output cannot select execution text.

## Declarative value concepts

Related stored values require explicit business metadata. A value concept has
a canonical name, aliases, a field, and one or more canonical members. The
compiler treats all concepts uniformly.

For example, an approved `off_day` concept may have `Day_Type` members `OFF
Day` and `OFF Day (ZAS)`. This is not an off-day code path: it is one registry
entry processed by the same resolver used for every categorical concept.

When a phrase matches multiple values without an approved family relationship,
the application asks for clarification instead of guessing.

## Evidence and semantic facts

Before accepting the LLM proposal, deterministic resolvers detect strong facts
from the question and trusted state. Each fact records:

- field or named concept;
- canonical candidate value;
- operator;
- exact supporting question text;
- origin: `question`, `trusted_state`, or `deterministic_default`;
- resolution status and candidates.

The LLM must attach question evidence directly beside each proposed filter,
predicate, measure, grouping, ordering, and limit choice. Python verifies that
the cited text is actually present and maps through the registry. Separate
free-form target paths such as `filters[0]` are not used. Model-provided
evidence is not trusted merely because it is present in JSON.

When the compiler expands a registered measure, predicate, value concept,
verified employee, date phrase, or internal scope, every generated constraint
inherits precise provenance from that source. No generated executable choice
may have empty or field-only provenance.

Multi-turn clarification stores the pending untrusted proposal plus serializable
semantic facts. A selected employee, interpretation, or catalog value becomes
a `trusted_state` fact and the proposal is compiled again. Follow-up handling
never mutates an executable filter directly.

## Compilation invariants

Plan validation is implemented as composable abstract `PlanInvariant` checks:

1. **Schema:** every field, operator, measure, predicate, and value is allowed.
2. **Grounding:** every executable choice has verified provenance.
3. **Coverage:** every strong detected user fact is used or explicitly
   clarified.
4. **Contradiction:** filters, predicates, polarity, and trusted state do not
   conflict.
5. **Answer contract:** aggregation, subject, unit, grain, and result shape
   agree.
6. **Capability:** the typed plan language can express the whole request.

The compiler may canonicalize spelling, casing, pluralization, dates, and exact
catalog matches. It must not silently replace one semantic field with another.
Ambiguous or unsupported requests stop before retrieval.

Business contradictions are declarative registry metadata, including both
predicate-to-predicate and predicate-to-filter relationships. The invariant
engine evaluates that metadata generically; it does not branch on `Day_Type`,
`Exception`, or any other named attendance column.

## Prompt policy

The prompt keeps only generic behavioral instructions:

- use the supplied schema;
- cite evidence for every choice;
- do not invent fields, values, or meanings;
- return ambiguity or unsupported capability instead of guessing;
- return a proposal, not SQL.

All planner-visible column descriptions, operators, controlled values, value concepts,
measures, and predicates are rendered from the registry. Existing prose rules
that map specific phrases to attendance filters are removed after their
registry equivalents and tests are in place.

## Runtime flow

1. Validate access and basic input.
2. Load only bounded, question-relevant catalog candidates and detect initial
   semantic facts from the question and trusted state.
3. Render the complete compact semantic registry plus those relevant
   candidates.
4. Ask the LLM once for `PlannerProposal` and apply its centralized structural
   validation.
5. Stop unsupported requests; otherwise perform a second bounded candidate
   lookup only for registry-valid, evidence-backed proposed catalog fields.
6. Merge facts deterministically, resolve values, and run all plan invariants.
7. Return clarification or validation failure when required.
8. Resolve any employee hint, add only verified identity provenance, and run
   the same invariant pipeline again before retrieval.
9. Produce the final `ExecutableQueryPlan`.
10. Compile `CompiledPostgresQuery` from allowlisted SQL expressions and bound
    parameters.
11. Execute read-only PostgreSQL and format the deterministic answer.

## Failure handling and observability

Sanitized events distinguish:

- facts detected;
- proposal received;
- constraint rejected as ungrounded;
- user fact left uncovered;
- contradiction found;
- clarification required;
- capability unsupported;
- executable plan compiled;
- SQL fingerprint executed.

Logs must not contain full prompts, raw employee records, connection strings,
unrestricted SQL parameter values, or model-provided explanation text. Safe
user messages come from application-owned mappings of controlled violation and
capability codes.

## Evaluation strategy

Tests combine hand-written regression cases with cases generated from the
semantic registry. Required invariants include:

- no executable constraint without provenance;
- no strong user fact silently omitted;
- no ambiguous value silently selected;
- no unknown database value accepted;
- no registered field without a resolver;
- no prompt business mapping outside the registry;
- no model SQL accepted by the executor;
- PostgreSQL remains parameterized and read-only.

Cross-column collision tests deliberately make the planner return a different
valid field/value than the one supported by the question. The compiler must
reject the proposal before any retrieval call.

## Rollout

The new compiler first runs through a non-production parity gate in unit tests
and local evaluation; the application does not make a second planner call in
normal requests. Production behavior switches only after all existing behavior
tests and the schema-generated positive and negative mutation matrix pass. The
old prompt rules and canonical question tables are removed in the same change
that enables the enforced semantic gate, so there is no period with two active
runtime sources of truth.
