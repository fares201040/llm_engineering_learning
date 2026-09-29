# Attendance browser replay, 2026-09-30

The local Gradio app at `http://127.0.0.1:7860/` was exercised through the
browser UI using the exact retained prompts in the
[manual conversation reference](2026-09-29-attendance-manual-conversation-reference.md).
The database held 3,964 attendance records dated September 1–7, 2026. Answers
were checked against the UI result panel, same-turn SQL/result traces, and
independent read-only PostgreSQL aggregates where counts were involved. The
temporary trace containing employee data was removed after testing.

| Reference IDs | Browser result |
| --- | --- |
| L01–L03 | Pass: day-type and Authorized splits matched aggregates. |
| L04 | Counts matched; answer included a status-by-day-type breakdown beyond the simpler requested totals. |
| L05–L12 | Pass: grouped counts, distinct people, employee dates/hours, shift/status, and unknown-ID clarification. |
| L13 | Initial replay expanded the corrected employee's worked/off-day counts into a daily report. After prompt repair, the UI returned 0 worked days and 1 off day; executed SQL counted distinct dates with the correct employee and week filters. |
| L14 | One initial replay safe-failed on a date-scope mismatch. A later replay answered the shift/status interpretation across the week with seven matching rows. The wording remains ambiguous between shift label and calendar day. |
| L15–L21 | Pass: date corrections, follow-ups, and September 4 status totals matched their result rows. |
| L22 | Department branch passed; the employee branch gave daily shift/day-type rows instead of per-shift day counts. L23 clarified the intended aggregation. |
| L23–L24 | Counts and Markdown table passed. L23's prose called an OFF day a day worked even though the per-shift counts were correct. |
| L25 | The intended interrupted-turn scenario was not reliably captured in the initial replay. A later pending answer was cleared; the chat became empty and the next count answer arrived without the old answer returning. |
| L26 | Pass: September 6 Draft count was 280. |
| L27 | Initial replays returned 280 with unrequested sample details, and one fresh-chat run misstated the 100-row sample size as the total. After prompt repair, two UI runs returned only the 280-record count, including one fresh chat; the traced SQL used a scalar `COUNT(*)` with Draft and date predicates. |
| L28 | The intended interruption completed too quickly in the initial replay, so this exact interruption was not verified. |
| L29–L36 | Pass: count, date/day-type table, status bullets, and day-type splits matched result rows. |
| L37 | Distinct-person counts matched; the answer used “present” loosely for all attendance statuses. |
| L38–L42 | Pass: exception rankings, distinct people by shift, status bullets, and Draft day-type bullets matched the data. |
| M01–M02 | Pass: 3,964 total records; Authorized count 775 and 3 countries. |
| M03 | An initial run over-grouped country and work location. Two later fresh UI runs returned separate Authorized country counts (Yemen 770, Uganda 4, Burundi 1) and 9 distinct Human Resource people at work location N/NA. The traced SQL used independent source scopes. |
| H01–H04 | Not faithfully replayed: the exact preceding employee report was not retained in the reference. Its missing wording affects follow-up scope. |

The changes add model guidance for corrected employee requests, terse record
counts, sample-free count answers, and independent breakdowns. A schema-grounded
SQL example demonstrates separate source scopes. The full online suite passed:
`227 passed, 20 subtests passed`. Browser replay remains important because
model outputs varied across identical prompts before the final guidance change.
