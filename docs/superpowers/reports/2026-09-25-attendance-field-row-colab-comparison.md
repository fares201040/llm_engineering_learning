# Attendance row versus field Chroma comparison

Date: 2026-09-25
Scope: synthetic attendance records only; no production PostgreSQL, private
evaluation corpus, credentials, or local attendance exports.

## Comparison contract

The Colab runner builds the existing row chunks and the new field chunks from the
same generated records, with `all-MiniLM-L6-v2` in distinct Chroma persistence
directories. It asks the same twenty synthetic questions of both collections (ten
name-and-field, five ID/date/field, and five field-only), records
top-five hits and distances, parent-record hit rates at 1/3/5, MRR at 5, query
latency, build time, and vector counts. The field arm additionally records whether
the expected field was retrieved. This measures retrieval only; it is not evidence
that the active direct-SQL chatbot answers better.

## Run status and results

The final twenty-case run completed in the CPU Colab session from the 40-file source
ZIP with SHA-256 `c97e0080032b8c15beb4d1ef5fcb0db62ed809b89ac504ac4031385420060212`.
It indexed 15 generated records as 15 row vectors and 100 field vectors. The full
case-by-case output, including the expected record, requested field, top-five hits,
distances, ranks, and query times, is saved in
`week5/new_implementation/colab/attendance_field_row_comparison_synthetic.json`.
The first ten name-and-field cases are also preserved separately in
`attendance_field_row_comparison_synthetic_v1.json`. The final run reported no
exceptions in its `issues` array.

| Query group | Cases | Row parent hit @1 / @3 / @5 | Field parent hit @1 / @3 / @5 | Field exact-field hit @5 |
| --- | ---: | --- | --- | ---: |
| Name and field | 10 | 0.80 / 0.90 / 1.00 | 0.30 / 0.60 / 1.00 | 0.10 |
| Employee ID, date, field | 5 | 0.40 / 0.80 / 1.00 | 0.60 / 0.80 / 1.00 | 1.00 |
| Field value only | 5 | 0.60 / 0.80 / 1.00 | 1.00 / 1.00 / 1.00 | 1.00 |
| All cases | 20 | 0.65 / 0.85 / 1.00 | 0.55 / 0.75 / 1.00 | 0.55 |

Across all cases, row MRR@5 was 0.7875 and field MRR@5 was 0.6933. Median Chroma
query time was 5.50 ms for row and 5.95 ms for field on this CPU session. These
short, single-session timings are descriptive and do not establish a speed winner.

The query wording strongly affects field retrieval. For nine of ten name-and-field
questions, the field collection's top five were the five `Name` chunks for that
employee, so the requested field was displaced. For the other ten questions, the
expected field appeared in the top five every time. Name-and-field cases 2–10 have
exact-field ranks 6, 6, 6, 6, 7, 16, 11, 6, and 10 respectively; case 1 ranked its
`Leave_Type` chunk first. Parent-record hit therefore overstates whether a usable
field answer was retrieved. In the field-only group the field index ranked the
correct parent first in all five cases, versus three for row chunks. This is a
retrieval behavior finding, not a non-embedding infrastructure issue.

The fixture is small and hand-authored, uses one Hugging Face model and one CPU
runtime, and does not evaluate answer generation or the active SQL-first chatbot.
It supports a targeted design comparison, not a general performance claim.

## Non-embedding issues and observations

### C01 — Colab T4 session assignment unavailable

- **Stage:** runtime provisioning, before source upload or model loading.
- **Command:** `wsl.exe -d Ubuntu-24.04 -- /home/faris/.local/bin/colab --auth=adc new --session attendance-phase2-3 --gpu T4`
- **Precondition:** `colab --auth=adc sessions` succeeded and reported no active sessions.
- **Observed:** the official CLI exited 1. Its `session.new` call reached `client.assign` / `_post_assignment`; the POST to Colab's assignment endpoint raised `ColabRequestError` with reason `Service Unavailable`. No Colab session was created.
- **Reproducibility:** the same service response occurred in the earlier handoff and again in this task on 2026-09-25. No authentication failure was reported.
- **Impact:** no source upload, deterministic notebook, index build, or retrieval query can run on that T4 session. This is infrastructure availability, not embedding quality.
- **Next diagnostic:** check `colab --auth=adc sessions`; retry the documented command when the service is available. Do not repeat ADC login unless an authentication error appears. A CPU runtime may be tried for this embedding-only comparison because the Qwen model is not involved.

### C02 — CPU runtime fallback succeeded (resolution and scope)

- **Stage:** runtime provisioning.
- **Command:** `wsl.exe -d Ubuntu-24.04 -- /home/faris/.local/bin/colab --auth=adc new --session attendance-embedding-compare`
- **Observed:** the official CLI reported `Session READY`. The sanitized source ZIP uploaded successfully. After the balanced comparison queries were added, the deterministic notebook validated the updated 40-file archive with SHA-256 `c97e0080032b8c15beb4d1ef5fcb0db62ed809b89ac504ac4031385420060212`, passed 156 tests plus its selected Ruff and compilation checks, and had zero error cells. The comparison runner then completed all 20 cases and saved a report with an empty `issues` array.
- **Impact:** the embedding comparison can run on CPU. Timing measurements are descriptive for this CPU session and must not be compared with the earlier T4/Qwen acceptance timings.

### C03 — No further non-embedding failures observed

- **Stages checked:** source packaging and SHA validation, dependency installation, deterministic notebook checks, synthetic fixture creation, both Chroma builds, 20 query executions, report save and download.
- **Observed:** all stages completed; the runner's `issues` array is empty. The only observed non-embedding failure was C01, the T4 assignment response.
- **Limits:** the Colab notebook's selected lint/format commands do not include the new comparison script or field-index module. The comparison did execute those modules end to end, and local Python compilation succeeded, but this is narrower static-check coverage than the notebook's pass count might suggest. The Qwen/SQL long-conversation acceptance was outside this retrieval comparison and remains a separate task.
