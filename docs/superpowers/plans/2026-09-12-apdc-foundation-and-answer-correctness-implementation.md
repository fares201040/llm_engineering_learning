# APDC Foundation and Answer Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve every source row safely, keep attendance stable, and make every supported question pass through deterministic access, validation, resolution, retrieval, calculation, and evidence gates before an answer is produced.

**Architecture:** PostgreSQL holds append-only physical source history and the typed attendance snapshot, while Chroma remains an attendance-only derived semantic index. The LLM proposes language interpretation, but code-owned schemas and compilers enforce domain, identity, dates, types, business calculations, clarification state, and backend scope before execution.

**Tech Stack:** Python 3.12, Pydantic, openpyxl, standard-library CSV and SQLite, PostgreSQL/JSONB, Chroma, OpenAI/LiteLLM, Gradio, unittest, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-11-attendance-correctness-design.md`

## Global Constraints

- Preserve all existing attendance record IDs and public return tuple shapes.
- Keep Chroma; do not migrate semantic retrieval to pgvector in this release.
- Keep the SQLite ledger; do not add a transactional outbox in this release.
- Never send unknown-domain rows to attendance JSONL, typed attendance,
  Chroma, prompts, or rendered context.
- Classification must be deterministic and must not call an LLM.
- Trusted application context owns domain scope; `QueryPlan` must not expose a
  model-controlled domain field.
- A failed structured constraint must stop or clarify; it must not broaden into
  semantic retrieval.
- Fix reusable rules at their owning boundary, not by hard-coding answers for
  individual evaluation questions.
- Preserve unrelated dirty-worktree changes and stage only explicit APDC paths
  if the user later requests a commit.

---

## Problem Before the Plan

The prior attendance assistant had already improved its basic structured data
handling, but it still had three classes of risk.

### Source and publication risk

- Only the attendance workbook path was well understood.
- CSV and unknown-domain files lacked a safe deterministic boundary.
- Physical source rows were not retained independently from the typed business
  projection.
- A disappeared, unreadable, invalid, or reclassified attendance source could
  lead to destructive synchronization.
- Chroma telemetry was not configured through the native client setting.
- Metadata-only changes could cause unnecessary embedding work.

### Question correctness risk

- LLM planner output could replace or omit explicit question constraints.
- Worked days, scheduled days, record counts, authorized records, employee
  counts, and percentages were not always protected by executable definitions.
- Employee clarification could lose IDs or repeat after selection.
- Semantic retrieval could lose a name-resolved employee filter.
- Date parsing could corrupt comparison operators, month/year ranges, or named
  temporal fields.
- List requests and distinct-employee questions could be changed into record
  counts.
- PostgreSQL temporal `IN` expressions could use the wrong array type.

### Evaluation risk

- The corpus was too small to represent the supported product surface.
- Some expected record IDs were recorded but not asserted.
- Grouped tests checked the number of groups but not their values.
- Clarification tests did not prove pending state was cleared.
- Direct dataset verification failed when `eval.py` was run as a script.
- Concurrent provider runs could confuse throttling with functional defects.

## Chosen Solution

The solution was a minimal attendance-safe foundation rather than a generic HR
query engine:

1. Deterministically discover and classify XLSX/CSV partitions.
2. Preserve all readable rows in one private append-only PostgreSQL envelope.
3. Publish raw and typed attendance atomically, then update Chroma.
4. Guard deletion/reclassification with manifest v3 and SQLite source state.
5. Keep Chroma explicitly attendance-only with separate embedding and metadata
   hashes.
6. Enforce trusted attendance scope before any provider or retrieval boundary.
7. Compile planner output through deterministic schema, business-rule, input,
   identity, and catalog gates.
8. Expand and verify the evaluation corpus against locked source truth.

---

### Task 1: Native Chroma Configuration

**Problem before:** Direct `PersistentClient(...)` construction was duplicated,
and telemetry behavior was not controlled through Chroma's native settings.

**Solution:** Centralize client construction and default native anonymized
telemetry to false.

**Files:**

- Create: `week5/new_implementation/chroma_client.py`
- Modify: `week5/new_implementation/config.py`
- Modify: `week5/new_implementation/ingest.py`
- Modify: `week5/new_implementation/answer.py`
- Test: `week5/new_implementation/test_chroma_client.py`
- Test: `week5/new_implementation/test_config.py`

**Interfaces:**

- Produces: `create_chroma_client()`.
- Consumes: `settings.chroma_db_path` and
  `settings.chroma_anonymized_telemetry`.

- [x] **Step 1: Add failing configuration tests**

```python
def test_chroma_telemetry_defaults_to_disabled():
    assert Settings.from_env({}).chroma_anonymized_telemetry is False
