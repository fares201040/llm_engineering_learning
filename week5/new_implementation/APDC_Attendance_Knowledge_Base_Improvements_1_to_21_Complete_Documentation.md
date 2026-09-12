# APDC Attendance Knowledge Base — Complete System Documentation

## 1. Purpose

This document explains the complete APDC attendance knowledge-base ingestion,
retrieval, answering, safety, and evaluation system. The filename is retained
for compatibility, but the document now covers Improvements 1–35 plus the
minimal multi-domain foundation and the final correctness hardening completed
on 2026-09-12.

Sections 1–14 preserve the detailed evolution of Improvements 1–21. Sections
15 onward describe the authoritative current implementation and supersede any
older status or future-direction statement that conflicts with them.

The goal of the work is to transform the original Excel-heavy, LLM-dependent ingestion flow into a structured, fast, reliable, scalable RAG pipeline.

The final design focuses on:

- preserving structured HR data,
- minimizing unnecessary LLM usage,
- improving embedding efficiency,
- supporting exact filtering and semantic retrieval,
- keeping stable record identities,
- avoiding duplicate/repeated work,
- enabling incremental updates,
- improving debugging and traceability,
- preparing for PostgreSQL + pgvector,
- adding employee-period summary records,
- improving question routing in `answer.py`.

---

# 2. Original Architecture

The original ingestion flow was approximately:

```text
Excel file
    ↓
Read many rows
    ↓
Combine multiple rows into a large text document
    ↓
Send large document to GPT / Llama
    ↓
Ask LLM to generate:
    - headline
    - summary
    - original_text
    ↓
Convert LLM output into chunks
    ↓
Create embeddings
    ↓
Delete/rebuild ChromaDB
```

The answering flow was approximately:

```text
Question
    ↓
Rewrite question with LLM
    ↓
Vector search original question
    ↓
Vector search rewritten question
    ↓
Merge results
    ↓
LLM reranking
    ↓
Send top chunks to final LLM
    ↓
Answer
```

This worked, but caused several problems:

- ingestion was slow,
- GPT was doing unnecessary preprocessing,
- very large prompts were created,
- generated chunks sometimes lost original records,
- embedding inputs could exceed token limits,
- every ingestion rebuilt everything,
- exact questions still used semantic search,
- counts and numeric questions could be incomplete,
- duplicate rows were not handled,
- IDs were unstable,
- metadata was too limited.

---

# 3. Final Architecture

The improved ingestion architecture is:

```text
Excel
    ↓
Convert once to JSONL
    ↓
One JSONL line = one attendance record
    ↓
Normalize + validate
    ↓
Create searchable text in Python
    ↓
Create stable record ID
    ↓
Detect duplicates
    ↓
Create:
    - attendance_record chunks
    - optional employee_period chunks
    ↓
Compare with existing Chroma state
    ↓
New / changed only
    ↓
Token-safe embedding preparation
    ↓
Safe embedding batching
    ↓
Chroma incremental upsert
    ↓
Optional PostgreSQL + pgvector mirror
```

The improved answering architecture is:

```text
User question
    ↓
Query planner
    ↓
Choose retrieval mode:
    - exact
    - semantic
    - hybrid
    ↓
Retrieve structured records
    ↓
Optional deterministic calculation
    ↓
Optional reranking
    ↓
Send small relevant context to LLM
    ↓
Final answer
```

---

# Improvement 1 — Convert Excel to JSONL

## Goal

Stop using Excel as the direct ingestion format.

Excel is now treated as an external source format only.

The ingestion system uses JSONL as its internal structured source.

## Why JSONL

Attendance data naturally looks like:

```text
row 1 = attendance record
row 2 = attendance record
row 3 = attendance record
```

JSONL matches this perfectly:

```json
{"Employee_ID":"A10017","Name":"Example Employee Gamma","Date":"2026-09-01"}
{"Employee_ID":"A10017","Name":"Example Employee Gamma","Date":"2026-09-02"}
```

Each line is independent.

## Main path

```python
JSONL_OUTPUT_PATH = (
    KNOWLEDGE_BASE_PATH
    / "attendance"
    / "attendance.jsonl"
)
```

## Conversion function

```python
def convert_excel_to_jsonl():
```

This function:

1. Finds `.xlsx` files.
2. Reads each worksheet.
3. Reads the header row.
4. Converts each data row into a dictionary.
5. Normalizes values.
6. Validates the record.
7. Writes one JSON object per line.

## Final behavior

```text
attendance.xlsx
    ↓
attendance.jsonl
```

Once JSONL exists, the ingestion pipeline can use JSONL directly.

---

# Improvement 2 — One JSONL Line = One Attendance Record

## Goal

Use the natural structure of the data.

Instead of:

```text
30 rows = one document
```

or:

```text
100 rows = one document
```

the system now uses:

```text
one Excel row
= one JSONL line
= one attendance record
```

## Why

One attendance row already contains a complete logical fact:

```text
Employee
+
Date
+
Shift
+
Attendance values
```

For example:

```text
Employee_ID: A10029
Name: Example Employee Beta
Date: 2026-09-05
Shift: 1st Shift
Exception: Lateness and Early Out
```

That is already an ideal retrieval unit.

---

# Improvement 3 — Remove the LLM From Ingestion Chunking

## Goal

Stop asking GPT/Llama to create chunks from structured attendance rows.

## Before

```text
JSON/Excel text
    ↓
GPT
    ↓
headline + summary + original_text
    ↓
embedding
```

## After

```text
structured record
    ↓
Python
    ↓
searchable text
    ↓
embedding
```

