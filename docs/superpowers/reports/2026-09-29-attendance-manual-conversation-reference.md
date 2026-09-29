# Attendance manual conversation reference

Updated: 2026-09-29

Code baseline: `e5e9dadc3b0ed0fd3cfc6b9370ce7df8840461db`

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

## Add a new manual case

Record the exact prompt and preceding turns, code commit, data snapshot or
coverage, visible answer, SQL or row evidence used to check it, and whether it
passed. For a long conversation, note when a correction should replace an old
employee, date, metric, or category. Before adding a case, compare its intent and
scope with M01–M03 and H01–H04; choose a different combination when possible.
Do not call a case correct because its wording looks plausible or an evaluator
reports a phrase match. Check the answer's numbers against the executed rows and
the SQL filters against the question.
