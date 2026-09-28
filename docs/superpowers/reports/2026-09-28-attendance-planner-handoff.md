# Attendance planner handoff

Date: 2026-09-28

Branch: `main`
Workspace: `D:\projects\llm_engineering_ed_donner\llm_engineering`

## Current design

`week5/new_app.py` calls `week5/new_implementation/online/pipeline.py`. The
reference and request-rewriting model is `qwen3.5:2b`; the PostgreSQL query
planner is `gpt-5-nano`. The same GPT planner drafts the answer from typed query
rows and independently reviews it. The separate answer and verifier models have
been removed. When the reviewed answer is materially wrong or unrelated and a
different query can supply the needed evidence from this database, review can
request a bounded SQL replan. SQL validation, authorized employee row scope,
execution bounds, and the shared provider-call budget still apply.

The models receive the current question, relevant full conversation history
with compaction for older text, trusted state, scope provenance, the discovered
database schema with field descriptions and standard values, executed SQL, and
typed rows. The lean SQL planner prompt was preferred over a longer guided
variant in the earlier same-case comparison. Schema-derived SQL examples remain
in both variants. Business field meaning belongs in the schema descriptions or
system prompts; see `week5/new_implementation/AGENTS.md` and the Cursor rule.
The Cursor rule also requires cautious, cross-case review of any system-prompt
edit because one sentence can affect planning, answer writing, and review.

## Root causes fixed

- A SQL guard rejected legitimate close-name searches as
  `unrequested_employee_filter`. It was removed. Unmatched written names remain
  employee search targets, and a follow-up can broaden to close matches.
- A runtime matcher turned stored value `MES-Eng` into acronym `me`, so ordinary
  “tell me” and “give me” forced `work_location='MES-Eng'` into SQL. The entire
  request-word-to-standard-value gate and its retries were removed. A similar
  wording-based rejection of unrequested date filters was retired; explicit
  verified date-scope checks remain.
- New broad requests now reset incidental employee/location scope. The planner
  gets `scope_provenance` to compare carried filters with the current and prior
  original user questions and prior SQL. An earlier answer's field value is not
  automatically a user-requested filter.
- The `work_location` schema description now says it is a group within a
  department and that one department can have multiple work-location groups.
  The PostgreSQL setup comment has the same meaning.
- Schema descriptions and SQL examples explain manual swipe detection by
  comparing all four effective and device swipe date/time pairs with null-safe
  `IS DISTINCT FROM`; explicit absence and its negation use the `exception`
  field. The previous active semantic-contract code is retired in
  `online/semantic_contracts.py`.
- Answer guidance distinguishes requested periods from observed date extent,
  record counts from distinct days, and absolute totals from rates. Calendar
  month coverage objects are supplied only when the request has a date period.

## Evidence and verification so far

- The final local selected suite passed **269 tests and 16 subtests**. Ruff lint
  passed for `week5/new_implementation` and `week5/new_evaluation`; `git diff
  --check` found no whitespace errors. Colab CPU notebook validation passed
  269 tests, Ruff lint and formatting, and Python compilation on an earlier
  source package. The final source package SHA-256 is
  `8904beaa2b3d62d05ca52607939757c60c58324fc878e025716b27e10a5a1a25`.
  It contains the inclusive-boundary prompt edit and has **not** had a final
  Colab notebook or live-model gate. A repository-wide Ruff format check flags
  two notebooks and an untouched `test_ingest_postgres.py`; Python source
  edited for this task was formatted and lint passed.
- Read-only PostgreSQL inspection found A10101 has seven September 1–7 rows,
  all with work location `N/NA`. Its September 1 adjusted `To_Time` is 18:29,
  versus device `Actual_To_Time` 11:31:03. MES-Eng instead has 126 Engineering
  rows totaling 137.88 overtime hours, exactly the old misleading answer.
- A five-turn Colab CPU reproduction of the user's Raed conversation returned
  close-name candidates, resolved A10101, detected the September 1 manual
  swipe, and compared overtime across all 16 departments without a MES-Eng
  filter. The broad comparison found Operations 3946.91 observed overtime
  hours and Engineering 2019.93. This reproduction used the package immediately
  before the independent final-review replan change.
- The checkpointed synthetic complex scenario completed four turns with
  mechanical pass flags. Turns 1–3 used correct SQL for worked days, explicit
  absence, and null-safe not-absent dates. Turn 3 incorrectly described the
  observed September 6 endpoint as uncovered; a generic inclusive-boundary
  prompt edit is packaged locally but needs live verification. Turn 4 asked for
  department worked-hours totals above 10 without a date limit. SQL added the
  unrequested observed range `2026-08-03` through `2026-09-06`, and the answer
  called `COUNT(*)` values “days” even though they count records. The next turn
  stalled and was interrupted; the checkpoint remains at four completed turns.
  These semantic errors mean the complex scenario is **not approved**, despite
  its automated pass flags.

## Remaining work in the next thread

1. Diagnose and fix the turn 4 date-scope and record-count wording errors with
   general schema/prompt guidance, then rerun it and related questions. Determine
   why turn 5 stalled, resume the checkpointed scenario through turn 8, and
   inspect SQL, typed rows, and final answer for every turn. Verify that a new
   month comparison retains only user-requested group eligibility.
2. Upload the final package, run the Colab notebook gate, and recheck the
   not-absent answer. The reviewed answer must treat an observed endpoint date
   as observed. Re-run the Raed reproduction if review behavior changes answers.
3. Run the private **311-case** evaluation on Colab CPU from a fresh checkpoint
   for the final source package. The earlier 31-case local checkpoint belongs
   to older code and is not final evidence. Manually review each complex answer
   and investigate failures using exact question, reference output, SQL,
   executed rows, and review decision. Automated phrase matching alone does
   not prove factual correctness.
4. Review the retired answer checks in `new_evaluation/acceptance.py` after the
   full workflow. It now marks prose review pending; confirm any remaining
   locale-only code gate is appropriate and keep result/SQL checks distinct
   from semantic answer correctness.

## Colab continuation

Session: `attendance-phase2-3` on CPU. Source ZIP and notebook are
`week5/new_implementation/colab/attendance_phase2_source.zip` and
`attendance_phase2_tests.ipynb`. Runtime configs are
`/content/.attendance_private_runtime.json` and
`/content/.attendance_phase3_runtime.json`; do not print credentials. Private
payload and result files stay outside Git. The checkpoint for the synthetic
long scenario is `/content/attendance-long-final-review.json`; its downloaded
copy is under ignored `week5/new_evaluation/results/`.

The WSL Colab CLI is `/home/faris/.local/bin/colab` in `Ubuntu-24.04` and uses
`--auth=adc --session attendance-phase2-3`. After source edits, run
`python -m week5.new_implementation.colab.package_source`, upload the ZIP and
notebook, restart the kernel, and execute the notebook gate. Use
`colab/run_acceptance_turn.py` with `ACCEPTANCE_SCENARIO=long`, the next
`ACCEPTANCE_TURN`, and
`ACCEPTANCE_CHECKPOINT=/content/attendance-long-final-review.json` for
checkpointed turns. `colab/reproduce_raed_scope.py` records the exact five-turn
real-data trace. `colab/run_private_eval.py` supports batches up to 50 and
`PRIVATE_EVAL_RESTART=1` for the first final-source batch; then resume without
that flag. Do not include private database rows or credentials in commits.