```

- [x] **Step 2: Add a failing constructor test**

```python
@patch("chromadb.PersistentClient")
def test_factory_passes_native_setting(client):
    create_chroma_client()
    assert client.call_args.kwargs["settings"].anonymized_telemetry is False
```

- [x] **Step 3: Implement the single factory**

```python
def create_chroma_client():
    from chromadb import PersistentClient
    from chromadb.config import Settings as ChromaSettings

    return PersistentClient(
        path=str(settings.chroma_db_path),
        settings=ChromaSettings(
            anonymized_telemetry=settings.chroma_anonymized_telemetry
        ),
    )
```

- [x] **Step 4: Replace direct production constructors and verify**

```powershell
rg -n "PersistentClient\(" week5/new_implementation
```

Expected: one production match in `chroma_client.py`.

---

### Task 2: Deterministic XLSX/CSV Source Boundary

**Problem before:** Source ingestion could not safely distinguish attendance
from unknown domains or preserve malformed/unknown physical partitions.

**Solution:** Introduce immutable source types, deterministic parsing, an exact
domain registry, and quarantine reason codes.

**Files:**

- Create: `week5/new_implementation/source_ingestion.py`
- Modify: `week5/new_implementation/ingest.py`
- Modify: `week5/new_implementation/config.py`
- Test: `week5/new_implementation/test_source_ingestion.py`
- Test: `week5/new_implementation/test_ingest_core.py`

**Interfaces:**

- Produces: `SourceFile`, `SourceRow`, `SourcePartition`,
  `PartitionClassification`, `RawSourceRow`.
- Produces: `discover_sources()`, `read_partitions()`,
  `classify_partition()`, and `build_raw_rows()`.
- Produces: `convert_sources_to_jsonl(ledger=None)`.
- Preserves: `convert_excel_to_jsonl(ledger=None)` compatibility wrapper.

- [x] **Step 1: Test stable discovery and lock-file exclusion**

```python
def test_discovery_finds_xlsx_and_csv_in_stable_order():
    sources = discover_sources(root, "*.xlsx", "*.csv")
    assert [item.relative_path for item in sources] == sorted(
        [item.relative_path for item in sources], key=str.casefold
    )
