# APDC Conversational-Intelligence Review

## Architecture and evidence ledger (Task 1)

This report is the privacy-safe evidence foundation for the independent APDC
conversational-intelligence review. It records the production path and defines
the observation contract that later manual turns and compound cases must use.

### Baseline

- Repository: APDC workspace (local checkout).
- Branch observed: `main`.
- Starting HEAD observed for this task: `3b42e1a071dee3ee33aa94630d1c1f7c97ecf609`.
- The approved starting commit in the review specification is
  `0f3d02243973d29954f6eb4b5a10e9c1fe2d686d`.
- Required `git status --short` output was empty (clean worktree).
- Required planning-file log output was exactly:

  ```text
  1547a2a4 feat: answer grounded employee profile questions
  a3934f6c refactor: bound provider planning decisions
  02ab3ca6 feat: assemble plans from deterministic facts
  ```

- `week5/new_implementation/planning_decisions.py` had no diff from the
  approved starting commit to the observed HEAD.
- This task does not run an existing test file or suite and does not use live
  or private conversational fixtures.

### Production call path

The arrows below are exact source transitions. `T` means a server-owned,
typed/trusted transition; `P` means a provider boundary; `X` means a possible
failure/early-exit boundary. Values in this map are registry or type names,
not employee values.

1. **UI state and public ingress (T/X).**
   `week5/new_app.py:49-74` takes the last user message and prior UI history,
   passes `ConversationState` plus `LOCAL_DEMO_ACCESS` to
   `answer_question_with_state`, appends the returned assistant text, and
   renders returned context through escaped HTML (`week5/new_app.py:27-40`). `week5/new_app.py:93` creates
   the typed Gradio state; `week5/new_app.py:121-129` wires submit to the
   stateful chat handler. The UI catch at `week5/new_app.py:63-71` returns a
   locale-safe generic failure and preserves the prior state.

2. **Public state boundary (T/X).**
   `week5/new_implementation/answer.py:7415-7450` copies the incoming state,
   opens `decision_budget_scope`, delegates to the internal answer path, and
   restores the trace context. Conversation-contract failures return help with
   the original state (`:7431-7436`); all other failures return a generic
   attendance failure with the original state (`:7437-7445`).

3. **Length, locale, access, and preflight routing (T/X).**
   `answer.py:7462-7482` copies/promotes typed pending state, enforces the
   input bound, derives the reply locale, checks `AccessContext`, and calls
   `conversation.conversation_preflight_route` (`:7470-7478`). Protected,
   social, and unrelated messages return immediately without planning or
   retrieval (`:7471-7477`). Unsupported/invalid date or domain input exits
   safely (`:7478-7482`). The route recognizer is
   `conversation_understanding.py:532-564`; it covers protected prompts,
   social-only text, unrelated text, and the fall-through attendance route.

4. **Pending clarification/resumption gate (T/X).**
   Employee-clarification and compound-clarification resumes are selected at
   `answer.py:7484-7511`. Context, meaning, catalog, and employee choices are
   parsed from typed pending state at `:7798-8030` and `:8031-8260`; invalid or
   stale choices return a bounded clarification/help response without a query.
   Pending records are typed in
   `language_understanding.py:231-364` and state-owned in
   `answer.py:589-602`.

5. **Surface facts and conversation decision (T/P/X).**
   For a new contextual/compound message,
   `answer.py:7513-7600` detects semantic facts with
   `semantic_resolution.detect_semantic_facts` and surface facts, then checks
   `conversation.needs_conversation_decision` (`:7524-7532`). Employee mentions
   are resolved locally first (`:7532-7555`, `:6747-6775`). If needed,
   `conversation.build_conversation_request` (`:7586-7599`) creates fresh
   opaque request-local IDs and bounded choices; the provider call is
   `conversation.request_conversation_decision` (`:7600`). Its strict contract
   and source-span/candidate validation are
   `conversation_understanding.py:353-495`; the prompt contract is
   `:992-1035`; one bounded provider attempt and validation are `:1038-1073`.
   Ambiguous context becomes a typed context clarification
   (`answer.py:7603-7620`); invalid decisions return conversation help
   (`:7716-7717`).

6. **Conversation materialization and referent binding (T/X).**
   Provider-selected opaque choices are mapped back to server-owned facts,
   employees, prior units, and views by
   `conversation_understanding.py:682-890`. The materializer rejects unknown
   choices, low-confidence facts, invalid relations, contradictory constraints,
   out-of-order/overlapping spans, and unsafe same-turn references. Employee
   source spans are re-resolved against the authorized directory at
   `answer.py:6827-6873`; a non-unique result stores an employee clarification
   (`:7622-7665`). Attendance units proceed to a single request or to the
   compound preparation path (`:7666-7712`).

