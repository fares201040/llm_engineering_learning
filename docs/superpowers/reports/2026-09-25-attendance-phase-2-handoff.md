# Attendance chatbot Phase 2 handoff

Date: 2026-09-25
Repository: `D:\projects\llm_engineering_ed_donner\llm_engineering`
Status: Local implementation and embedding-switch checks pass. Phase 3 Gradio acceptance passed turns 1–4 on synthetic data; turn 5 still fails semantic checks. The latest source snapshot awaits Colab verification because T4 session creation returned `Service Unavailable`.

## Start here: current application state

- Read `week5/ARCHITECTURE.md`, `week5/new_implementation/LLM_PLANNER.md`,
  `week5/new_implementation/POSTGRES_SETUP.md`, and this handoff before changing
  the `week5/new_*` application. The UI entry point is `week5/new_app.py`.
- The online path rewrites references, resolves employees within the authorized
  PostgreSQL directory, plans read-only SQL, executes bounded queries, writes and
  verifies an answer, then publishes verified conversation state. The question and
  history reach the model stages. The current SQL retry policy is one initial execution
  and at most two retries for `psycopg.ProgrammingError` or `psycopg.DataError`.
- The default embedding provider is local `all-MiniLM-L6-v2`; set
  `EMBEDDING_PROVIDER=openai` and `EMBEDDING_MODEL=text-embedding-3-large` to
  select OpenAI without code changes. `embedding.py` uses the selected provider's
  tokenizer and input limit. Chroma collections are model-specific. The current
  local Chroma store has 6,904 document parts and 568 employee names, both
  384-dimensional, in `docs_chunks_07db4e1f186d` and
  `docs_employees_07db4e1f186d`. The legacy `docs` collection was removed after
  verifying the rebuild. The rebuild command now preserves its source collection
  so a future provider switch can be reversed by configuration.
- Local Gradio answered “who is fares hasan” with confirmation choices including
  the stored `Faris Nasser Ali (A11017)` as option 2. The name is stored without
  “Hassan”; the semantic top five alone did not include A11017. The PostgreSQL
  first-name candidate path supplied the option. It does not silently bind a
  fuzzy name to an employee.
- Current local verification: 153 online, evaluator, acceptance, and UI tests plus
  3 subtests passed after adding a third eligible SQL planner attempt. The source
  ZIP and input notebook match SHA-256
  `6a1853bd6bbc79a7cd01feb98b6c521bee66eeb547c86de8fb3e39dc8d26ad88`.
  This exact snapshot has not run in Colab. OpenAI embedding construction was
  checked without a paid API request; a live OpenAI rebuild was not run because
  the configured account previously returned `credit_balance_exhausted`.
- Remaining acceptance work: restore a Colab T4 session, run the deterministic
  notebook on the exact ZIP, inspect its output cells, then resume the synthetic
  long conversation from the four-turn checkpoint. Turn 5's grouped comparison
  needs a root-cause fix before turns 6–8 or broader Arabic/complex prompts.
  `week5/new_implementation/colab/LOCAL_COLAB_SYNC_GUIDE.md` gives the exact workflow.
- Treat `week5/new_evaluation/results/` as private 311-case output. It is ignored
  by Git and must not enter the source ZIP or a commit. The ignored
  `week5/new_implementation/.env.postgres` contains credentials; do not print,
  modify, or commit it. The saved `attendance_phase3*.json` checkpoints are
  generated synthetic data.

## Earlier update — 2026-09-25

The last executed Colab source snapshot was
`d171a25ed2fe5cb11e3c2277ac10b8765a5fa33f1f7f4adaf571b827244ed60c`:
149 deterministic tests, Ruff lint and formatting, and compilation passed. The
current source archive and notebook pin are
`29c97100195a91d7edfa9d4a2ee27a9cb0e907e0666b9739b971b27925446ae2`;
they have not run remotely. The previous Colab runtime expired. Repeated attempts to
create a new T4 session returned `Service Unavailable` from Colab.

The Gradio callback path was exercised with one live question per CLI invocation.
Turns 1–4 passed their factual SQL/result/answer and state-continuity checks across
verified checkpoints. Turn 5, “Compare that with last month,” revealed weak-model
errors in reference resolution, grouped SQL eligibility, difference direction, and
available-date explanations. Each semantically wrong answer was stopped by the
synthetic oracle. The latest local changes pass no remote test yet. Resume with the
four-turn synthetic checkpoint after Colab is available and deterministic checks
pass; then retry turn 5 before sending another live question. Turns 6–8 and broader
Arabic and nested-query questions remain untested.

## Earlier review update — 2026-09-25

The current sanitized ZIP hash is
`fccad7a9e9ea23cb3e0c70c80e4734258a9072f0811584b269606adfe1f1a19d`.
The executed Colab notebook validated it and passed 127 deterministic tests, Ruff
lint, formatting for 19 files, and compilation with zero error cells. The installer
failure was diagnosed as missing `zstd`; installing it allowed Ollama/Qwen smoke
testing and a temporary synthetic PostgreSQL fixture of 16 rows and three employees.
The runner now uses a fresh Python process per turn to avoid Colab kernel import cache.

