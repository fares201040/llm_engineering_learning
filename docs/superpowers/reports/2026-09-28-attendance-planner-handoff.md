# Attendance planner handoff

Updated: 2026-09-30

Branch: `main`

Baseline before this continuation: `25478e1f`

## Current runtime

`week5/new_app.py` serves the Gradio chat. It calls the direct SQL pipeline in
`week5/new_implementation/online/pipeline.py`. The configured local reference and
planner models are both `openai/gpt-4.1-mini`; `config.py` uses those as defaults but
environment settings can override them. The reference model resolves informal
follow-ups and employee references. The planner uses the allowlisted PostgreSQL
schema to write one read-only SQL query, then drafts and independently reviews the
answer against executed rows. Review can request a bounded requery. Only the reviewed
answer and verified state are published.

The Gradio chatbot renders Markdown with HTML sanitization. It shows the user's
question immediately, shows a short `Generating Answer...` assistant message while the
answer is prepared, and progressively replaces it with the **reviewed final answer**
in cumulative chunks. This display pacing happens after review, not during model
generation. Clear and Submit
events use client sequence numbers and a per-session gate so obsolete work cannot
repopulate a cleared chat. A running local server must be restarted to load a new
commit.

## Conversation and scope changes in the current commit

- Reference and planner prompts now preserve all clauses in rough, abbreviated,
  multi-part questions. Independent clauses keep independent population and filter
  scopes. Reviewer guidance checks each SQL branch and shared CTE against the
  requested clause before accepting its answer.
- New requests use the original question as the authoritative planner request.
  The reference rewrite remains separately labeled as an interpretation, so it can
  help unpack shorthand without silently importing an earlier filter.
- Follow-ups can carry a verified employee only when a contributing source row path
  uses that employee ID. A scalar lookup subquery cannot make that employee the next
  active subject.
- Explicit day and date ranges, month abbreviations, and shared-year month lists are
  parsed as calendar intervals. Multiple periods receive no false global bound.
  Employee unions with one common explicit date retain the date guard; independent
  criteria clauses can have their own periods.
- Valid bounded grouped `COUNT(*) OVER()` output can be given the structural
  `matched_count` alias without changing the grouping measure.
- The `position` schema description distinguishes a job role from `grade` and
  `gradeset`.

These changes use SQL structure, request provenance, schema descriptions, and model
review. They do not encode a test employee, an answer value, or a special response
for one request phrase. The governing instructions are in
`week5/new_implementation/AGENTS.md`.

## Historical verification on the earlier conversation logic commit

- `.venv\Scripts\python.exe -m unittest discover -s week5/new_implementation -p 'test_*.py'`:
  **254 tests, OK**.
- `.venv\Scripts\python.exe -m pytest -q week5/test_new_app.py`:
  **20 passed**.
- Ruff lint and format checks on the nine changed Python files passed;
  `git diff --cached --check` passed before commit.
- A live local model and database turn for `auth by country; hr work loc ppl count,
  all hr` produced separate Authorized-country and all-HR work-location query
  branches. The answer gave Yemen 770, Burundi 1, Uganda 4, and 9 distinct HR
  employees at `N/NA`. This is one observed run, not a guarantee of deterministic
  model output.
- The previous session's browser check observed incremental rendering of a long
  reviewed Markdown answer and verified that Clear followed by an immediate new
  Submit did not restore the old reply. The latest conversation changes were tested
  by direct model call and unit tests; the already running Gradio process had not
  been restarted at handoff time.

The exact checked prompts retained from this work are in
[`2026-09-29-attendance-manual-conversation-reference.md`](2026-09-29-attendance-manual-conversation-reference.md).
The local launch, browser conversation, UI checks, and SQL/row review procedure
are in the [browser test guide](../../../week5/new_implementation/BROWSER_TEST_GUIDE.md).

## September 30 continuation

The browser replay report records the prior L01–L42 and M01–M03 checks. This
continuation used fresh Gradio browser chats and the same 3,964-row database
snapshot dated September 1–7, 2026. The exact new prompts and observations are
in the [manual conversation reference](2026-09-29-attendance-manual-conversation-reference.md)
under N01–N07. Independent read-only aggregates checked the reported counts and
worked-hour totals. Same-turn SQL was captured for the final two-part repair.

- N01–N03 passed as browser answers: a two-date Draft/Authorized comparison,
  a Pending-only follow-up that retained the dates, and independent country-person
  and Engineering-location reports. Tables and bullets rendered as HTML table
  rows and list items in the Gradio chat.
- N04 showed a real presentation error: an Engineering hours ranking inferred
  that the leading location was more active or staffed. The answer and review
  prompts now tell the models to state observed totals and shares without
  attributing them to unmeasured staffing or activity. A neighboring fresh-chat
  ranking (N05) returned the supported ranking and share without that inference.
  The exact N04 conversation was not rerun after the change.
- The initial N07 two-part follow-up safe-failed after four SQL attempts. The
  runtime date parser had recognized only September 5 in “Sep 2 versus Sep 5,
  2026,” so its structural date check rejected an otherwise independent query.
  It now recognizes both dates and leaves the different branch scopes to the
  planner and reviewer. On the browser rerun, SQL used separate CTEs: September
  2 and 5 all-department worked hours, and all-date Draft counts by country.
  Both rendered tables matched independent aggregates.
