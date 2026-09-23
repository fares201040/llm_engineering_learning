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

The application also starts every attendance read in `REPEATABLE READ READ ONLY` and
applies local statement, lock, and idle-transaction timeouts. The database role remains
required so a code defect cannot turn the planner connection into a writer.
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
versions are implementation details, not deployment settings. The decision stages
default to `openai/gpt-4.1-nano`; useful overrides are:

```env
LLM_PLAN_AUDIT_MODEL=openai/gpt-4.1-nano
LLM_PLAN_AUDIT_TIMEOUT_SECONDS=30
LLM_PLAN_AUDIT_MAX_OUTPUT_TOKENS=1000
LLM_REFERENCE_MODEL=openai/gpt-4.1-nano
LLM_ANSWER_MODEL=openai/gpt-4.1-nano
LLM_ANSWER_VERIFIER_MODEL=openai/gpt-4.1-nano
```

The shared active-turn limit is eleven provider calls. Keep
`POSTGRES_READONLY_DSN` configured independently from the ingestion writer DSN.
