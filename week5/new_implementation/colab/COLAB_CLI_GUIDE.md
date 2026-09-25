# Google Colab CLI with WSL2

This guide uses the official Google Colab CLI from Ubuntu 24.04 under WSL2. It covers a user-level install, Google ADC login, T4 session creation, file transfer, notebook execution, and shutdown. No Ubuntu installation commands are needed. The CLI is already installed as version 0.7.2 in the current WSL account and ADC is already available; reuse that setup instead of reinstalling or signing in again. For full local-to-Colab source sync, deterministic tests, and synthetic live acceptance, continue with [`LOCAL_COLAB_SYNC_GUIDE.md`](LOCAL_COLAB_SYNC_GUIDE.md).

The current verified archive hash and the stopped long-scenario turn 3 are recorded in
the sync guide. The earlier Ollama installer failure was caused by missing `zstd` and
is resolved in the synthetic setup helper.

## 1. Open Ubuntu and enter the project

From PowerShell, open the Ubuntu distribution already installed:

```powershell
wsl.exe -d Ubuntu-24.04
```

In the Ubuntu shell, Windows drive `D:` is available under `/mnt/d`:

```bash
cd /mnt/d/projects/llm_engineering_ed_donner/llm_engineering
```

Use Linux paths such as `/mnt/d/...` with `colab`, not Windows paths such as `D:\...`.

## 2. Install `uv` and Google Cloud CLI

Install Astral `uv` in the current WSL user account:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version
```

Install Google Cloud CLI from Google's official Linux x86-64 archive, without `sudo`:

```bash
mkdir -p "$HOME/.local/opt"
cd /tmp
curl -fL --retry 3 \
  -o google-cloud-cli-linux-x86_64.tar.gz \
  https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-linux-x86_64.tar.gz
```

Check the archive against the checksum on Google's current installation page before extracting it. The archive used for this setup was Google Cloud CLI 586.0.0 and had SHA-256 `c1cd1823624a33f2341d0d384aafe2d7b24c1b77c9b131087d772ee0791ff2be`:

```bash
printf '%s  %s\n' \
  c1cd1823624a33f2341d0d384aafe2d7b24c1b77c9b131087d772ee0791ff2be \
  /tmp/google-cloud-cli-linux-x86_64.tar.gz | sha256sum -c -
tar -xzf /tmp/google-cloud-cli-linux-x86_64.tar.gz -C "$HOME/.local/opt"
export PATH="$HOME/.local/opt/google-cloud-sdk/bin:$HOME/.local/bin:$PATH"
gcloud version
```

Add the user-level tools to `PATH` for future Ubuntu shells (run once):

```bash
printf '\nexport PATH="$HOME/.local/opt/google-cloud-sdk/bin:$HOME/.local/bin:$PATH"\n' >> "$HOME/.bashrc"
source "$HOME/.bashrc"
```

Google may update the archive and checksum. If so, use the current values from [Google Cloud CLI installation](https://docs.cloud.google.com/sdk/docs/install-sdk), not the archived checksum above.

## 3. Install the official Colab CLI

The Colab CLI is supported on Linux and macOS; use it inside WSL Ubuntu, not directly in PowerShell. The tested install uses the official `v0.7.2` source tag so `uv` applies Google's Jupyter client source mapping. A plain PyPI install previously selected a same-named Jupyter package that lacked an API this CLI needs:

```bash
uv tool install --force \
  --from git+https://github.com/googlecolab/google-colab-cli.git@v0.7.2 \
  google-colab-cli
```

Confirm the CLI and its Colab-maintained kernel client are available:

```bash
colab version
"$(uv tool dir)/google-colab-cli/bin/python" -c \
  'import jupyter_kernel_client as j; print(j.__version__, hasattr(j, "JupyterSubprotocol"))'
```

The second command should print version `0.8.0` and `True`. The official release's [`pyproject.toml`](https://github.com/googlecolab/google-colab-cli/blob/v0.7.2/pyproject.toml) and [`uv.lock`](https://github.com/googlecolab/google-colab-cli/blob/v0.7.2/uv.lock) identify the Colab-maintained dependency and pinned commit. Re-check the release files when upgrading the CLI.

## 4. Authenticate with Application Default Credentials

This WSL account already has working ADC for the current task. Skip this section
unless `colab --auth=adc sessions` reports an actual authentication failure. Never
repeat login just because the session list is empty; an empty list means there is no
runtime to show, not that credentials are invalid.

Start a fresh gcloud login from WSL:

```bash
gcloud auth application-default login --no-launch-browser \
  --scopes=openid,https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/userinfo.email,https://www.googleapis.com/auth/colaboratory
