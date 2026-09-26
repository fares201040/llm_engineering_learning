# Attendance Private Colab Evaluation Design

## Goal

Run the authorized 311-case attendance evaluation against its matching private
attendance dataset in the existing ephemeral Colab runtime, without mixing private
payloads with the sanitized source snapshot or exposing database credentials.

## Data boundary

- Export only `public.attendance_records` and
  `week5/new_evaluation/tests.jsonl` from the authorized local environment.
- Never export a PostgreSQL DSN, `.env` file, ingestion table, credential, sanitized
  placeholder corpus, or prior evaluation result.
- Package private inputs separately from `attendance_phase2_source.zip`.
- Keep local payloads and downloaded checkpoints under the ignored
  `week5/new_evaluation/results/` directory.
- Validate SHA-256 checksums before extraction or import.

## Required manifest

The payload is accepted only when all of these facts match:

- attendance rows: `3964`
- distinct employees: `568`
- minimum attendance date: `2026-09-01`
- maximum attendance date: `2026-09-07`
- evaluation cases: `311`
- dataset manifest fingerprint:
  `6860e7657deb91023d6f199c230edf9b7d40cd3e402a1ad23ccea39f3487dde9`

The generated 311-line `synthetic_manifest` corpus from the sanitized source archive
is never valid private evaluation input, regardless of its line count.

## Runtime boundary

The Colab importer creates a fresh ephemeral PostgreSQL database, imports only the
validated attendance rows, and creates a generated login with `SELECT` access only to
`public.attendance_records`. The role defaults to read-only transactions. Its DSN is
written only to the mode-0600 runtime configuration already used by live Colab
helpers and is never printed.

## Evaluation and recovery

The evaluator writes a checkpoint after every completed case. A resumed run accepts
only a checkpoint whose evaluator/runtime fingerprints and selected case indices
match the current run, skips the completed prefix, and continues with the next case.
Failures retain their native `BehaviorEval` stage/check flags and case indices.
Checkpoints can be downloaded into the ignored local results directory between
batches.

## Completion and cleanup

Classify each failure by its native failed check, reproduce it, and apply only generic
schema, SQL, or state fixes under systematic debugging and TDD. Rerun affected cases
and the relevant deterministic/full checks after each fix. On completion, download
the final checkpoint, delete private payload/config/report artifacts in Colab, drop
the private database and role where possible, and stop the runtime.
