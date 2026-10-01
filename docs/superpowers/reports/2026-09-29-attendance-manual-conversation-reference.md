# Attendance manual conversation reference

Updated: 2026-09-29

Code baseline: `e5e9dadc3b0ed0fd3cfc6b9370ce7df8840461db`

## September 29 long UI conversation after `d80f5c33`

These prompts were entered in the local Gradio chat at `127.0.0.1:7865`, in
order within each sequence. The app was restarted between sequences to load
code changes. The checked SQL evidence was the query-result JSON shown in the
UI plus separate read-only PostgreSQL aggregates for the reported counts.
Employee-specific raw rows and names are intentionally omitted here. `pass`
means the visible answer matched those rows and the requested scope; a failure
is recorded explicitly.

| ID | Exact prompt | Visible outcome and check |
| --- | --- | --- |
| L01 | `sep 1 2026 day types? counts pls` | **Pass:** Working Day 460, OFF Day (ZAS) 86, OFF Day 19, all statuses. Independent `attendance_date`/`status`/`day_type` aggregate summed to these values. |
| L02 | `only auth ones that day? same split` | **Pass:** September 1 Authorized: Working Day 58, OFF Day (ZAS) 32, OFF Day 11. Independent aggregate matched. |
| L03 | `nah sep 3 instead; keep auth split` | **Pass:** September 3 Authorized: OFF Day (ZAS) 50, Working Day 38, OFF Day 1. Date changed; Authorized remained. |
| L04 | `wait all statuses sep 3, same day types` | **Pass:** September 3 all statuses: Working Day 396, OFF Day (ZAS) 157, OFF Day 12. Authorized filter dropped. |
| L05 | `status counts then, sep 3 all day types` | **Pass:** Nine status/day-type cells; Authorized 50/38/1, Draft 39/198/1, Pending For Authorization 68/160/10 (OFF Day (ZAS)/Working Day/OFF Day). UI rendered Markdown list items. |
| L06 | `on 3rd off days only, both kinds: ppl count by dept, all statuses` | **Pass:** Distinct employees for both off-day types on September 3: Operations 68, Engineering 59, then seven departments totaling 42. Independent `COUNT(DISTINCT employee_id)` by department matched all nine groups. |
| L07 | `no date now: countries for pending only, recs each` | **Pass:** Pending For Authorization across all dates: Yemen 1,512, Burundi 5, Uganda 3. Old date and off-day filters dropped; independent SQL matched. |
| L08 | `all statuses now, sep 1-7, countries n recs each` | **Pass:** September 1–7 all statuses: Yemen 3,950, Burundi 7, Uganda 7. Pending filter dropped; independent SQL matched. |
| L09 | `A11017 only, sep 1-7: worked days, off days, leave days separately pls` | **Pass:** Employee and week scope; three separate measures matched the UI result row. Per-person values omitted. |
| L10 | `which days did he work? hrs each` | **Pass:** Reused the verified employee and week; four worked-date rows matched the visible answer. Per-person dates and hours omitted. |
| L11 | `him on 2nd, shift + status?` | **Fail on baseline:** Four planner attempts selected September 2, but the validator incorrectly required the previous September 1–7 scope; UI displayed the safe failure message. A direct three-turn replay reproduced it. |

The L11 root cause was `_mentions_time_period` missing ordinal day references.
The reference rewrite resolved September 2, but `_required_date_scope` carried
the prior week and rejected the correct single-day SQL. The regression test
first failed with the previous range, then passed after the temporal-reference
fix. A direct three-turn replay then returned a verified single-day answer.

| ID | Exact prompt | Visible outcome and check |
| --- | --- | --- |
| L12 | `A11018 sep 1-7 worked n off days?` | **Pass:** No matching employee ID; UI asked for a corrected code. |
| L13 | `sorry A10042. sep 1-7 same` | **Pass:** Corrected employee, same week and worked/off-day measures; UI result rows matched the answer. Per-person details omitted. |
| L14 | `him on 2nd shift/status?` | **Ambiguous:** Interpreted `2nd shift` as a shift label and answered across the week. The next turn clarified the intended date; this is not counted as a date-scope failure. |
| L15 | `no, date 2nd of Sep, what shift/status just that day` | **Pass:** One September 2 row for the corrected employee, with requested fields. |
| L16 | `what about 5th?` | **Fail before second fix:** The planner selected September 5, but the validator retained September 2 and rejected four attempts. UI displayed the safe failure message. |

