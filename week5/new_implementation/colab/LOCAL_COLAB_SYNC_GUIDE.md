# Local and Colab source sync and testing

This guide makes the local project checkout the source of truth and sends a
deterministic, sanitized source snapshot to Google Colab for testing. Colab does not
edit or synchronize the local Python files back into the checkout. Apply code changes
locally, rebuild the snapshot, and rerun the notebook to keep both sides aligned.

For the WSL, Google Cloud SDK, Colab CLI installation, and ADC login steps, see
[`COLAB_CLI_GUIDE.md`](COLAB_CLI_GUIDE.md). The commands below assume those tools are
installed in Ubuntu 24.04 under WSL2 and ADC is already available there.

## Current handoff status (2026-09-25)

- ADC and the official CLI work in WSL. The previous T4 runtime expired and its
  `/content` directory became inaccessible. Attempts to create a replacement T4
  returned Colab `Service Unavailable`; retry session creation when service resumes.
- The latest row-only executed notebook validated snapshot
  `d171a25ed2fe5cb11e3c2277ac10b8765a5fa33f1f7f4adaf571b827244ed60c`:
  149 tests, Ruff lint, formatting for 19 files, and compilation passed. The current
  ZIP and input notebook pin are
  `6a1853bd6bbc79a7cd01feb98b6c521bee66eeb547c86de8fb3e39dc8d26ad88`;
  this newer snapshot has not run in Colab.
- The current source permits three SQL planner/execution attempts when the first
  two PostgreSQL queries fail with programming or data errors. The local attendance
  online/evaluator/acceptance/UI suite passed 153 tests and 3 subtests. Colab T4
  allocation still returned `Service Unavailable` after this change.
- A separate local Gradio reproduction of “who is fares hasan” found that
  PostgreSQL whole-name similarity returned no options and the former OpenAI
  embedding fallback returned HTTP 429 (`credit_balance_exhausted`). PostgreSQL
  first-name token candidates now include the stored three-part name A11017.
  Employee-name Chroma embeddings now use local `all-MiniLM-L6-v2`; a direct
  semantic search alone did not place A11017 in its top five, so confirmation
  still depends on the PostgreSQL candidate path for this question. The local
  Gradio reply offered A11017 as option 2. The current Chroma store has only the
  MiniLM document (6,904 parts) and employee (568 names) collections, each with
  384-dimensional vectors.
- Embeddings can be switched by `EMBEDDING_PROVIDER` and `EMBEDDING_MODEL`. The
  provider-specific splitter and model-specific Chroma collections are documented
  in `week5/new_implementation/POSTGRES_SETUP.md`. The current local suites passed
  102 online tests and 38 ingestion/configuration/rebuild/UI tests. OpenAI client
  construction was checked without a paid API request; no OpenAI collection was
  rebuilt because the configured account previously exhausted its credits. The
  current archive still needs Colab verification.
- The official Ollama installer needed `zstd`. The setup helper now installs it before
  Ollama, and the synthetic 16-row database plus Qwen 3.5 4B smoke test passed before
  expiration. Runtime credentials stayed only in the temporary Colab VM.
- The Gradio callback path was exercised one question per CLI invocation. Verified
  turns 1–4 passed, including follow-up negation and a department aggregate. Turn 5,
  a relative-month grouped comparison, repeatedly generated SQL or answers with
  incorrect eligibility, values, or coverage. The semantic oracle stopped each
  failed attempt; turns 6–8 and broader Arabic/complex cases have not run. Use the
  synthetic four-turn checkpoint `attendance_phase3_ui_prefix4.json` after the new
  snapshot passes deterministic checks, then retry turn 5 before advancing.
- Do not ask the user to sign in again unless `colab --auth=adc sessions` reports an
  actual authentication failure. Never print a runtime config or synthetic DSN.

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

## 3. Create or reuse the Colab T4 runtime

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

Confirm the new runtime is listed and reports a T4 before uploading source.

Verify the accelerator and state:

```bash
colab --auth=adc status --session attendance-phase2-3
```

T4 availability depends on the Colab account and current capacity. The deterministic
tests mock model and database calls, so they do not use the GPU; the T4 is for a later
live model run if one is needed.

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
by the setup helper. The current live blockers are Colab session creation and turn 5
comparison accuracy. Do not advance after a failed semantic turn or infer correctness
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

Never run multiple live turns as a batch, the seven-case batch, or the private
311-case evaluation. Stop after any failed turn. The other named Wail, Faris, and
generic-subject scenarios remain blocked until they have explicit factual oracles.

## 8. Stop the runtime

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
