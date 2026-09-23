# Generic Attendance Subject Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fixed cohort-column handling with a generic criteria-span subject contract that supports any compatible attendance catalog field or predicate.

**Architecture:** The reference model identifies explicit employees, exact natural-language criteria spans, and their set relationship without receiving schema. The existing semantic planner maps those spans to catalog-validated subject filters; execution combines the resulting scope with server-owned authorization and ordinary result filters in one parameterized query.

**Tech Stack:** Python 3.12, Pydantic v2, LiteLLM structured outputs, psycopg/PostgreSQL, `unittest`, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-23-attendance-online-v1-design.md`

## Global Constraints

- Work in the current dirty checkout; never reset, clean, or overwrite unrelated changes.
- Replace the uncommitted fixed `COHORT_FIELD_IDS`/`CohortReference` approach; do not preserve it as compatibility code.
- The reference provider receives no logical or physical database schema and emits no field IDs, predicates, or SQL.
- Employee identity and authorization remain application-owned.
- Every SQL literal and limit remains parameterized.
- Preserve one semantic unit, one attendance source, atomic publication, and the eleven-call ceiling.
- Preserve the narrow public façade and offline ingestion behavior.
- Write focused tests before each production change, but do not execute any test or verification command until implementation and manual review are complete and the user authorizes final testing.
- Stage exact intended files and inspect `git diff --cached` before every commit.

## Review Focus

- A criterion containing prompt-injection text remains data and can only become a catalog-valid parameterized filter.
- `Faris and employees with worked hours over 8` compiles authorization AND an employee/criteria union; `Faris in Engineering` compiles an intersection.
- A missing subject never becomes `all_authorized` without an exact overall or cross-cohort cue.
- Confirming one fuzzy employee preserves already-resolved employees, criteria spans, and union/intersection mode.
- Aggregate-derived employee sets used by another query return precise unsupported capability instead of being approximated as row filters.

---

### Task 1: Replace fixed cohorts with generic criteria spans

**Files:**
- Modify: `week5/new_implementation/online/reference.py`
- Modify: `week5/new_implementation/online/catalog.py`
- Modify: `week5/new_implementation/tests/online/test_planning.py`

**Interfaces:**
- Consumes: `EvidenceSpan`, `FilterComponent`, existing employee-reference types, authoritative PostgreSQL directory binding.
- Produces: `ResolvedSubject`, `SubjectScope`, `PendingSubject`, `SubjectReference`, and `BoundReferences.subject: ResolvedSubject | None`.

- [ ] **Step 1: Write contract tests before production edits**

Add focused `unittest` cases constructing these responses:

```python
SubjectReference(
    mode="criteria",
    criteria=(EvidenceSpan(start=5, end=37, text="employees who worked over 8 hours"),),
)

SubjectReference(
    mode="union",
    employees=(
        CurrentReference(
            key="employee",
            value_kind="employee_name",
            value="Faris Hassan",
            mention=EvidenceSpan(start=5, end=17, text="Faris Hassan"),
        ),
    ),
    criteria=(EvidenceSpan(start=22, end=55, text="employees absent in September"),),
)
```

Assert exact-span validation, required selector shapes, multiple employees, prior-subject reuse, and `all_authorized` cue validation. Assert that no reference response contains a catalog field ID.

- [ ] **Step 2: Replace the fixed cohort contract**

Delete `COHORT_FIELD_IDS`, `CohortReference`, and `BoundCohort`. Define:

```python
class SubjectReference(_Strict):
    mode: Literal["employees", "criteria", "union", "intersection", "all_authorized"]
    employees: tuple[EmployeeReference, ...] = Field(default=(), max_length=20)
    criteria: tuple[EvidenceSpan, ...] = Field(default=(), max_length=8)
    scope_cue: EvidenceSpan | None = None


class ResolvedSubject(_Strict):
    mode: Literal["employees", "criteria", "union", "intersection", "all_authorized"]
    employee_ids: tuple[str, ...] = Field(default=(), max_length=20)
    criteria: tuple[EvidenceSpan, ...] = Field(default=(), max_length=8)
    scope_cue: EvidenceSpan | None = None
    inherited: bool = False


class SubjectScope(_Strict):
    mode: Literal["employees", "criteria", "union", "intersection", "all_authorized"]
    employee_ids: tuple[str, ...] = Field(default=(), max_length=20)
    criteria: tuple[FilterComponent, ...] = Field(default=(), max_length=16)


