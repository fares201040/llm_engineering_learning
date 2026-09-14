# APDC Tolerant Multilingual Input Implementation Record

**Status:** Complete and merged locally into `main` on 2026-09-14.

## Objective and immutable boundaries

This implementation lets the APDC attendance assistant understand concise,
misspelled, grammatically incomplete, English, Arabic, and mixed-language input
without making executable semantics probabilistic. Tolerance ends at the input
boundary. Registries, typed facts, the compiler, invariant validation,
parameterized backend execution, and answer contracts remain authoritative.

The implementation contains no complete-question maps, typo dictionaries,
employee-specific rules, expected-answer maps, raw or model-generated SQL,
silent fallback, or partial execution. An unresolved employee, meaning, intent,
value, contradiction, unsupported capability, or unauthorized scope stops before
retrieval.

## End-to-end architecture

```text
Question + trusted access/session state
  -> access and domain preflight
  -> QuestionSurface (original text, reply locale, source-bound candidates)
  -> exact employee resolution or PendingEmployeeClarification
  -> semantic facts and residual-token validation
  -> meaning/catalog clarification when required
  -> result-operation completeness check
  -> deterministic PlanningDraft and internal PlannerProposal
  -> optional bounded PlannerDecision over request-local candidate IDs
  -> compile_proposal() and invariant validation
  -> ExecutableQueryPlan + AnswerContract
  -> parameterized PostgreSQL or controlled Chroma retrieval
  -> deterministic calculation/rendering or bounded narrative completion
```

`answer_question()`, `answer_question_with_state()`, and `fetch_context()` retain
their public signatures. Compatibility state fields are mirrored, but new
clarification logic is owned by the typed pending union.

## Contracts and registries

`attendance_schema.py` defines strict, immutable contracts for surface
candidates, localized aliases, input-understanding results, result intents,
answer contracts, and pending clarifications. Unknown keys, blank evidence,
invalid spans, duplicate candidate IDs, invalid target references, and invalid
resolved states are rejected.

Localized aliases reference canonical identifiers from the existing field,
measure, predicate, calculation, operator, interpretation, and result-intent
registries. Referential-integrity tests prevent aliases from creating a parallel
business-semantics layer. `EvidenceOrigin.user_clarification` can be assigned
only after a reply is validated against trusted stored options.

## Source-preserving language analysis

`language_understanding.py` preserves the original string and source offsets.
It applies Unicode NFKC, whitespace/casing/punctuation normalization, Arabic
diacritic and tatweel removal, Arabic/Latin digit normalization, and conservative
Arabic letter normalization. It never rewrites employee IDs, dates, numeric
values, negation, comparison direction, or Boolean structure.

Fuzzy semantic matching applies only to registry aliases of at least four
characters. Automatic acceptance requires a score of at least `0.90` and a
margin of at least `0.08` over the next candidate for the same source span.
Overlapping aliases prefer the longest registered meaning. Lower confidence,
ties, and unknown semantic residue—including Arabic definite/indefinite noisy
modifiers—produce typed clarification. They do not become strong facts.

Reply locale is Arabic when Arabic alphabetic tokens equal or outnumber Latin
alphabetic tokens and English otherwise. The locale persists through every
clarification turn.

## Employee identity policy

The resolution order is:

1. Exact employee ID: proceed.
2. One exact normalized full name: proceed.
3. Duplicate exact full name: clarify.
4. Prefix, substring, reordered-token, transliterated, or fuzzy name: clarify,
   even when exactly one candidate exists.
5. No qualifying candidate: request a corrected full name or employee ID.

Arabic exact-name matching removes diacritics and uses safe letter variants.
Candidate generation occurs only after the access check and only against the
authorized employee directory. Results are ordered by match quality, normalized
name, and employee ID; limited by `CONSTRAINT_CANDIDATE_LIMIT`; and displayed as
`Name — Employee ID` only.

A single uncertain candidate uses localized yes/no confirmation. Multiple
candidates accept displayed number, exact displayed name, or employee ID,
including Arabic-Indic option numbers. `all` or `both` is accepted only when the
saved original request explicitly requested multiple employees. A confirmed
candidate is revalidated against the current directory before execution.

## Completeness and clarification lifecycle

A request is complete only when verified facts identify a registered measure or
calculation, explicit record-list intent, explicit field projection, semantic
retrieval intent, or employee-profile intent. One and only one entailed
`INTERPRETATION_PRESETS` entry may complete a short predicate question. Ambiguous
attendance, overtime, ranking, numeric, or filter-only input pauses. A bare name
asks one concise localized question and never retrieves.

