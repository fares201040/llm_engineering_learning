# Attendance chatbot: browser use and manual test guide

This guide covers the local Gradio app in `week5/new_app.py`. Use the
[manual conversation reference](../../docs/superpowers/reports/2026-09-29-attendance-manual-conversation-reference.md)
to avoid repeating already checked questions. The counts in that reference belong
to its recorded database snapshot; check the current data before treating them as
expected values.

## 1. Start the local app

1. Open PowerShell in the repository root. Use the project virtual environment and
   confirm that the local PostgreSQL attendance database is available.
2. Configure `OPENAI_API_KEY` and `POSTGRES_READONLY_DSN` in the PowerShell
   environment or the repository root `.env`. The runtime also reads
   `week5/new_implementation/.env.postgres`. See [PostgreSQL setup](POSTGRES_SETUP.md)
   for database and model settings. Keep credentials out of test records and Git.
3. Start the app and leave its terminal open:

   ```powershell
   .\.venv\Scripts\python.exe -m week5.new_app
   ```

4. Use the **Local URL printed by Gradio**. The local launch normally opens a
   browser automatically. A prior session used `http://127.0.0.1:7865/`; that port
   is an example, not a fixed app setting. If an old server is already running,
   check which process owns the printed port before restarting it. Restart after
   code, configuration, or model changes so the browser uses the current source.
5. Check that the page shows **APDC Attendance Assistant**, a chat, a question
   box, and a collapsed **Retrieved Context** accordion. If the page does not
   load, inspect the app terminal first. A browser refresh does not load changed
   Python code into an existing server.

The [Colab chatbot guide](colab/COLAB_CHATBOT_GUIDE.md) has a different launcher
and a generated authenticated URL. Use its start and stop steps for Colab; the
conversation and answer checks below still apply.

## 2. Open and inspect the browser

For a person testing manually, open the printed URL in a browser. Keep the app
terminal visible for failures. Record the current Git commit (`git rev-parse HEAD`),
the test date, the database snapshot or coverage, and the URL/port before sending
the first question.

For an agent using the Codex browser tool:

1. Call `cua.getState()` to list browsers and tabs. Select the tab whose URL is
   the printed Gradio URL; do not assume the in-app browser already has it open.
   If needed, open it with `cua.createBrowserTab("iab", url, { visible: true })`.
2. Bind the selected tab with `cua.getTab(tabId, { browser: browserId })` and read
   `tab.getAXState()`. Use the fresh accessibility index for the **Your Question**
   textbox and for buttons. If an element is missing from that state, use
   `tab.getAXStateAndScreenshot()` to inspect the page visually.
3. Enter a question with `tab.setValue(textboxIndex, question)` or
   `tab.paste(textboxIndex, question, { format: "text" })`, then submit with
   `tab.pressKey(textboxIndex, "Return")`. Read `tab.getAXState()` after the action.
   Re-find element indexes after each page change; do not reuse an old index.
4. For a long answer, capture successive accessibility states (or screenshots)
   while the reply changes. Inspect the final state only after the answer stops
   changing. The browser tool's observations wait for the UI; no fixed sleep is
   needed between observations.

## 3. Send a conversation

1. Start with a short, informal question. Vary spelling, abbreviations, incomplete
   grammar, and everyday wording. Check that the app answers the intent rather
   than requiring a polished question.
2. Continue **in the same chat** with short follow-ups: a pronoun, a changed date,
   a changed status, a corrected employee, an added field, and a request to remove
   a prior filter. Keep the prior turns because they are part of the test input.
3. Include multi-part requests whose clauses have different scopes. For example,
   ask for one category breakdown and a separate whole-population count. Check
   each clause independently so a filter from one branch does not leak to another.
4. Use **Clear** when a question must start with no conversation history. Confirm
   the chat and Retrieved Context become empty, then send the new question.
   Run an occasional Clear while `Thinking ...` is visible, immediately submit a
   new question, and check that the old answer never returns.
5. Choose fresh cases by comparing with the manual reference first. A useful new
   case changes the intent or scope, not just a name or date in an old question.
   Avoid saving private employee details in the reference document.