```

- [x] **Step 2: Test independent worksheet and CSV partitions**

Verify UTF-8 BOM decoding, bounded delimiter detection, row 2 as the first CSV
data row, and independent classification for each worksheet.

- [x] **Step 3: Test quarantine boundaries**

Cover unknown folders, attendance schema mismatch, duplicate normalized
headers, and a safe row-zero `read_error` sentinel.

- [x] **Step 4: Implement exact domain rules**

```python
DOMAIN_RULES = {
    "attendance": {
        "folder": "attendance",
        "required_headers": frozenset({"Employee_ID", "Name", "Date"}),
    }
}
```

- [x] **Step 5: Prove unknown rows never reach attendance output**

Assert that unknown partitions return raw rows but create no valid or invalid
attendance JSONL rows and never enter chunk generation.

---

### Task 3: Append-Only Raw History and Atomic Typed Publication

**Problem before:** Typed attendance retained business facts but not a durable
history of every readable physical row or unknown source revision.

**Solution:** Add one private raw JSONB table and publish raw plus canonical
attendance in one PostgreSQL transaction.

**Files:**

- Modify: `week5/new_implementation/ingest.py`
- Modify: `week5/new_implementation/setup_postgres.ps1`
- Modify: `week5/new_implementation/POSTGRES_SETUP.md`
- Test: `week5/new_implementation/test_ingest_postgres.py`

**Interfaces:**

- Produces: `store_raw_rows_to_postgres(raw_rows, *, connection=None) -> int`.
- Produces: `publish_postgres_snapshot(documents, raw_rows, generation,
  stats=None) -> IngestionStats`.
- Preserves: `sync_attendance_records_to_postgres()` compatibility wrapper.

- [x] **Step 1: Add raw envelope and provenance schema tests**

```sql
CREATE SCHEMA IF NOT EXISTS private_ingestion;
CREATE TABLE IF NOT EXISTS private_ingestion.raw_source_rows (...);
ALTER TABLE attendance_records ADD COLUMN IF NOT EXISTS raw_row_key TEXT;
```

- [x] **Step 2: Test write ordering and rollback**

Assert raw insert occurs before attendance upsert/delete on one connection and
that a PostgreSQL error rolls back both safe-snapshot writes.

- [x] **Step 3: Implement idempotent raw insertion**

Use `ON CONFLICT (raw_row_key) DO NOTHING`. Never update or delete historical
raw rows and never log `payload_json`.

- [x] **Step 4: Update typed provenance without changing logical IDs**

```sql
attendance_records.content_hash IS DISTINCT FROM EXCLUDED.content_hash
OR attendance_records.raw_row_key IS DISTINCT FROM EXCLUDED.raw_row_key
```

- [x] **Step 5: Implement unsafe-snapshot behavior**

Preserve readable raw/quarantine rows, leave typed attendance and Chroma
unchanged, keep the generation pending, and raise an operational error.

---

### Task 4: Manifest v3, Cache Coverage, and Deletion Guards

**Problem before:** Output integrity, parser-version reclassification, raw-row
coverage, and prior attendance-source disappearance were not one coherent
publication gate.

**Solution:** Upgrade the manifest and additive SQLite source snapshot and
compare prior valid attendance partitions before destructive sinks.

**Files:**

- Modify: `week5/new_implementation/ingestion_state.py`
- Modify: `week5/new_implementation/ingest.py`
- Modify: `week5/new_implementation/config.py`
- Test: `week5/new_implementation/test_ingestion_state.py`
- Test: `week5/new_implementation/test_ingest_postgres.py`

**Interfaces:**

- Manifest version: 3.
- Parser version: 1.
- SQLite additive fields: `parser_version`, `partition_summary_json`, and
  `raw_row_count`.
- Safety setting: `ALLOW_ATTENDANCE_SOURCE_REMOVAL`, default false.

- [x] **Step 1: Write migration and cache invalidation tests**

Cover v1→v3 reparse, parser-version change, artifact hash tampering, and missing
raw PostgreSQL coverage.

- [x] **Step 2: Write destructive-sink guard tests**

Cover removed, unreadable, reclassified, invalid, and empty attendance
snapshots plus a newly introduced unknown source.

- [x] **Step 3: Implement manifest v3 without raw payloads**

Store sources, partition summaries, and JSONL/audit output hashes and counts.

- [x] **Step 4: Implement explicit source-removal authorization**

Only the one ingestion run with
`ALLOW_ATTENDANCE_SOURCE_REMOVAL=true` may intentionally remove a prior
attendance source.

---

### Task 5: Attendance-Only Chroma and Correct Hash Decisions

**Problem before:** Semantic scope was implicit, metadata-only changes could
cause embeddings, and model/schema changes were not fully represented in the
embedding decision.

**Solution:** Stamp trusted domain metadata and compare embedding-input and
metadata hashes separately.

**Files:**

- Modify: `week5/new_implementation/ingest.py`
- Test: `week5/new_implementation/test_ingest_core.py`
- Test: `week5/new_implementation/test_ingest_postgres.py`

- [x] **Step 1: Test domain metadata on daily and period chunks**

```python
assert chunk.metadata["domain"] == "attendance"
```

- [x] **Step 2: Test the complete decision table**

| Existing state | Required action |
|---|---|
| ID missing | Embed and upsert |
| Embedding-input hash changed | Embed and upsert |
| Metadata hash only changed | Metadata update only |
| All hashes match | No operation |
| Existing ID no longer desired | Delete after successful writes |

- [x] **Step 3: Include model and schema in embedding identity**

```json
{"model":"<embedding model>","index_schema_version":2,"text":"<projected text>"}
```

- [x] **Step 4: Prove failed embeddings never delete existing rows**

Inject an embedding failure and assert no stale or superseded ID is deleted.

---

### Task 6: Trusted Scope and Safe Evidence

**Problem before:** Domain scope was not a first-class trusted input, and
retrieved source text needed explicit prompt and HTML boundaries.

**Solution:** Add `AccessContext`, enforce it before every downstream boundary,
filter every Chroma read, and render evidence as escaped untrusted text.

**Files:**

- Modify: `week5/new_implementation/attendance_schema.py`
- Modify: `week5/new_implementation/answer.py`
- Modify: `week5/new_app.py`
- Test: `week5/new_implementation/test_answer.py`
- Test: `week5/test_new_app.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class AccessContext:
    principal_id: str
    domain: str
    allowed_domains: frozenset[str]