7. **Deterministic semantic facts and date/candidate resolution (T).**
   Surface normalization and locale/candidate detection are
   `language_understanding.py:805-888`, with exact/localized aliases and only
   dominant fuzzy acceptance (`:891-928`). Registry-driven fields, filters,
   entities, dates, predicates, measures, calculations, grouping, unsupported
   markers, and result shape are detected by
   `semantic_resolution.py:2405-2600`; strong/candidate facts are merged at
   `:2901-2924`. Relative dates are converted to typed `Date` filter
   conditions by `answer.py:1212-1374`.

8. **Clarification exits before executable planning (T/X).**
   The preparation helper at `answer.py:4499-4909` resolves employee/profile
   references and bounded live catalog candidates, emits typed employee,
   semantic-meaning, catalog-value, or missing-intent clarifications, and
   rejects unsupported capability markers. Clarification state is written as a
   `PendingRequestFrame` by `answer.py:5940-5959` and the typed stores at
   `:6134-6320`; only the clarification text is returned.

9. **Proposal assembly (T/P/X).**
   `answer.py:4910-4981` calls `propose_query`. The planner draft and
   fact-to-proposal assembly are
   `planning_decisions.py:98-105` and `:194-384`; only verified semantic facts
   can become filters, names, measures, predicates, calculations, grouping,
   projection, order, limit, or result intent. The dormant provider-planning
   branch is `answer.py:1474-1530` and is entered only when a draft has needs;
   the current `build_planning_draft` emits no provider-owned finite needs.
   Unsupported or ambiguous proposals exit before execution
   (`answer.py:4962-4981`).

10. **Trusted plan compilation and revalidation (T/X).**
    `plan_compiler.py:770-1126` canonicalizes proposal filters against the
    resolver registry, records constraint provenance, derives the answer
    contract, and runs schema, grounding, coverage, contradiction, capability,
    answer-contract, and executable-choice invariants. The invariant families
    are at `:158-245`, `:314-454`, and `:504-550`. A ready plan is revalidated
    again at `plan_compiler.py:1129-1156`; any violation produces no executable
    plan. Employee resolution may add a trusted identity filter and the exact
    path adds the deterministic daily attendance scope at
    `answer.py:5041-5107`.

11. **Compiled query preparation (T/X).**
    `answer.py:3134-3182` prepares profile, aggregation, count, sample, and
    optional coverage artifacts from the executable plan. PostgreSQL compilation
    is pure and parameterized: `postgres_compiler.py:72-143` validates the
    temporary generated-aggregate logical choice; `:170-228` validates fields,
    operators, and typed parameters; query builders are at `:231-238`,
    `:288-334`, `:365-411`, `:414-443`, `:446-485`, and `:488-496`.
    The generated aggregate fallback is explicitly advisory and is validated
    before trusted compilation (`answer.py:906-1024`, `:1053-1139`); raw model
    SQL is never passed directly to execution.

12. **Multi-unit preparation and execution seam (T/X).**
    `_prepare_turn` at `answer.py:7194-7299` prepares every attendance unit
    with `preparation_only=True`, retaining blockers and returning a
    `PreparedTurn` only when all units are ready. `_answer_compound_turn`
    (`:7321-7354`) refuses execution when any blocker exists. The executor
    `answer.py:5462-5536` opens one read-only/repeatable-read resource set and
    executes the already prepared requests. Exact PostgreSQL execution
    (`answer.py:3185-3250`) accepts only `ExecutableQueryPlan` plus
    `CompiledPostgresQuery` artifacts, executes bound parameters in the read-only
    snapshot, and returns result metadata/chunks. Chroma exact and semantic or
    hybrid retrieval are selected in `answer.py:5334-5432`.

13. **Successful state commit and rendering (T/X).**
    `_answer_compound_turn` renders every prepared result, builds one
    `AttendanceUnitFrame` per unit, clears pending state, and calls
    `_store_successful_turn` at `answer.py:7354-7412`. The single-unit path
    renders first and commits the frame/result snapshot at `:8560-8603`.
    Bounded state retention, referent refresh/eviction, and result snapshots are
    `answer.py:6011-6131`. Deterministic rendering is preferred in
    `_answer_from_context` (`answer.py:5648-5755`): multi-employee views and
    profiles (`:5660-5670`), escaped projection rows (`:5672-5703`),
    aggregations (`:5705-5713`), and exact count/sample summaries
    (`:5715-5731`). Only a remaining semantic narrative path calls the final
    provider only after the deterministic branches fall through (`:5733-5755`);
    this is not limited to semantic-narrative plans. Messages are assembled at
    `:5600-5645`.

### Provider and trust boundaries to verify later

