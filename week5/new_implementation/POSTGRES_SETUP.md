# PostgreSQL setup

## Runtime read-only role

Use a dedicated application role that can connect and select only the approved
attendance objects. The exact role name is deployment-owned; its permissions should be
equivalent to:

```sql
ALTER ROLE attendance_reader SET default_transaction_read_only = on;
REVOKE ALL ON SCHEMA private_ingestion FROM attendance_reader;
REVOKE ALL ON ALL TABLES IN SCHEMA private_ingestion FROM attendance_reader;
GRANT USAGE ON SCHEMA public TO attendance_reader;
GRANT SELECT ON TABLE attendance_records, knowledge_chunks TO attendance_reader;
```

Install deterministic fuzzy-name matching once as an administrator:

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

The application also starts every attendance read in `REPEATABLE READ READ ONLY` and
applies local statement, lock, idle-transaction, row-count, and response-size bounds.
The database role remains required so model-produced SQL cannot write data.
Configure its connection separately as `POSTGRES_READONLY_DSN`; keep the ingestion
writer connection in `POSTGRES_DSN`.

The ingestion pipeline uses PostgreSQL for exact, structured attendance
queries and keeps semantic vectors in Chroma by default. This works with a
plain PostgreSQL installation; pgvector is optional.

## First-time setup on Windows

From the repository root, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\week5\new_implementation\setup_postgres.ps1
```

The script installs PostgreSQL 17 with `winget` when needed, waits for the
server, creates the `apdc_attendance` database, runs a connection health check,
and writes a machine-local `.env.postgres` file. The file is ignored by git
because it contains the generated password.

If PostgreSQL is already installed, pass its password explicitly:

```powershell
powershell -ExecutionPolicy Bypass -File .\week5\new_implementation\setup_postgres.ps1 `
  -SkipInstall -PostgresPassword 'your-postgres-password'
```

## Run ingestion

Install Python dependencies and run the offline pipeline as a module:

```powershell
uv sync
uv run python -m week5.new_implementation.ingest
```

The script loads `.env.postgres` regardless of the current working directory.
With the default `ENABLE_PGVECTOR=false`, `attendance_records` is synced to
PostgreSQL while `knowledge_chunks` remains in Chroma for semantic search.
The same transaction preserves every readable XLSX/CSV row in
`private_ingestion.raw_source_rows`. Unknown folders are quarantined there and
never enter attendance JSONL, typed attendance rows, Chroma, prompts, or UI
context. The private table is append-only; source revisions retain their raw
history.

Stop the Gradio app before the first format-v3 ingestion. Restart it only after
the run completes without a pending SQLite generation. Attendance-source
removal is blocked by default; for an intentional one-run removal, set
`ALLOW_ATTENDANCE_SOURCE_REMOVAL=true` for that ingestion process.

To use PostgreSQL vector search, use a PostgreSQL server with pgvector (for
example, a `pgvector/pgvector` Docker image), then set:

```env
ENABLE_PGVECTOR=true
```

Only enable that flag after `CREATE EXTENSION vector` succeeds on the target
database.

## Online runtime configuration

`attendance-online/v1` is the only runtime and state version. Provider boundary
versions are implementation details, not deployment settings. For the current Phase 2
local run, configure all three model roles as follows:

```env
LLM_REFERENCE_MODEL=ollama_chat/qwen3.5:4b
LLM_PLANNER_MODEL=ollama_chat/qwen3.5:4b
LLM_ANSWER_MODEL=ollama_chat/qwen3.5:4b
LLM_PLANNER_MAX_OUTPUT_TOKENS=512
```

For these local Ollama calls, the provider sets `reasoning_effort="none"`,
`temperature=0`, and `num_ctx=8192`. The answer model is used for separate writer and
verifier calls. The maximum provider-call budget is eight. Keep
`POSTGRES_READONLY_DSN` configured independently from the ingestion writer DSN.

The SQL planner receives the complete typed column catalog and descriptions plus a
request-specific projection of JSON-only fields. JSON fallbacks duplicated by typed
columns are omitted. The answer writer and verifier receive the current question,
history, trusted context, date-coverage summary, executed SQL, result, and authoritative
employees, but not the schema.

Employee lookup uses the authorized PostgreSQL directory first. If a written name or
ID remains unresolved, the runtime may query the configured Chroma collection and
show up to five choices. These choices are restricted to the caller's employee scope,
cross-checked against PostgreSQL, and never trusted until the user confirms one. The
existing Chroma fallback obtains query embeddings through the configured OpenAI
embedding client; local Qwen covers the three conversational roles, but this separate
fallback may require OpenAI embedding access if it is invoked.

## Direct-SQL limitation

The online runtime executes the model's SQL directly. It does not yet parse an AST,
enforce one read-only statement structurally, validate returned identifiers/functions/
operators/joins, inject row authorization, inject limits, or parameterize model
literals. Read-only permissions prevent writes but do not prevent unauthorized reads
within the granted objects, expensive valid SQL, or prompt-injected SQL. Grant the
reader role only the minimum attendance objects and use the configured timeouts and
bounds. The structural safeguards are explicitly deferred future work.

## Colab test workflow

Use [`colab/LOCAL_COLAB_SYNC_GUIDE.md`](colab/LOCAL_COLAB_SYNC_GUIDE.md) for the
complete WSL2/official CLI upload, hash verification, test, live synthetic acceptance,
and shutdown instructions. It is the canonical local-to-Colab workflow.

The deterministic Phase 2/3 notebook is
[`colab/attendance_phase2_tests.ipynb`](colab/attendance_phase2_tests.ipynb). Its
paired source archive contains an explicit 32-file allowlist, test helpers, and a
generated count-only placeholder manifest. It excludes `.env` files, credentials,
attendance rows, the private evaluation corpus, and result artifacts. The notebook
uses mocked model/database boundaries; it does not call an LLM or connect to
PostgreSQL.

For an approved live long-conversation acceptance, run
`colab/prepare_synthetic_runtime.py` first. It provisions a temporary read-only local
PostgreSQL database with synthetic rows and installs Qwen 3.5 4B in the Colab T4 VM.
Then call `colab/run_acceptance_turn.py` once per turn and inspect each result before
continuing. No production DSN, database tunnel, real attendance rows, or external
Google credential is needed. The generated test DSN remains in a mode-0600 runtime
file inside the temporary Colab VM and is not placed in the source archive or a
repository `.env` file.