Long-scenario turns 1 and 2 passed with five September worked dates for A11017 and
the explicit September 6 absence. Turn 3 asked for dates not absent. The latest SQL
used the correct `exception IS DISTINCT FROM 'Absent'` predicate but omitted the
inherited September interval, so it returned August 3–4 in addition to September
1–5. The exact five-row oracle rejected the answer/result; turns 4–8 were not run.
See `week5/new_implementation/colab/attendance_phase3_long_synthetic_negation.json`
for the saved synthetic result. The T4 session was stopped. The model verifier did not
catch the scope error before publishing state, so this remains a runtime limitation.

The sections below record the earlier handoff state before this review. Use the
review update above and the Colab sync guide for current verification status.

## Earlier handoff snapshot

This section records the earlier pre-commit state. Preserve existing and user changes;
do not reset, revert, clean, or overwrite unrelated files. The ignored local
`week5/new_implementation/.env.postgres` contains credentials and was not displayed
or modified.

## Phase 2 architecture and behavior

The active online flow is employee-reference/request rewrite → authorized employee
resolution and confirmation → physical-schema load → request-specific SQL planning →
bounded read-only PostgreSQL execution → answer writing → independent verification by
the same answer model → atomic publication of verified state.

The configured model roles are reference/rewriter, SQL planner, and answer. The answer
role makes separate writer and verifier calls. The current local roles are all
`ollama_chat/qwen3.5:4b`, with `reasoning_effort="none"`, temperature 0, and
`num_ctx=8192`. The planner output limit is 512 tokens. The app's provider-call ceiling
is eight calls per turn.

The physical `DatabaseContext` is built from the configured attendance objects. The
planner receives all typed relational columns and descriptions, with a request-specific
`record_json` projection: typed-equivalent JSON entries are removed, and only relevant
JSON-only fields are included. A measured planner payload was 63.2% smaller after this
projection. The full schema normally goes to the planner once; an eligible SQL retry
gets the same projection again. The writer and verifier do not receive the schema.
They receive the current question, rewritten request, history, trusted context, date
coverage, executed SQL, typed result and coverage, employees, and locale; the verifier
also receives the proposed answer.

The initial SQL is executed without a model review pass. There is exactly one initial
database execution and at most one retry, only after `psycopg.ProgrammingError` or
`psycopg.DataError`. The retry receives the failed SQL, error type, database error text
capped at 4,000 characters, retry number, and the unchanged `SharedModelContext`.
Connection, timeout, result-bound, authorization, provider, and answer failures do not
trigger SQL repair. The exact model SQL runs in a read-only, repeatable-read transaction
with configured statement, lock, and idle-transaction timeouts, row and response-size
bounds, and rollback on success or failure.

Attendance meanings are explicit:

- `Actual_From_*` and `Actual_To_*` are immutable device-swipe values.
- `From_*` and `To_*` are clerk-adjustable, payroll-effective values for payroll
  calculations.
- `total_worked_hrs > 0` proves attendance. Zero or null alone does not prove absence.
- Explicit absence is `exception = 'Absent'`.
- Scheduled work dates use `day_type = 'Working Day'`.
- A generic off-day query includes both `OFF Day` and `OFF Day (ZAS)`.
- Typed relational columns take priority over `record_json` fallbacks.

## Unsupported and malformed inputs

Requests outside the attendance domain stop before SQL planning. Recognized malformed
employee ID shapes, impossible supported date forms, and malformed or non-finite
numeric comparisons stop safely before planning. An unknown standalone employee ID
gets no alternatives. An unresolved name can receive confirmation-only candidates
after authorized-directory scoping and PostgreSQL cross-checking.

For an attendance concept the schema cannot represent, the planner returns a safe
`SELECT` with the `unsupported_capability` alias. The pipeline surfaces an unsupported
outcome and publishes no verified turn. Empty/malformed provider output fails safely;
planner Markdown fences are rejected. Other SQL structure is not parsed or rewritten
before PostgreSQL execution.

The separate Chroma employee-name fallback originally called the OpenAI embedding
client. It now defaults to local `all-MiniLM-L6-v2` embeddings in a model-specific
384-dimensional collection. `EMBEDDING_PROVIDER=openai` selects OpenAI embeddings
without changing code; that model requires its own collection. It was not used for
the verified Case 17 turn.
In the local Gradio reproduction of “who is fares hasan,” whole-name PostgreSQL
similarity returned no candidates, and the Chroma embedding request returned HTTP
429 (`credit_balance_exhausted`). The current attendance table stores the expected
employee under a shorter three-part name. A first-name token PostgreSQL fallback
now offers that authorized employee among confirmation choices without
requiring embeddings; the exact Gradio question returned those choices locally.
The local semantic top five for “Fares Hasan” alone did not include A11017.

## Case 17 live result and performance

Case 17 asked: “Exclude off days and count A10017's scheduled work dates.” The
authoritative result was **5**. The SQL was:

```sql
SELECT COUNT(DISTINCT attendance_date) AS scheduled_work_dates
FROM public.attendance_records
WHERE employee_id = 'A10017'
  AND day_type = 'Working Day';
```

