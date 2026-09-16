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
  `answer.py:3272-3280` must also be counted. Count attempted provider calls
  separately from budget claims and record only the integer count; never record
  payloads. Grounded deterministic requests should remain at zero provider
  completions.

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