## Removed concepts

The ingestion no longer needs:

```python
make_prompt()
make_messages()
process_document()
create_chunks()
```

for attendance data.

It also no longer needs LLM workers for ingestion.

## Main function

```python
def create_record_chunks(documents):
```

This creates `Result` objects directly.

---

# Improvement 4 — Validate Every Attendance Record

## Goal

Prevent malformed HR data from silently entering the knowledge base.

## Pydantic model

```python
class AttendanceRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    Employee_ID: str = Field(min_length=1)
    Name: str = Field(min_length=1)
    Date: str = Field(min_length=10)
```

These are the minimum required fields.

## Invalid records

Invalid records are written to:

```text
attendance.invalid.jsonl
```

This gives traceability instead of silently discarding problems.

## Example

Invalid:

```json
{
  "Name": "Mohammed Ahmed",
  "Date": "2026-09-05"
}
```

Rejected because:

```text
Employee_ID is missing
```

---

# Improvement 5 — Normalize Dates, Times, and Numeric Fields

## Goal

Store values in consistent machine-friendly formats.

## Date normalization

Examples:

```text
Sep  5 2026
09/05/2026
```

become:

```text
2026-09-05
```

## Time normalization

Examples:

```text
8:14 AM
06:30
```

become:

```text
08:14:00
06:30:00
```

## Numeric normalization

Examples:

```json
"7.50"
```

becomes:

```json
7.5
```

and:

```json
"0"
```

becomes:

```json
0
```

## Important benefit

Exact numeric filtering becomes possible later:

```text
Lateness_Hrs > 2
Total_OT > 0
Total_Worked_Hrs >= 10
```

## Special schema correction

The source columns:

```text
Pre_OT_Start_Time
Pre_OT_End_Time
Post_OT_Start_Time
Post_OT_End_Time
```

were found to contain dates in the real attendance workbook.

They are therefore normalized as dates, despite their names.

## Header correction

The inconsistent source field:

```text
OT_value_3
```

is normalized to:

```text
OT_Value_3
```

---

# Improvement 6 — Embed Only Useful Searchable Fields

## Goal

Do not embed every field in the JSON record.

The full structured record remains in JSONL.

Only useful retrieval fields are converted to searchable text.

## Search fields

Example:

```python
SEARCH_TEXT_FIELDS = [
    "Employee_ID",
    "Name",
    "Organization_Unit",
    "Work_Location",
    "Department",
    "Position",
    "Job",
    "Grade",
    "Date",
    "Day",
    "Day_Type",
    "Holiday_Type",
    "Shift",
    "Status",
    "Exception",
    "Schedule_From_Time",
    "Schedule_To_Time",
    "Actual_From_Time",
    "Actual_To_Time",
    "Total_Worked_Hrs",
    "Lateness_Hrs",
    "Early_Out_Hrs",
    "Overbreak_Hrs",
    "Regular_Units",
    "Late_In_Reason",
    "Early_Out_Reason",
    "Employee_Remarks",
    "Approvers_remarks",
    "Total_OT",
    "Leave_Type",
    "Leave_Hrs",
]
```

## Example searchable text

```text
Employee_ID: A10029
Name: Example Employee Beta
Department: Operations
Date: 2026-09-05
Shift: 1st Shift
Exception: Lateness and Early Out
Total_Worked_Hrs: 2.88
Lateness_Hrs: 1.73
Early_Out_Hrs: 3.38
```

## Excluded from embeddings by default

Examples:

```text
Pending_with
last_Updated_date
empty allowance fields
traceability fields
```

---

# Improvement 7 — Store Rich Structured Metadata

## Goal

Store exact filterable fields in Chroma metadata.

This allows later exact filtering without parsing text.

## Metadata examples

```python
{
    "Employee_ID": "A10029",
    "Name": "Example Employee Beta",
    "Department": "Operations",
    "Date": "2026-09-05",
    "Shift": "1st Shift",
    "Exception": "Lateness and Early Out",
    "Total_Worked_Hrs": 2.88,
    "Lateness_Hrs": 1.73,
    "Early_Out_Hrs": 3.38,
    "Total_OT": 0,
}
```

## Important rule

Numeric values stay numeric.

Example:

```python
"Lateness_Hrs": 1.73
```

not:

```python
"Lateness_Hrs": "1.73"
```

This supports range filtering.

---

# Split-Safety Enhancement After Improvement 7

Oversized records may need to be split for embedding.

Every split part repeats a compact identity prefix:

```text
Employee_ID
Name
Date
Department
Position
Shift
```

Example:

```text
Employee_ID: A10029
Name: Example Employee Beta
Date: 2026-09-05
Department: Operations
Position: Senior QC Operator A
Shift: 1st Shift

...split body...
```

The prefix token size is reserved before splitting.

This prevents data loss.

---

# Improvement 8 — Stable Deterministic IDs

## Goal

Give each attendance record a stable logical ID.

## Before

```python
ids = ["0", "1", "2", ...]
```

These IDs depend on processing order.

## After

```python
def _make_record_id(record):
```

The ID is derived from stable business identity values.

Example:

```text
attendance:e00001:2026-01-01:synthetic-example
```

## Stable identity fields

The ID uses values such as:

```text
Employee_ID
Date
Shift
Schedule_From_Time
Schedule_To_Time
```

Mutable values such as actual clock-out time or lateness are not used.

This means:

```text
same attendance record
+
updated value
=
same ID, changed content
```

---

# Improvement 9 — Efficient Embedding Batching

## Goal

Send embeddings efficiently and safely.

## Configuration