The optimized complete pipeline took about 4 minutes 6 seconds:

| Stage | Approximate time |
|---|---:|
| Reference | 36 s |
| Planner | 128 s |
| SQL execution | 0.05 s |
| Answer writer | 45 s |
| Answer verifier | 37 s |

Tiny direct Qwen prompts take about 2.35 seconds. Ollama uses 100% CPU on this
machine. PyTorch is CPU-only, BitsAndBytes and Accelerate are not installed, and the
GTX 960M has 4 GB with a driver too old for current Ollama GPU acceleration. Qwen is
already Q4_K_M quantized. BitsAndBytes NF4 is not suitable here. Two orphaned
`llama-server` processes use about 15 MB each and are not the bottleneck; do not
terminate unrelated applications.

## Verification evidence and limits

The saved Colab deterministic run passed **120 tests in 12.88 seconds**, Ruff lint,
format checking for 19 files, and Python compilation. The executed notebook is
[`week5/new_implementation/colab/attendance_phase2_tests_output.ipynb`](../../../week5/new_implementation/colab/attendance_phase2_tests_output.ipynb).
The run validated source ZIP SHA-256
`8ef93f238f6233cb3222e25d88b779299cfdb6f2e7ca9c631918bd78800f7c69` and had no
notebook cell errors. The ZIP contained 32 allowlisted source/helper files plus a
generated 311-line count-only placeholder manifest; it excluded `.env` files,
credentials, attendance data, prior results, and the private 311-case evaluation
questions/answers. The deterministic notebook mocks model/database calls and does not
use the GPU.

That evidence is for the saved ZIP hash only. The local archive and helper diagnostic
code have since changed. The sanitized archive was rebuilt on 2026-09-25 as
`51dbbc17aa96390875986a693fde0d5dd7d02cf76db51693256cd01a1641815f`; the notebook
input pins the same hash. This updated snapshot is ready to upload but has not run in
Colab. Rerun deterministic tests against it before claiming current-source
verification. The notebook input hash is maintained by `colab/package_source.py`.

Manual acceptance review found a false pass for expected unsupported cases; that was
corrected. The acceptance runner was then strengthened so each answered step requires
explicit answer facts, result facts, and state-continuity checks. The approved long
scenario has semantic oracles. Wail, Faris, and generic-subject scenarios lack complete
semantic oracles and are blocked before making a live call. UI review covered
`new_app.py` and `test_new_app.py` (message history, trusted-state updates, reset,
error handling, and escaped source rendering); no additional UI defect was identified.

The synthetic live setup provisioned PostgreSQL and a synthetic-only database, then
failed in the official Ollama installer with exit status 1. The first captured output
ended after the installer cleanup/install banner. Error reporting was improved in the
helper to include installer stdout and stderr, but that newer diagnostic has not yet
been run in Colab. Qwen download/smoke testing and live acceptance did not happen. The
original Colab T4 runtime is no longer active: on 2026-09-25 the official CLI reported
`No active sessions found on server`. Its PostgreSQL database and private runtime
config were ephemeral and are gone. Create a fresh T4 session named
`attendance-phase2-3` before uploading the current source. The setup helper ran more
than once in the previous VM; review its idempotence before using it again. Never print
a private runtime config or DSN.

No seven-case batch or 311-case evaluation was run. Case 17 is the only completed
non-synthetic live case. Cases **20, 23, 26, 29, 94, and 128** remain unverified.
Never run multiple live questions in one command. Inspect and report each live result,
semantic facts, and state transition before considering another.

## Colab and Phase 3 scope

The user approved the sanitized source-ZIP plus Colab CLI workflow because local model
inference is too slow, and selected a T4 GPU for live model work. The user also
explicitly approved Phase 3 UI review and long-conversation acceptance on a synthetic
database, superseding the original Phase 3 stop line in the pasted Phase 2 request.
The UI review is complete; only the approved acceptance work remains. Do not add new
UI features or broaden Phase 3 scope.

The official Colab CLI 0.7.2 and working ADC are already installed/available in WSL
Ubuntu 24.04. The authenticated CLI currently lists no active runtimes; create the
named T4 session again. Do not ask the user to authenticate unless an actual ADC
failure occurs. The complete installation/authentication guide is
[`week5/new_implementation/colab/COLAB_CLI_GUIDE.md`](../../../week5/new_implementation/colab/COLAB_CLI_GUIDE.md),
and the canonical local-to-Colab upload/hash/test/live workflow is
[`week5/new_implementation/colab/LOCAL_COLAB_SYNC_GUIDE.md`](../../../week5/new_implementation/colab/LOCAL_COLAB_SYNC_GUIDE.md).
The ready-to-paste next-agent request is provided in the accompanying chat message,
not as a repository document.

The next agent must preserve the committed work and any later user changes. Do not
reset, revert, clean, or overwrite unrelated work. Do not put authorization codes,
Google credentials, API keys, or database passwords in `.env`, notebooks, archives,
chat, or logs. No Google credential is currently needed. Live model/DB work is limited
to the temporary synthetic fixture; do not tunnel to production or use real attendance
rows.
