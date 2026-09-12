# PostgreSQL setup

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

Install Python dependencies and run the existing pipeline:

```powershell
uv sync
uv run python .\week5\new_implementation\ingest.py
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