class PendingSubject(_Strict):
    mode: Literal["employees", "union", "intersection"]
    resolved_employee_ids: tuple[str, ...] = Field(default=(), max_length=20)
    criteria: tuple[EvidenceSpan, ...] = Field(default=(), max_length=8)
```

Validate these exact shapes: employees `(IDs=true, criteria=false)`, criteria `(false, true)`, union/intersection `(true, true)`, all-authorized `(false, false, cue=true)`. `inherited=True` is allowed only for a prior reference and may omit a new cue because it reuses a verified `SubjectScope`. A prior reference must be the only employee selector.

- [ ] **Step 3: Simplify the reference prompt and payload**

Remove `allowed_cohort_fields`. Tell the model to return exact employee values and exact criteria text only. Preserve the identity-claim rules, explicit broad-scope cue, clear-follow-up rule, and absence of schema/SQL.

- [ ] **Step 4: Preserve complete pending scope during employee confirmation**

Change pending confirmation to carry `PendingSubject`. On selection, append the authoritative ID and construct `ResolvedSubject` without discarding resolved IDs, criteria spans, or mode. More than one unresolved fuzzy employee fails safely and requests exact references.

- [ ] **Step 5: Manually inspect Task 1 without running code**

Trace exact ID, exact name, unique fuzzy, multiple employees, criteria-only, union, intersection, explicit all-authorized, missing subject, and prior-subject cases. Search for `COHORT_FIELD_IDS`, `CohortReference`, and `BoundCohort`; active code and tests must contain no hits.

---

### Task 2: Materialize catalog-backed subject filters

**Files:**
- Modify: `week5/new_implementation/online/reference.py`
- Modify: `week5/new_implementation/online/planner.py`
- Modify: `week5/new_implementation/online/query.py`
- Modify: `week5/new_implementation/online/audit.py`
- Modify: `week5/new_implementation/tests/online/test_query.py`
- Modify: `week5/new_implementation/tests/online/test_planning.py`

**Interfaces:**
- Consumes: `ResolvedSubject`, `SubjectScope`, `FilterComponent`, `BooleanExpr`, `AttendanceCatalog`, `QueryLimits`.
- Produces: `ReadyPlan.subject_filters`, `SubjectMaterializationError`, and `materialize_subject(...)`.

- [ ] **Step 1: Write subject-materialization tests before production edits**

Cover generic mappings rather than named special cases:

```python
worked = FilterComponent(
    component_key="subject-worked-hours",
    evidence=EvidenceSpan(start=19, end=43, text="worked more than 8 hours"),
    expression=Condition(field_id="worked_hours", operator="gt", value=8),
)

absent = FilterComponent(
    component_key="subject-absence",
    evidence=EvidenceSpan(start=22, end=28, text="absent"),
    expression=Predicate(predicate_id="absent"),
)
```

Assert support for text, number, date, closed-value, and predicate criteria through the same path. Assert rejection when evidence falls outside every declared criterion, when planner filters repeat subject filters as ordinary result filters, and when employees/all-authorized receive subject filters.

- [ ] **Step 2: Add the planner output boundary**

Extend `ReadyPlan` with:

```python
subject_filters: tuple[FilterComponent, ...] = Field(default=(), max_length=16)
```

Pass `ResolvedSubject` as `bound_subject`. The prompt instructs the semantic writer to translate only supplied criteria spans using the complete provider-safe logical catalog. It must keep subject filters separate from result filters and return capability `set` when criteria require an aggregate-derived employee set, or the existing precise capability for a join, ranking, window, or second query unit.

- [ ] **Step 3: Add one reusable filter validator**

Extract query validation so both subject filters and ordinary query filters use the same Boolean-depth, node, field, operator, predicate, literal-length, and list-cardinality checks:

```python
def validate_filters(
    filters: tuple[BooleanExpr, ...],
    catalog: AttendanceCatalog,
    limits: QueryLimits,
) -> tuple[QueryIssue, ...]:
    issues: list[QueryIssue] = []
    if len(filters) > limits.max_filter_components:
        issues.append(QueryIssue("filter_count", "filters", "too many filter components"))
    for index, expression in enumerate(filters):
        path = f"filters.{index}"
        depth, nodes = _boolean_metrics(expression)
        if depth > limits.max_boolean_depth:
            issues.append(QueryIssue("boolean_depth", path, "Boolean expression is too deep"))
        if nodes > limits.max_boolean_nodes:
            issues.append(QueryIssue("boolean_nodes", path, "Boolean expression has too many nodes"))
        for condition in _conditions(expression):
            field = catalog.fields.get(condition.field_id)
            if field is None:
                issues.append(QueryIssue("unknown_field", path, "unknown logical field"))
                continue
            if not field.filterable:
                issues.append(QueryIssue("field_role", path, "field is not filterable"))
            if condition.operator not in field.operators:
                issues.append(QueryIssue("operator_type", path, "operator is incompatible with field"))
            values = condition.value if isinstance(condition.value, tuple) else (condition.value,)
            if len(values) > limits.max_list_cardinality:
                issues.append(QueryIssue("list_cardinality", path, "literal list is too large"))
            if any(isinstance(value, str) and len(value) > limits.max_literal_length for value in values):
                issues.append(QueryIssue("literal_length", path, "literal is too long"))
            for value in values:
                try:
                    catalog.canonicalize_value(condition.field_id, value)
                except ValueError:
                    issues.append(QueryIssue("literal_type", path, "literal is incompatible with field type"))
                    break
        for predicate in _predicates(expression):
            if predicate.predicate_id not in catalog.predicates:
                issues.append(QueryIssue("unknown_predicate", path, "unknown business predicate"))
    return tuple(issues)