`PendingClarification` discriminates employee selection, semantic meaning,
catalog value, and missing intent. Each variant stores the original question,
reply locale, verified facts, a safe proposal when available, and only the finite
options displayed. Meaning options retain evidence text and spans; construction
fails if they do not match the original question.

Resolution order is employee identity, remaining semantic/catalog ambiguity,
completeness, recompilation from the original request plus confirmed facts,
candidate revalidation, then retrieval. A complete new attendance question
cancels any pending branch. Invalid selection-like input repeats the concise
clarification. A valid selection automatically resumes the first request.

## Employee profiles

`employee_profile` is a typed registry result intent for identity/profile
questions. It exposes only `Employee_ID`, `Name`, `Department`, `Position`, and
`Work_Location`. The compiler derives and revalidates this projection; neither
the provider nor the user surface can add fields.

PostgreSQL compiles a parameterized `SELECT DISTINCT` from the
`ExecutableQueryPlan`. Chroma implements equivalent verified filtering,
projection, and deduplication. Conflicting stored values are reported as multiple
recorded values instead of being silently collapsed. Profiles use deterministic
rendering and never rely on the final LLM to invent identity details.

## Rendering and provider boundary

Employee, meaning, catalog, and missing-intent clarifications; profiles;
projections; aggregations; record summaries; truncation notices; and safe errors
have deterministic English and Arabic templates. Material fuzzy corrections may
be disclosed, while harmless presentation normalization is silent.

Fully grounded requests skip provider planning. If an unresolved provider choice
is ever needed, `PlannerDecision` may select only request-local candidate IDs.
It cannot author fields, values, calculations, grouping, ordering, limits,
projections, contracts, backend choices, or SQL. Narrative completion receives
only bounded retrieved evidence, the verified answer contract, and a verified
reply-language instruction.

## Privacy and evaluator display

Production observability emits only request ID, controlled stage/status, locale,
clarification kind, candidate/correction counts, match-method counts, and failure
codes. It never emits questions, evidence text, names, employee IDs, option
labels, scores, catalog values, SQL, or parameters.

The local analyst evaluator intentionally includes `Question` in behavior,
retrieval, and answer detail tables. This makes local results understandable and
does not change production diagnostics or logging.

## Review corrections

The final review added regressions and fixes for longest-alias overlap, unknown
Arabic residual modifiers, Arabic exact-name normalization, meaning-option
evidence provenance, cancellation across every pending branch, locale persistence
after missing intent, localized projection/summary/truncation output, configured
candidate limits, pre-catalog unresolved-input gating, and direct evaluator
script imports. The independent final reviewer found no Critical or Important
remaining issue.

## Verification and history

The implementation commits, in order, are:

- `727a034b` — tolerant-input contracts
- `81f3263b` — multilingual surface analysis
- `c5eef5c7` — uncertain employee clarification
- `2fa55ca5` — clarification lifecycle and request resumption
- `1547a2a4` — employee profiles and localized rendering
- `781bfe75` — typed-understanding execution gate
- `5081cb55` — public robustness matrix and documentation
- `49774c1e` — formatting
- `9478b521` — final review remediation
- `ca639fdc` — direct evaluator-import compatibility

Fresh merged verification ran 489 discovered `week5` tests in 228.677 seconds
with no failures. Ruff lint passed for `week5/new_implementation`, its 38 files
passed the format check, and `git diff --check` passed. Provider-backed scoring
and the authorized private corpus remain separate from this deterministic safety
evidence.

## Supported scope and remaining limitations

English, Arabic, and mixed English-Arabic attendance wording are supported.
Transliteration is conservative and always confirmed. Uncommon, low-confidence,
or multiply plausible paraphrases require a finite clarification. Candidate lists
are intentionally bounded, so the user may be asked for more name characters.

Nested Boolean filters, HAVING, windows, cross-period comparisons, grouped
percentages, unsupported calculations, genuine multi-stage aggregation, and
other unregistered advanced operations remain typed, controlled, and fail
closed. Expanding these capabilities requires new registry semantics, compiler
support, backend parity, tests, and an explicit design review; input tolerance
must never approximate them.

## Reading order for the next agent

Read this record together with the schema-grounded handoff and provider-decision
plan. Then inspect `attendance_schema.py`, `language_understanding.py`,
`semantic_resolution.py`, `planning_decisions.py`, `plan_compiler.py`,
`answer.py`, `query.py`, both backend paths, and their adjacent tests. Treat code
and executable tests as authoritative if any historical document disagrees.
