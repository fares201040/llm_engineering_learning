# Local and Colab source sync and testing

This guide makes the local project checkout the source of truth and sends a
deterministic, sanitized source snapshot to Google Colab for testing. Colab does not
edit or synchronize the local Python files back into the checkout. Apply code changes
locally, rebuild the snapshot, and rerun the notebook to keep both sides aligned.

For the WSL, Google Cloud SDK, Colab CLI installation, and ADC login steps, see
[`COLAB_CLI_GUIDE.md`](COLAB_CLI_GUIDE.md). The commands below assume those tools are
installed in Ubuntu 24.04 under WSL2 and ADC is already available there.

## Current handoff status (2026-09-26)

- The sanitized 48-file ZIP and executed notebook match SHA-256
  `4bdca6e1f88225563d09e333f3aef46afecccd720688976136cbf5f5cc8ac164`.
  The A100 notebook passed 239 deterministic tests, Ruff lint and formatting for 29
  files, and Python compilation.
- The authorized private evaluator completed all 311 cases on the same runtime and
  evaluator fingerprints with zero failures. The separate 25-case regression replay,
  including its employee-confirmation follow-up, also completed with no failures.
- The synthetic 16-row PostgreSQL fixture and Qwen 3.5 4B ran on T4. The UI long
  conversation passed turns 1–7 on the preceding snapshot. Turn 8 was rerun on
  this exact snapshot from the verified seven-turn checkpoint and passed all
  semantic, outcome, and state checks. The final checkpoint reports eight turns.
- `run_synthetic_eval.py` ran the evaluator against four generated cases on this
  snapshot: missing join table, invalid date, non-finite threshold, and grouped
  department ranking. It reported four completed and no failures. The ranking
  initially failed because the SQL planner invented September/August filters for a
  date-unbounded question. A SQL predicate scope guard now rejects that plan
  before execution and uses the three-attempt planner retry path.
- The private payload remained separate from the sanitized source ZIP and the ignored
  `.env.postgres` was never uploaded. The private database, role, runtime settings,
  payload, and reports are removed by the cleanup command after reports are downloaded.
- The exact T4 and A100 session creation commands are below. Check session status before
  reuse; Colab runtimes can expire. Do not ask for ADC sign-in unless the CLI
  reports an authentication failure. Never print runtime credentials.

## 1. Open the local checkout in WSL

From PowerShell, start the Ubuntu distribution that is already installed:

```powershell
wsl.exe -d Ubuntu-24.04
```

In Ubuntu, enter the Windows checkout through its mounted drive:

```bash
cd /mnt/d/projects/llm_engineering_ed_donner/llm_engineering
```

Use this WSL path for all `colab` commands. Do not make a second checkout under
`/home` for the same test run; two writable copies make it easy to test stale code.

## 2. Review the local tree and build the sanitized snapshot

Check the working tree before packaging so you know which edits are in the snapshot:

```bash
git status --short
git diff --stat
```

Build the archive and update the SHA-256 pin in the paired notebook in one step:

```bash
python3 week5/new_implementation/colab/package_source.py
sha256sum week5/new_implementation/colab/attendance_phase2_source.zip
```

The packager has an explicit source-file allowlist. It creates the 311-line count-only
manifest with generated placeholder records rather than copying the private
`week5/new_evaluation/tests.jsonl`. It excludes environment files, credentials,
attendance rows, result folders, and other repository files by construction. Keep the
private evaluation corpus and any `.env` file out of Colab.

The archive SHA-256 printed by `sha256sum` must equal
`expected_archive_sha256` in `attendance_phase2_tests.ipynb`. The packager updates that
value automatically. Colab checks it again before extracting any source.

## 3. Create or reuse the Colab runtime

List existing sessions first:

```bash
colab --auth=adc sessions
```

If the named session is not listed, create the T4 runtime:

```bash
colab --auth=adc new --session attendance-phase2-3 --gpu T4
```

From PowerShell, the equivalent direct WSL command is:

```powershell
wsl.exe -d Ubuntu-24.04 -- /home/faris/.local/bin/colab --auth=adc new --session attendance-phase2-3 --gpu T4
```

Run either creation command only when the named session is absent.

