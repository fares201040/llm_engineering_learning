# APDC Independent Conversational-Intelligence Review and Improvement

## Purpose

Independently determine whether the APDC attendance chatbot understands users'
actual intent across realistic conversations while preserving its typed query,
privacy, compiler, parameter-binding, and atomicity boundaries. Confirmed product
defects will be repaired in their owning layers with focused RED/GREEN evidence
and an independent review before acceptance.

## Repository and delivery boundary

- Work in `D:\projects\llm_engineering_ed_donner\llm_engineering` on `main`.
- The confirmed starting commit is
  `0f3d02243973d29954f6eb4b5a10e9c1fe2d686d`.
- Commit valid corrections with clear messages, but do not push.
- Preserve unrelated user work if the worktree changes during execution.
- Keep `week5/new_implementation/planning_decisions.py` unchanged unless a
  reproduced architectural defect proves that its contract itself must change.
- Use `gpt-5.6-luna` with `max` reasoning for debugging, implementation, fixes,
  and testing. Use a fresh `gpt-5.6-sol` reviewer with `medium` reasoning for
  every implementation before acceptance.

## Architectural invariants

The conversation layer may consider the complete relevant session state: the
current question, typed conversation frames, active and historical referents,
prior result snapshots, pending clarifications, safe schema-registry concepts,
and request-local catalog choices. This state remains typed and bounded.

Provider requests must not contain raw private history, employee directories,
unrestricted database contents, DSNs, SQL parameters, exception details, or the
complete physical schema. A provider may receive only the privacy-safe typed
facts and finite request-local choices needed to resolve the current ambiguity.
Already-grounded requests must retain deterministic zero-provider-call behavior.

Provider output is advisory. It must satisfy a strict decision contract and be
validated against request-local candidates. The trusted schema, plan compiler,
executable-plan validator, PostgreSQL compiler, and bound-parameter execution
path remain the only authorities for executable database work. Raw
model-generated SQL must never be executed. A model-produced SQL-shaped choice
is not acceptable merely because a later parser happens to reduce it safely;
the long-term interface must express the unresolved logical decision directly.

Every unit in a compound request must be understood, resolved, validated,
compiled, and prepared before any unit executes. A later invalid, ambiguous, or
unsupported unit prevents all execution and all partial publication. Successful
multi-unit execution must render every requested component with enough verified
scope to distinguish employees, periods, fields, and operations.

## Review method

### Production-path tracing

Trace the public flow from `week5/new_app.py` and
`answer_question_with_state()` through conversation routing, semantic analysis,
referent resolution, clarification, plan compilation, query preparation,
execution, state commit, and deterministic or narrative rendering. Inspect the
typed state at representative boundaries without printing private values.

For each representative workflow, record:

- decision route, normalized question, and reply locale;
- active and historical referents;
- semantic facts and provenance;
- resolved employee, filters, dates, field candidates, and operation;
- pending clarification state;
- executable plan and compiler-selected expression;
- bound parameter count and types, not values;
- retrieval/result metadata; and
- final deterministic rendering.

### Sequential conversation

Run one continuous synthetic/public conversation of approximately 50 turns.
The sequence must include:

1. Explicit employee/date/filter/calculation requests.
2. Pronouns and implicit references including “he,” “she,” “they,” “that
   employee,” “the same person,” “those days,” “that period,” “the previous
   result,” “what about last month?”, and “and his overtime?”.
3. Topic switches followed by returns to older employees and results.
4. Corrections of employee, period, field, and operation.
5. Multi-turn clarification and resumption.
6. Long-distance referent recovery after substantial intervening context.
7. English, Arabic, and genuinely mixed-language requests.
8. Misspellings, presentation variants, abbreviations, colloquial wording, and
   incomplete phrases.
9. Both genuine ambiguity and sufficiently grounded shorthand.
10. Unsupported operations and adversarial requests.
11. SQL injection, prompt injection, schema extraction, employee enumeration,
    history extraction, and diagnostic-leakage attempts.
