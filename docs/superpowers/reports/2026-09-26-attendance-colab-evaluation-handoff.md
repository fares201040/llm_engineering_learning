# Attendance synthetic Colab evaluation handoff

> Historical snapshot. The current implementation and remaining-work handoff is
> [2026-09-28-attendance-planner-handoff.md](2026-09-28-attendance-planner-handoff.md).
> Several architecture and test details below were superseded on September 28.

Date: 2026-09-26
Branch: `codex/attendance-llm-planner`

## Application map

`week5/new_app.py` receives the Gradio question, recent chat history, and trusted
conversation state. `online/pipeline.py` authorizes the session, loads the PostgreSQL
employee directory, handles a pending confirmation, validates literal inputs, and
uses `online/reference.py` to identify new requests and follow-ups. Employee lookup
tries the authorized PostgreSQL directory first and uses a model-specific Chroma
employee-name collection only as a confirmation candidate fallback. A candidate
must match the authorized directory before being offered; no fuzzy result is
silently accepted as an employee ID.

`online/context.py` projects the allowlisted attendance schema and row/date coverage
for the model. `online/planner.py` requests one PostgreSQL query. The pipeline checks
known semantic constraints and retries eligible planner/database rejections up to
three attempts. `online/execution.py` runs a bounded read-only query. Selected
follow-ups use schema-checked native SQL builders in `online/comparison.py` and
`online/running_total.py` to preserve verified grouping, metric, and eligibility.
`online/answering.py` writes and independently verifies the answer against the
executed result and coverage. Only a verified answer appends a `VerifiedTurn` to
conversation state; failures preserve previous trusted state.

Embeddings remain configurable between Hugging Face and OpenAI with separate
model-specific Chroma collections. Attendance data uses the retained row-chunk
approach. Chroma does not replace PostgreSQL SQL planning for attendance analytics.

## Issue classes and native fixes

| Class | Observed synthetic failure | Root cause and fix |
| --- | --- | --- |
| Follow-up month comparison | Turn 5 lost grouped eligibility, miscomputed period values, or described incomplete coverage as full months. | The model reauthored the previous grouped query. A native builder reads the previous verified SQL shape, retains its metric/HAVING eligibility, and derives both calendar periods. The renderer checks each group and arithmetic. |
| Reference and subject scope | General date aggregates could be called missing-employee or outside-domain. | Reference binding now recognizes general authorized scope; schema-grounded aggregate requests cannot be vetoed solely by a model unsupported verdict. |
| Answer coverage | A factual date-range sentence or “no bounded sample” could be rejected. | Coverage checks distinguish factual availability from a claimed requested interval or positive sampling claim. |
| Running total follow-up | A model-built cumulative answer could vary in metric or arithmetic. | The native builder derives the date series and metric from verified grouped SQL; the renderer checks every cumulative value. |
| Independent analytic date scope | Synthetic department ranking requested no period, yet planned SQL conditionally aggregated September and August. The answer verifier rejected the resulting month answer. | The reference stage preserved the original question; the planner introduced the periods. A SQL AST predicate check now rejects an unrequested `attendance_date` filter before execution and supplies scope feedback for the existing three-attempt planner retry. It permits grouping by date without a date filter. |
| Evaluator false signals | Joined subset checks could reuse one row; predicate and unsupported capability checks could misclassify outcomes; failed traces omitted details. A one-line answer could swap group values yet pass a line-level association check. | The evaluator now uses SQL-aware predicate scope, distinct row matching, exact capability comparisons, and retains failed check details. Answer associations are bounded by the expected group labels, including when several groups share one line. |

These fixes use schema, SQL shape, request scope, and result coverage. They do not
encode a specific employee or the four synthetic questions as production answers.

## Verification and limits

- Sanitized source ZIP SHA-256:
  `98a304a5dae85c965b65f0999ef88528effebd9f67d4d4f0fce93202c1322f7d`;
  42 allowlisted files. The T4 notebook reported 187 passed deterministic tests,
  Ruff lint and formatting for 23 files, and Python compilation.
- The T4 synthetic fixture had 16 attendance rows and three employees, served by a
  temporary read-only PostgreSQL role and Qwen 3.5 4B. UI turns 1–7 passed on the
  immediately preceding snapshot. Turn 8 passed on the final snapshot using the
  verified seven-turn checkpoint, with eight completed turns and all outcome,
  semantic, and state checks true.
- `run_synthetic_eval.py` invoked `week5.new_evaluation.eval --all --test-file` on
  four generated cases. It reported `completed: 4` and `failures: []` on the final
  snapshot. The earlier grouped ranking failure was reproduced and traced through
  reference, SQL, execution, answer writing, and verifier stages before its fix.
- The private 311-case corpus was not sent to Colab or run there. The ZIP contains
  only a generated 311-line count-only placeholder manifest. Broader behavior,
  Arabic prompts, and the private corpus remain unverified by these four cases.

The ignored `week5/new_implementation/.env.postgres` and
`week5/new_evaluation/results/` stay outside Git and the source ZIP. Do not log
credentials or private result contents. Before any 311-case Colab run, resolve the
earlier synthetic-only data boundary and prepare a corpus with an approved transfer
scope. The exact T4 setup and upload commands are in
`week5/new_implementation/colab/LOCAL_COLAB_SYNC_GUIDE.md`.