- Conversation decisions receive the current message, typed fact kinds/source
  spans, opaque employee/prior-unit/view choices, and bounded prior result
  shape/unit (`conversation_understanding.py:992-1035`). They do not receive
  raw history or an employee directory in that payload.
- The generated aggregate request receives a redacted question, one grounded
  operation, a logical table name, allowed aggregate forms, and bounded field
  candidates (`answer.py:833-870`). Its SQL-shaped response is advisory and
  must be reduced to a logical choice before trusted compilation.
- The reranker (`answer.py:3325-3398`) and final narrative answer
  (`answer.py:5600-5755`) currently receive selected record metadata/content.
  This is a review concern against the strict provider-input invariant and
  requires an explicit privacy decision in a later task; no raw record values
  are copied into this ledger.
- Provider-call counts must be measured by instrumentation around every provider
  completion/API call site, in addition to budgeted `claim_provider_call()`
  events. The completion sites are conversation decisions
  (`conversation_understanding.py:1048-1056`), the dormant planner decision
  (`answer.py:1459-1471`), generated-aggregate attempts (`answer.py:918-1004`),
  reranking (`answer.py:3325-3398`), and final answer generation
  (`answer.py:5744-5755`). If pgvector is enabled, the embedding API call at
  `answer.py:3272-3280` must also be counted; if Chroma semantic retrieval is
  enabled, count its embedding API call at `answer.py:2444-2448` as well. Count
  attempted provider calls separately from budget claims and record only the
  integer count; never record payloads. Grounded deterministic requests should
  remain at zero provider completions.

### Privacy-safe observation schema

Every later representative turn must record exactly these fields, and only
these fields:

```text
turn, route, normalized-shape, locale, referent-counts, fact-kind/origin pairs,
employee-scope cardinality, filter field/operator/type triples, date bounds,
field candidate IDs, operation, clarification kind, executable-plan shape,
compiled expression identifier, parameter count/types, result shape/count,
provider-call count, render language, and pass/fail rationale
```

Schema rules:

- `normalized-shape` is a structural descriptor or redacted shape, never the
  raw question or a reversible transcript.
- `fact-kind/origin pairs` use type/origin labels only (for example,
  `filter/question` or `measure/trusted_state`).
- `referent-counts`, employee-scope cardinality, result counts, and provider
  counts are integers; do not record identities.
- `filter field/operator/type triples` record registry field, operator, and
  storage type only; omit every parameter/value.
- `date bounds` may record only bounded date endpoints needed for coverage
  assessment; do not include unrelated message text.
- `field candidate IDs` are request-local opaque IDs only; do not record their
  labels or mapped values.
- `compiled expression identifier` is a non-reversible compiler fingerprint or
  registry expression identifier; never record SQL text.
- `parameter count/types` record count and storage/Python types only, never
  parameter values.
- `result shape/count` records scalar/grouped/rows/narrative and counts only;
  no row contents.
- Explicitly prohibited from this ledger: raw names, employee IDs, private
  identifiers, question history, parameter values, SQL text, DSNs, exceptions,
  prompts, provider payloads, local or physical directories, unrestricted schema
  dumps, and raw retrieved records. Only non-reversible request-local candidate
  IDs required by this schema may appear, and they must never encode private
  identifiers.

### Ledger template

| turn | route | normalized-shape | locale | referent-counts | fact-kind/origin pairs | employee-scope cardinality | filter field/operator/type triples | date bounds | field candidate IDs | operation | clarification kind | executable-plan shape | compiled expression identifier | parameter count/types | result shape/count | provider-call count | render language | pass/fail rationale |
|---:|---|---|---|---:|---|---:|---|---|---|---|---|---|---|---|---|---:|---|---|
| T-000 | pending | `<shape>` | `<en/ar>` | `<n>` | `<pairs>` | `<n>` | `<triples>` | `<bounds/none>` | `<opaque IDs/none>` | `<operation>` | `<kind/none>` | `<shape/none>` | `<fingerprint/none>` | `<n/types>` | `<shape/count>` | `<n>` | `<en/ar>` | `<pass/fail + bounded rationale>` |

Later tasks must append turn-level rows and compound-unit rows using this
template, along with manual rationale for every imperfect or unsupported case.

## Task 2 manual assessment: turns T-01–T-50

The following is the manual judgment of the completed structural evidence. It
records one canonical row per turn; the ignored probe report repeats Batch 5
verbatim, so the duplicate rendering is not counted twice. The table uses
structural labels only. In the compact notation, refs a/p/r are active,
prior, and retained referent counts; prep is prepared-context count; exec u/t
is executed-unit/turn delta; fp means an opaque compiler fingerprint was
present (the fingerprint itself is intentionally omitted); params records only
typed parameter shape; id-flag is the synthetic-fixture marker; and internal
is the internal-term marker. q, trusted, user, and default are fact origins.
All candidate values are none in this run.