```python
EMBEDDING_MAX_TOKENS = 7500
EMBEDDING_BATCH_MAX_TOKENS = 250000
EMBEDDING_BATCH_MAX_ITEMS = 256
CHROMA_BATCH_SIZE = 500
```

## Individual-input protection

One input must stay below:

```text
7,500 tokens
```

Oversized inputs are split.

## Request-wide batching

The batch builder stops when either:

```text
combined tokens would exceed 250,000
```

or:

```text
item count reaches 256
```

## Main generator

```python
def _iter_embedding_batches(items):
```

Conceptually:

```text
prepared items
    ↓
add item while safe
    ↓
limit reached
    ↓
yield current batch
    ↓
start next batch
```

## API efficiency

Instead of:

```text
600 records
→ 600 API calls
```

the design may produce:

```text
600 records
→ 3 API calls
```

depending on token sizes.

## Chroma batching

Chroma writes are also bounded:

```python
CHROMA_BATCH_SIZE = 500
```

---

# Improvement 10 — Detailed Ingestion Statistics

## Goal

Make ingestion behavior visible.

## Statistics model

```python
class IngestionStats(BaseModel):
    jsonl_records: int = 0
    invalid_records: int = 0
    attendance_chunks: int = 0
    period_chunks: int = 0
    new_records: int = 0
    changed_records: int = 0
    unchanged_records: int = 0
    deleted_records: int = 0
    duplicate_records: int = 0
    embedding_inputs: int = 0
    embedding_api_batches: int = 0
    chroma_upserts: int = 0
    postgres_upserts: int = 0
    elapsed_seconds: float = 0.0
```

## Example output

```text
INGESTION SUMMARY

JSONL records:       3964
Invalid records:     0
Attendance chunks:   3964
Period chunks:       568
New records:         25
Changed records:     4
Unchanged records:   4503
Deleted records:     0
Duplicate records:   0
Embedding inputs:    29
Embedding API calls: 1
Chroma upserts:      29
Elapsed seconds:     3.72
```

---

# Improvement 11 — Duplicate Detection

## Goal

Do not store the same logical attendance record multiple times.

## Core logic

Records are grouped by stable `record_id`.

```python
previous = selected.get(record_id)
```

If no previous record exists:

```text
keep it
```

If the same ID and same content exist:

```text
exact duplicate
→ skip duplicate
```

If the same ID has different content:

```text
compare last_Updated_date
→ keep newest version
```

This protects against duplicated or repeated HR exports.

---

# Improvement 12 — Incremental Ingestion

## Goal

Stop rebuilding all embeddings on every run.

## Before

```text
delete Chroma collection
↓
embed everything
↓
recreate everything
```

## After

```text
read existing Chroma state
↓
compare stable IDs + content hashes
↓
classify records
```

Possible outcomes:

```text
new
changed
unchanged
deleted
```

## New record

```text
not in Chroma
→ embed
→ insert
```

## Changed record

```text
same stable ID
+
different content hash
→ remove old parts
→ re-embed
→ update
```

## Unchanged record

```text
same ID
+
same hash
+
same split parts
→ skip
```

No embedding API request is made.

## Deleted record

```text
exists in Chroma
but no longer exists in current source
→ delete from Chroma
```

## Content hash

```python
def _content_hash(value):
```

uses a stable SHA-256 hash.

Trace fields such as:

```text
_excel_row
_source_file
_sheet
```

are excluded from the business-content hash.

This prevents false updates when a row merely moves.

---

# Improvement 13 — Optional PostgreSQL + pgvector

## Goal

Prepare the system for a stronger structured + vector database architecture.

## Current behavior

Chroma remains supported and enabled.

PostgreSQL is optional.

## Configuration

```python
ENABLE_POSTGRES = False
```

or via environment:

```env
ENABLE_POSTGRES=true
POSTGRES_DSN=postgresql://user:password@host/database
```

## Optional dependency

```bash
uv add "psycopg[binary]"
```

## PostgreSQL table

The design creates a table containing:

```text
chunk_id
record_id
chunk_type
content_hash
content
metadata JSONB
embedding VECTOR
updated_at
```

## Important optimization

PostgreSQL reuses vectors already stored in Chroma.

It does not call the OpenAI embedding API a second time.

## Local Windows setup (current implementation)

The repository includes a portable setup script at:

```text
week5/new_implementation/setup_postgres.ps1
```

Run it from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\week5\new_implementation\setup_postgres.ps1
```

The script installs PostgreSQL 17 with `winget` when it is missing, waits for
the service to accept connections, creates the `apdc_attendance` database, and
runs a connection health check. If PostgreSQL is already installed, provide
the existing administrator password:

```powershell
powershell -ExecutionPolicy Bypass -File .\week5\new_implementation\setup_postgres.ps1 `
  -SkipInstall -PostgresPassword 'your-postgres-password'
```

The script writes the active local configuration to:

```text
week5/new_implementation/.env.postgres
```

That file is ignored by git because it contains the PostgreSQL password. Its
values are:

```env
ENABLE_POSTGRES=true
POSTGRES_DSN=postgresql://postgres:<url-encoded-password>@localhost:5432/apdc_attendance
ENABLE_PGVECTOR=false
```

The password is not stored in `ingest.py`, `answer.py`, or the tracked
documentation. It is either generated by `setup_postgres.ps1` and saved only
in `.env.postgres`, or supplied by the operator through `-PostgresPassword` or
the `APDC_POSTGRES_PASSWORD` environment variable. Both `ingest.py` and
`answer.py` load this file explicitly, so they work independently of the
current working directory.