12. Rephrased repeated questions and explanations/comparisons of prior answers.
13. Long messages containing relevant and irrelevant clauses.

The transcript uses synthetic employee identifiers, names, categorical values,
and result rows only. Every answer receives a manual assessment of intent,
field, employee/date/filter scope, historical-context use, referent preservation,
completeness, grounding, locale, consistency, clarification behavior, privacy,
and rendered-plan/result agreement.

### Compound matrix

Exercise two- and three-unit messages, multiple employees and periods, English
and Arabic units, mixed supported and unsupported operations, and valid first
units followed by ambiguous or invalid later units. Include corrections that
affect only one unit and follow-ups that select one result from a prior compound
answer. Instrument the execution seam so any execution before complete
preparation is directly observable.

## Defect workflow

For each observed failure:

1. Reproduce it through a public boundary with synthetic data.
2. Identify the owning architectural layer and trace the root cause.
3. Add a new narrowly targeted regression and run only that test to confirm RED.
4. Implement the smallest native correction; do not add question-specific
   aliases, private fixtures, prompt-only patches, duplicated semantic rules,
   unrestricted fuzzy matching, hidden compatibility paths, or broad refactors.
5. Run only the new regression and isolated manual probe to confirm GREEN.
6. Re-run the affected conversational sequence.
7. Recheck privacy, multilingual behavior, state and referents, compiler
   ownership, parameter binding, provider-call budget, and compound atomicity.
8. Dispatch a fresh Sol-medium review. Fix Critical or Important findings and
   obtain a fresh scoped review before accepting the implementation.
9. Commit the accepted correction with a focused message.

The initial independent static review supplied reproduction targets rather than
pre-approved fixes: generated `COUNT(*)` candidate handling, generated-decision
repair context, Arabic scalar scope attribution, grouped-table cell escaping,
and the typed ownership of the recent SQL-shaped provider fallback. Each must be
confirmed through public behavior before production changes.

## Test sequencing

During exploration and development, do not run existing test files or existing
test suites. Allowed commands are limited to new focused RED/GREEN regressions,
isolated manual probes, and syntax/import checks directly required by changed
code.

After all manual evaluation, fixes, and independent reviews are complete, run
the relevant existing focused suites exactly once. Then run Ruff check, Ruff
format check without rewriting, Python compilation and import checks, Git
whitespace validation, scoped diff inspection, and an explicit comparison
showing whether `planning_decisions.py` changed from the starting commit.

Run the representative offline answer-quality and BIRD/Contract EX evaluation
only after implementation is complete. Inspect every non-passing or low-scoring
case manually. Correct genuine product defects in their owning layer; document
intentionally unsupported cases rather than weakening expectations.

## Acceptance criteria

- The approximately 50-turn transcript and compound matrix have turn-by-turn
  assessments and no unexplained imperfect answer.
- Grounded requests avoid unnecessary clarification and provider calls.
- Genuine ambiguity creates a useful typed clarification and resumes correctly.
- Context survives topic changes without stale or unsafe referent reuse.
- English, Arabic, and mixed-language answers preserve the correct scope and
  render naturally.
- Unsupported and adversarial requests fail closed without leaking schema,
  SQL, parameters, prompts, exceptions, employee data, history, diagnostics, or
  telemetry.
- Every compound unit is prepared before execution and failures publish or
  execute no earlier partial result.
- Every executable query is rebuilt and revalidated through the trusted
  compiler with bound parameters.
- Every accepted implementation has focused RED/GREEN evidence and a fresh
  Sol-medium approval.
- The final existing-test gate, static checks, import/compilation checks,
  whitespace check, and offline evaluation results are recorded truthfully.

## Completion report

Report the starting and final HEADs, all manual scenarios, important per-turn
failures and corrections, root causes and owning layers, focused RED/GREEN
evidence, safe state representation, provider inputs and privacy boundaries,
semantic improvements, evaluation scores, remaining weak cases, final tests and
static checks, independent review findings, unavailable live/private checks,
preserved limitations, changed files, and commits. Do not claim perfection
without evidence, and do not push.