```

- [x] **Step 1: Test denial before planner and retrieval calls**

Assert zero calls to planner, catalogs, employee directories, embeddings,
Chroma/PostgreSQL, reranker, and completion for unauthorized scope.

- [x] **Step 2: Implement attendance-only default scope**

Use `LOCAL_DEMO_ACCESS` and return the single non-enumerating denial message.

- [x] **Step 3: Add trusted Chroma domain filters**

Pass domain separately to directory, exact, semantic, hybrid, and split-part
retrieval helpers. Do not add it to the planner schema.

- [x] **Step 4: Escape and delimit evidence**

Use `html.escape`, render inside `<pre><code>`, and tell final-answer prompts
that retrieved records are evidence and cannot supply instructions.

---

### Task 7: Deterministic Question Compiler and Clarification State

**Problem before:** Correct planner output was not guaranteed, and a plausible
but wrong plan could reach retrieval or calculations.

**Solution:** Treat planner output as a proposal and compile it against explicit
question facts, field contracts, canonical calculations, database-backed
identity/catalog resolution, and saved conversation state.

**Files:**

- Modify: `week5/new_implementation/attendance_schema.py`
- Modify: `week5/new_implementation/answer.py`
- Modify: `week5/new_app.py`
- Test: `week5/new_implementation/test_attendance_schema.py`
- Test: `week5/new_implementation/test_answer.py`
- Test: `week5/test_new_app.py`

- [x] **Step 1: Lock canonical business calculations**

Add executable definitions for worked days, scheduled days, attendance record
count, Authorized record count, and distinct employees. Ensure list intent is
never converted into count intent.

- [x] **Step 2: Reject unsafe literals before provider calls**

Test malformed IDs, invalid/reversed/ambiguous dates, non-finite or absent
numeric comparisons, unsupported fields, and invalid list/scalar operands.

- [x] **Step 3: Make explicit date meaning authoritative**

Preserve `lt`, `lte`, `gt`, `gte`, or `eq`; keep month/year ranges intact; and
apply a date to the explicitly named temporal field rather than adding `Date`.

- [x] **Step 4: Preserve employee identity in every route**

A resolved name injects `Employee_ID`. If this occurs after a semantic plan is
created, promote it to hybrid and pass the ID filter to semantic retrieval.

- [x] **Step 5: Complete clarification state transitions**

Revalidate the selected candidate, resume the saved plan without replanning,
then clear pending candidates, plan, constraint, and question on success.

- [x] **Step 6: Align PostgreSQL types and grouped behavior**

Use storage-specific array casts. Remove unrequested group limits, preserve
explicit top/bottom N, and follow SQL null semantics for average.

- [x] **Step 7: Protect Authorized and employee meanings**

Map Authorized attendance to workflow `Status`. Remove conflicting
`Exception=Authorized`, `OT_Authorized`, and `OT_Not_Authorized` planner
filters. If employees are the counted subject, retain distinct employee count
even when the qualifying phrase contains “attendance records”.

---

### Task 8: Evaluation Corpus and Honest Assertions

**Problem before:** The 150-question corpus did not cover enough phrasings, and
the evaluator could pass while ignoring record identity, grouped values, or
uncleared clarification state.

**Solution:** Expand to 304 unique source-verified questions and make every
stored expectation executable.

**Files:**

- Modify: `week5/new_evaluation/tests.jsonl`
- Modify: `week5/new_evaluation/test.py`
- Modify: `week5/new_evaluation/eval.py`
- Modify: `week5/new_evaluation/test_eval.py`
- Test: `week5/new_evaluation/test_benchmark.py`

**Interfaces:**

- `TestQuestion.expected_record_ids: list[str]`.
- `TestQuestion.expected_group_values: list[dict]`.
- `expected_plan.required_filter` and `expected_plan.required_filters`.
- `BehaviorEval.record_ids_ok` and `BehaviorEval.group_values_ok`.

- [x] **Step 1: Derive expected facts from locked JSONL**

Validate 3,964 rows, 568 employees, the seven-day range, every referenced
employee ID, scalar calculations, percentages, and grouped values.

- [x] **Step 2: Add broad question coverage**

Cover exact, semantic, hybrid, identity ambiguity, multiple employees,
attendance/worked/scheduled/Authorized metrics, controlled fields, dates,
numeric comparisons, grouped aggregation, percentages, malformed inputs, and
unsupported domains.

- [x] **Step 3: Enforce previously ignored expectations**

Compare exact record IDs, every required filter, group keys and values, and all
pending clarification fields after selection.

- [x] **Step 4: Make float comparisons display-safe**

Use `math.isclose(..., abs_tol=0.005)` for expected display precision while
retaining full-precision calculations.

- [x] **Step 5: Support package and direct CLI execution**

Verify both:

```powershell
& '.venv\Scripts\python.exe' -m week5.new_evaluation.eval --verify-dataset
& '.venv\Scripts\python.exe' week5/new_evaluation/eval.py --verify-dataset
```

---

### Task 9: Manual Debugging and Acceptance Verification

**Problem before:** A green unit suite alone could miss live planner variation,
database type behavior, state transitions, or provider concurrency failures.

**Solution:** Combine focused red/green tests, sequential live evaluation,
manual end-to-end probes, datastore parity, and independent review.

**Files:**

- Verify: `week5/new_implementation/*`
- Verify: `week5/new_evaluation/*`
- Verify: `week5/new_app.py`
- Verify: `week5/test_new_app.py`

- [x] **Step 1: Run focused tests for every reproduced defect**

Expected: each new test fails before its fix and passes afterward.

- [x] **Step 2: Run the complete local suites**

```powershell
& '.venv\Scripts\python.exe' -m unittest discover `
  -s week5/new_implementation -p 'test_*.py' -v
& '.venv\Scripts\python.exe' -m unittest `
  week5.test_new_app `
  week5.new_evaluation.test_eval `
  week5.new_evaluation.test_benchmark -v
```

Recorded result: 177 implementation tests and 22 app/evaluation/benchmark
tests passed.

- [x] **Step 3: Run sequential live behavior evaluation**

Run provider-dependent cases sequentially to separate functional failures from
concurrency throttling. Recorded result: 300/300 passed without retry. Affected
date and Authorized-record categories passed after their corrections, and each
later regression case passed individually against the final rule set.

- [x] **Step 4: Manually trace critical questions**

Verify:

- employee-scoped semantic query returns only A10029 and uses hybrid mode;
- ambiguous example-name query clears all pending state after selection;
- a synthetic fixture row returns its exact expected record ID;
- temporal `IN` executes successfully against PostgreSQL;
- list attendance returns rows rather than a count;
- “Count distinct employees with Authorized attendance records” returns 370.

- [x] **Step 5: Verify datastore parity and no-change ingestion**

Confirm 3,964 attendance rows, 568 employees, 3,964 valid raw attendance rows,
4,532 attendance-domain Chroma chunks, zero pending generations, and zero
writes/embeddings for an unchanged ingestion.

- [x] **Step 6: Run final static checks**

```powershell
& '.venv\Scripts\ruff.exe' check `
  week5/new_implementation week5/new_evaluation `
  week5/new_app.py week5/test_new_app.py
& '.venv\Scripts\ruff.exe' format --check `
  week5/new_implementation week5/new_evaluation `
  week5/new_app.py week5/test_new_app.py
& '.venv\Scripts\python.exe' -m compileall -q `
  week5/new_implementation week5/new_evaluation week5/new_app.py
git diff --check
```

- [x] **Step 7: Request independent review**

Recorded result: the reviewer reproduced adjacent edge cases, the issues were
fixed with new tests, and the final focused review found no remaining
actionable issue.

---

## Acceptance Result

The implementation is accepted because:

- existing attendance IDs and facts remain stable;
- every readable physical source row is preserved in PostgreSQL mode;
- unknown data is quarantined and excluded from attendance/query surfaces;
- unsafe attendance snapshots cannot destructively synchronize;
- telemetry is disabled through Chroma's native configuration;
- metadata-only changes avoid embedding calls;
- trusted attendance scope is enforced before all downstream boundaries;
- deterministic rules protect identity, dates, types, calculations, and
  clarification state from planner errors;
- all local tests and static checks pass;
- the expanded source-verified evaluation passes live checks;
- independent review reports no remaining actionable issue.

## Deferred Work

The following require separate approved plans: production authentication,
PostgreSQL RLS, row/field authorization, separate database roles, transactional
outbox/shared publication, pgvector migration, additional HR-domain adapters,
cross-domain querying, raw-history retention automation, persisted performance
gates, and physical decomposition of the large implementation modules.