The same temporal-reference fix was broadened to recognize a bare ordinal day
without treating a shift label such as `2nd Shift` as a date. The focused test
first failed for L16 and then passed after the change.

| ID | Exact prompt | Visible outcome and check |
| --- | --- | --- |
| L17 | `A10042 on Sep 2 2026, shift/status pls` | **Pass after restart:** One requested employee/date row; shift and workflow status matched. |
| L18 | `what about 5th?` | **Pass after restart:** Moved to September 5 while retaining the employee and shift/status fields; one requested row matched. |
| L19 | `and 4th? plus day type, worked hrs` | **Pass:** Moved to September 4, retained prior fields, added day type and worked hours; one row matched. |
| L20 | `new person A10029, same on 4th` | **Pass:** Replaced the employee, retained date and measures; one row matched. |
| L21 | `no person now: 4th status counts all employees` | **Pass:** Dropped employee scope. Authorized 230, Draft 160, Pending For Authorization 175 on September 4; independent SQL matched. |
| L22 | `4th auth recs by dept; separately A10029 sep 1-7 shifts n day counts` | **Mixed:** The department branch used September 4 Authorized and matched all 16 department counts, including Engineering 59 and Operations 57. The employee branch kept its own week and reported three distinct shift labels and two day types. `shifts n day counts` was ambiguous; L23 specified per-shift counts. |
| L23 | `nah per shift how many days for A10029 sep1-7, no auth filter` | **Pass:** 1st Shift 5 days, 2nd Shift 1, OFF 1; independent `COUNT(DISTINCT attendance_date)` by shift matched. Authorized did not leak from L22. |
| L24 | `all employees sep 1-7: daily status counts, markdown table pls` | **Pass:** Seven-date, three-status Markdown table rendered as a real HTML table. All 21 cells matched UI result rows and independent SQL. The reviewed answer appeared progressively at observed text lengths 190, 303, and 347 characters. |
| L25 | `sep 1-7 all depts daily worked hrs totals, table with status too pls` | **Interrupted intentionally for Clear/Submit:** No final answer accepted. |
| L26 | `how many Draft on Sep 6 2026?` | **Answer pass, Clear behavior failed before repair:** 280 Draft records matched independent SQL, and L25 did not reappear. Older conversation messages stayed visibly in the chat after Clear, including after a second idle Clear click. |

The L17–L26 sequence used the updated pipeline, before the concurrently edited
UI progress-text change was loaded into the running server. The server needs a
restart for any later UI edit.

## UI repair and Markdown display checks

The Clear event had reset the trusted gate and context but had not output a new
value to the visible Chatbot component. Returning empty chatbot history from the
event fixed both an idle Clear and Clear followed by immediate Submit in the
actual UI. The pending old turn never reappeared. The newer UI build also showed
`Thinking ...` **inside** the chat while verification ran, then replaced it with
the reviewed answer.

