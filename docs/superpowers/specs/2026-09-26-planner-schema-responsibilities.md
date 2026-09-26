# Planner Schema Responsibilities

The reference model rewrites the complete request, preserves multi-part and follow-up meaning, and extracts explicit employee identities. It does not receive database schema and cannot reject a request as unsupported.

The application resolves employee IDs and names, including confirmation flows, and appends authoritative identities to the rewritten request. General criteria may describe any number of employees and do not require a named employee.

The SQL planner receives the complete PostgreSQL schema on every planner call, including every described `record_json` field. It decides whether a concept is representable and uses the existing `unsupported_capability` SQL protocol when it is not. Typed relational columns remain preferred when they represent the same concept.

For manual, modified, adjusted, or clerk-entered swipe requests, a matching record is one where any payroll-effective `From_*` or `To_*` value differs from its corresponding immutable `Actual_*` device value. All four date/time pairs must be compared with PostgreSQL `IS DISTINCT FROM`.

Local Ollama SQL-planner calls use a 65,536-token context window. Other model stages retain the smaller default context. Model-dependent tests run on a Colab A100; deterministic unit tests run locally.

The behavior must remain correct for incomplete wording, explicit employees, criteria selecting many employees, confirmation follow-ups, and messages containing multiple compatible questions or requests.
