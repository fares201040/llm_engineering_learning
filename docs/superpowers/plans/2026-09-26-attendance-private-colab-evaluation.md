# Attendance Private Colab Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run a bounded, resumable, credential-free private attendance evaluation in the existing ephemeral Colab runtime.

**Architecture:** A local exporter creates a private ZIP containing only canonical attendance JSONL, the real case JSONL, and a checksummed manifest. A Colab importer validates the archive before creating a fresh database and read-only role, while the existing evaluator gains compatible checkpoint/resume semantics. Separate runner and cleanup helpers keep the private lifecycle explicit.

**Tech Stack:** Python 3.12, `unittest`, `psycopg`, PostgreSQL, ZIP/JSONL, Google Colab CLI, Qwen 3.5 4B.

**Spec:** `docs/superpowers/specs/2026-09-26-attendance-private-colab-evaluation-design.md`

## Global Constraints

- Export only `public.attendance_records` and `week5/new_evaluation/tests.jsonl`.
- Keep the private payload separate from `attendance_phase2_source.zip`.
- Require 3,964 rows, 568 employees, coverage 2026-09-01 through 2026-09-07, 311 cases, and fingerprint `6860e7657deb91023d6f199c230edf9b7d40cd3e402a1ad23ccea39f3487dde9`.
- Reject the generated `synthetic_manifest` placeholder corpus.
- Never print or package DSNs, `.env` files, credentials, ingestion tables, or prior results.
- Store runtime credentials only in a mode-0600 Colab config.
- Download checkpoints only into ignored `week5/new_evaluation/results/`.
- Delete private Colab artifacts and stop the runtime at completion.

## Review Focus

- A 311-line placeholder corpus must be rejected by semantic marker, not accepted by count.
- A ZIP with an unexpected member, traversal path, duplicate member, or mismatched member hash must be rejected before extraction.
- A checkpoint from another corpus, selection, or runtime fingerprint must not skip work.
- An interrupted checkpoint must resume only the contiguous completed prefix and preserve earlier failures.
- Import failure must not leave a broadly privileged role or publish a credential-bearing config.

---

### Task 1: Private payload contract and local exporter

**Files:**
- Create: `week5/new_implementation/colab/private_payload.py`
- Create: `week5/new_implementation/colab/export_private_payload.py`
- Create: `week5/new_implementation/colab/test_private_payload.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: authorized local `POSTGRES_DSN`, `public.attendance_records`, private case JSONL, and dataset manifest.
- Produces: `validate_case_bytes(data: bytes) -> int`, `validate_payload_archive(path: Path, expected_sha256: str | None = None) -> dict`, and an ignored private payload ZIP with exactly `attendance_records.jsonl`, `tests.jsonl`, and `manifest.json`.

- [ ] Write tests that reject `synthetic_manifest`, unexpected/duplicate/traversal members, forbidden credential-shaped members, wrong hashes, and wrong required manifest facts; assert a valid three-member archive passes.
- [ ] Run `python -m unittest week5.new_implementation.colab.test_private_payload -v`; expected failure is missing payload functions.
- [ ] Implement the minimal payload validation and deterministic archive builder, then implement the exporter with a fixed `SELECT * FROM public.attendance_records ORDER BY record_id` boundary and no credential serialization.
- [ ] Run the focused tests, then the Colab helper/evaluator test modules; expected PASS.
- [ ] Commit the task.

### Task 2: Resumable evaluator checkpoint

**Files:**
- Modify: `week5/new_evaluation/eval.py`
- Modify: `week5/new_evaluation/test_eval.py`

**Interfaces:**
- Consumes: selected indexed cases, output path, and optional `--resume` flag.
- Produces: atomic checkpoint schema containing status, selected indices, completed prefix, failures, and fingerprints; compatible fresh reports remain readable.

- [ ] Write tests proving interrupted runs persist each case, `--resume` skips the completed prefix, preserves failures, and rejects mismatched fingerprints or selected indices.
- [ ] Run focused tests; expected failures identify absent resume behavior.
- [ ] Implement minimal checkpoint validation and resume flow without changing `evaluate_behavior` semantics.
- [ ] Run focused and complete deterministic evaluator tests; expected PASS.
- [ ] Commit the task.

### Task 3: Ephemeral private runtime, runner, and cleanup

**Files:**
- Create: `week5/new_implementation/colab/prepare_private_runtime.py`
- Create: `week5/new_implementation/colab/run_private_eval.py`
- Create: `week5/new_implementation/colab/cleanup_private_runtime.py`
- Create: `week5/new_implementation/colab/test_private_runtime.py`
- Modify: `week5/new_implementation/colab/package_source.py`
- Modify: `week5/new_implementation/colab/LOCAL_COLAB_SYNC_GUIDE.md`

**Interfaces:**
- Consumes: validated private payload at a fixed `/content` path and the source-root runtime.
- Produces: fresh private PostgreSQL database, read-only runtime config, bounded batch/resume runner, and idempotent cleanup helper.

- [ ] Write tests for validation-before-database mutation, fixed table/role privileges, mode-0600 config, bounded runner arguments, and idempotent artifact cleanup.
- [ ] Run focused tests; expected failures identify missing helpers.
- [ ] Implement importer/runner/cleanup helpers and include only their source/tests in the sanitized archive allowlist.
- [ ] Run focused tests, source packager tests/checks, the complete 187-test-equivalent deterministic selection, Ruff, formatting, and compilation; expected PASS.
- [ ] Commit the task.

### Task 4: Authorized Colab evaluation and generic remediation loop

**Files:**
- Generate ignored: `week5/new_evaluation/results/attendance_private_payload.zip`
- Generate ignored: `week5/new_evaluation/results/attendance-private-eval.json`
- Modify only as justified by reproduced generic failures.

**Interfaces:**
- Consumes: Tasks 1-3 artifacts, active `attendance-phase2-3` session, matching private local database/corpus.
- Produces: downloaded resumable/final report, classified native failures, verified generic fixes, and a cleaned/stopped Colab runtime.

- [ ] Verify the active session, export the bounded payload locally, record its SHA-256 without printing secrets, upload source and private payload separately, and validate/import it in Colab.
- [ ] Run bounded batches with checkpoint downloads. For every failure, use systematic debugging to reproduce the native failed check before any fix.
- [ ] For each generic fix, write and observe a failing regression test, implement the minimal schema/SQL/state change, rerun the affected case/batch, and rerun relevant deterministic checks.
- [ ] Finish all 311 cases, download the final checkpoint, validate completion/failure counts and fingerprints, then run private cleanup and stop the runtime.
- [ ] Run final local verification and commit any justified generic fixes and documentation updates.