After Return, the user message should appear promptly with an assistant
`Thinking ...` message. The pipeline completes SQL execution, drafting, and answer
review before it reveals the reviewed reply in cumulative Markdown chunks. This is
**progressive display after review**, not live provider-token streaming. A short
answer can appear in one update; use a longer list or table to check progressive
display. Do not send the next normal turn until the prior answer is final. A safe
failure message is a result to investigate, not an answer pass.

## 4. Review each answer against evidence

For every turn, write down the exact question and its relevant prior turns. Before
reading the reply, identify what the user actually requested:

| Check | What to verify |
| --- | --- |
| Subject and population | Employee, department, country, or all employees; whether a prior subject should carry forward or be dropped. |
| Time and category | Exact date/range, status, day type, shift, and exceptions; whether each was added, replaced, or removed. |
| Measure and grouping | Records (`COUNT(*)`), distinct people (`COUNT(DISTINCT employee_id)`), distinct days, hours, or another measure; requested group keys and ordering. |
| Multi-part scope | Each clause has its own filters; shared CTEs do not silently narrow a broader clause. |
| Answer | Every number and label matches the rows; no unsupported claim is added from a limited sample. |

Open **Retrieved Context** after the final reply. Its `sql_result` JSON exposes
the executed result rows and metadata. Compare the visible answer to those rows,
including labels, totals, and missing groups. This panel does **not** by itself
show the executed SQL. To verify that the query selected the right population and
filters, use a captured SQL trace from the same browser turn or another
same-turn diagnostic that records `executed_sql`. A later direct model replay may
choose different SQL, so label it as a reproduction, not as the browser turn's
exact query. If the SQL is unavailable, mark SQL scope **unverified**.

For important counts, independently run a read-only PostgreSQL aggregate using
the same dates, statuses, and population. Check `COUNT(*)` versus distinct people
and distinct attendance dates explicitly. A sample or bounded result is not the
full population unless its total is separately established. Check the final
answer, executed SQL, and rows together before marking an answer correct. Follow
the [planner guidance](AGENTS.md) when investigating a wrong plan: inspect the
question, history, schema payload, prompt, SQL, rows, draft answer, review, and
`scope_provenance`; fix general logic or model-facing descriptions rather than
matching one phrase or hardcoding an expected number.

## 5. Check the browser display and state

- **Progress:** confirm `Thinking ...` appears in the chat, then is replaced by
  the reviewed answer. For a long reply, observe at least two different answer
  states before the final state. The reply should grow cumulatively without a
  duplicate assistant message.
- **Markdown:** request a bullet list, a table, and a list followed by a closing
  sentence. Confirm the rendered page has actual list items, a table, and a
  separate paragraph. Raw `-` markers or `|` table syntax in the final UI are a
  display failure. Check that HTML remains sanitized.
- **Conversation state:** check a follow-up retains only justified subject and
  filters. A broad new request should not inherit an old employee or date.
- **Clear:** test once while idle and once during `Thinking ...`. The old chat and
  context should disappear, and an old pending reply must not repopulate the page.
  The next submit should use a fresh conversation.
- **Errors:** if a safe failure or obviously wrong count appears, capture the
  prompt, preceding turns, terminal error, result JSON, and available SQL before
  restarting. Do not classify an intentionally interrupted turn as a pass or fail.

## 6. Record the test and finish

Add verified new cases to the [manual conversation reference](../../docs/superpowers/reports/2026-09-29-attendance-manual-conversation-reference.md).
For each case retain the exact prompt, preceding turns, commit, data coverage,
visible answer, SQL and row evidence, independent aggregate when used, and one
status: **pass**, **fail**, **ambiguous**, **interrupted**, or **unverified**. Note
which filters a correction should replace. Do not copy credentials or raw private
employee rows into Git. If a defect is fixed, restart the app and retest the
failed case plus neighboring scopes in the actual browser.

When testing is done, stop the app in the PowerShell terminal that launched it
with Ctrl+C. If the process belongs to another session, leave it running and
coordinate with its owner.