```

`validate_query` calls `validate_filters` and then validates output-specific rules.

- [ ] **Step 4: Materialize the executable subject**

Define:

```python
class SubjectMaterializationError(ValueError):
    pass


def materialize_subject(
    resolved: ResolvedSubject,
    subject_filters: tuple[FilterComponent, ...],
    *,
    active_subject: SubjectScope | None,
    catalog: AttendanceCatalog,
    limits: QueryLimits,
) -> SubjectScope:
    if resolved.inherited:
        if active_subject is None or subject_filters:
            raise SubjectMaterializationError("invalid inherited subject")
        return active_subject
    requires_filters = resolved.mode in {"criteria", "union", "intersection"}
    if requires_filters != bool(subject_filters):
        raise SubjectMaterializationError("subject filters do not match subject mode")
    for component in subject_filters:
        if not any(
            span.start <= component.evidence.start
            and component.evidence.end <= span.end
            for span in resolved.criteria
        ):
            raise SubjectMaterializationError("subject evidence is outside criteria spans")
    issues = validate_filters(
        tuple(component.expression for component in subject_filters),
        catalog,
        limits,
    )
    if issues:
        raise SubjectMaterializationError("; ".join(item.code for item in issues))
    return SubjectScope(
        mode=resolved.mode,
        employee_ids=resolved.employee_ids,
        criteria=subject_filters,
    )
