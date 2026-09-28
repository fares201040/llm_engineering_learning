# Run the attendance chatbot in Colab

The launcher rebuilds the source package every time, so edits to the current local
application files are included automatically. It uses Qwen 3.5 2B for reference
resolution and `gpt-5-nano` through the OpenAI API for SQL planning, final
answering, and answer review. The reviewer can request another bounded query
when a materially wrong answer needs evidence available in the database.

## One-time requirements

Before the first run, confirm that:

1. The chatbot already works locally and the PostgreSQL attendance database is running.
2. The project virtual environment exists at `.venv`.
3. The Colab CLI and its Google login are configured in `Ubuntu-24.04` under WSL.
4. Your Colab account can create the runtime you select (CPU is available without a GPU allocation).

The existing [`COLAB_CLI_GUIDE.md`](COLAB_CLI_GUIDE.md) explains the Colab CLI login
if it has not already been configured.

## Start the chatbot

Open PowerShell in the repository root and run one command:

```powershell
.\week5\new_implementation\colab\start_colab_chatbot.ps1
```

The script automatically:

- packages the current local app code;
- exports the authorized attendance data;
- prompts for CPU, T4, L4, G4, A100, or H100, then creates or reuses the
  `attendance-chatbot` session (Enter keeps A100 as the default);
- uploads the code and data;
- installs PostgreSQL, Ollama, and Python dependencies;
- downloads `qwen3.5:2b` when needed;
- starts the chatbot and prints a public Gradio URL.

The first run on a new Colab session downloads Qwen; later starts in the same
live session reuse it. The SQL planner also requires an OpenAI API key.
If the named session already exists, the script asks whether to reuse its current
hardware or stop it and create a new session with your selected runtime.

The command prints a generated login password followed by a URL that looks similar
to this:

```text
https://xxxxxxxxxxxx.gradio.live
```

Open it and sign in with the printed user and password. You can then ask attendance
questions in English or Arabic. The Colab session must remain active for the chatbot
link to work.

## Stop the chatbot

When finished, run:

```powershell
.\week5\new_implementation\colab\stop_colab_chatbot.ps1
```

This removes the temporary private database and payload, stops the Colab runtime, and
deletes the temporary local ZIP files.

## After changing the app code

Run the same start command again. It rebuilds the package from the current files before
uploading it. Uncommitted edits to existing packaged files are included.

When a new runtime Python file is added to the application, add its repository-relative
path once to `SOURCE_PATHS` in
[`package_source.py`](package_source.py). This explicit list prevents credentials,
environment files, result reports, and unrelated private files from being uploaded.

## Common problems

### Colab asks for authentication

Run the login steps in [`COLAB_CLI_GUIDE.md`](COLAB_CLI_GUIDE.md), then start the
chatbot again.

### The selected GPU is unavailable

Choose another GPU or CPU on the next run. Availability depends on your Colab
subscription and current capacity. CPU inference can be very slow.

### No Gradio URL appears

Read the last error in PowerShell. Then stop the session with the stop command and run
the start command again. The launcher reports packaging, upload, model, database, and
application errors at the step where they occur.