If T4 allocation is unavailable or the account has reached its accelerator limit,
create a Colab CPU session instead (omit `--gpu`):

```powershell
wsl.exe -d Ubuntu-24.04 -- /home/faris/.local/bin/colab --auth=adc new --session attendance-phase2-3
```

When an A100 is available, use the same session name with the A100 accelerator:

```powershell
wsl.exe -d Ubuntu-24.04 -- /home/faris/.local/bin/colab --auth=adc new --session attendance-phase2-3 --gpu A100
```

Use either creation command only when the named session is absent. Confirm the
session is listed and inspect its accelerator before uploading source.

Verify the accelerator and state:

```bash
colab --auth=adc status --session attendance-phase2-3
```

T4 availability depends on the Colab account and current capacity. The deterministic
tests mock model and database calls and do not use the GPU. Live Qwen inference on
Colab CPU can take several minutes per turn; the setup helper assigns longer model
stage timeouts automatically in a CPU session.

## 4. Upload the exact local snapshot

Upload the ZIP to a fixed path on the runtime:

```bash
REPO=/mnt/d/projects/llm_engineering_ed_donner/llm_engineering
colab --auth=adc upload \
  --session attendance-phase2-3 \
  "$REPO/week5/new_implementation/colab/attendance_phase2_source.zip" \
  /content/attendance_phase2_source.zip
```

The upload is a one-way snapshot. The notebook removes only its generated
`/content/attendance_phase2_source` extraction directory before unpacking the new
archive, so files left by an older snapshot cannot be imported accidentally.

## 5. Run the deterministic Phase 2 and Phase 3 checks

Execute the paired notebook against that runtime:

```bash
colab --auth=adc exec \
  --session attendance-phase2-3 \
  --file "$REPO/week5/new_implementation/colab/attendance_phase2_tests.ipynb" \
  --timeout 1200
```

The notebook validates the ZIP hash, installs only the direct test dependencies, and
runs online runtime, configuration, evaluator, acceptance-checkpoint, and Gradio UI
tests. It also runs the selected Ruff lint and formatting rules plus Python
compilation. These checks make no live model calls and do not connect to PostgreSQL.
They load the generated count-only fixture, never the private 311-case evaluation.

`colab exec` saves the executed notebook beside the local input as
`attendance_phase2_tests_output.ipynb`. Review its output cells before treating the
run as complete:

- The archive cell reports the validated file count and SHA-256.
- The pytest cell has a zero exit code and no failed tests.
- The Ruff lint, formatting, and compilation cells all complete successfully.
- The notebook has no error output. A notebook runner can return a successful shell
  status while a cell records an exception, so inspect the output notebook itself.

## 6. Repeat after a local code change

Make the code or test edit in this checkout, then rebuild and upload the snapshot
again:

```bash
python3 week5/new_implementation/colab/package_source.py
colab --auth=adc upload \
  --session attendance-phase2-3 \
  "$REPO/week5/new_implementation/colab/attendance_phase2_source.zip" \
  /content/attendance_phase2_source.zip
colab --auth=adc exec \
  --session attendance-phase2-3 \
  --file "$REPO/week5/new_implementation/colab/attendance_phase2_tests.ipynb" \
  --timeout 1200
```

Rebuilding updates the archive and notebook hash together. The notebook replaces its
previous remote extraction before tests run, and `colab exec` writes a fresh output
notebook back beside the local notebook. Do not copy Python files manually into the
Colab VM; that bypasses the hash check and makes the tested revision unclear.

If you intentionally edit a file inside Colab to investigate a problem, copy the
reviewed change back into the local checkout yourself, inspect the local diff, rebuild
the archive, and rerun the notebook. Colab-side edits are temporary and are not the
canonical source.

## 7. Run the optional live long-conversation acceptance

The live acceptance helpers use only generated synthetic data. They create a temporary
read-only PostgreSQL role/database inside the Colab VM with 16 synthetic rows and
download `qwen3.5:4b` into the T4 runtime. No production DSN, tunnel, external Google
credential, or real attendance row is used. The generated test DSN is kept in a
mode-0600 file inside the ephemeral Colab VM; it is not included in the ZIP or written
to the repository's `.env` files.

