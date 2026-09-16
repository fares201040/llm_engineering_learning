# APDC continuation request after Task 3

You are resuming an approved, independent APDC conversational-intelligence
review in this repository.

## Repository state

- Working directory: `D:\projects\llm_engineering_ed_donner\llm_engineering`
- Branch: `main`
- Do not push.
- Original review baseline: `0f3d02243973d29954f6eb4b5a10e9c1fe2d686d`
- Accepted Task 3 HEAD: `935b125f`
- Tasks 1, 2, and 3 are complete.
- Continue with Tasks 4 through 8 only. Do not redo completed work.

## Read these sources before acting

1. `docs/superpowers/specs/2026-09-16-apdc-independent-conversational-intelligence-review.md`
2. `docs/superpowers/plans/2026-09-16-apdc-independent-conversational-intelligence-review.md`
3. `.superpowers/sdd/2026-09-16-apdc-independent-conversational-intelligence-review/progress.md`
4. `docs/superpowers/reports/2026-09-16-apdc-conversational-intelligence-review.md`
5. `.superpowers/sdd/2026-09-16-apdc-independent-conversational-intelligence-review/task-3-report.md`
6. `week5/new_implementation/APDC_Attendance_Remaining_Improvements_16_to_35.md`
7. For Task 7, `week5/new_evaluation/BIRD_METRIC.md` and the evaluator entry points.

Treat the approved spec and plan as binding. Preserve the Task 3 typed aggregate
boundary: no raw user question, SQL, schema, provider output, secrets, or
private values in the aggregate provider payload; request-local typed candidate
IDs only; deterministic count is zero-provider-call; compiler-owned
parameterized SQL remains authoritative; semantic conflicts and unresolved
external ambiguity fail closed but confirmed clarification must resume.

## Required execution model

Use subagent-driven development sequentially:

- Implementation, debugging, focused regression creation, and verification:
  fresh `gpt-5.6-luna` with reasoning `max`.
- Independent review of every implementation/fix range:
  fresh `gpt-5.6-sol` with reasoning `medium`.
- Do not run parallel implementers against shared files.
- Do not let subagents spawn their own subagents.
- Resolve every Critical or Important review finding. A task is complete only
  after a fresh scoped Sol-medium review returns clean.
- Maximum five fix rounds per task; at rounds 4 and 5 use a fresh Luna-max
  implementer.

## Test boundary

Do not run any pre-existing test file or suite during Tasks 4-7. Use only newly
added focused regressions, minimal manual/public probes, import/compile checks,
and `git diff --check`. The pre-existing focused suites are reserved for the
single Task 8 final regression gate. If that one-time gate fails, do not rerun
it; diagnose from its captured output, add/run only a new focused regression,
and report final-gate versus post-gate evidence separately.

Do not change expected answers merely to make tests pass. Do not modify
`week5/new_implementation/planning_decisions.py` unless an explicit,
documented architectural necessity is proven; the final gate must compare it
against the original baseline.

## Remaining work

### Task 4 — Arabic scalar scope

Add focused RED tests for direct and compound Arabic scalar answers with
distinct synthetic employees/periods. Append locale-appropriate scope derived
only from the verified plan to all Arabic scalar aggregate branches (count,
distinct count, percentage, sum, average, min, max). Re-run only the new class
and the affected Task 2 manual turns. Obtain clean Sol-medium review, then
commit `fix: retain scope in Arabic aggregate answers`.

### Task 5 — grouped-result cell safety

Add English/Arabic RED tests with `Ops | North` and `Remote\nAnnex`. Create
one shared deterministic Markdown cell formatter that collapses CR/LF and
escapes pipes for all grouped labels without changing numeric formatting.
Obtain clean review and commit
`fix: escape grouped attendance result cells`.

### Task 6 — remaining Task 2 defects

Adjudicate every numbered `D<N>` in the main report before editing. For every
confirmed root cause, add a focused public-API RED test, apply the smallest
owning-layer correction, run only that regression plus the minimum affected
conversation sequence, verify typed state/provider calls/privacy/parameter
shape/atomicity, obtain independent review, and create one commit per defect or
tightly coupled root cause. Record unsupported, probe-error, or unavailable
items explicitly; discard nothing silently.

### Task 7 — offline quality and BIRD/Contract EX

Inspect commands and fixture availability without executing existing suites,
then run the authorized representative offline evaluation once. Record case
counts and all requested answer-quality/execution/contract metrics. Manually
disposition every weak case. Route genuine defects back through the Task 6
RED/GREEN/review workflow. Never invent unavailable scores.

### Task 8 — one-time final gate

Run the relevant existing APDC focused suites exactly once, then Ruff check,
Ruff format check, compile/import checks, whitespace/scoped-diff checks,
`planning_decisions.py` comparison, final whole-branch Sol-medium review, and
complete the main report. Any post-gate fix uses Luna-max, a new focused
regression only, an affected manual probe, and a fresh Sol-medium review; do
not rerun the existing suites. Commit the completed report and leave the
worktree clean. Do not push.

## Completed Task 3 evidence

Task 3 commits are:

- `9a436fc2` — typed aggregate decision boundary
- `3bce01bd` — prompt privacy/count hardening
- `f46cf59e` — typed-only provider context
- `81f27a51` — fail-closed semantic conflicts
- `935b125f` — resumable clarification and explicit conflicts

The final focused Task 3 class passed 15/15 tests; syntax/import and diff checks
passed; no pre-existing suite was run; `planning_decisions.py` was unchanged.
A fresh Sol-medium acceptance review returned PASS with no Critical, Important,
or Minor findings.

Start by confirming branch/HEAD/worktree status and reading the listed
documents. Then execute Task 4 exactly as planned, continuing autonomously
through Task 8 unless a genuine authorization/data blocker requires user input.
