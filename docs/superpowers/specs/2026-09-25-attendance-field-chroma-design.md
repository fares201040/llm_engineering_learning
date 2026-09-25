# Attendance field-level Chroma experiment

## Goal and scope

Build a second attendance search index whose unit is one populated business field
from one canonical attendance record. Compare it later with the existing row-level
attendance index in Colab using synthetic data. This work does not change the active
direct-SQL answer pipeline or its PostgreSQL-first, Chroma employee-name confirmation
fallback. The existing row index remains available and unmodified.

The field index is an experiment, not an assertion that field chunks retrieve better.
Smaller chunks may make a field-specific question easier to match, but they increase
embedding count and can separate facts that a question needs together.

## Source and chunk contract

The standalone builder reads the existing validated canonical attendance JSONL
projection without refreshing source files or writing PostgreSQL. It refuses a missing,
stale, invalid, or empty projection. It uses the same canonical duplicate-selection
rule as row ingestion, so each logical record has one selected version.

For each selected record, the builder visits every business field except
`Employee_ID`, `Shift`, and `Date`. It skips null and blank values, retaining zero and
false. Trace fields whose names begin with `_` are not business columns. Each emitted
chunk contains only that field's value and the three identifying values in readable
sentences, for example:

> On 2026-09-05, employee A11017 had shift Morning. Total worked hours was 8.

If a shift is missing, the sentence says that the shift was not recorded. Field labels
are derived deterministically from source field names; the original field name and
value are also retained in Chroma metadata. The document text is plain language, not
JSON or JSONL. No LLM is used for the conversion.

Each chunk receives a stable ID derived from the existing logical record ID and the
exact source field name. Metadata contains the parent record ID, field name, employee
ID, shift, date, chunk type, source locator, and hashes needed for incremental sync.
The builder uses the configured Hugging Face or OpenAI embedding model and its token
limits. Model-specific collection names continue to prevent vector-dimension mixing.

## Storage and build flow

The new index uses its own Chroma persistence directory, separate from
`CHROMA_DB_PATH`. A dedicated setting, `CHROMA_FIELD_DB_PATH`, defaults to a sibling
directory named `new_preprocessed_db_fields`. The directory is ignored by Git.
The builder has an explicit module command; normal ingestion does not build the
experiment automatically. It reuses the existing incremental embedding/upsert logic
with a supplied Chroma client and field collection name. Repeated builds update changed
field chunks and remove stale ones only after replacement embeddings are stored.

The builder does not print attendance values, credentials, or vector contents. It
reports aggregate counts and the selected model/collection. It never reads the private
evaluation corpus or results directory.

## Later comparison

The comparison is a separate, later Colab activity. It must use only synthetic
attendance records, the same embedding provider and model, the same source snapshot,
the same questions, and the same retrieval settings for both approaches. The row arm
uses attendance-record chunks from the original index; the field arm uses
attendance-field chunks from the new database. Retrieval results should be judged at
both field and parent-record level, then answer quality can be evaluated separately.
The current chatbot is not switched to the new index before that comparison.

No tests or evaluation are run as part of this implementation, per the user's current
instruction. Verification status and this limitation must be reported explicitly.
