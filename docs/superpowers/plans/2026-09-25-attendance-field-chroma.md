# Attendance Field Chroma Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The user asked to defer testing and Colab evaluation.

**Goal:** Build a separate, incrementally maintained Chroma index with one natural-language chunk per populated attendance business field and retain the existing row index.

**Architecture:** A pure chunk builder transforms canonical attendance documents into field chunks with stable parent and field identities. A standalone command reads the current validated JSONL projection, points the existing embedding sync at a second Chroma persistence directory, and never changes the normal ingestion or online answer route.

**Tech Stack:** Python, Pydantic, Chroma PersistentClient, configured Hugging Face/OpenAI embeddings.

**Spec:** `docs/superpowers/specs/2026-09-25-attendance-field-chroma-design.md`

## Global Constraints

- Keep the current row-level Chroma database and the online direct-SQL/employee-name fallback behavior unchanged.
- Use only populated business fields other than `Employee_ID`, `Shift`, and `Date`; retain numeric zero and false.
- Store field chunks in a separate Chroma persistence directory and a model-specific collection.
- Never log attendance values, credentials, or private evaluation material.
- Do not run tests or the Colab comparison in this implementation session, per the user's instruction.

## Review Focus

The following checks are specified for the later user-approved test session and are not run now:

1. A null or blank field emits no chunk; `0` and `False` do.
2. Two fields of one record receive different stable IDs; the same input rebuild retains IDs.
3. An edited field re-embeds only that field, while a removed field is deleted after upsert.
4. A missing, stale, invalid, or empty canonical projection cannot delete the existing field index.
5. The field builder cannot write to the original row Chroma directory.

---

### Task 1: Isolate the second Chroma target

**Files:**
- Modify: `week5/new_implementation/config.py`
- Modify: `week5/new_implementation/chroma_client.py`
- Modify: `week5/new_implementation/ingest.py`
- Modify: `.gitignore`

**Interfaces:**
- `Settings.chroma_field_db_path: Path` reads `CHROMA_FIELD_DB_PATH` and defaults to a sibling of `CHROMA_DB_PATH` named `<row-directory-name>_fields`.
- `create_chroma_client(path: Path | str | None = None)` uses the supplied path or the existing default.
- `sync_embeddings_to_chroma(chunks, stats=None, ledger=None, generation=None, *, client=None, collection_name=None, checkpoint_stage="chroma")` uses the supplied target without altering its current default behavior.

- [ ] Add the optional field database path to `Settings` and derive its default after reading `CHROMA_DB_PATH` once.
- [ ] Make the Chroma client constructor accept an optional path and preserve its current no-argument behavior.
- [ ] Parameterize `sync_embeddings_to_chroma` at its collection creation and ledger checkpoint calls. Keep the existing `chroma` defaults; the field command will call it without a ledger.
- [ ] Add `week5/new_preprocessed_db_fields/` to `.gitignore`.
- [ ] Inspect the resulting diff without running a test.

### Task 2: Build one readable chunk per field

**Files:**
- Create: `week5/new_implementation/field_index.py`

**Interfaces:**
- `create_field_chunks(documents: Sequence[dict]) -> list[Result]` uses `canonicalize_documents`, `_make_record_id`, `_content_hash`, and `_business_record` from `ingest.py`.
- `build_field_index(documents: Sequence[dict], *, db_path: Path | None = None) -> IngestionStats` creates the field collection in the separate database and calls the parameterized sync.

- [ ] Build a deterministic field label by replacing underscores with spaces, preserving the exact source key in metadata.
- [ ] For each canonical record, skip only null and whitespace-only values and the three identifying fields. Format one sentence containing employee ID, date, shift, field label, and value. Render a missing shift as “shift not recorded.”
- [ ] Give each field a stable ID: `<parent-record-id>:field:<SHA-1 of exact field name>`. Store `parent_record_id`, `field_name`, `field_value`, `Employee_ID`, `Shift`, `Date`, `chunk_type`, and source locator as scalar metadata. Set `Result.record_id` to the field ID so existing incremental sync handles field removal independently.
- [ ] Calculate `Result.content_hash` from the exact field name, identifier values, and field value. Use `collection_name_for_model(settings.chroma_collection_name, settings.embedding_model, "fields")` and `create_chroma_client(settings.chroma_field_db_path)`.
- [ ] Keep generated text and field values out of log statements.

### Task 3: Add a safe standalone builder command

**Files:**
- Modify: `week5/new_implementation/field_index.py`
- Modify: `week5/new_implementation/POSTGRES_SETUP.md`
- Modify: `week5/new_implementation/LLM_PLANNER.md`

**Interfaces:**
- `python -m week5.new_implementation.field_index` builds the configured field index from the existing canonical JSONL projection.

- [ ] Before building, check that the canonical JSONL exists and `_jsonl_needs_refresh()` is false. Reject nonzero `_count_invalid_records()`.
- [ ] Read every nonempty JSONL line through `_documents_from_jsonl()`. Compare the parsed document count with the nonempty line count so a skipped invalid record aborts before any Chroma mutation. Reject an empty projection.
- [ ] Call `build_field_index` only after those checks pass. Print aggregate record, field-chunk, and sync counts plus the chosen model and field collection; do not print source rows, field values, or connection settings.
- [ ] Document the explicit build command, `CHROMA_FIELD_DB_PATH`, its separate directory, provider/model behavior, and the fact that chatbot answers still use direct SQL.
- [ ] Inspect the diff and report the untested status. Leave Colab comparison and any source ZIP change for the later evaluation session.

## Deferred verification

When the user authorizes testing, add focused tests for the five Review Focus cases, build both indexes from the same synthetic source, and compare retrieval and answer quality in Colab. Do not use the private evaluation corpus or a production DSN there.