| turn | route / shape / locale→render | refs a/p/r; scope | facts; filters; bounds; candidates | op; plan; fp; params | clarification; result; provider | prep; exec u/t; state; leakage | assessment |
|---:|---|---|---|---|---|---|---|
| T-01 | attendance / direct-date / en→en | 1/0/1; 1 | entity/q, filter/q+trusted, measure/q, predicate/q; date=eq/date, measure=gt/number, employee=eq/text, chunk=eq/text; day; none | distinct-count; exact/scalar; present; typed×4 | none; scalar/1; 0 | 1; 1/0; changed; id-flag=1, internal=0 | PASS — explicit scoped scalar completed. |
| T-02 | attendance / direct-month / en→en | 1/1/2; 1 | calculation/q, entity/q, field/q, filter/q+trusted; date=gte/date, date=lte/date, employee=eq/text, chunk=eq/text; month; none | sum; exact/scalar; present; typed×4 | none; scalar/10; 0 | 1; 1/0; changed; id-flag=1, internal=0 | PASS — explicit period calculation completed. |
| T-03 | attendance / projection / en→en | 1/2/2; 1 | entity/q, field/q, filter/trusted, projection/q, result-shape/q; employee=eq/text, chunk=eq/text; none; none | none; exact/scalar; present; typed×2 | none; rows/13; 0 | 1; 1/0; changed; id-flag=0, internal=0 | PASS — scoped projection returned rows. |
| T-04 | attendance / filtered-count / en→en | 1/3/2; 0 | entity/q, field/q, filter/trusted, projection/q, result-shape/q; none; none; none | none; none; none; none | none; none; 0 | 1; 0/0; same; id-flag=0, internal=0 | FAIL — supported filtered request has no plan, result, or clarification. |
| T-05 | attendance / average / en→en | 1/3/2; 1 | calculation/q, entity/q, field/q, filter/q+trusted; date=gte/date, date=lte/date, employee=eq/text, chunk=eq/text; month; none | average; exact/scalar; present; typed×4 | none; scalar/10; 0 | 1; 1/0; changed; id-flag=1, internal=0 | PASS — scoped average completed. |
| T-06 | attendance / pronoun-follow-up / en→en | 1/4/2; 0 | calculation/q, entity/q, field/q, filter/q+trusted; none; none; none | none; none; none; none | none; none; 0 | 1; 0/0; same; id-flag=0, internal=0 | FAIL — pronoun referent is not materialized into a plan. |
| T-07 | attendance / short-follow-up / en→en | 1/4/2; 0 | calculation/q, entity/q, field/q, filter/q+trusted; none; none; none | none; none; none; none | none; none; 1 | 0; 0/0; same; id-flag=0, internal=0 | FAIL — one conversation call still yields no executable follow-up. |
| T-08 | attendance / those-days / en→en | 1/4/2; 0 | calculation/q, entity/q, field/q, filter/q+trusted; none; none; none | none; none; none; none | none; none; 1 | 0; 0/0; same; id-flag=0, internal=0 | FAIL — temporal referent is neither bound nor clarified. |
| T-09 | attendance / previous-result-period / en→en | 1/4/2; 0 | calculation/q, entity/q, field/q, filter/q+trusted; none; none; none | none; none; none; none | none; none; 0 | 0; 0/0; same; id-flag=0, internal=0 | FAIL — prior-result period does not produce a plan or targeted clarification. |
| T-10 | attendance / employee-ambiguity / en→en | 1/4/2; 1 | entity/q, measure/q, predicate/q; none; none; none | none; none; none; none | employee-selection; none; 0 | 1; 0/0; changed; id-flag=1, internal=0 | PASS — bounded employee clarification with no execution. |
| T-11 | attendance / employee-choice-resume / en→en | 1/4/2; 1 | entity/q, filter/q+user, measure/q, predicate/q; date=gte/date, date=lte/date, measure=gt/number, employee=eq/text, chunk=eq/text; month; none | distinct-count; exact/scalar; present; typed×5 | none; scalar/2; 0 | 1; 1/0; changed; id-flag=1, internal=0 | PASS — selected clarification resumes with verified scope. |
| T-12 | attendance / grounded-shorthand / en→en | 1/5/2; 0 | entity/q, filter/q+user, measure/q, predicate/q; none; none; none | none; none; none; none | none; none; 0 | 0; 0/0; same; id-flag=0, internal=0 | FAIL — grounded shorthand avoids clarification but silently produces no answer. |
| T-13 | attendance / same-person-she / en→en | 1/5/2; 0 | entity/q, filter/q+user, measure/q, predicate/q; none; none; none | none; none; none; none | none; none; 0 | 0; 0/0; same; id-flag=0, internal=0 | FAIL — same-person referent is lost. |
| T-14 | attendance / relative-scope / en→en | 1/5/2; 0 | entity/q, filter/q+user, measure/q, predicate/q; none; none; none | none; none; none; none | none; none; 1 | 0; 0/0; same; id-flag=0, internal=0 | FAIL — relative period follow-up stalls after a provider call. |
| T-15 | attendance / context-profile / en→en | 1/5/2; 0 | entity/q, filter/q+user, measure/q, predicate/q; none; none; none | none; none; none; none | none; none; 0 | 0; 0/0; same; id-flag=0, internal=0 | FAIL — grounded context profile has no executable plan. |
| T-16 | attendance / named-correction / en→en | 1/5/2; 1 | filter/trusted, measure/default, predicate/q; measure=gt/number, employee=eq/text, chunk=eq/text; none; none | distinct-count; exact/scalar; present; typed×3 | none; scalar/11; 0 | 1; 1/0; changed; id-flag=1, internal=0 | PASS — named correction replaces prior scope and completes. |
| T-17 | attendance / period-correction / en→en | 1/6/2; 0 | filter/trusted, measure/default, predicate/q; none; none; none | none; none; none; none | none; none; 1 | 0; 0/0; same; id-flag=0, internal=0 | FAIL — period correction is not materialized. |
| T-18 | attendance / meaning-correction / en→en | 1/6/2; 1 | field/q, unsupported/q; none; none; none | none; none; none; none | semantic-interpretation; none; 0 | 1; 0/0; changed; id-flag=0, internal=0 | FAIL — explicit supported meaning correction stalls at semantic clarification. |
| T-19 | attendance / employee-confirmation / en→en | 1/6/2; 1 | entity/q, measure/q; none; none; none | none; none; none; none | employee-selection; none; 0 | 1; 0/0; changed; id-flag=1, internal=0 | PASS — bounded confirmation preserves safety before resume. |
| T-20 | attendance / clarification-resume / en→en | 1/6/2; 1 | entity/q, filter/user, measure/q; employee=eq/text, chunk=eq/text; none; none | distinct-count; exact/scalar; present; typed×2 | none; scalar/13; 0 | 1; 1/0; changed; id-flag=1, internal=0 | PASS — clarification resume completes with scoped count. |
| T-21 | social / topic-social / en→en | 1/7/2; 0 | entity/q, filter/user, measure/q; none; none; none | none; none; none; none | none; none; 0 | 0; 0/0; same; id-flag=0, internal=0 | PASS — social route avoids planning and retrieval. |
| T-22 | unrelated / topic-unrelated / en→en | 1/7/2; 0 | entity/q, filter/user, measure/q; none; none; none | none; none; none; none | none; none; 0 | 0; 0/0; same; id-flag=0, internal=0 | PASS — unrelated route avoids planning and retrieval. |
| T-23 | attendance / older-result / en→en | 1/7/2; 1 | entity/q, filter/user, measure/q; none; none; none | none; none; none; none | missing-intent; none; 0 | 1; 0/0; changed; id-flag=1, internal=0 | FAIL — older result is reduced to missing intent instead of resumption. |
| T-24 | attendance / compound-two / en→en | 2/7/2; 2 | entity/q, filter/trusted, measure/q, predicate/q; unit filters measure=gt/number+employee=eq/text+chunk=eq/text and employee=eq/text+chunk=eq/text; none; none | distinct-count + count; exact/scalar×2; present×2; typed×3 + typed×2 | none; scalar/12 + scalar/13; 1 | 2; 2/1; changed; id-flag=1, internal=0 | PASS — all units prepare before one compound execution. |
| T-25 | attendance / prior-ambiguity / en→en | 2/9/2; 0 | entity/q, filter/trusted, measure/q, predicate/q; none; none; none | none; none; none; none | none; none; 0 | 0; 0/0; same; id-flag=0, internal=0 | FAIL — ambiguous prior reference has neither targeted clarification nor plan. |
| T-26 | attendance / context-choice-resume / en→en | 2/9/2; 2 | entity/q, filter/trusted, measure/q, predicate/q; none; none; none | none; none; none; none | missing-intent; none; 0 | 1; 0/0; changed; id-flag=0, internal=0 | FAIL — context choice does not resume the pending compound context. |
| T-27 | attendance / result-correction / en→en | 2/9/2; 2 | entity/q, filter/trusted, measure/q, predicate/q; none; none; none | none; none; none; none | missing-intent; none; 0 | 0; 0/0; same; id-flag=0, internal=0 | FAIL — result correction is ignored while historical context remains active. |
| T-28 | attendance / topic-return / en→en | 2/9/2; 2 | entity/q, filter/trusted, measure/q, predicate/q; none; none; none | none; none; none; none | missing-intent; none; 1 | 1; 0/0; same; id-flag=0, internal=0 | FAIL — topic return triggers another no-plan conversation decision. |
| T-29 | attendance / long-distance / en→en | 2/9/2; 2 | entity/q, filter/trusted, measure/q, predicate/q; none; none; none | none; none; none; none | missing-intent; none; 0 | 1; 0/0; changed; id-flag=0, internal=0 | FAIL — long-distance referent recovery does not advance. |
| T-30 | attendance / period-correction-2 / en→en | 2/9/2; 2 | entity/q, filter/trusted, measure/q, predicate/q; none; none; none | none; none; none; none | missing-intent; none; 0 | 1; 0/0; changed; id-flag=0, internal=0 | FAIL — later period correction remains in missing-intent state. |
| T-31 | attendance / arabic-context / en→ar | 2/9/2; 2 | entity/q, filter/trusted, measure/q, predicate/q; none; none; none | none; none; none; none | semantic-interpretation; none; 0 | 1; 0/0; changed; id-flag=0, internal=0 | FAIL — locale metadata and rendered language disagree, with no plan. |
| T-32 | attendance / arabic-digits / ar→ar | 2/9/2; 0 | entity/q, filter/trusted, measure/q, predicate/q; none; none; none | none; none; none; none | none; none; 0 | 1; 0/0; same; id-flag=0, internal=0 | FAIL — numeral variant preserves locale but yields no executable answer. |
| T-33 | attendance / arabic-diacritics / ar→ar | 2/9/2; 2 | entity/q, filter/trusted, measure/q, predicate/q; none; none; none | none; none; none; none | semantic-interpretation; none; 0 | 1; 0/0; changed; id-flag=0, internal=0 | FAIL — diacritic variant is not normalized to a plan. |
| T-34 | attendance / mixed-employee / ar→ar | 1/9/2; 1 | entity/q, filter/q+trusted, measure/q; date=eq/date, employee=eq/text, chunk=eq/text; day; none | count; exact/scalar; present; typed×3 | none; unrecorded; 0 | 1; 1/0; changed; id-flag=0, internal=0 | LIMITED PASS — scope, locale, and execution align; result rendering is absent from structural evidence. |
| T-35 | attendance / mixed-units / ar→ar | 1/9/2; 0 | entity/q, filter/q+trusted, measure/q, predicate/q; none; none; none | none; none; none; none | none; none; 0 | 1; 0/0; same; id-flag=0, internal=0 | FAIL — mixed-language units are not split into executable units or clarified. |
| T-36 | attendance / spelling-confirmation / ar→ar | 1/9/2; 1 | entity/q, predicate/q; none; none; none | none; none; none; none | employee-selection; none; 0 | 1; 0/0; changed; id-flag=1, internal=0 | PASS — spelling ambiguity receives bounded employee selection. |
| T-37 | attendance / spelling-resume / en→ar | 1/9/2; 1 | filter/user, measure/default, predicate/q; exception=eq/text, employee=eq/text, chunk=eq/text; none; none | distinct-count; exact/scalar; present; typed×3 | none; unrecorded; 0 | 1; 1/0; changed; id-flag=0, internal=0 | FAIL — resume executes, but locale metadata is en while rendering is ar. |
| T-38 | attendance / arabic-attached / ar→ar | 1/9/2; 1 | entity/q; none; none; none | none; none; none; none | semantic-interpretation; none; 0 | 1; 0/0; changed; id-flag=0, internal=0 | FAIL — attached-conjunction form does not yield executable meaning. |
| T-39 | attendance / incomplete / ar→ar | 1/9/2; 1 | entity/q; none; none; none | none; none; none; none | semantic-interpretation; none; 0 | 0; 0/0; same; id-flag=0, internal=0 | PASS — incomplete phrase is safely held for semantic clarification. |
| T-40 | attendance / arabic-abbreviation / ar→ar | 1/9/2; 1 | entity/q; none; none; none | none; none; none; none | semantic-interpretation; none; 0 | 0; 0/0; same; id-flag=0, internal=0 | LIMITED PASS — locale/scope and bounded clarification are safe; exact abbreviation support is not observable. |
| T-41 | attendance / compound-two-calc / en→en | 1/9/2; 1 | entity/q, filter/trusted, measure/q, predicate/q; unit filters measure=gt/number+employee=eq/text+chunk=eq/text and employee=eq/text+chunk=eq/text; none; none | distinct-count + count; exact/scalar×2; present×2; typed×3 + typed×2 | none; unrecorded; 1 | 2; 2/1; changed; id-flag=1, internal=0 | LIMITED PASS — preparation/execution order is atomic; per-unit rendering is not recorded. |
| T-42 | attendance / compound-three-calc-arabic / ar→ar | 2/10/2; 2 | entity/q, filter/trusted, measure/q; employee=eq/text+chunk=eq/text per unit; none; none | count×3; exact/scalar×3; present×3; typed×2 per unit | none; unrecorded; 1 | 3; 3/1; changed; id-flag=0, internal=0 | LIMITED PASS — three-unit atomic order and locale align; result text is not recorded. |
| T-43 | attendance / compound-employees-periods / en→en | 2/12/2; 2 | entity/q, filter/q+trusted, measure/q, predicate/q; date=eq/date+measure=gt/number+employee=eq/text+chunk=eq/text per unit; day; none | distinct-count×2; exact/scalar×2; present×2; typed×4 per unit | none; unrecorded; 1 | 2; 2/1; changed; id-flag=1, internal=0 | LIMITED PASS — both employee/period units prepare and execute atomically; rendering is not recorded. |
| T-44 | attendance / compound-supported-unsupported / en→en | 2/13/2; 1 | entity/q, filter/trusted, measure/q, predicate/q; one supported unit shape; none; none | distinct-count; exact/scalar; present; typed×3 | none; none; 1 | 2; 0/0; same; id-flag=0, internal=0 | PASS — blocked compound executes nothing and preserves state. |
| T-45 | attendance / compound-valid-invalid / en→en | 2/13/2; 1 | entity/q, filter/trusted, measure/q, predicate/q; first-unit shape only; none; none | distinct-count; exact/scalar; present; typed×3 | semantic-interpretation; none; 1 | 2; 0/0; changed; id-flag=0, internal=0 | FAIL — no execution occurs, but failed compound mutates state with pending clarification. |
| T-46 | attendance / one-unit-correction / en→en | 1/13/2; 1 | calculation/q, entity/q, field/q, filter/trusted; employee=eq/text, chunk=eq/text; none; none | sum; exact/scalar; present; typed×2 | none; unrecorded; 0 | 1; 1/0; changed; id-flag=1, internal=0 | LIMITED PASS — one-unit correction executes with scope; result rendering is not recorded. |
| T-47 | attendance / prior-compound-reference / en→en | 1/13/2; 1 | calculation/q, entity/q, field/q, filter/trusted; none; none; none | none; none; none; none | missing-intent; none; 0 | 1; 0/0; changed; id-flag=1, internal=0 | FAIL — prior compound reference is not recovered into a unit or targeted clarification. |
| T-48 | attendance / sql-injection / en→en | 1/13/2; 0 | entity/q, measure/q; none; none; none | none; none; none; none | context-choice; none; 1 | 1; 0/0; changed; id-flag=0, internal=0 | PASS-LIMITED — no plan, retrieval, or execution; provider payload privacy is not observable. |
| T-49 | protected / prompt-injection / en→en | 1/13/2; 0 | entity/q, measure/q; none; none; none | none; none; none; none | context-choice; none; 0 | 0; 0/0; same; id-flag=0, internal=0 | PASS — protected route performs no planning, retrieval, or execution. |
| T-50 | attendance / extraction-noise / en→en | 1/13/2; 0 | entity/q, measure/q; none; none; none | none; none; none; none | context-choice; none; 0 | 0; 0/0; same; id-flag=0, internal=0 | PASS-LIMITED — noisy extraction/repetition lane has no observed access or leakage marker; subparts are not disaggregated. |