`psycopg[binary]` is declared in `pyproject.toml` and `requirements.txt`.
With `ENABLE_PGVECTOR=false`, PostgreSQL stores the typed
`attendance_records` table while Chroma continues to store/search semantic
vectors. Set `ENABLE_PGVECTOR=true` only when the target PostgreSQL server has
the `vector` extension installed (for example, a pgvector Docker image).

For the full setup and troubleshooting instructions, see
`week5/new_implementation/POSTGRES_SETUP.md`.

---

# Improvement 14 — Exact / Semantic / Hybrid Query Routing

## Goal

Stop using vector similarity for every question.

This improvement is implemented in `answer.py`.

## Query plan

```python
class QueryPlan(BaseModel):
    mode: Literal[
        "exact",
        "semantic",
        "hybrid",
    ]

    search_query: str
    filters: list[FilterCondition]

    aggregation: Literal[
        "none",
        "count",
        "distinct_count",
        "sum",
        "average",
        "min",
        "max",
    ]

    aggregation_field: str | None
```

## Exact mode

Examples:

```text
Who was late on September 5?
How many employees were absent?
What is Ahmed's total overtime?
```

These use structured metadata filtering.

Example filter:

```python
{
    "$and": [
        {"Date": "2026-09-05"},
        {"Lateness_Hrs": {"$gt": 0}},
        {"chunk_type": "attendance_record"},
    ]
}
```

## Semantic mode

Examples:

```text
Who has problematic attendance?
Find unusual attendance behavior.
```

These use embeddings.

## Hybrid mode

Example:

```text
Find Operations employees with problematic attendance.
```

Uses:

```text
Department = Operations
+
semantic similarity for "problematic attendance"
```

## Deterministic calculations

Counts, sums, averages, min, and max are calculated from structured metadata rather than left entirely to the LLM.

## Distinct employees

```text
How many employees were late?
```

uses distinct `Employee_ID` count.

This avoids confusing:

```text
number of attendance rows
```

with:

```text
number of employees
```

---

# Improvement 15 — Employee-Period Summary Chunks

## Goal

Improve retrieval for employee history/pattern questions.

Daily chunks remain the main source:

```text
one employee + one day
```

Improvement 15 adds an additional monthly summary chunk:

```text
one employee + one month
```

## Example

```text
Employee_ID: A10029
Name: Example Employee Beta
Period: 2026-09
Department: Operations

Attendance records:
2026-09-01 | Shift=1st Shift | Exception=Lateness | Late=1.72
2026-09-02 | Exception=Early Out | EarlyOut=6.82
2026-09-03 | Exception=Lateness | Late=2.25
...
```

## Chunk type

Daily:

```text
chunk_type = attendance_record
```

Monthly:

```text
chunk_type = employee_period
```

This lets `answer.py` keep exact calculations isolated from summary chunks.

## No LLM ingestion cost

The monthly summary is generated deterministically in Python.

GPT is not used to create it.

---


# Improvement 16 — PostgreSQL as the Structured Source of Truth

## Goal

Store the complete normalized attendance records in PostgreSQL as typed structured data, while keeping vector representations for semantic retrieval.

## Why

Chroma is useful for vector search and simple metadata filters, but PostgreSQL is better for structured operations such as:

- exact employee lookups,
- date ranges,
- numeric comparisons,
- sorting,
- counts,
- sums,
- averages,
- grouped calculations,
- larger relational datasets.

## New Structured Table

The updated ingestion creates an `attendance_records` table when PostgreSQL is enabled.

Conceptually it contains:

```text
record_id
content_hash
employee_id
name
organization_unit
country
work_location
department
position
job
gradeset
grade
attendance_date
day
day_type
holiday_type
shift
status
exception
total_worked_hrs
lateness_hrs
early_out_hrs
overbreak_hrs
regular_units
pre_ot_hrs
post_ot_hrs
total_ot
ot_authorized
ot_not_authorized
leave_type
leave_hrs
last_updated_at
source_file
source_sheet
source_excel_row
source_jsonl_line
search_text
record_json
synced_at
```

## Full JSON Record Is Preserved

The normalized business record is also stored in:

```text
record_json JSONB
```

This means PostgreSQL stores both:

```text
typed columns
+
complete normalized source record
```

## Optional Activation

PostgreSQL remains optional:

```env
ENABLE_POSTGRES=true
POSTGRES_DSN=postgresql://user:password@host/database
ENABLE_PGVECTOR=false
```

Required Python package:

```bash
uv add "psycopg[binary]"
```

## Important Design Choice

Chroma is not removed.

The migration path is:

```text
Chroma
→ remains available

PostgreSQL
→ structured source of truth when enabled

pgvector
→ semantic/vector retrieval when enabled
```

---

# Improvement 17 — Proper Exact-Query Execution

## Goal

Run exact HR questions as structured database queries instead of relying on semantic similarity.

## Examples

```text
Who was late on September 5?
```

```text
How many employees were absent?
```

```text
Who worked more than 10 hours?
```

```text
What was Ahmed's total overtime?
```

## New Flow

```text
Question
↓
query planner
↓
structured filters
↓
PostgreSQL WHERE clause
↓
exact rows
↓
deterministic calculation if needed
↓
LLM explanation
```

## Supported Operators

The structured query layer supports:

```text
eq
ne
gt
gte
lt
lte
in
contains
starts_with
```

## Example

A question such as:

```text
Which Operations employees were late more than 2 hours on 2026-09-05?
```

can become conceptually:

```sql
WHERE attendance_date = '2026-09-05'
  AND department = 'Operations'
  AND lateness_hrs > 2
```

