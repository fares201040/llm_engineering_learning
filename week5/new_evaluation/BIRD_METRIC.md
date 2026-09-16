# APDC BIRD-Style Contract Execution Accuracy

The repository did not define a BIRD metric before this implementation. The
authoritative BIRD-SQL benchmark reports Execution Accuracy (EX) by comparing
the executed result of predicted SQL with the executed result of gold SQL. It
also reports Reward-based Valid Efficiency Score (R-VES) for correct queries.

The private APDC corpus does not contain an authoritative gold-SQL and timing
contract. Consequently, this evaluator reports **BIRD-style Contract Execution
Accuracy (Contract EX)**, not an official BIRD leaderboard score, and does not
report R-VES.

## Local scoring contract

A case is eligible only when its `TestQuestion` contains at least one verified
output expectation:

- matched row count;
- deterministic calculation;
- normalized result;
- record identity; or
- grouped values.

The evaluator runs the existing grounded `fetch_context` execution seam once,
without answer generation or multi-turn evaluation. Each applicable component
receives 1 for a match or 0 for a mismatch. Record identifiers and grouped rows
require exact set equality, so unexpected extra results fail. Calculation and
normalized-result contracts compare all explicitly verified output fields;
extra execution metadata is ignored. The case Contract EX is 1 only when every
applicable component matches; otherwise it is 0. Overall Contract EX is the
arithmetic mean over cases that received a valid verdict.

Cases without verified output expectations and cases whose contract expects a
controlled non-execution are skipped. Evaluator or infrastructure failures are
reported separately and excluded from the scored denominator. If no case
receives a verdict, the score is unavailable rather than zero.

## Efficiency and caching

Successful verdicts are cached in process using a bounded cache keyed by the
metric version, verified dataset fingerprint, and complete case fingerprint.
Changing any of those inputs causes recomputation. Skips and failures are not
cached. Analysts can explicitly bypass the cache from the BIRD tab.

## Privacy boundary

The dashboard exposes the analyst-visible `Question`, category, boolean
component verdicts, score, status, safe cause code, and cache status. It does
not expose employee records, generated or reference answers, prompts, plans,
SQL, parameters, retrieval documents, conversation history, credentials, or
raw exception text.