| ID | Exact prompt | Visible outcome and check |
| --- | --- | --- |
| L27 | `Draft recs on Sep 6 2026?` | **Count pass, answer scope issue:** 280 Draft records matched independent SQL, but the answer added unrequested claims about exception categories based on 100 sampled detail rows out of 280 matches. This prompted tighter count-answer guidance. |
| L28 | `how many Authorized on Sep 5 2026?` | **Interrupted intentionally for Clear/Submit:** `Thinking ...` appeared, then Clear removed this pending turn. |
| L29 | `Draft on Sep 6 2026, count only` | **Fail before prompt repair:** immediately after L28 Clear, the UI answered 568, the all-status total, rather than 280 Draft records. The result JSON contained a scalar all-record count. A separate direct run of the same prompt produced the correct status-filtered SQL, showing the model behavior was variable. |
| L30 | `sep 1-7 day types by date, markdown table pls` | **Pass after display repair:** Seven-date, three-day-type table rendered as a real HTML table. All 21 counts matched the UI result rows and independent PostgreSQL aggregate. The UI showed `Thinking ...`, then complete Markdown chunks at observed text lengths 145, 231, and 272; the table was present from the first reviewed chunk. |
| L31 | `Draft on Sep 6 2026, count only` | **Pass after prompt repair:** 280 Draft records, one scalar result. The September 1–7 date range and day-type grouping from L30 did not leak. Three additional direct model/database count checks for Draft, Authorized, and Pending For Authorization all used the requested status predicates and matched independent aggregates. |
| L32 | `Sep 2 2026 status totals pls in bullets` | **Pass in final restarted UI:** Authorized 74, Draft 234, Pending For Authorization 257; the three UI result rows and independent SQL matched. `Thinking ...` appeared first, followed by complete reviewed chunks at observed text lengths 67 and 149. The final Markdown rendered as three actual list items. |
| L33 | `sep 7 status rec counts as bullets` | **Pass after the last display restart:** Authorized 65, Draft 381, Pending For Authorization 122; UI result rows matched the independent daily-status aggregate, and Markdown rendered three actual list items. |
| L34 | `sep 5 draft by day type pls` | **Fail before deterministic filter guard:** The UI returned all statuses (OFF Day 6, OFF Day (ZAS) 202, Working Day 360). The exact executed SQL had the September 5 date predicate but no `status = 'Draft'`. |
| L35 | `sep 5 draft by day type pls` | **Pass after guard:** The planner's corrected executed SQL had `attendance_date = '2026-09-05' AND status = 'Draft'`; OFF Day (ZAS) 40 and Working Day 148. UI rows and answer agreed. |
| L36 | `nah authorized sep 5 same split` | **Pass:** Exact executed SQL replaced Draft with `status = 'Authorized'` and retained September 5 and day-type grouping. OFF Day (ZAS) 121, Working Day 17; UI rows matched. |
| L37 | `drop status, sep 7 ppl by country` | **Pass:** SQL used September 7 with no status predicate and `COUNT(DISTINCT employee_id)` by country. Burundi 1, Uganda 1, Yemen 566; UI rows matched. |
| L38 | `all dates: exception counts for Working Day only top 8` | **Pass:** SQL dropped the date, filtered `day_type = 'Working Day'`, grouped by exception, and limited to eight. The eight UI rows matched the displayed counts; the largest was Absent 811. |
| L39 | `no Working Day filter, only Draft exception counts all dates` | **Pass:** SQL removed day type and date restrictions and used only `status = 'Draft'` before grouping by exception. Eight UI rows matched, led by Absent 768. |
| L40 | `all records, by shift how many distinct people?` | **Pass:** SQL had no date, status, or day-type restriction and counted distinct employee IDs by shift. UI rows matched: OFF 566, 1st Shift 428, 2nd Shift 419, 3rd Shift 268, Normal Shift 82. |
| L41 | `Sep 2 2026 status totals pls in bullets` | **Pass after Markdown spacing repair:** Authorized 74, Draft 234, Pending For Authorization 257. The final browser DOM contained three actual list items. |
| L42 | `sep 5 draft by day type pls; bullets then one closing sentence` | **Pass after Markdown spacing repair:** Exact UI rows showed OFF Day (ZAS) 40 and Working Day 148. The final browser DOM contained two list items followed by a separate closing paragraph. |

After L31, an idle Clear visibly emptied the chat. The count-guidance repair
was checked with live model calls and this UI turn; it does not establish that
every stochastic model run will preserve a category filter.

L34 showed that prompt guidance alone did not reliably enforce a named workflow
status. For a single-scope request with one explicitly named status, a
deterministic pre-execution check now rejects a SQL plan when that exact status
is absent from a contributing WHERE path and asks the planner to retry. The
check defers mixed-scope requests to the existing answer review. The L35–L40
sequence was captured with a temporary
local SQL trace; the log and launcher are outside Git. L41–L42 checked final
Markdown structure in the browser after the spacing change.

