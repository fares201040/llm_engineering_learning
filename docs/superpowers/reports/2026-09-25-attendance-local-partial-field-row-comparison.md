# Local partial attendance field versus row retrieval comparison

Date: 2026-09-25. The detailed question, answer key, hit lists, and distances are
private and remain only in the ignored local file
`week5/new_evaluation/results/field_row_local_semantic_partial_comparison.json`.
No real attendance record was uploaded to Colab or committed.

## Scope and method

- Source projection: 3,964 validated attendance records. The existing MiniLM row
  collection covers all 3,964 records as 6,045 attendance vectors (6,904 vectors
  including other chunk types).
- Interrupted field build: 38,144 vectors stored in a separate Chroma database.
  These cover 1,162 records completely and one record partially. The partial record
  was excluded. The full field index would contain 127,215 logical field chunks.
- Both searches used the same `all-MiniLM-L6-v2` query embeddings and the same
  1,162 complete-record candidate set through Chroma metadata filters. The row
  query selected only `attendance_record` chunks; the field query selected only
  `attendance_field` chunks. Each search fetched the top 50 chunks. Parent-record
  ranks deduplicated repeated chunks from the same record. Exact-field hit rates
  were computed on the raw returned field chunks.
- 60 deterministic, source-grounded questions: 20 employee-name/date/field,
  20 employee-ID/date/field, and 20 distinctive field-value questions. The latter
  included three narrative remarks and values from authorized overtime, lateness,
  total worked hours, and actual start time. Distinctive field values were unique
  across the full source projection, not just the indexed subset.

| Question group | Row correct record @5 | Field correct record @5 | Field requested field @5 |
| --- | ---: | ---: | ---: |
| Name + date + field (20) | 6 | 1 | 1 |
| ID + date + field (20) | 0 | 2 | 2 |
| Field value only (20) | 0 | 2 | 2 |
| **All 60** | **6** | **5** | **5** |

Correct-record hit at 10 was 11/60 for each approach. Correct-record hit at 1 was
4/60 for rows and 1/60 for fields. Median filtered Chroma query time was 91 ms for
rows and 357 ms for fields on this local machine. The field index searched many more
vectors per candidate record, so these timings describe this setup only.

## Findings

Neither approach achieved useful stand-alone record retrieval accuracy on these
questions. Row chunks did slightly better overall and clearly better for questions
with employee names and dates. Field chunks did slightly better when no name was
given, but their best value-only group still found the target in the top five for
only 2/20 questions. The three narrative-remark questions missed in both indexes.
Inspecting hit categories showed some value queries retrieved the right *kind* of
field but another record's value; other queries matched related columns instead of
the requested column. Exact employee IDs, dates, times, and numbers are poor
semantic-only discriminators at this scale.

The field build was stopped at the user's request, so this is a comparison on the
shared 1,162-record subset, not the whole attendance population. The subset comes
from the order in which the existing builder processed records; it is not a random
sample. The 60 questions are generated from source fields rather than written by
independent users. These limits prevent a general claim that either chunking scheme
is superior for the complete application.

## Practical direction

Keep PostgreSQL for exact employee/date/value filtering and calculations. For a
future semantic fallback, first narrow candidates with structured filters, then
compare row and field retrieval or reranking on the narrowed set. Do not replace the
row index with the partial field index based on this run. The current active chatbot
uses PostgreSQL SQL for attendance answers and Chroma fallback for uncertain employee
resolution; this evaluation did not change that routing.
