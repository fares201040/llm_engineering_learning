# APDC Minimal Multi-Domain Foundation

> Historical architecture summary. For the complete problem statement,
> task-by-task implementation record, correctness fixes, and final verification,
> read `docs/superpowers/plans/2026-09-12-apdc-foundation-and-answer-correctness-implementation.md`.

## Goal

Keep the existing attendance assistant stable while adding the smallest safe
foundation for XLSX/CSV sources and future HR domains.

## Implemented architecture

1. Discover XLSX and CSV files deterministically and hash each physical source.
2. Read each worksheet or CSV as a separate partition. The first non-empty row
   is the header.
3. Classify by the first folder below the knowledge-base root. Only the
   registered attendance schema is admitted to the attendance pipeline.
4. Preserve every readable row in the append-only PostgreSQL table
   `private_ingestion.raw_source_rows`. Unknown or malformed partitions are
   quarantined and never projected into attendance data.
5. Publish raw rows and canonical `attendance_records` together in one
   PostgreSQL transaction. Preserve the existing attendance record identity and
   attach `raw_row_key` as provenance.
6. Synchronize Chroma only after PostgreSQL succeeds. Chroma remains an
   attendance-only derived index and carries trusted `domain=attendance`
   metadata.
7. Use separate embedding-input and metadata hashes. Re-embed only when text,
   model, or index schema changes; update metadata without an embedding request
   for provenance-only changes.
8. Enforce a trusted attendance `AccessContext` before planning, directory
   lookup, retrieval, reranking, or answer generation. Escape all displayed
   source content and treat retrieved text as untrusted evidence.

## Safety rules

- A new unknown source is preserved and quarantined without deleting healthy
  attendance data.
- Unknown data aborts before Chroma when PostgreSQL is disabled because the raw
  rows cannot be preserved.
- Invalid attendance rows, an empty attendance snapshot, a removed attendance
  partition, an unreadable prior source, or reclassification of prior
  attendance blocks typed-table and Chroma publication.
- Intentional source removal requires the one-run
  `ALLOW_ATTENDANCE_SOURCE_REMOVAL=true` override.
- Manifest format 3 records source hashes, formats, partition summaries, and
  output integrity hashes, while the additive SQLite ledger stores parser and
  raw-row coverage metadata.
- Chroma telemetry is disabled through its native client setting by default.

## Intentionally deferred

Production identity/RLS, an outbox, pgvector migration, generic JSONB querying,
new business-domain adapters, and cross-domain joins remain out of scope until
real samples and access rules are approved.

## Verification

Run the unit, app, and evaluation suites plus Ruff, compileall, and
`git diff --check`. Perform the first format-v3 database/index migration only
while the Gradio app is stopped; validate PostgreSQL and Chroma counts before
restart.