This is a record of **questions actually sent to the attendance chatbot and
answers checked**, so later manual sessions can choose new cases instead of
repeating these. Counts below describe the local attendance dataset observed on
September 29, 2026; verify the dataset before using them as a future numeric
oracle. A correct answer must also use the right population and period. The
reference is not a runtime rule or a hardcoded application fixture.

## Current code and local database: checked exact prompts

| ID | Conversation turn / exact user question | Checked answer | Evidence and scope |
| --- | --- | --- | --- |
| M01 | New chat: `how many attend recs u got` | **3,964 attendance records** | Live model/database reply on September 29. Count covers all available attendance rows. |
| M02 | After M01: `of those how many auth? and countries count all pls` | **775 Authorized records; 3 distinct countries**: Burundi, Uganda, Yemen | Live reply and SQL checked. SQL independently counts rows with `status = 'Authorized'` and distinct countries across **all** rows. |
| M03 | New chat: `auth by country; hr work loc ppl count, all hr` | Authorized records by country: **Yemen 770, Uganda 4, Burundi 1**. Separately, **9 distinct Human Resource employees** at work location `N/NA`. | Live reply and SQL checked. The country branch filters Authorized; the HR branch filters department only. The HR count must not inherit Authorized. |

M01 and M02 were run consecutively against the current commit. M03 was run as a
separate new request. A related earlier prompt was `auth ones per country. hr dept
work locs n how many ppl each, all hr`. One browser run before the final prompt
review returned only 8 HR people, because it leaked the Authorized filter into
the HR branch. Later direct runs gave 9. This variant is **not** a passing
baseline; it is an attempted case worth revisiting when checking consistency.

## Earlier checked conversation prompts with no retained full numeric reply

These exact short follow-ups were sent in the prior session, and their scope or
field behavior was checked then. The full answers and SQL were not retained in
this handoff, so **do not treat them as numeric oracles**. Their preceding
employee report and database version must be reconstructed before replay.

| ID | Exact user question | Behavior checked in the prior session |
| --- | --- | --- |
| H01 | `same on sep 2` | Reused the requested measures and employee, replacing the previous day with September 2. |
| H02 | `4 sep same, overnight?` | Switched to September 4, preserved the relevant employee context, and answered the overnight part. |
| H03 | `no, all data now: status n day type counts` | Dropped the prior employee and date scope; answered both status and day-type breakdowns over all available records. |
| H04 | `him dept job grade gradeset?` | Retrieved separate department, job, grade, and gradeset fields for the referenced employee. |

The earlier employee report behind H01 and H02 asked about employee `A11026`
across September 1–7, 2026, but its exact wording was not retained. Other
follow-ups requested shift, weekday, and exception counts; their exact prompts
were not retained. They are intentionally not presented as verbatim test cases.

## New browser questions, September 30 continuation

N01–N04 were sent consecutively in one Gradio chat before the final guidance
changes. N05 started a fresh chat after those changes. N06–N07 were consecutive
in another fresh chat after the date parser repair. The database still held
3,964 attendance records dated September 1–7, 2026. The source baseline was
`25478e1f`; subsequent fixes in this continuation changed the code under test.
Only N06–N07 have same-turn executed SQL captured in this continuation. For the
other entries, numeric checks use independent read-only aggregates, but exact
browser-turn SQL scope is **unverified**.