- The initial in-chat progress text is now `Generating Answer...`. A fresh
  browser turn showed it before the reviewed answer. The Gradio Chatbot still
  renders Markdown with HTML sanitization; no fixed answer template was added.

These changes retain model discretion. The parser fix removes a false rejection;
it does not choose SQL or compute the answer. The prompt addition gives the
answerer and reviewer a general evidence boundary for analytical prose.

### Remaining issues and evidence gaps

1. L14 remains ambiguous between a shift label and a calendar date. The user
   clarified it in L15, but the first shorthand question has no single proven
   intended interpretation.
2. L04 returned correct totals with an unrequested detailed split. L22's
   “shifts n day counts” did not produce per-shift day counts without L23's
   clarification. L23's counts were correct, but its prose called an OFF day a
   worked day. These scope and wording cases have not been rerun in this
   continuation.
3. L37's numeric result matched the database, but the prose used “present” for
   people across all attendance statuses. That wording has not been rerun.
4. The exact H01–H04 conversation cannot be faithfully replayed because its
   preceding employee question was not retained. L25 and L28 interruption
   timing also remains unverified for those exact turns. A separate Clear while
   pending was previously observed to keep old work from repopulating the chat.
5. Only the final N07 repair has same-turn SQL in this continuation. N01–N05
   match independent aggregates and visible browser rows, but their exact SQL
   scope remains unverified. LLM outputs can vary across runs; single passing
   replays do not establish deterministic acceptance.
6. The Colab notebook gate and private evaluation have not been rerun for this
   source. The earlier verification numbers above remain historical.

## Next work

### September 30 fresh long-conversation continuation

Starting from clean `c4c88f2b`, a fresh Gradio browser conversation exercised
independent shift breakdowns, OFF-day follow-up, removal of inherited filters,
decimal hour totals, a correction, and different record/person measures. The
database remained at 3,964 rows dated September 1–7. Same-turn SQL, result rows,
draft, and review were captured locally outside Git and compared with read-only
aggregates. Exact nonprivate prompts and outcomes are R01–R06 in the manual
reference.

- R01–R02 passed with separate source scopes, correct aggregates, rendered tables
  and bullets.
- R03 exposed a UNION cast of decimal worked hours to `bigint`. General planner
  and review guidance now preserves numeric precision. A restarted browser
  replay returned Uganda 21.47 and Yemen 15,681.14 hours while retaining the
  correct 89/78 Authorized counts.
- R05–R06 exposed a separate model failure: correct person and record counts
  were followed by an unsupported duplicate-record explanation. One review
  even contradicted its own no-duplicates SQL check. Three model-guidance
  adjustments did not make the explanation reliable with `gpt-4.1-mini`, so
  those ineffective additions were removed. No wording-specific runtime rule
  was added. A future architectural choice should improve model reasoning or
  the evidence shape and be measured with fresh browser replays.
- The exact N03-to-N04 Engineering follow-up was rerun. Its first replay
  safe-failed because the planner repeatedly called PostgreSQL's two-argument
  ROUND on a double-precision expression. General numeric-cast guidance fixed
  the observed browser rerun after one SQL retry. The top-three hours and shares
  matched independent aggregates; the answer did not repeat country counts or
  infer staffing/activity from the hours.
- An explicit **Clear conversation** button was wired to the existing sequence
  gate after the Chatbot's built-in Clear callback did not fire in this session.
  Keyboard activation cleared a pending turn, and the next count answer appeared
  alone. Mouse-click automation did not establish a reliable result.
- Shared schema and answer/review guidance now presents clock times in 24-hour
  `HH:MM` without altering stored evidence. A nonempty browser swipe-time answer
  showed `08:18` and `15:41`, matching source `HH:MM:SS` values.
- A broad all-date/all-department report produced only part of 112 grouped rows
  and omitted requested day summaries. It remains an incomplete report case.

The exact H01–H04 seed is still unavailable. L14 remains ambiguous; the exact
L25/L28 interruptions and the Colab/private gates were not rerun in this
continuation. Do not treat earlier
browser passes or historical evaluation counts as current acceptance results.

## Earlier next-work list

1. Revisit the L22/L23 and L37 wording cases with same-turn SQL and row traces.
   Use new nearby requests to test whether aggregation and analytical labels
   generalize, without adding phrase-specific runtime rules.
2. Revisit the older synthetic long scenario. The September 28 run observed a
   wrong turn-4 date restriction and record/day wording, then an interrupted turn 5.
   These are historical observations; this commit has not rerun that Colab scenario.
   Diagnose current behavior before treating those issues as resolved.
3. Rebuild and rerun the sanitized Colab notebook gate and any authorized private
   evaluation for this exact source before claiming Colab or 311-case acceptance.
   Earlier archive hashes, test counts, and Colab pass flags are historical evidence
   only. Keep credentials, private rows, payloads, and result files out of Git.

## Historical Colab continuation

The older CPU session was `attendance-phase2-3`. The source package and notebook are
`week5/new_implementation/colab/attendance_phase2_source.zip` and
`attendance_phase2_tests.ipynb`; the WSL Colab CLI is
`/home/faris/.local/bin/colab` in `Ubuntu-24.04`. Rebuild the package with
`python -m week5.new_implementation.colab.package_source` before upload; the old
archive hash must not be reused. The synthetic scenario checkpoint was
`/content/attendance-long-final-review.json`. Earlier reports and the Colab guide
remain useful for commands and historical diagnostics, but do not establish a pass
for the current commit.