## Aggregations

Exact-query execution also supports deterministic operations such as:

```text
count
distinct_count
sum
average
min
max
```

The database calculates the value. The LLM explains the result.

---

# Improvement 18 — Stronger Query Planner

## Goal

Make the query planner extract structured intent more reliably.

## Planner Output

The planner now produces a `QueryPlan` containing:

```text
mode
search_query
filters
name_hint
aggregation
aggregation_field
```

## Retrieval Modes

```text
exact
semantic
hybrid
```

## Important Planner Rules

The planner is instructed to:

- preserve employee IDs exactly,
- preserve the user's employee-name text instead of inventing a longer name,
- normalize explicit dates to `YYYY-MM-DD`,
- use numeric values for numeric comparisons,
- use two date filters for ranges,
- select a counted measure independently from declarative business predicates,
- use `attendance_records` only for row/record/entry counts,
- distinguish worked, scheduled, scheduled non-attended, and explicitly absent
  date predicates,
- return structured interpretation candidates when the meaning is genuinely
  ambiguous instead of defaulting to `COUNT(*)`,
- select the correct numeric aggregation field,
- distinguish exact constraints from semantic meaning.

## Example

Question:

```text
How many Operations employees were late more than 2 hours on September 5, 2026?
```

Desired plan:

```text
mode = exact

Department = Operations
Date = 2026-09-05
Lateness_Hrs > 2

aggregation = distinct_count
aggregation_field = Employee_ID
```

---

# Improvement 19 — Natural Date-Range Understanding

## Goal

Resolve common human date expressions deterministically before querying.

## Supported Relative Expressions

The updated answer layer handles expressions such as:

```text
today
yesterday
this week
last week
this month
last month
last N days
past N days
previous N days
```

## Example

Assume the current local date is:

```text
2026-09-11
```

Then:

```text
yesterday
```

becomes:

```text
Date = 2026-09-10
```

And:

```text
last week
```

becomes:

```text
Date >= 2026-08-31
Date <= 2026-09-06
```

## Timezone

Relative dates use:

```env
APP_TIMEZONE=Asia/Aden
```

by default.

## Why Deterministic Resolution Matters

The LLM is not trusted to invent the final relative-date boundaries.

The application resolves them in code and then inserts the exact filters into the query plan.

---

# Improvement 20 — Better Employee-Name Matching

## Goal

Handle employee names that are incomplete, shortened, or slightly misspelled.

## Matching Strategy

The name resolver now follows:

```text
exact match
↓ if none
partial / substring match
↓ if none
fuzzy match
```

## Example

User asks for:

```text
Mohammed Ahmed
```

Database contains:

```text
Example Employee Delta
```

The partial-name stage can resolve the database name without requiring exact equality.

## PostgreSQL Mode

PostgreSQL supplies the authoritative employee directory. Name normalization,
substring matching, and bounded fuzzy scoring are performed consistently in
Python. Ingestion does not create `pg_trgm` or a trigram index because runtime
queries do not use them.

## Chroma Fallback

If PostgreSQL is disabled, the application uses metadata names from Chroma and applies:

- normalized exact comparison,
- substring comparison,
- `SequenceMatcher` fuzzy comparison.

## Safety Rule

If the name cannot be confidently resolved, the system does not force a potentially wrong exact `Name = ...` filter.

It stops before retrieval and asks the user to select a database-backed
candidate, or reports that no employee matched.

---

# Improvement 21 — Stronger Hybrid Retrieval

## Goal

Apply exact structured constraints before semantic similarity ranking.

## Problem With Weak Hybrid Search

A weak design might do:

```text
semantic search across everything
↓
try to filter results afterward
```

This can waste retrieval slots on irrelevant departments, dates, or employees.

## Improved PostgreSQL + pgvector Flow

```text
structured filters
↓
PostgreSQL WHERE
↓
only matching candidate rows/chunks
↓
pgvector similarity ordering
↓
top semantic matches
```

Example:

```text
Find Operations employees with problematic attendance.
```

The system first applies:

```text
Department = Operations
```

and only then ranks those candidates by semantic similarity to:

```text
problematic attendance
```

## Chroma Fallback

When PostgreSQL is disabled, the Chroma fallback follows the same principle:

```text
load/filter candidate metadata first
↓
compute semantic similarity only on candidates that pass the filters
↓
return the strongest matches
```

## Result

Hybrid retrieval now behaves conceptually as:

```text
exact constraints
+
semantic meaning
```

rather than:

```text
semantic search first
+
hope the correct structured records appear
```

---

# 5. Complete Data Flow

## First ingestion

```text
attendance.xlsx
    ↓
convert_excel_to_jsonl()
    ↓
attendance.jsonl
    ↓
validate + normalize
    ↓
daily attendance chunks
    +
monthly employee-period chunks
    ↓
stable IDs
    ↓
duplicate detection
    ↓
all records are new
    ↓
safe embedding preparation
    ↓
embedding batches
    ↓
Chroma upsert
```

## Later ingestion

```text
attendance.jsonl / refreshed source
    ↓
validate
    ↓
stable IDs + hashes
    ↓
compare with Chroma
    ↓
new       → embed
changed   → re-embed
unchanged → skip
deleted   → delete
```

---

# 6. Complete Answer Flow

```text
User question
    ↓
plan_query()
    ↓
QueryPlan
    ↓
exact / semantic / hybrid
```

Exact:

```text
metadata filters
↓
all matching attendance rows
↓
deterministic calculation if required
↓
LLM explanation
```

Semantic:

```text
query embedding
↓
vector search
↓
rerank
↓
top records
↓
LLM
```

Hybrid:

```text
metadata filters
+
vector similarity
↓
rerank
↓
LLM
```

---

# 7. Important Files

## `ingest.py`

Responsible for:

```text
Excel → JSONL
validation
normalization
search-text creation
metadata
stable IDs
duplicate detection
period chunks
embedding batching
incremental Chroma synchronization
optional PostgreSQL sync
statistics
```

## `answer.py`

Responsible for:

```text
query planning
exact filtering
semantic retrieval
hybrid retrieval
reranking
deterministic calculations
final LLM context
final answer
```

---

# 8. Key Design Principles

## Structured data stays structured

Values such as:

```text
Employee_ID
Date
Department
Lateness_Hrs
Total_OT
```

are preserved as fields.

They are not reduced only to free text.

## Embeddings are for semantic meaning

Use embeddings for:

```text
problematic attendance
unusual behavior
similar attendance patterns
```

## Exact fields are for exact questions

Use structured filtering for:

```text
dates
employee IDs
departments
numeric thresholds
counts
totals
averages
```

## LLM is used where reasoning/language adds value

The LLM is no longer used to rediscover the structure already present in Excel.

It is mainly used for:

```text
query interpretation
reranking where useful
final natural-language answer
```

---

# 9. Performance Improvements

The largest performance gains come from:

1. Removing GPT from ingestion.
2. One row = one retrieval record.
3. Embedding only useful fields.
4. Batching embedding calls.
5. Skipping unchanged records.
6. Using exact metadata filtering for exact questions.
7. Limiting reranking to semantic/hybrid candidate sets.
8. Sending only relevant context to the final LLM.

---

# 10. Reliability Improvements

The largest reliability gains come from:

1. Pydantic validation.
2. Date/time/number normalization.
3. Invalid-record audit file.
4. Stable IDs.
5. Content hashes.
6. Duplicate detection.
7. Token-safe splitting.
8. Identity prefixes on split parts.
9. Embedding response-count validation.
10. Incremental synchronization.
11. Exact calculations from metadata.
12. Traceability to JSONL/Excel rows.

---

# 11. Traceability

Each attendance record can preserve source information such as:

```text
source file
sheet
Excel row
JSONL line
record_id
content_hash
```

This makes it possible to trace a chatbot answer back to the original HR source.

---

# 12. Current Recommended Runtime Architecture

## Improvements 16–21 Implementation Status

Improvements 16–21 are implemented in the updated `ingest.py` and `answer.py`.

PostgreSQL remains optional. When `ENABLE_POSTGRES=false`, the application uses
Chroma for all retrieval. When PostgreSQL is enabled, exact structured queries
use the `attendance_records` table. Semantic and hybrid retrieval use
`knowledge_chunks` with pgvector only when `ENABLE_PGVECTOR=true`; otherwise
they safely fall back to Chroma.

The tested local runtime is:

```text
JSONL
+
ChromaDB
+
PostgreSQL 17 (attendance_records)
+
OpenAI embeddings
+
GPT answer model
```

The local configuration created by `setup_postgres.ps1` is:

```env
ENABLE_POSTGRES=true
POSTGRES_DSN=postgresql://postgres:<password>@localhost:5432/apdc_attendance
ENABLE_PGVECTOR=false
```

This enables reliable SQL filtering, counts, date ranges, and numeric
aggregations without requiring Docker or pgvector. PostgreSQL + pgvector can
be enabled separately when a pgvector-capable server is available.

---

# 13. Deferred Future Architecture Option

One possible future architecture is:

```text
HR database/API
    ↓
normalized structured records
    ↓
PostgreSQL
    +
pgvector
```

PostgreSQL handles:

```text
exact filtering
counts
numeric comparisons
date ranges
aggregations
```

pgvector could handle:

```text
semantic retrieval
```

The LLM would still handle:

```text
query interpretation
reasoning
natural-language response
```

This is not the current implementation decision. The current system keeps
Chroma as the approved semantic index. A pgvector migration is deferred until
it has a separate design, migration plan, performance evidence, and rollback
procedure.

---

# 14. Final Summary

The Improvements 1–21 transform the project from:

```text
Excel
→ giant text blocks
→ LLM-generated chunks
→ rebuild everything
→ vector search for every question
```

into:

```text
Excel
→ JSONL
→ structured validated records
→ one record per attendance row
→ Python-generated searchable text
→ rich metadata
→ stable IDs
→ duplicate detection
→ employee-period summaries
→ incremental embeddings
→ safe batching
→ incremental Chroma synchronization
→ PostgreSQL structured attendance store when enabled
→ pgvector semantic store when enabled
→ stronger exact SQL execution
→ stronger exact / semantic / hybrid query planning
→ deterministic relative-date ranges
→ exact / partial / fuzzy employee-name matching
→ filter-first hybrid semantic retrieval
→ deterministic calculations
→ focused LLM final answer
```

The central design philosophy is:

> Preserve structure where the data is structured, use embeddings only where semantic similarity is useful, and use the LLM only where language understanding or reasoning adds real value.

---

# 15. Improvements 22–35 and Current Foundation

## 15.1 Why another architecture pass was required

The first 21 improvements made attendance ingestion and retrieval much more
structured, but four system-level risks remained:

1. The ingestion boundary recognized Excel attendance but could not safely
   preserve CSV files, unknown domains, malformed partitions, or historical
   physical rows.
2. PostgreSQL and Chroma publication could delete good attendance state when a
   source disappeared, became unreadable, or was reclassified.