| ID | Exact user question and preceding turn | Browser observation and evidence | Status |
| --- | --- | --- | --- |
| N01 | New chat: `For Sep 2 and Sep 5, 2026, show Draft and Authorized record counts for each day in a Markdown table. Which day has more Draft records?` | Real HTML table: Sep 2 Draft 234, Authorized 74; Sep 5 Draft 188, Authorized 138. Sep 2 has more Draft records. All four cells matched read-only aggregates. | Pass for answer and Markdown; SQL scope unverified. |
| N02 | After N01: `Same two dates, but Pending For Authorization only. Give each daily count and the combined total in bullets.` | Real list items: Sep 2 257, Sep 5 242; combined 499. The prior Draft/Authorized categories were replaced. Counts matched aggregates. | Pass for answer and Markdown; SQL scope unverified. |
| N03 | After N02: `Across all available dates, count distinct people by country. Separately, sum worked hours by work location for Engineering only. Present both as Markdown tables, then say which Engineering location has the most hours.` | Two real tables. Distinct people: Burundi 1, Uganda 1, Yemen 566. Engineering location hours matched all ten aggregate groups; Shift-Eng led at 868.61 hours. The all-country branch was not narrowed to Engineering in the visible result. | Pass for answer and Markdown; SQL scope unverified. |
| N04 | After N03: `For Engineering only, keep all dates and show the top 3 work locations by worked hours. Add each location’s share of all Engineering worked hours, and one brief takeaway. Do not repeat the country counts.` | The table values and percentages matched the 3,608.21-hour department denominator: Shift-Eng 868.61/24.07%, RTG-Eng 763.51/21.16%, QC-Eng 555.19/15.39%. The takeaway added “most active or staffed,” which the hour totals cannot establish. | Fail for unsupported inference; exact follow-up not rerun after guidance. |
| N05 | New chat after guidance: `For Engineering across all available dates, rank the top 3 work locations by worked hours. Include each share of the Engineering total and a brief takeaway in a Markdown table.` | Real table gave the same top three amounts and shares. Takeaway stated that they collectively account for over 60% of observed Engineering hours; it made no staffing or activity claim. `Generating Answer...` appeared first. | Pass for neighboring answer and Markdown; SQL scope unverified. |
| N06 | New chat after date parser repair: `For Engineering, what are the total worked hours across all available dates?` | 3,608.21 hours. Same-turn SQL used `SUM(total_worked_hrs)` with only `department = 'Engineering'`, matching the aggregate. | Pass. |
| N07 | After N06: `Ignore Engineering now. Compare total worked hours for all departments on Sep 2 versus Sep 5, 2026, and say which day is higher. Separately, across all dates, count Draft records by country. Use two Markdown tables.` | Initial browser attempt safe-failed with `date_scope_mismatch` after four SQL attempts. After the shared-year date parser repair, same-turn SQL used independent CTEs: all-department hours on the two dates, and all-date Draft record counts by country. The rendered tables showed 2,386.87 hours on Sep 2 and 2,219.30 on Sep 5; Draft counts Burundi 1 and Yemen 1,668. Independent aggregates matched. | Fail before repair; pass after repair. |

These cases exercise follow-up scope, analysis, two-part requests, and browser
Markdown rendering. They do not establish a deterministic success rate across
model runs. The later source changes gave the models general evidence guidance
and removed a false date-scope rejection; they did not encode these answer values.

## Fresh browser conversation, September 30 follow-up

This replay began at `c4c88f2b` with a clean checkout. The read-only database
still held 3,964 records for September 1–7, 2026. The same-turn local trace
recorded reference scope, executed SQL, rows, draft, and review. Its private
contents remain outside Git. The independent aggregates below were run against
the same snapshot. Later prompt and UI changes were loaded only after restarting
the app. Status describes the observed browser run, not a deterministic guarantee.