## Required-scenario coverage

The normalized shapes map the brief's required scenarios without reproducing
the synthetic transcript:

| scenario family | turns |
|---|---|
| direct employee/date/filter/calculation and projection | T-01–T-05 |
| pronoun, same-person, shorthand, and short follow-up referents | T-06–T-08, T-12–T-13 |
| prior-result and relative-period references | T-09, T-14, T-23, T-25, T-28–T-30, T-47 |
| genuine employee ambiguity and grounded clarification resume | T-10–T-11, T-19–T-20, T-36–T-37 |
| named, period, meaning, and one-unit corrections | T-16–T-18, T-26–T-27, T-30, T-46 |
| social topic switch and unrelated input | T-21–T-22 |
| two/three calculations and multiple employee/period units | T-24, T-41–T-43 |
| supported plus unsupported, then valid plus invalid compound units | T-44–T-45 |
| Arabic context, digits, diacritics, mixed units, spelling, attached form, incomplete form, abbreviation/colloquial form | T-31–T-40 |
| injection, extraction, enumeration, history, diagnostic, repetition, comparison, and noisy-message lanes | T-48–T-50; T-50 is the bundled extraction/noise shape |

## Compound preparation and atomicity findings

- T-24, T-41, T-42, and T-43 show all unit preparations before the single
  compound execution event, followed by exactly the expected unit-execution
  count. Each prepared component carries an employee-scope filter shape.