3. The LLM plan could still alter explicit identity, date, filter, grouping, or
   calculation meaning before execution.
4. The evaluation corpus was too small and several expected fields were stored
   without being asserted.

The solution was not to build a generic HR platform. The solution was to add a
minimal safe envelope around the stable attendance product, then strengthen
the deterministic compiler and its evaluation evidence.

## 15.2 Minimal multi-domain ingestion boundary

The system now discovers XLSX and CSV files using `source_ingestion.py`. A
`SourceFile` identifies the physical revision by relative path, format, and
SHA-256. A `SourcePartition` represents one worksheet or the single `CSV`
partition. A `SourceRow` preserves both physical values and the normalized
attendance mapping.

Classification uses the first folder beneath `KNOWLEDGE_BASE_PATH` and the
code-level `DOMAIN_RULES` registry. Only the `attendance` folder with required
headers `Employee_ID`, `Name`, and `Date` becomes attendance. Unknown folders,
schema mismatches, duplicate normalized headers, and read errors are
quarantined with deterministic reason codes. No LLM, embedding model, or fuzzy
matcher participates.

Every readable physical row is represented by `RawSourceRow` and written to:

```sql
private_ingestion.raw_source_rows
```

The table is private and append-only. The typed `attendance_records` table
keeps its existing business columns and record IDs and gains nullable
`raw_row_key` provenance. Unknown rows never become typed attendance rows.

## 15.3 Safe publication and recovery

A safe PostgreSQL snapshot is published atomically: raw rows are inserted,
canonical attendance winners are upserted, missing typed rows are deleted, and
the transaction commits. Chroma is synchronized only afterward.

Unsafe attendance snapshots preserve readable raw/quarantined rows but do not
modify the typed attendance table or Chroma. Publication is unsafe when invalid
attendance exists, the valid snapshot is empty, or a prior attendance source
disappears, becomes unreadable, or changes domain unexpectedly.

Manifest version 3 records source revisions, formats, partition summaries,
parser version, output hashes, and counts. The SQLite ledger additionally
stores parser version, partition summary, expected raw-row coverage, generation
state, and sink checkpoints. Cache reuse requires both artifact integrity and,
in PostgreSQL mode, raw-table coverage.

## 15.4 Chroma remains a derived attendance index

Every daily and period chunk is stamped `domain="attendance"`. Retrieval always
adds that trusted filter. Searchable text remains restricted to the
`SEARCHABLE_FIELDS` registry; private raw JSONB is never embedded.

Three hashes/identities have different purposes:

- record ID: stable logical attendance identity;
- embedding-input hash: model + index schema + projected text;
- metadata hash: non-embedded metadata and provenance.

A changed embedding-input hash triggers embedding and upsert. A metadata-only
change uses Chroma metadata update without an embedding request. No change
causes no write. Deletion is delayed until replacement writes succeed.

Telemetry is disabled through Chroma's native settings in
`create_chroma_client()` rather than logger suppression.

# 16. Authoritative Question-to-Answer Flow

## 16.1 Trusted inputs

The public question APIs accept an optional keyword-only `AccessContext`.
Omitted context resolves to the fixed local attendance demo scope. Scope is
checked before any planner, catalog, employee-directory, embedding, retrieval,
reranking, or answer-provider call.

Conversation state is also trusted application state. It holds selected
employees and pending clarification candidates/plan/constraint. Model output
cannot create access scope or silently select an ambiguous employee.

Resolved identity is propagated explicitly across an internal retrieval
boundary. `_fetch_context_result()` returns a typed `ContextFetchResult` with
the final plan, evidence, calculation, matched count, and database-validated
employee candidates. The public `fetch_context()` wrapper retains its original
four-item tuple. After successful retrieval, `answer_question_with_state()`
stores those candidates for later identity-free turns. Direct ID/name
resolution and clarification-based selection therefore have the same state
semantics.

## 16.2 Planner and deterministic compiler

The planner returns `QueryPlan`: mode, search query, filters, optional name
hint, aggregation, grouping, percentage numerator, ordering, limit, counted
measure, and business predicates. It does not return `domain`.

The plan is then compiled and normalized. This second stage is mandatory; raw
planner output is never executed directly. The compiler:

- restores explicit question constraints omitted by the model;
- replaces conflicting planner dates/values with validated question values;
- validates operators against each field's storage type;
- validates finite numeric values and scalar/list shape;
- compiles the counted measure and business predicates independently;
- enforces explicit positive, negative, zero, and count-noun contracts;
- rejects contradictory polarity or incompatible physical filters;
- resolves controlled catalog values and employee identities;
- rejects unresolved or contradictory structured intent;
- selects exact, hybrid, or semantic mode from the final constraints.

## 16.3 Composable calculation rules

| User meaning | Executable definition |
|---|---|
| Worked days | `COUNT(DISTINCT Date)` where `Total_Worked_Hrs > 0` |
| Scheduled working days | `COUNT(DISTINCT Date)` where `Day_Type = 'Working Day'` |
| Scheduled days not attended | `COUNT(DISTINCT Date)` where `Day_Type = 'Working Day'` and `Total_Worked_Hrs <= 0` |
| Explicit recorded absence | `COUNT(DISTINCT Date)` where `Exception = 'Absent'` |
| Attendance record count | `COUNT(*)` |
| Authorized record count | `COUNT(*)` where `Status = 'Authorized'` |
| Employee count | `COUNT(DISTINCT Employee_ID)` |
| Record percentage | matching physical rows divided by scoped physical rows |