| ID | Exact prompt and preceding turn | Browser, SQL, and independent check | Status |
| --- | --- | --- | --- |
| R01 | New chat: `Across all available dates, by shift show (1) distinct employees with positive worked hours, and (2) OFF-day attendance record counts. Use separate Markdown tables.` | Executed independent source branches: positive hours grouped by shift with distinct employee IDs, and both OFF day types grouped by shift. Real tables showed 289, 211, 141, 48, and 312 people across the five shifts; the OFF-shift OFF-day count was 1,112. Independent aggregates matched. | Pass. |
| R02 | After R01: `For that OFF shift, split OFF-day records into positive worked hours versus zero or missing worked hours. Give record counts and distinct people for each group as bullets, then one brief takeaway.` | SQL retained OFF shift and both OFF day types, and grouped by positive versus nonpositive/missing hours. Browser bullets and independent SQL agreed: 566 records/312 people with positive hours, 546 records/353 people without. | Pass. |
| R03 | After R02: `Correction: drop the OFF shift and day-type filters. Compare Authorized record counts on Sep 3 versus Sep 6, 2026. Separately, across all available dates, total positive worked hours by country. Use two Markdown tables and say which date has more Authorized records.` | SQL kept the two branches independent and counted 89 versus 78 Authorized records correctly. It cast the country hour sums to `bigint` for a UNION, so browser rows showed Uganda 21 instead of 21.47 and Yemen 15,681 instead of 15,681.14. Read-only sums established the lost decimals. | Fail before precision guidance. |
| R04 | After R03: `Those country hours look rounded. Give the positive worked-hour totals to two decimal places by country across all available dates. Drop the Authorized and two-date filters; only revise the country-hours report.` | SQL removed the status and date filters. Real table showed Burundi 29.00, Uganda 21.47, Yemen 15,681.14; independent sums matched. A fresh two-part browser replay after shared precision guidance also kept these decimals and the 89/78 counts. | Pass for observed correction and fresh replay. |
| R05 | After R04: `On Sep 6, 2026, by approval status count distinct people who actually attended (positive worked hours). Separately count all attendance records by status on that date. Two tables, then explain why those measures differ.` | SQL returned the right values: Authorized 5 people/78 records, Draft 119/280, Pending 165/210. Independent SQL found no duplicate employee/date/status groups and positive-record counts equaled positive-person counts. The answer incorrectly attributed the gap to possible duplicate records. Fresh browser replays after three prompt adjustments still made unsupported duplicate claims, once adding invented split-shift and swipe causes. Those prompt adjustments were not retained. | Fail: explanatory inference, counts pass. |
| R06 | After R05: `That explanation assumes duplicate records. For Sep 6, check whether any employee has more than one attendance record within a status, then revise the explanation of why the two measures differ. Keep it brief.` | SQL found no duplicate groups. The draft said so, but review inserted a contradictory multiple-record explanation. The correct cause on this snapshot is the positive-hours eligibility condition: within each status, all records and all people coincide, while only 5/119/165 records have positive hours. | Fail before attempted prompt guidance; exact follow-up not rerun afterward. |
| R07 | Fresh chat, exact N03 context: `Across all available dates, count distinct people by country. Separately, sum worked hours by work location for Engineering only. Present both as Markdown tables, then say which Engineering location has the most hours.` Then exact N04 follow-up: `For Engineering only, keep all dates and show the top 3 work locations by worked hours. Add each location’s share of all Engineering worked hours, and one brief takeaway. Do not repeat the country counts.` | First exact follow-up safe-failed after four attempts at `ROUND(double precision, integer)`. General PostgreSQL numeric-cast guidance was added and the app restarted. The next browser replay succeeded after one SQL retry, casting the entire percentage expression to numeric before rounding. Same-turn rows and independent totals gave Shift-Eng 868.61/24.07%, RTG-Eng 763.51/21.16%, QC-Eng 555.19/15.39% out of 3,608.21 Engineering hours. The answer omitted country counts and avoided a staffing or activity claim, though “workload” was a loose label for total hours. | Fail before SQL guidance; pass on observed rerun. |

On 2026-09-30, a fresh browser chat at `http://127.0.0.1:7860/` replayed
R05's fully spelled-out request with the configured `openai/gpt-5-nano` model.
The executed SQL used separate positive-hour distinct-employee and all-record
CTEs, grouped by status for September 6, then combined their six rows. The
tables again showed 5/78, 119/280, and 165/210. Neither CTE measured
positive-hour record counts or repeated employees. The final answer again
suggested that multiple records per employee explain the difference. A shared
schema/prompt example requiring those intermediate measurements was present
during this replay and did not change the behavior; that unverified prompt
change was removed. The production snapshot's independently checked
positive-hour record counts are 5, 119, and 165, so this remains a failed
explanation, not a verified fix. The runtime model change is commit `b85ec161`.

A representative nonempty swipe-time browser turn after the time guidance displayed
effective and device clock times as `08:18` and `15:41`, matching stored values
with `:00` seconds. The employee identifier and trace are intentionally omitted
here. The explicit **Clear conversation** control visibly emptied a pending chat
when activated by keyboard; a new count request then showed only its own turn and
returned the independently checked 74 Authorized records on September 2. Mouse
click replay on Clear was inconclusive in the automation surface.

