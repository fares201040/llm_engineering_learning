# Attendance planner handoff

Updated: 2026-09-29

Branch: `main`

Conversation logic baseline: `e5e9dadc3b0ed0fd3cfc6b9370ce7df8840461db`

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
question immediately, shows a short `Thinking ...` assistant message while the
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

## Verification on the conversation logic baseline

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

## Next work

1. Restart Gradio from the current checkout and run a fresh **many-turn sequence of
   short, informal messages in the actual UI**. Check rendered answers, SQL and rows
   when a number or scope is in doubt, follow-up state, correction, Clear/Submit,
   Markdown, and visible progressive display. Record each new verified prompt in the
   manual conversation reference. The user requested this in a new clean chat.
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