```

For a new subject, validate filter evidence containment against `resolved.criteria`. For `inherited=True`, require no new subject filters and reuse `active_subject`. Reject duplicate component keys and any selector/mode mismatch.

- [ ] **Step 5: Include the subject in both audit passes**

Send the immutable `ResolvedSubject`, candidate subject filters, and active verified subject to the auditor. A repaired candidate must pass `materialize_subject` plus ordinary query materialization before final audit.

- [ ] **Step 6: Manually inspect Task 2 without running code**

Trace one criterion for each scalar type and one nested Boolean criterion. Confirm every accepted field/operator/predicate comes only from `online.catalog`, and confirm no department/absence/hours-specific branch exists outside catalog data.

---

### Task 3: Compile and publish the generic subject consistently

**Files:**
- Modify: `week5/new_implementation/online/execution.py`
- Modify: `week5/new_implementation/online/state.py`
- Modify: `week5/new_implementation/online/pipeline.py`
- Modify: `week5/new_implementation/online/answering.py`
- Modify: `week5/new_implementation/tests/online/test_execution.py`
- Modify: `week5/new_implementation/tests/online/test_pipeline.py`

**Interfaces:**
- Consumes: `SubjectScope`, `AttendanceQuery`, `AccessContext`.
- Produces: one `BoundAttendanceQuery` reused by main, coverage, witness, and narrative execution; atomically published active subject.

- [ ] **Step 1: Write compiler and pipeline tests before production edits**

Assert these parameterized shapes:

```text
employees:      authorization AND employee_id = ANY(%s)
criteria:       authorization AND (<compiled criteria>)
union:          authorization AND (employee_id = ANY(%s) OR (<compiled criteria>))
intersection:   authorization AND (employee_id = ANY(%s) AND (<compiled criteria>))
all_authorized: authorization
```

Include prompt-injection literals, nested `not`/`any`, restricted access, criteria-only state publication, union follow-up reuse, narrative CTE scope, and failure-state preservation.

- [ ] **Step 2: Generalize subject SQL compilation**

Delete cohort-specific lower/equality compilation. Compile every subject criterion through the same `_compile_expr` path as ordinary filters. Keep authorization clauses outside the subject parentheses, then append ordinary query filters with `AND`.

- [ ] **Step 3: Reuse the identical subject for coverage, witnesses, and narrative**

Coverage applies authorization plus subject only. Witness applies authorization, subject, and ordinary query filters. Narrative uses an attendance-table CTE compiled from authorization plus subject and restricts chunks to those employee IDs. `all_authorized` narrative with unrestricted access continues to fail closed because it has no bounded employee set.

- [ ] **Step 4: Publish verified subject state atomically**

Store `SubjectScope` on `VerifiedTurn` and as `ConversationState.active_subject_scope`. Preserve `active_employee_ids` only as the explicit-ID compatibility view and enforce equality with `active_subject_scope.employee_ids`. Clarification stores only pending employee confirmation; unsupported and failed outcomes preserve the exact prior state.

- [ ] **Step 5: Keep grounded names scoped to explicit identities**

Continue adding authoritative names for explicit bound IDs. Criteria-only groups do not require enumerating every matching employee name unless employee name is part of the requested result rows.

- [ ] **Step 6: Manually inspect Task 3 without running code**

Trace the four requested cases plus fuzzy confirmation and authorization failure from reference response through final SQL parameters and state publication. Inspect every call site of `bind_query`, `compile_coverage`, `compile_witness`, and narrative retrieval for the same `SubjectScope` object.

---

### Task 4: Align evaluation, docs, and final verification gate

**Files:**
- Modify: `week5/new_evaluation/acceptance.py`
- Modify: `week5/new_evaluation/test_acceptance.py`
- Modify: `week5/new_implementation/LLM_PLANNER.md`
- Modify: `week5/ARCHITECTURE.md`
- Modify: `docs/superpowers/plans/2026-09-23-attendance-online-v1.md`

**Interfaces:**
- Consumes: final reference, planner, scope, outcome, and observer contracts.
- Produces: acceptance scenarios and documentation matching the generic runtime.

- [ ] **Step 1: Add acceptance scenarios before implementation is considered ready**

Add named cases for multiple exact employees, criteria by organizational text, criteria by absence predicate, criteria by numeric worked-hours condition, employee/criteria union, employee/criteria intersection, explicit cross-cohort all-authorized, missing subject clarification, inherited criteria subject, and aggregate-derived-set unsupported.

- [ ] **Step 2: Remove fixed-cohort documentation and stale symbols**

Document criteria spans, planner-owned catalog mapping, outer authorization conjunction, and the future SQL-boundary independence. Search active code, tests, configuration, and current docs for `COHORT_FIELD_IDS`, `CohortReference`, `BoundCohort`, and any prompt listing specific subject columns; every hit must be removed or be an explicit historical statement in this plan.

- [ ] **Step 3: Complete manual review and stop before tests**

Review the complete diff for schema duplication, hardcoded question phrases, employee-scope broadening, non-parameterized literals, state divergence, and unsupported aggregate-derived sets. At this point tell the user implementation and manual debugging are complete and wait for authorization to test.

- [ ] **Step 4: Run the final consolidated verification only after user authorization**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s week5/new_implementation/tests -t . -p "test_*.py" -v
.\.venv\Scripts\python.exe -m unittest week5.new_evaluation.test_eval week5.new_evaluation.test_acceptance week5.test_new_app -v
.\.venv\Scripts\python.exe -m compileall -q week5/new_implementation week5/new_evaluation week5/new_app.py
.\.venv\Scripts\ruff.exe check week5/new_implementation week5/new_evaluation week5/new_app.py
.\.venv\Scripts\ruff.exe format --check week5/new_implementation week5/new_evaluation week5/new_app.py
git diff --check
```

Expected: every command exits `0`; unittest reports no failures/errors; Ruff emits no diagnostics; `git diff --check` emits no errors.

- [ ] **Step 5: Run live behavioral gates after deterministic checks pass**

Run the complete 311-case behavior evaluation and the named Wail, Faris four-turn, generic-subject, and long-conversation acceptance scenarios. Credentials or database unavailability is a blocker and must not be reported as success.

- [ ] **Step 6: Stage, inspect, and commit the verified implementation**

Stage only the generic-subject implementation, tests, evaluation, and current documentation. Inspect `git diff --cached`, then commit:

```text
refactor: generalize attendance subject scope
```