One deliberately broad daily-department Markdown request was bounded in the
answer: it showed only part of 112 grouped rows and did not summarize every day.
That request is not a passing all-dates report. A more compact result or narrower
requested period is still needed for a complete response.

## September 30 count reconciliation replay

The local Gradio app was restarted after each code change. Same-turn executed
SQL was captured without storing private rows in this report. The database
still had 3,964 rows across September 1–7. Independent read-only aggregates
for September 6 found, by status, all records/all people/positive-hour
records/positive-hour people: Authorized 78/78/5/5, Draft 280/280/119/119,
and Pending For Authorization 210/210/165/165.

| ID | Exact prompt and preceding turn | Browser and SQL evidence | Status |
| --- | --- | --- | --- |
| R08 | Fresh chat: `On Sep 6, 2026, by approval status count distinct people who actually attended (positive worked hours). Separately count all attendance records by status on that date. Two tables, then explain why those measures differ.` | A model proposal used two matching date/status CTEs plus a UNION and presentation ORDER BY. The runtime rewrote it to one grouped SELECT with `COUNT(*)`, `COUNT(DISTINCT employee_id)`, and positive-hours FILTER counts. The browser showed two real tables with 5/78, 119/280, 165/210. Its final paragraph still speculated about multiple entries despite no duplicate within a status. | Counts and tables pass; explanation fails. |
| R09 | Fresh replay of R08 after review guidance | The same four-count grouped SQL executed and all six requested values matched independent aggregates. The browser correctly attributed gaps of 73, 161, and 45 records to nonpositive or missing worked hours, without a duplicate-record explanation. The model rendered the requested two tables as lists. | Explanation and counts pass; table format fails. |
| R10 | After R09: `That explanation assumes duplicate records. For Sep 6, check whether any employee has more than one attendance record within a status, then revise the explanation of why the two measures differ. Keep it brief.` | SQL grouped by status and employee on September 6 and checked for counts above one. The browser correctly reported no duplicates and attributed the gap to records without positive hours. | Pass for the requested correction. |

One intervening stochastic replay of R08 proposed positive-hour people for
Authorized only, but all-record counts for every status; the browser published
that asymmetric result. This remains a known failure. General planning guidance
was strengthened afterward, and R09 used matched scopes, but one passing run
does not prove that future model proposals will always preserve every group.
No wording-specific runtime condition was added for these questions.

## Sequential multi-query browser check

The first live run after adding typed multi-step planning failed before SQL:
the planner numbered two clauses `1, 2`, while the pipeline accepted only
`0, 1`. After normalizing either numbering convention, a fresh browser run of
the following exact question executed two independent read-only SQL steps:

`Compare Authorized record counts on Sep 3 versus Sep 6, 2026. Separately, across all available dates, total positive worked hours by country. Use two Markdown tables and say which date has more Authorized records.`

The first step grouped Authorized records by the two dates and returned
September 3 **89** and September 6 **78**. The second grouped positive worked
hours by country across all dates and returned Burundi **237.01**, Uganda
**187.47**, and Yemen **97,847.31**. The browser rendered both Markdown tables
and correctly identified September 3 as higher. An independent read-only
aggregate matched the worked-hour values. At this check, the database had
**21,418** rows dated August 1–September 7, 2026; do not compare its all-date
totals with the earlier 3,964-row snapshot. This is one observed success, not
a guarantee that every reference interpretation will decompose correctly.

After the shared-date and plan-retry review fixes, a fresh browser replay of
the same question again executed two separate SQL steps. It rendered two real
tables with the same 89/78 Authorized counts and 97,847.31/237.01/187.47
country-hour totals. The answer identified September 3 as higher and described
the all-date totals as bounded by the observed September 7 endpoint.

## October 1 multi-query acceptance replay

