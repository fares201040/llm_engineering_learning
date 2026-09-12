# APDC Composable Attendance Intent Design

## Purpose

Prevent positive attendance calculations from being applied to negative or
complementary questions. Replace monolithic calculation intent with a small,
declarative semantic model that represents what is counted separately from
the business conditions applied to it.

This design must answer the following query correctly without adding a
question-specific function:

> Tell me how many days employee A11017 did not attend during September 2026.

For the currently loaded records, the deterministic result is one scheduled
working date with no positive worked hours. Because the source covers only
2026-09-01 through 2026-09-07, the answer must also disclose that the requested
month is only partially covered.

## Constraints

- PostgreSQL remains authoritative for exact facts when enabled.
- The LLM interprets language, but code owns executable meanings and safety
  validation.
- Do not add rules for individual employees or functions for individual
  question phrasings.
- Preserve the public answer and retrieval return shapes.
- Preserve existing employee, date, authorization, clarification, retrieval,
  and access-control behavior.
- Never silently answer a positive calculation when the question contains an
  unresolved negative or zero-valued condition.
- Remove only superseded APDC intent code, tests, imports, and documentation.
  Preserve unrelated dirty-worktree content and generated source data.
- Every production behavior change starts with a failing regression test.

## Confirmed Failure

The existing planner maps `attend`, `did not attend`, `absent`, and
`zero worked hours` to the monolithic `worked_days` calculation. The canonical
compiler then correctly but destructively enforces `Total_Worked_Hrs > 0`.
This produces the opposite of the requested result or an impossible predicate
combination.

Observed live results for A11017 were:

| Question meaning | Current result | Correct loaded-data result |
|---|---:|---:|
| attended/worked dates | 4 | 4 |
| did not attend | 4 | 1 |
| recorded absent dates | 0 | 1 |
| scheduled dates not worked | 5 | 1 |
| zero-worked-hours dates | 4 | 3 across all recorded dates, or 1 within scheduled dates |
| attendance rows | 7 | 7 |

The root cause is the query representation, not PostgreSQL or final-answer
wording.

## Business Semantics

The system will distinguish these concepts:

- `worked`: `Total_Worked_Hrs > 0`.
- `not_worked`: `Total_Worked_Hrs <= 0`.
- `scheduled_working_day`: `Day_Type = "Working Day"`.
- `absent`: `Exception = "Absent"`; this is the explicit recorded HR outcome.
- `authorized`: `Status = "Authorized"`; this is workflow status, not proof of
  work.

Natural-language `did not attend` means scheduled working dates that were not
worked: `scheduled_working_day AND not_worked`. It does not count off-days.
Approved leave is physically non-attended unless the user asks for unexcused
absence; explicit `absent` remains the narrower recorded HR outcome. This
distinction is declarative and may be changed in one business registry if APDC
policy changes.

## Composable Query Intent

Replace the calculation-name-as-program design with orthogonal fields:

```text
measure
    operation: count | distinct_count | sum | average | min | max | percentage
    field: Date | Employee_ID | numeric field | null for rows

business_predicates
    scheduled_working_day | worked | not_worked | absent | authorized

filters
    employee, date, department, shift, explicit field values, and other scope
```

Examples:

```text
worked days
    measure = distinct_count(Date)
    business_predicates = [worked]

scheduled working days
    measure = distinct_count(Date)
    business_predicates = [scheduled_working_day]

did not attend
    measure = distinct_count(Date)
    business_predicates = [scheduled_working_day, not_worked]

absent days
    measure = distinct_count(Date)
    business_predicates = [absent]

attendance records
    measure = count(rows)
    business_predicates = []
```

`attendance_schema.py` will contain immutable definitions for measures and
business predicates. Each predicate compiles to ordinary typed
`FilterCondition` objects, so PostgreSQL and Chroma continue to use the same
validated execution path.

The existing `CalculationName`, monolithic calculation definitions, and
question-ignoring compatibility wrapper become stale once all internal callers
and tests use the composable model. Remove them rather than maintaining two
competing sources of truth. Retain compatibility only at an actual public
boundary proven by a test.

## Planner and Deterministic Safety Contract

The planner receives concise definitions of every measure and predicate. It
must select the measure and zero or more predicates independently.

Code then validates the proposed plan before retrieval:

1. Compile each declared business predicate from the registry.
2. Merge independent explicit filters without overwriting them.
3. Detect incompatible predicates such as `worked` plus `not_worked`.
4. Detect incompatible constraints on the same field instead of replacing one
   silently.
5. Enforce explicit controlled values such as `Absent` through the field
   registry.
6. Apply a generic polarity contract: negative or zero-valued attendance/work
   language must produce a negative predicate or explicit non-positive numeric
   condition. Otherwise stop with clarification; never execute the opposite
   positive plan.
7. Require scheduled-day scope for generic `did not attend`; explicit
   all-recorded-date wording may intentionally request all zero-hour dates.

The polarity contract is a safety validator, not a question-to-answer mapper.
It recognizes general operators such as `not`, `no`, `without`, and `zero`
near a relevant attendance/work concept. The declarative planner output still
selects the executable business meaning. This gives the LLM flexibility while
preventing silent inversion.

Ambiguous phrases such as a bare `days` request continue to use clarification
state. Clarification choices should be generated from composable intent
options rather than the removed monolithic calculation catalog.

## Data Coverage Contract

Exact date-range answers must compare the requested range with authoritative
available coverage. Coverage comes from PostgreSQL `MIN(attendance_date)` and
`MAX(attendance_date)` when PostgreSQL is active and from validated source
metadata in the fallback path.

Coverage does not change the calculated value. It adds deterministic result
metadata and answer text. For the target query, the response should be
equivalent to:

> 1 scheduled working day was not attended in the available records. The
> available attendance data covers September 1-7, 2026, not the full month.

Do not let the final-answer LLM omit or alter this caveat.

## Execution and Formatting

The compiled plan uses the existing exact aggregation path:

```sql
COUNT(DISTINCT attendance_date)
WHERE employee_id = 'A11017'
  AND attendance_date >= DATE '2026-09-01'
  AND attendance_date <= DATE '2026-09-30'
  AND day_type = 'Working Day'
  AND total_worked_hrs <= 0
```

The actual SQL builder remains parameterized; the literal SQL above is only a
semantic illustration.

Deterministic aggregation formatting will use intent-aware labels such as
`worked days`, `scheduled working days`, `scheduled non-attended days`, and
`recorded absent days`. It must not fall back to the generic phrase "matched
the requested criteria" when a known business predicate combination is
available.

## Evaluation and Regression Protection

Add permanent source-verified cases for:

- attended, worked, scheduled, non-attended, and explicitly absent dates;
- zero hours across recorded dates versus zero hours within scheduled dates;
- off-days excluded from generic non-attendance;
- authorized status remaining independent from attendance state;
- approved leave behavior;
- explicit employee and selected-employee follow-ups;
- full and partial requested date coverage;
- contradictions and dropped negation stopping before retrieval;
- positive/negative paraphrase pairs.

Add invariant tests where data permits:

```text
scheduled_worked_dates + scheduled_not_worked_dates
    = scheduled_working_dates

recorded_absent_dates <= scheduled_not_worked_dates
```

Mocked planner tests prove compilation and safety gates. Live provider tests
prove representative paraphrases produce the expected normalized plans.
Direct PostgreSQL queries remain the truth oracle.

## Stale-Code Cleanup

After all callers have migrated and tests are green:

- remove the monolithic calculation registry and its compatibility wrapper;
- remove obsolete `CalculationName` and pending-calculation state names;
- remove prompt instructions that equate all attended wording directly with
  `worked_days`;
- replace tests that mock the removed representation with behavior tests over
  measures and predicates;
- update APDC documentation and evaluation expectations;
- remove temporary diagnostics and unused imports introduced during the work.

Do not delete unrelated files, databases, notebooks, or user changes.

## Verification

Completion requires:

- focused red/green tests for every confirmed negative-intent failure;
- the complete implementation, app, and evaluation unit suites;
- Ruff check and formatting verification;
- Python compilation and `git diff --check`;
- direct PostgreSQL truth checks for all target concepts;
- live planner, normalized-plan, SQL-result, and final-answer probes;
- positive controls proving worked/scheduled/record calculations are unchanged;
- negative controls proving contradictions cannot cross the retrieval gate;
- an independent code review with no unresolved critical or important issue;
- a final stale-code and dirty-worktree scope audit.