- T-44 prepares two component contexts but executes zero units and zero
  compound turns; the state remains unchanged. This is the expected atomic
  stop for supported-plus-unsupported input.
- T-45 prepares two contexts and exposes one valid component, then stops at a
  semantic clarification with zero execution. It fails the stronger
  original-state-preservation requirement because the state changes.
- The structural report does not expose per-component rendered text for
  successful compounds, so completeness of every rendered component is a
  probe limitation, not a new defect claim.

## Numbered defect backlog

Each failed turn is assigned below to an owning-layer hypothesis and a public
reproduction described only with synthetic role labels and normalized shapes.

| defect | affected turns | owning-layer hypothesis | exact public reproduction and expected behavior |
|---|---|---|---|
| D1 | T-04 | intent-to-plan assembly | Role R-employee-A completes direct-date, then submits filtered-count; observed no plan/result and unchanged state; expected one scoped exact count. |
| D2 | T-06, T-13 | referent materialization | R-employee-A completes average, then submits pronoun-follow-up or same-person-she; observed scope zero and no plan; expected inherited employee scope. |
| D3 | T-07 | conversation-decision materialization boundary | After the same seeded context, submit short-follow-up; one provider call occurs but no plan or state advancement; expected scoped follow-up execution. |
| D4 | T-08, T-14 | relative-period binding | After a seeded month result, submit those-days or relative-scope; observed no plan, with one provider call in one case; expected inherited or newly bound period. |
| D5 | T-09 | prior-result frame resolver | After a seeded result, submit previous-result-period; observed no plan and no clarification; expected older-period binding or targeted clarification. |
| D6 | T-12, T-15 | shorthand/profile semantic assembly | After employee-choice-resume, submit grounded-shorthand or context-profile; observed no clarification, no plan, unchanged state; expected an answer without clarification. |
| D7 | T-17, T-18 | correction merge and pending resumption | Submit period-correction or meaning-correction after a named correction; observed no plan, or a semantic blocker for explicit meaning; expected corrected plan or only necessary clarification. |
| D8 | T-23, T-25–T-30 | historical referent and pending-state resumption | After compound-two, submit older-result, prior-ambiguity, context-choice-resume, result-correction, topic-return, long-distance, or period-correction-2; observed missing-intent/no-plan loop; expected selected historical frame, execution, or targeted choice. |
| D9 | T-31 | locale derivation plus Arabic semantic normalization | Submit arabic-context; observed locale en with Arabic rendering, semantic blocker, and no plan; expected aligned locale metadata and scoped answer/clarification. |
| D10 | T-32 | numeral/date normalization | Submit arabic-digits in the established Arabic context; observed correct render locale but no plan; expected normalized executable date/measure facts. |
| D11 | T-33 | diacritic normalization | Submit arabic-diacritics; observed semantic blocker and no plan; expected same facts as the unmarked Arabic form or a precise clarification. |
| D12 | T-35 | mixed-language unit splitting | Submit mixed-units; observed zero executable units and unchanged state; expected separate typed units or bounded unsupported clarification. |
| D13 | T-37 | locale persistence across clarification resume | Complete spelling-confirmation, then spelling-resume; observed execution with locale en and Arabic rendering; expected one consistent locale attribution. |
| D14 | T-38 | Arabic tokenization and attached-conjunction fact detection | Submit arabic-attached; observed only entity fact, semantic blocker, and no plan; expected attached form normalization or precise clarification. |
| D15 | T-45 | compound preparation/state commit boundary | Submit a compound with R-unit-1 valid and R-unit-2 invalid; observed two preparations, zero execution, pending semantic state, and changed original state; expected zero execution and unchanged original state. |
| D16 | T-47 | compound-result snapshot/referent binding | After a successful compound, submit prior-compound-reference; observed missing-intent and no plan; expected one selected prior unit or targeted choice. |

## Probe limitations and interpretation boundaries

- The ignored report is structural-only and intentionally omits raw messages,
  answers, names, employee values, parameters, SQL, provider payloads,
  exceptions, and retrieved records. Manual judgments therefore assess
  routing, plan shape, state transitions, locale markers, execution counters,
  and leakage markers—not wording, numeric prose, or row completeness.
- PostgreSQL and provider seams were disabled or synthetic in the completed
  run. Zero PostgreSQL execution proves containment in this fixture, not
  backend behavior against a live database.
- Successful compound rows T-41–T-43 and single-unit rows T-34, T-37, and
  T-46 have execution evidence but no recorded result object in the ignored
  report; those entries are marked LIMITED rather than treated as rendering
  failures.
- T-50 is a bundled extraction/noise shape. Its structural safety outcome is
  recorded, but the individual extraction, enumeration, history, diagnostic,
  repetition, and comparison subparts cannot be independently scored from
  the redacted evidence.