The local app was restarted from the current working tree. The browser
automation surface failed to initialize, so these submissions used the live
Gradio `/submit_chat` endpoint on `http://127.0.0.1:7860/`. The database still
had 21,418 attendance rows. The first two combined-count requests were answered
with one SQL execution each; their published totals matched independent
read-only aggregates: 18,229 Authorized out of 21,418 overall, and 565 records
on each of September 3 and 4, 2026.

For `Use separate queries for these independent requests, then give one answer:
(1) count Authorized attendance records on 2026-09-03; (2) total positive worked
hours by country across all available dates.`, the same-turn log showed one
`multi_plan` and two `sql_execution` completions. The answer gave **89** records
for the first part and **237.01**, **187.47**, and **97,847.31** positive hours
for Burundi, Uganda, and Yemen. All four values matched independent read-only
aggregates. The INFO log did not include SQL text, so SQL predicate scope was
not independently inspected in this replay.

After the shared read-only snapshot change, the app was restarted and the same
two-query request was submitted again. The log again showed one `multi_plan`,
two successful SQL executions, and reviewed publication. The answer repeated
the independently checked 89 record count and 237.01/187.47/97,847.31 country
totals. A direct read-only executor check also confirmed that two queries can
run on one connection and that a failed query rolls back to its savepoint so
the next query succeeds.

After removing the date-literal guard and requiring the final reviewer to
account for every scope clause, another restarted live run again produced two
SQL executions and the same independently verified values.

## October 1 continuous chat and scope check

These three questions were submitted in order through the live Gradio
`/submit_chat` endpoint on `http://127.0.0.1:7860/`, with one persistent chat
session. This was an endpoint conversation, **not a browser UI check**. The app
was running code from `3e33174853692c81d384ca0d00843f5d58149ebb`.
The database snapshot had 21,418 attendance rows. Independent read-only
aggregates for September 3 found 565 records: 89 Authorized, 238 Draft, and
238 Pending For Authorization. The Retrieved Context JSON supplied the scalar
rows below; the same-turn executed SQL was unavailable, so SQL predicate scope
is unverified.

| ID | Exact prompt and preceding turns | Published answer and row check | Status |
| --- | --- | --- | --- |
| C01 | New chat: `How many attendance records are there on 2026-09-03?` | Answer and result row gave **89**, labeled Authorized. The requested all-status count is **565**. | **Fail**: the answer narrowed the population without a requested status filter; same-turn SQL unavailable. |
| C02 | After C01: `I meant all statuses, not just Authorized. What is the total record count on 2026-09-03?` | Answer and result row gave **565**, matching the independent all-status count. | **Pass** for the numeric answer; SQL scope unverified. |
| C03 | After C02: `How many of those are Draft?` | Answer and result row gave **238** Draft records, matching the independent aggregate. | **Pass** for the numeric answer; SQL scope unverified. |

The reviewer prompt now includes a general example requiring a new query when
an unrequested workflow-status filter narrows an attendance count. Answer and
review guidance also distinguish accessible records from the `Authorized`
workflow status. Three separate direct pipeline reproductions of C01 after the
first prompt edit executed date-only SQL and returned 565. A later direct pair
after both edits executed date-only SQL for C01 (565) and date-plus-Authorized
SQL for the neighboring explicit-status question (89). These are independent
reproductions, not the endpoint turns' exact SQL and not browser verification.

The Codex browser control failed to initialize (`failed to write kernel
assets: The system cannot find the path specified`). A fallback launch of a
local headless Chrome browser was rejected by automatic policy review as
blocked by policy. Browser display, Clear behavior, and post-edit browser
retest therefore remain **unverified** for this session. The running app was
not restarted after the prompt edits, so the endpoint conversation above
does not validate those edits in the Gradio process.

## Add a new manual case

Record the exact prompt and preceding turns, code commit, data snapshot or
coverage, visible answer, SQL or row evidence used to check it, and whether it
passed. For a long conversation, note when a correction should replace an old
employee, date, metric, or category. Before adding a case, compare its intent and
scope with M01–M03 and H01–H04; choose a different combination when possible.
Do not call a case correct because its wording looks plausible or an evaluator
reports a phrase match. Check the answer's numbers against the executed rows and
the SQL filters against the question.