After the deterministic notebook has passed and source ZIP is uploaded/extracted,
provision the runtime with the exact helper from the local checkout:

```bash
colab --auth=adc exec \
  --session attendance-phase2-3 \
  --file "$REPO/week5/new_implementation/colab/prepare_synthetic_runtime.py" \
  --timeout 1200
```

This installs PostgreSQL and `zstd` in the Colab VM, installs/starts Ollama, downloads
Qwen 3.5 4B, creates its synthetic fixture and read-only login, and makes a model smoke
request. It stores the runtime-only connection settings at
`/content/.attendance_phase3_runtime.json`. The setup prints only the synthetic row
and employee counts, never the generated DSN or password.

The earlier Ollama installation failure was caused by missing `zstd` and is resolved
by the setup helper. Review each semantic check rather than inferring correctness
from an aggregate pass flag alone.

Run **one** long-scenario turn with one CLI call. This example starts the sequence:

```bash
colab --auth=adc exec \
  --session attendance-phase2-3 \
  --file "$REPO/week5/new_implementation/colab/run_acceptance_turn.py" \
  --env ACCEPTANCE_SCENARIO=long \
  --env ACCEPTANCE_TURN=1 \
  --timeout 900
```

The helper starts a fresh Python process to call `week5.new_evaluation.acceptance` for
exactly one 1-based turn, avoiding stale imports in Colab's persistent kernel, and
prints its checkpoint report. Review the answer, SQL result, `outcome_ok`,
`semantic_ok`, `missing_answer_facts`, `missing_result_facts`,
`state_continuity_ok`, and `passed`. Report each result to the user before considering
the next live question. Continue with `ACCEPTANCE_TURN=2` only after the preceding
turn has been inspected and reported. Repeat sequentially through turn 8. Any false
check or unexpected outcome ends the sequence; inspect the saved checkpoint and
resolve the cause before a new run. The acceptance runner blocks answered turns without
an explicit semantic oracle, and exact value/date expectations are defined against the
synthetic fixture.

The checkpoint at `/content/attendance-phase3-long.json` contains only this synthetic
test's state and history. Download it before stopping if you need a local record:

```bash
colab --auth=adc download \
  --session attendance-phase2-3 \
  /content/attendance-phase3-long.json \
  "$REPO/week5/new_implementation/colab/attendance_phase3_long_synthetic.json"
```

## 8. Run the attendance evaluator on synthetic cases

The source archive includes four generated-case expectations for the direct-SQL
evaluator: a missing join target, an impossible date, a non-finite number, and a
grouped worked-hours ranking. Run the evaluator through the prepared private runtime
settings after the deterministic notebook and synthetic database setup have passed:

```bash
colab --auth=adc exec \
  --session attendance-phase2-3 \
  --file "$REPO/week5/new_implementation/colab/run_synthetic_eval.py" \
  --timeout 2400
```

The runner invokes `week5.new_evaluation.eval --all --test-file` with the synthetic
case file and writes `/content/attendance-synthetic-eval.json`. Inspect each failed
flag in that report before changing application logic. The private 311-case corpus
is excluded from the archive; it is not evaluated in Colab.

Never run multiple live turns as a batch. Stop after any failed turn. The other
named Wail, Faris, and generic-subject scenarios need explicit factual oracles.
The private 311-case corpus uses the separately approved bounded workflow below.

## 9. Run the authorized private 311-case evaluation

Keep this payload separate from `attendance_phase2_source.zip`. The local exporter
selects only `public.attendance_records`, reads the ignored private case JSONL, checks
the fixed dataset oracle (3,964 rows, 568 employees, 2026-09-01 through 2026-09-07,
311 cases, and the approved dataset fingerprint), and writes only three members under
the ignored results directory. It never serializes a DSN or environment file.

```powershell
.\.venv\Scripts\python.exe -m week5.new_implementation.colab.export_private_payload
Get-FileHash -Algorithm SHA256 .\week5\new_evaluation\results\attendance_private_payload.zip
```

Record the printed payload SHA-256 without opening or printing the private members.
Upload source and private data as separate files:

```powershell
wsl.exe -d Ubuntu-24.04 -- /home/faris/.local/bin/colab --auth=adc upload --session attendance-phase2-3 /mnt/d/projects/llm_engineering_ed_donner/llm_engineering/week5/new_implementation/colab/attendance_phase2_source.zip /content/attendance_phase2_source.zip
wsl.exe -d Ubuntu-24.04 -- /home/faris/.local/bin/colab --auth=adc upload --session attendance-phase2-3 /mnt/d/projects/llm_engineering_ed_donner/llm_engineering/week5/new_evaluation/results/attendance_private_payload.zip /content/attendance_private_payload.zip
```

Run the deterministic notebook first. On a newly created runtime, also run the
Section 7 synthetic-runtime preparer to install/start PostgreSQL and Ollama and pull
Qwen; the private importer replaces only the database/config used by its own runner.
Then prepare the private database, passing the exact payload digest through the
process environment (replace `<sha256>`):

```powershell
wsl.exe -d Ubuntu-24.04 -- /home/faris/.local/bin/colab --auth=adc exec --session attendance-phase2-3 --file /mnt/d/projects/llm_engineering_ed_donner/llm_engineering/week5/new_implementation/colab/prepare_private_runtime.py --env ATTENDANCE_PRIVATE_PAYLOAD_SHA256=<sha256> --timeout 1200
```

The helper validates the archive before database mutation, imports a fresh ephemeral
database, and creates a generated role with only `CONNECT`, schema `USAGE`, and table
`SELECT`. The DSN exists only in `/content/.attendance_private_runtime.json`, mode
0600, and is never printed.

Run one bounded batch at a time. The default is 10 cases; set a value from 1 to 50.
Every completed case is checkpointed atomically, and later calls resume only when the
corpus, runtime/evaluator fingerprints, and selected indices match.
After changing runtime answer logic, explicitly start a new checkpoint by adding
`--env PRIVATE_EVAL_RESTART=true` to the first batch command. Omit it thereafter so
later batches resume. This flag deletes only the fixed remote evaluation report; it
does not alter the payload or private database.

```powershell
wsl.exe -d Ubuntu-24.04 -- /home/faris/.local/bin/colab --auth=adc exec --session attendance-phase2-3 --file /mnt/d/projects/llm_engineering_ed_donner/llm_engineering/week5/new_implementation/colab/run_private_eval.py --env PRIVATE_EVAL_BATCH_SIZE=10 --timeout 3600
wsl.exe -d Ubuntu-24.04 -- /home/faris/.local/bin/colab --auth=adc download --session attendance-phase2-3 /content/attendance-private-eval.json /mnt/d/projects/llm_engineering_ed_donner/llm_engineering/week5/new_evaluation/results/attendance-private-eval.json
```

Inspect `status`, `completed`, and each native failed check before the next batch. A
failed flag must be reproduced and classified before a generic schema/SQL/state fix.
Do not upload a modified payload to continue an existing checkpoint.

After downloading the final report, remove the private database, role, config,
payload, extracted files, and remote report, then stop the runtime:

```powershell
wsl.exe -d Ubuntu-24.04 -- /home/faris/.local/bin/colab --auth=adc exec --session attendance-phase2-3 --file /mnt/d/projects/llm_engineering_ed_donner/llm_engineering/week5/new_implementation/colab/cleanup_private_runtime.py --timeout 600
wsl.exe -d Ubuntu-24.04 -- /home/faris/.local/bin/colab --auth=adc stop --session attendance-phase2-3
wsl.exe -d Ubuntu-24.04 -- /home/faris/.local/bin/colab --auth=adc sessions
```

## 10. Stop the runtime

After the deterministic checks and any optional live turns are complete, release the
accelerator:

```bash
colab --auth=adc stop --session attendance-phase2-3
colab --auth=adc sessions
```

The second command should show no active session for this work when it is finished.

## Official CLI references

- [Google Colab CLI README and command index](https://github.com/googlecolab/google-colab-cli/blob/main/README.md)
- [Google Colab CLI file upload and download guide](https://github.com/googlecolab/google-colab-cli/blob/main/docs/03_file_management.md)
- [Google Colab CLI execution guide](https://github.com/googlecolab/google-colab-cli/blob/main/docs/02_execution_and_interactive.md)