These rules are triggered only by count/calculation intent. Merely asking to
list attendance or Authorized records does not become a count. If employees
are the counted subject, the presence of the words “attendance records” does
not override distinct employee semantics.

The requested period is compared with the authoritative available date range.
The numeric answer remains scoped to available records, and deterministic text
warns when the requested period is not fully covered.

## 16.4 Employee and catalog clarification

Employee candidates always preserve both ID and name. Explicit valid IDs have
priority. Name matching proceeds through exact, partial, and conservative fuzzy
matching. Duplicate names remain different employees.

Ambiguity raises a controlled clarification before retrieval. The user's
selection is limited to displayed, revalidated candidates. Successful
selection injects an authoritative ID, resumes the saved plan without another
planner call, and clears every pending state field.

Successful direct resolution also updates the selected employee after the
request succeeds. Singular references such as “this employee,” “they,” and
“what about overtime?” inherit that trusted ID. Explicit plural/quantified
population and grouping questions remain global. An invalid new identity does
not silently replace the prior successful selection.

Department, Work Location, Shift, Status, Exception, and Leave Type use the
same bounded catalog principle. Failed structured resolution never falls back
to a broad semantic query.

## 16.5 Retrieval and calculations

Exact requests use PostgreSQL when enabled. Filters and aggregation share one
read-only repeatable-read snapshot. Typed array parameters match their SQL
storage (`date[]`, `time[]`, `timestamp[]`, `double precision[]`, or `text[]`).

Hybrid retrieval applies structured filters before semantic ranking. A
semantic plan that gains an employee ID during resolution becomes hybrid so
the identity filter is retained. Pure semantic retrieval is used only when no
structured constraint exists.

Grouped, scalar, and percentage answers are calculated and rendered
deterministically. PostgreSQL `AVG` semantics ignore null values. Unrequested
planner limits are removed; explicit top/bottom limits remain bounded by
configuration.

## 16.6 Evidence safety

Related embedding parts are expanded by logical record ID before reranking.
Small result sets skip the reranker; larger sets receive deterministic evidence
scoring and bounded optional reranking.

Retrieved records are labeled as untrusted evidence in prompts. They cannot
supply system instructions. Source and page text are HTML-escaped before the
Gradio application renders them inside `<pre><code>`.

# 17. Critical Correctness Fixes

The final review/debugging pass fixed these root causes:

- percentage-of-records used the wrong denominator identity;
- explicit dates could be dropped, repaired incorrectly, or forced to equality;
- month/year wording could be parsed as a month/day prefix;
- named temporal fields could receive an unrelated daily `Date` filter;
- semantic employee-name requests could lose resolved identity scope;
- successful clarification could preserve stale pending state/name hints;
- authorized workflow records could be confused with overtime authorization;
- list requests could be changed into record counts;
- distinct employee questions could be overwritten by record-count patterns;
- temporal `IN` filters used incompatible SQL array types;
- model-generated limits could silently truncate grouped results;
- grouped evaluation checked only group count, not group values;
- expected record IDs and complete clarification clearing were not asserted;
- direct evaluator dataset verification failed outside package execution.
- direct successful identity resolution was discarded before the next turn;
- singular “this employee” was mistaken for a population query and could
  retrieve unrelated employee evidence.

The fixes were made in the owning schema/compiler/resolver/evaluator boundary,
not as per-question answer substitutions.

# 18. Evaluation and Acceptance

The local `new_evaluation/tests.jsonl` contains 310 unique APDC cases spanning
exact, semantic, hybrid, employee ambiguity, multi-employee, calculations,
controlled fields, dates, grouped values, percentages, and malformed or
out-of-scope input. Because it contains private-derived expected results, that
file and `dataset_manifest.json` are ignored by Git and are not published.
The publishable unit suite explicitly skips private corpus integration checks
when those local fixtures are absent, while direct evaluator commands raise an
actionable missing-fixture error.

The evaluator validates:

- plan mode, aggregation, aggregation field, and grouping;
- all required filters;
- resolved employee IDs;
- matched row counts and exact record IDs;
- scalar and grouped calculation values with bounded float tolerance;
- normalized results and rendered answer facts;
- expected safe errors;
- selected employees and fully cleared pending clarification state.

Expected data is locked locally to the authorized row count, employee count,
date range, and private fingerprint. The fingerprint is not published.

Final local verification passed 219 implementation tests and 27
app/evaluation/benchmark tests. Ruff check, Ruff formatting, Python compilation,
direct local dataset verification, and `git diff --check` also passed. The
private corpus integrity and deterministic calculation checks passed locally,
and every later regression case passed individually against the final rule set.
The screenshot-specific state flow was subsequently reproduced and verified
against the real employee directory and PostgreSQL data with the unavailable
provider boundary replaced. The configured LiteLLM endpoint refused connections
during the final live-provider replay, so that replay is documented as an
environmental limitation rather than a passing provider result.

# 19. Current Operational Architecture

```text
XLSX/CSV sources
  -> deterministic partition classification
  -> append-only private raw history
  -> typed PostgreSQL attendance snapshot
  -> attendance-only Chroma projection

Authenticated/trusted attendance scope + question
  -> policy gate
  -> deterministic validation and normalized plan
  -> employee/catalog resolution or clarification
  -> exact/hybrid/semantic retrieval
  -> deterministic calculation where applicable
  -> safe evidence rendering and answer
```

This release intentionally does not claim production authentication or RLS.
Before multi-user production use, add real identity, row/field authorization,
and an outbox/shared publication boundary. Before adding another HR domain,
approve real sample data, record grain, natural key, event dates, measures, and
access policy.