```

Open the URL printed by gcloud in a browser, complete Google's sign-in and consent prompts, then paste that flow's code directly into the waiting WSL terminal. Each code is short-lived and bound to the request that generated it; do not reuse a code from an older flow.

**Never put an authorization code, ADC file, API key, or database password in this repository's `.env`, notebook, source ZIP, or chat.** The code is entered once at the CLI prompt. gcloud saves ADC credentials locally at:

```text
~/.config/gcloud/application_default_credentials.json
```

Do not upload, print, or commit that file. The Colab CLI reads ADC when invoked with `--auth=adc`. A warning that ADC has no quota project does not necessarily prevent Colab session access; only configure a quota project if you have a Google Cloud project and an API that requires one. See [gcloud ADC login](https://docs.cloud.google.com/sdk/gcloud/reference/auth/application-default/login).

Verify authentication without printing a token:

```bash
colab --auth=adc sessions
```

`No active sessions found` means authentication worked but no Colab runtime is currently registered.

## 5. Create and inspect a T4 session

List sessions first. If `attendance-phase2-3` is absent, create it with a T4; otherwise
reuse the existing session:

```bash
# Check before allocating another runtime.
colab --auth=adc sessions

# Run only when `attendance-phase2-3` is absent.
colab --auth=adc new --session attendance-phase2-3 --gpu T4
colab --auth=adc sessions
```

From PowerShell, the equivalent session-creation command is:

```powershell
wsl.exe -d Ubuntu-24.04 -- /home/faris/.local/bin/colab --auth=adc new --session attendance-phase2-3 --gpu T4
```

Use it only when `attendance-phase2-3` is absent.

T4 availability depends on the Google account's Colab plan and current accelerator availability. The Phase 2 deterministic tests below mock model/database boundaries and do not use the GPU. The T4 is selected for the already-approved synthetic live acceptance described in `LOCAL_COLAB_SYNC_GUIDE.md`.

## 6. Upload the sanitized source and run the Phase 2/3 deterministic notebook

From the repository root in WSL, upload the sanitized source snapshot:

```bash
REPO=/mnt/d/projects/llm_engineering_ed_donner/llm_engineering
ZIP="$REPO/week5/new_implementation/colab/attendance_phase2_source.zip"
NOTEBOOK="$REPO/week5/new_implementation/colab/attendance_phase2_tests.ipynb"

colab --auth=adc upload \
  --session attendance-phase2-3 \
  "$ZIP" /content/attendance_phase2_source.zip
```

Execute the notebook remotely on the T4 session:

```bash
colab --auth=adc exec \
  --session attendance-phase2-3 \
  --file "$NOTEBOOK" \
  --timeout 1200
```

`colab exec` sends notebook code cells to the Colab kernel and writes an output notebook alongside the local input, named `attendance_phase2_tests_output.ipynb`. The notebook verifies the source ZIP SHA-256, extracts only its sanitized snapshot, installs a minimal test environment, and runs mocked Phase 2 runtime/evaluator, Phase 3 acceptance-checkpoint and Gradio UI tests plus Ruff and compilation checks. Its paired archive contains an explicit 33-file allowlist and generated placeholder manifest; it excludes `.env` files, credentials, attendance rows, previous results, and the private 311-case evaluation corpus. The deterministic notebook does not call an LLM or connect to PostgreSQL. For the separately approved live test, follow `LOCAL_COLAB_SYNC_GUIDE.md`; it uses only a generated synthetic PostgreSQL database and a Colab T4 model.

## 7. Common session commands

```bash
# List runtimes
colab --auth=adc sessions

# List files on the Colab VM
colab --auth=adc ls --session attendance-phase2-3 /content

# Execute a local Python file remotely
colab --auth=adc exec --session attendance-phase2-3 --file ./script.py --timeout 300

# Download a remote file
colab --auth=adc download --session attendance-phase2-3 /content/result.json ./result.json

# View recent command history
colab --auth=adc log --session attendance-phase2-3

# Stop the runtime and release its accelerator
colab --auth=adc stop --session attendance-phase2-3
```

Upload the sanitized ZIP again when creating a new runtime; Colab VM files are not a backup. Stop the session when finished, especially after selecting a GPU.

## 8. Troubleshooting

- **`JupyterSubprotocol` missing:** reinstall the tested official source release with the `uv tool install --force --from ...@v0.7.2` command in section 3. The PyPI wheel alone produced this failure in the current setup.
- **`invalid_grant` or invalid code verifier:** discard that code and start a fresh gcloud login. Complete only the URL printed by the current WSL command and paste its code in that command's prompt.
- **No active sessions:** run the T4 `colab new` command in section 5, then list sessions again.
- **Quota-project warning:** do not paste ADC credentials or set a guessed project. `colab --auth=adc sessions` is the read-only check for the Colab API path.
- **WSL cannot find `colab` or `gcloud`:** check `PATH` and reload `~/.bashrc`; do not install or reinstall Ubuntu to fix a PATH issue.

## Official references

- [Google Colab CLI](https://github.com/googlecolab/google-colab-cli)
- [Colab CLI release v0.7.2 dependency configuration](https://github.com/googlecolab/google-colab-cli/blob/v0.7.2/pyproject.toml)
- [Colab CLI v0.7.2 lock file](https://github.com/googlecolab/google-colab-cli/blob/v0.7.2/uv.lock)
- [Google Cloud CLI installation](https://docs.cloud.google.com/sdk/docs/install-sdk)
- [gcloud Application Default Credentials login](https://docs.cloud.google.com/sdk/gcloud/reference/auth/application-default/login)
- [uv installation](https://docs.astral.sh/uv/getting-started/installation/)
