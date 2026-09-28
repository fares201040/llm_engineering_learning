# Attendance planner guidance

The planner should understand the database through the schema payload and its system
prompt. Put every model-facing explanation of a field, stored standard value, business
meaning, nullable comparison, or query pattern in one of these two places:

- Schema metadata in `online/context.py`: PostgreSQL column comments, fallback column
  descriptions, JSON field descriptions, observed `standard_values`, and
  `DatabaseContext.business_meanings`.
- The planner system prompt in `online/planner.py`, including schema-derived examples
  from `online/planner_examples.py`. Keep alternate prompt variants aligned.

Do not hide a business interpretation in `pipeline.py` validators, answer formatting,
evaluation matchers, or another runtime rule. Runtime validation should address SQL
safety, authorization, and structurally verified scope. Explain any missing business
meaning to the model in the schema or prompt, then verify the resulting SQL and answer.
Avoid rules keyed to a question's phrasing or to one employee, date, or test case.
For attendance swipe fields, call the clerk-adjustable `From_*`/`To_*` values
"effective swipes" and the `Actual_*` values "device swipes". This database does
not contain a separate payroll dataset; do not imply one exists in model guidance.

When the planner gives a wrong SQL decision or answer, inspect the exact question,
history, schema payload, prompt, generated SQL, executed rows, draft answer, and
reviewed answer. Find the missing, incomplete, or misleading information the model
received. Correct that information in the schema descriptions or system prompt and
add a valid general example when it helps. Recheck the failed case and neighboring
cases, then read their final answers for factual correctness. Do not patch the symptom
with a special request phrase, employee, date, or answer-text rule.

SQL examples must be valid, schema-grounded, and framed as illustrations rather than
as filters to copy. Do not use angle-bracket placeholders in executable-looking SQL.
Keep data coverage distinct from the requested period and from the current date.
For follow-ups, trace every carried person, location, date, and category to the
user's current or prior request. A field appearing only in a prior result or answer
is not a requested filter. Check `scope_provenance` when debugging inherited scope;
new broad questions must not inherit an earlier employee or location.

Before claiming answer correctness, review the SQL, executed rows, and final answer
together. A literal phrase match or embedding similarity alone is not sufficient to
verify numeric values, which group owns a value, or a statement about data coverage.
