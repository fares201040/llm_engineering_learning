# Schema-Grounded Query Compilation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent any technically valid but semantically unsupported LLM choice from reaching PostgreSQL, using one registry-driven pattern for every attendance column.

**Architecture:** The LLM returns a typed but untrusted `PlannerProposal` whose semantic choices carry their own evidence. Deterministic resolvers and invariant checks compile it into `ExecutableQueryPlan`; only that type may be compiled into parameterized PostgreSQL. Field metadata, business concepts, prompt context, validation, and generated tests all come from one semantic registry.

**Tech Stack:** Python 3.11+, Pydantic 2, `abc.ABC`, LiteLLM structured output, psycopg, unittest, PostgreSQL, existing Chroma fallback.

**Spec:** `docs/superpowers/specs/2026-09-13-schema-grounded-query-compilation-design.md`

## Global Constraints

- Do not add or execute raw LLM-generated SQL, including an `expected_sql` or `custom_sql` escape hatch.
- Only `ExecutableQueryPlan` may cross the retrieval-orchestration gate. Low-level helpers may receive derived filters or compiled SQL only from that gated path.
- Every executable filter, measure, predicate, grouping, order, and limit must have verified provenance.
- Every strong semantic fact found in the question or trusted state must be represented or cause clarification/rejection.
- Derive retrieval mode and semantic search text in Python from verified facts and the original question; they are not free-form planner choices.
- Keep one source of truth for field metadata, aliases, values, concepts, measures, predicates, operators, and SQL expressions.
- Send every planner-visible field definition to the planner in compact registry-generated form; keep internal execution-only fields and SQL expressions out of the prompt, and send only bounded relevant catalog candidates rather than whole sensitive/high-cardinality catalogs.
- Keep generic prompt instructions, but remove phrase-to-filter and phrase-to-measure business rules from prompt prose.
- Use abstract classes only where implementations genuinely vary: field resolution and plan invariants.
- Preserve access checks, read-only transactions, parameter binding, employee revalidation, clarification flows, Chroma fallback, and the public four-item `fetch_context()` result.
- Preserve all unrelated dirty-worktree changes.

## Corrected decision about model SQL

The LLM will not receive an executable SQL field. The useful diagnostic intent is represented by:

- `AnswerContract`: expected shape, unit, subject, and grain;
- `unsupported_capabilities`: controlled identifiers for requests the typed plan cannot represent;
- `CompiledPostgresQuery`: SQL and parameters generated only by Python from `ExecutableQueryPlan`.

An offline evaluation report may ask an LLM to explain a failed case, but no reported SQL is accepted by runtime code. Repeated unsupported cases are handled by deliberately extending the typed plan language and tests.

## File Structure

- Modify `week5/new_implementation/attendance_schema.py`: canonical semantic registry and proposal/executable models.
- Create `week5/new_implementation/semantic_resolution.py`: pure abstract field resolvers and fact detection.
- Create `week5/new_implementation/plan_compiler.py`: pure abstract invariants and proposal compilation.
- Create `week5/new_implementation/postgres_compiler.py`: pure parameterized SQL compilation artifacts.
- Modify `week5/new_implementation/answer.py`: I/O adapters, planner call, catalog/employee access, state flow, execution.
- Create focused resolver, compiler, and SQL compiler test modules.
- Modify existing schema, answer, observability, application-state, and evaluation tests.

The new pure modules import `attendance_schema.py`; they must not import `answer.py`. `answer.py` is the composition root and imports the new modules. This prevents circular imports.

## Execution Preflight

Before Task 1, run `git status --short`, inspect the diff for every target file, and run the current schema, answer, and app tests once. The present worktree already contains user changes in `attendance_schema.py` and project documentation. If those edits are still present when implementation begins, treat them as the working baseline and stage only task-owned hunks; do not stash, reset, overwrite, or commit unrelated hunks. The `git add` commands below name intended files, not permission to stage unrelated changes: use `git add -p` for any pre-dirty file, then inspect `git diff --cached` and run `git diff --cached --check` before each commit. Use the `superpowers:using-git-worktrees` skill if isolated execution is selected and an agreed starting state is available.

---

### Task 1: Add complete semantic metadata and distinct proposal/executable models

**Files:**
- Modify: `week5/new_implementation/attendance_schema.py:12-620`
- Modify: `week5/new_implementation/test_attendance_schema.py:1-340`

**Interfaces:**
- Produces: `PlannerProposal`, `ExecutableQueryPlan`, `AnswerContract`, typed proposed-choice models, `UnsupportedCapability`, `ResolutionKind`, `ValueConceptDefinition`, `RetrievalIntentDefinition`.
- Produces: `render_planner_schema() -> str`.
- Keeps: `QueryPlan` as the executable payload base during migration so existing result/evaluation serialization remains compatible.

- [ ] **Step 1: Add failing boundary and completeness tests**

Add tests with these assertions:

```python
class SemanticSchemaContractTests(unittest.TestCase):
    def test_proposal_is_not_an_executable_plan(self):
        proposal = schema.PlannerProposal(
            status="ready",
            measure=schema.ProposedMeasureChoice(
                name="distinct_dates", evidence_text="days"
            ),
            answer_contract=schema.AnswerContract(
                shape="scalar", unit="dates", subject_field="Date", grain=["Date"]
            ),
        )
        self.assertNotIsInstance(proposal, schema.QueryPlan)
        self.assertNotIsInstance(proposal, schema.ExecutableQueryPlan)

    def test_every_field_has_complete_resolution_metadata(self):
        for name, definition in schema.FIELD_DEFINITIONS.items():
            self.assertTrue(definition.description, name)
            self.assertEqual(bool(definition.operators), definition.filterable, name)
            self.assertTrue(definition.resolution_kind, name)
            self.assertTrue(definition.natural_names, name)

    def test_registry_references_are_valid(self):
        for field, definition in schema.FIELD_DEFINITIONS.items():
            self.assertTrue(set(definition.operators) <= schema.FILTER_OPERATORS, field)
            for alias in definition.value_aliases:
                if definition.resolution_kind == "closed_value":
                    self.assertIn(alias.canonical_value, definition.closed_values, field)
                else:
                    self.assertEqual(definition.resolution_kind, "catalog", field)
        for concept in schema.VALUE_CONCEPT_DEFINITIONS.values():
            self.assertIn(concept.field, schema.FIELD_DEFINITIONS)
            definition = schema.FIELD_DEFINITIONS[concept.field]
            if definition.resolution_kind == "closed_value":
                self.assertTrue(set(concept.members) <= set(definition.closed_values))
            else:
                self.assertEqual(definition.resolution_kind, "catalog")

    def test_planner_schema_contains_every_registered_field(self):
        rendered = schema.render_planner_schema()
        for name, definition in schema.FIELD_DEFINITIONS.items():
            if definition.planner_visible:
                self.assertIn(name, rendered)
            else:
                self.assertNotIn(f'"name":"{name}"', rendered)
```

In the same contract suite, validate every registry-to-registry reference, not only value concepts: predicate required/incompatible `RequiredFilter` fields/operators/values, measure aggregation fields and roles, predicate incompatibility names, interpretation measure/predicate names, and the derived PostgreSQL field map. These tests are the startup guard against metadata drift.

- [ ] **Step 2: Run the focused tests and confirm failure**

```powershell
uv run python -m unittest week5.new_implementation.test_attendance_schema.SemanticSchemaContractTests -v
```

Expected: the new models and metadata are not defined.

- [ ] **Step 3: Define all proposal-side types in one task**

Add these types now so later tasks do not redefine or widen them:

```python
ResolutionKind = Literal[
    "identifier", "entity", "temporal", "numeric",
    "closed_value", "catalog", "free_text",
]
EvidenceOrigin = Literal["question", "trusted_state", "deterministic_default"]
PlanningStatus = Literal["ready", "ambiguous", "unsupported"]
UnsupportedCapability = Literal[
    "nested_boolean_filters",
    "having_filter",
    "window_calculation",
    "cross_period_comparison",
    "multi_stage_aggregation",
]
FilterOperator = Literal[
    "eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "starts_with"
]
FILTER_OPERATORS: frozenset[FilterOperator] = frozenset(get_args(FilterOperator))
FilterScalar = str | float
FilterValue = FilterScalar | list[FilterScalar]
RegistryFilterValue = FilterScalar | tuple[FilterScalar, ...]


class FilterCondition(BaseModel):
    field: str
    operator: FilterOperator
    value: FilterValue


@dataclass(frozen=True, kw_only=True)
class RequiredFilter:
    field: str
    operator: FilterOperator
    value: RegistryFilterValue


class ProposedFilter(BaseModel):
    field: str
    operator: FilterOperator
    value: FilterValue
    evidence_text: str


class ProposedMeasureChoice(BaseModel):
    name: MeasureName
    evidence_text: str


class ProposedPredicateChoice(BaseModel):
    name: BusinessPredicateName
    evidence_text: str


class ProposedFieldChoice(BaseModel):
    field: str
    evidence_text: str


class ProposedOrderChoice(BaseModel):
    field: str
    direction: Literal["asc", "desc"]
    evidence_text: str


class ProposedNameHint(BaseModel):
    value: str
    evidence_text: str


class ProposedLimit(BaseModel):
    value: int = Field(gt=0)
    evidence_text: str


class ProposedCalculation(BaseModel):
    operation: Literal["count", "distinct_count", "sum", "average", "min", "max", "percentage"]
    field: str | None = None
    evidence_text: str
    percentage_condition: ProposedFilter | None = None


class AnswerContract(BaseModel):
    shape: Literal["scalar", "grouped", "rows", "narrative"]
    unit: Literal["dates", "records", "employees", "hours", "percentage", "value"]
    subject_field: str | None = None
    grain: list[str] = Field(default_factory=list)


class PlannerProposal(BaseModel):
    status: PlanningStatus
    filters: list[ProposedFilter] = Field(default_factory=list)
    name_hint: ProposedNameHint | None = None
    measure: ProposedMeasureChoice | None = None
    business_predicates: list[ProposedPredicateChoice] = Field(default_factory=list)
    calculation: ProposedCalculation | None = None
    group_by: list[ProposedFieldChoice] = Field(default_factory=list)
    order_by: ProposedOrderChoice | None = None
    limit: ProposedLimit | None = None
    answer_contract: AnswerContract | None = None
    interpretation_candidates: list[InterpretationName] = Field(default_factory=list)
    unsupported_capabilities: list[UnsupportedCapability] = Field(default_factory=list)
    explanation: str | None = None


class ExecutableQueryPlan(QueryPlan):
    answer_contract: AnswerContract
```

Evidence is embedded beside each proposal choice; do not use an arbitrary string path such as `filters[0]` to connect evidence to a separate structure. `ProposedFilter` deliberately does not inherit `FilterCondition`, so proposal-side values cannot accidentally pass an executable-filter type check. Use `FilterOperator` everywhere an operator crosses a model or registry boundary; do not repeat operator string literals in `RequiredFilter`, semantic facts, provenance, or resolver APIs. Registry collections stay immutable tuples and are converted to executable lists only at the compiler boundary.

All planner-provided models use `ConfigDict(extra="forbid", str_strip_whitespace=True)`, and every evidence-bearing string rejects blank-after-trimming values. Put context-free cross-field rules in one `PlannerProposal @model_validator(mode="after")`, so direct construction, `model_validate_json()`, and clarification-state deserialization enforce the same contract. It must reject `ready` without `answer_contract`, interpretation candidates on `ready`, `unsupported` without a controlled capability, capability identifiers on a non-unsupported status, execution-bearing choices on `unsupported`, mutually exclusive `measure` and `calculation`, and structurally inconsistent answer shape/grouping. `ProposedCalculation` owns its operation shape: distinct/sum/average/min/max require a field, percentage also requires its condition, and plain row count does not invent a field. Rules that need question facts or catalog results remain compiler invariants rather than being duplicated in `answer.py`.

- [ ] **Step 4: Make every `FieldDefinition` explicit and keyword-based**

Replace the current positional/partially derived definition with these complete keyword-only types. `resolution_kind`, roles, and execution metadata are all explicit:

```python
@dataclass(frozen=True, kw_only=True)
class ValueAliasDefinition:
    natural_name: str
    canonical_value: str


@dataclass(frozen=True, kw_only=True)
class FieldDefinition:
    storage_type: Literal["text", "date", "time", "datetime", "number"]
    description: str
    sql_expression: str
    natural_names: tuple[str, ...]
    operators: tuple[FilterOperator, ...]
    resolution_kind: ResolutionKind
    closed_values: tuple[str, ...] = ()
    value_aliases: tuple[ValueAliasDefinition, ...] = ()
    filterable: bool = True
    groupable: bool = True
    orderable: bool = True
    aggregatable: bool = False
    searchable: bool = True
    metadata: bool = True
    context: bool = True
    planner_visible: bool = True
```

Update every field construction, including `_MISSING_LIVE_FIELDS` and overtime-slot loops, to keyword arguments. Populate `natural_names` with a normalized canonical form even when no extra alias exists, for example `Schedule_From_Date` → `schedule from date`. Convert current regex-style `aliases` into explicit literal phrases; do not expose regex syntax as planner vocabulary or retain a second alias source. This is what guarantees baseline coverage for every column.

Use these strategy rules:

- `Employee_ID`: `identifier`;
- `Name`: `entity`;
- number fields: `numeric`;
- date/time/datetime fields: `temporal`;
- controlled enums: `closed_value`;
- low-cardinality database categories: `catalog`;
- unbounded remarks/descriptions: `free_text`.

Mark internal fields such as `chunk_type` as `planner_visible=False`. They remain available for deterministic application scoping but are excluded from the LLM schema. Do not render `sql_expression` in the planner prompt; it is application-only metadata.

Derive `FILTERABLE_FIELDS`, numeric/search/context collections, and `POSTGRES_FIELD_MAP` from `FIELD_DEFINITIONS` and its role flags. Do not keep independent field-name sets that can drift from the registry.

- [ ] **Step 5: Enrich measure and predicate definitions instead of prompt prose**

Replace the existing registry types with these complete models and update all their entries:

```python
@dataclass(frozen=True, kw_only=True)
class MeasureDefinition:
    description: str
    aggregation: Literal["count", "distinct_count"]
    aggregation_field: str | None
    natural_names: tuple[str, ...]
    answer_unit: Literal["dates", "records", "employees"]
    default_answer_shape: Literal["scalar", "grouped"] = "scalar"


@dataclass(frozen=True, kw_only=True)
class PredicateDefinition:
    description: str
    required_filters: tuple[RequiredFilter, ...]
    natural_names: tuple[str, ...]
    incompatible_with: tuple[BusinessPredicateName, ...] = ()
    incompatible_filters: tuple[RequiredFilter, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ValueConceptDefinition:
    field: str
    description: str
    natural_names: tuple[str, ...]
    members: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class RetrievalIntentDefinition:
    description: str
    natural_names: tuple[str, ...]
```

Move existing high-confidence phrase patterns from `_explicit_attendance_contract()` into these definitions.

Translate the existing `SEMANTIC_INTENT_PATTERNS` into one `RETRIEVAL_INTENT_DEFINITIONS` registry of literal natural names. It is consumed only by deterministic fact detection and mode derivation; because the LLM no longer selects a backend, this registry does not need to be rendered as planner instructions.

`default_answer_shape` is a default only. An explicit, grounded `group_by` determines the final grouped shape; the registry must not make a measure permanently scalar. Predicate incompatibility is interpreted as an unordered relationship, so it may be declared once and must not be duplicated in both definitions. Use `incompatible_filters` for declarative cross-column conflicts between a predicate and a directly requested filter; for example, the existing worked-versus-explicit-absence rule becomes metadata rather than `_filter_explicitly_selects_absence()` control flow.

Add generic value-family metadata:

```python
VALUE_CONCEPT_DEFINITIONS = MappingProxyType({
    "off_day": ValueConceptDefinition(
        field="Day_Type",
        description="Dates classified by the source system as either off-day category.",
        natural_names=("off day", "off days"),
        members=("OFF Day", "OFF Day (ZAS)"),
    ),
})
```

This entry is authoritative business metadata, not special compiler control flow. Do not add unapproved synonyms such as `rest day`.

- [ ] **Step 6: Generate compact planner schema from the registries**

Implement `render_planner_schema()` as compact JSON with deterministic item ordering and serialization. Include each planner-visible field's canonical name, normalized natural names, storage type, operators, resolution kind, controlled values, and roles. Include measures, predicates, incompatibilities, interpretations, and value concepts from their registries. Exclude `sql_expression` and other execution-only metadata.

- [ ] **Step 7: Run the whole schema suite**

```powershell
uv run python -m unittest week5.new_implementation.test_attendance_schema -v
```

- [ ] **Step 8: Commit the schema boundary**

```powershell
git add week5/new_implementation/attendance_schema.py week5/new_implementation/test_attendance_schema.py
git commit -m "refactor: define schema-grounded planner models"
```

---

### Task 2: Implement generic abstract resolvers and typed semantic facts

**Files:**
- Create: `week5/new_implementation/semantic_resolution.py`
- Create: `week5/new_implementation/test_semantic_resolution.py`

**Interfaces:**
- Consumes: semantic registries from `attendance_schema.py` and injected catalog/employee data.
- Produces: `SemanticFact`, `ResolutionContext`, `ResolutionOutcome`, `FieldResolver`, `ResolverRegistry`.
- Produces: `detect_semantic_facts(question: str, context: ResolutionContext) -> tuple[SemanticFact, ...]`.
- Produces: `merge_semantic_facts(*groups: Iterable[SemanticFact]) -> tuple[SemanticFact, ...]`.
- Produces the shared `normalize_semantic_text()` and `evidence_occurs()` helpers used by detection, candidate lookup, and compilation.

- [ ] **Step 1: Write failing resolver-contract tests**

```python
class SemanticResolutionTests(unittest.TestCase):
    def test_value_concept_uses_generic_resolver(self):
        context = ResolutionContext(catalog={
            "Day_Type": ("Working Day", "OFF Day", "OFF Day (ZAS)"),
        })
        facts = detect_semantic_facts("off days for A11017", context)
        fact = next(item for item in facts if item.concept_name == "off_day")
        self.assertEqual(fact.field, "Day_Type")
        self.assertEqual(fact.values, ("OFF Day", "OFF Day (ZAS)"))
        self.assertEqual(fact.evidence_text.casefold(), "off days")

    def test_partial_catalog_collision_requires_clarification(self):
        context = ResolutionContext(catalog={"Shift": ("Night A", "Night B")})
        result = ResolverRegistry.default().canonicalize(
            field="Shift", raw_value="night", evidence_text="night shift", context=context
        )
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(result.candidates, ("Night A", "Night B"))

    def test_every_field_resolution_kind_has_an_implementation(self):
        registry = ResolverRegistry.default()
        for definition in FIELD_DEFINITIONS.values():
            self.assertTrue(registry.supports(definition.resolution_kind))
```

Add a focused merge test proving that a resolved strong fact replaces its matching candidate while two disagreeing strong facts are both retained for contradiction checking.

- [ ] **Step 2: Run the new tests and verify import failure**

```powershell
uv run python -m unittest week5.new_implementation.test_semantic_resolution -v
```

- [ ] **Step 3: Implement immutable fact/result models**

```python
class EmployeeReference(BaseModel):
    model_config = ConfigDict(frozen=True)
    employee_id: str
    name: str


class SemanticFact(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["filter", "measure", "predicate", "calculation", "group_by", "order_by", "limit", "entity", "semantic_intent"]
    field: str | None
    operator: FilterOperator | None
    values: tuple[str | float, ...]
    concept_name: str | None
    evidence_text: str
    origin: EvidenceOrigin
    strength: Literal["strong", "candidate"]


@dataclass(frozen=True)
class ResolutionContext:
    catalog: Mapping[str, tuple[str, ...]]
    employees: tuple[EmployeeReference, ...] = ()


@dataclass(frozen=True)
class ResolutionOutcome:
    status: Literal["resolved", "ambiguous", "unknown", "semantic_only"]
    values: tuple[str | float, ...] = ()
    candidates: tuple[str, ...] = ()
```

`SemanticFact` is a frozen Pydantic model from the beginning because clarification state must serialize it. Keep employee reference data in this pure module or schema module; do not import `EmployeeCandidate` from `answer.py`.

Implement `merge_semantic_facts()` here rather than in the runtime. It preserves first-seen order, removes exact duplicates, replaces a candidate with a resolved strong fact for the same normalized semantic target/evidence/origin, and never drops two disagreeing strong facts; disagreement must remain visible to `ContradictionInvariant`.

- [ ] **Step 4: Implement the `FieldResolver` abstraction**

```python
class FieldResolver(ABC):
    @property
    @abstractmethod
    def kinds(self) -> frozenset[ResolutionKind]:
        raise NotImplementedError

    @abstractmethod
    def detect(self, question: str, field: str, context: ResolutionContext) -> tuple[SemanticFact, ...]:
        raise NotImplementedError

    @abstractmethod
    def canonicalize(self, field: str, raw_value: object, evidence_text: str, context: ResolutionContext) -> ResolutionOutcome:
        raise NotImplementedError
```

Implement `IdentifierResolver`, `EntityResolver`, `TemporalResolver`, `NumericResolver`, `ClosedValueResolver`, `CatalogValueResolver`, and `FreeTextResolver`. Do not create one subclass per column.

Build normalized phrase indexes as multi-maps from phrase to all matching canonical fields, measures, predicates, or value concepts. Never let a dictionary overwrite one registered meaning with another; if the same literal phrase maps to multiple meanings and surrounding evidence does not disambiguate it, emit candidates and require clarification.

- [ ] **Step 5: Implement one deterministic matching policy**

Normalize Unicode, case, whitespace, underscore/hyphen boundaries, and simple plural endings once in `normalize_semantic_text()`. This produces comparison keys only; canonical database values and identifiers are returned unchanged, and `Employee_ID` remains exact. `evidence_occurs()` uses that same representation and matches a non-empty token sequence with boundaries—not an arbitrary substring such as `off` inside `office`. Do not create separate prompt, lookup, and compiler normalization rules. Match in this order:

1. registered value concept;
2. exact canonical value;
3. exact approved value alias;
4. unique exact normalized catalog value;
5. bounded partial candidates requiring clarification;
6. unknown or semantic-only outcome.

Do not let fuzzy similarity silently create equality filters.

Value resolution is always scoped to the selected field definition. A value or alias that is valid in another column cannot justify switching fields, and a value concept carries its field explicitly. This is the generic rule that prevents any cross-column substitution.

For a registered alias or value-concept member on a `catalog` field, accept the mapped canonical value only when that value is present in the current injected catalog. Stale metadata must resolve as unknown rather than bypassing database-value validation.

- [ ] **Step 6: Generate baseline cases for every field**

Loop over `FIELD_DEFINITIONS`. Assert each canonical normalized field name is detectable, each closed value resolves to itself, and each resolution kind has exactly one owning resolver. Add cross-column collision fixtures for at least `Status` versus `OT_Authorized`, `Exception` versus `Day_Type`, and `Leave_Type` versus `Leave_Hrs`, plus a duplicate natural-name fixture proving the multi-map returns ambiguity instead of last-write-wins behavior.

- [ ] **Step 7: Run resolver and schema suites**

```powershell
uv run python -m unittest week5.new_implementation.test_semantic_resolution week5.new_implementation.test_attendance_schema -v
```

- [ ] **Step 8: Commit the resolver layer**

```powershell
git add week5/new_implementation/semantic_resolution.py week5/new_implementation/test_semantic_resolution.py
git commit -m "feat: add registry-driven semantic resolvers"
```

---

### Task 3: Compile proposals with abstract semantic invariants

**Files:**
- Create: `week5/new_implementation/plan_compiler.py`
- Create: `week5/new_implementation/test_plan_compiler.py`

**Interfaces:**
- Consumes: `PlannerProposal`, `SemanticFact`, `ResolverRegistry`, semantic registries.
- Produces: `CompilationContext`, `InvariantContext`, `PlanViolation`, `ConstraintProvenance`, `PlanCompilationResult`, `PlanInvariant`.
- Produces: `compile_proposal(proposal: PlannerProposal, context: CompilationContext) -> PlanCompilationResult`.
- Produces: `revalidate_executable_plan(plan: ExecutableQueryPlan, context: CompilationContext, provenance: tuple[ConstraintProvenance, ...]) -> PlanCompilationResult`.

- [ ] **Step 1: Write failing invariant tests**

Add the original cross-column mismatch without treating it as a special case:

```python
def test_valid_but_ungrounded_different_column_is_rejected(self):
    question = "off days for A11017"
    resolution_context = ResolutionContext(catalog={
        "Day_Type": ("Working Day", "OFF Day", "OFF Day (ZAS)"),
        "Exception": ("Absent", "Lateness", "OK"),
    })
    facts = detect_semantic_facts(question, resolution_context)
    proposal = PlannerProposal(
        status="ready",
        filters=[ProposedFilter(
            field="Exception", operator="eq", value="Absent", evidence_text="off days"
        )],
        measure=ProposedMeasureChoice(name="distinct_dates", evidence_text="days"),
        answer_contract=AnswerContract(
            shape="scalar", unit="dates", subject_field="Date", grain=["Date"]
        ),
    )
    result = compile_proposal(
        proposal,
        CompilationContext(
            question=question,
            facts=facts,
            resolution_context=resolution_context,
        ),
    )
    self.assertFalse(result.ready)
    self.assertEqual(
        {item.code for item in result.violations},
        {"ungrounded_constraint", "uncovered_fact"},
    )
```

Also test a correct generic value-family filter, false evidence, an uncovered date, a records/dates answer-contract conflict, incompatible predicates and predicate-versus-filter metadata, unknown fields/operators, ambiguous catalog values, unsupported capabilities, and deterministic exact/semantic/hybrid mode derivation.

- [ ] **Step 2: Run the compiler tests and verify import failure**

```powershell
uv run python -m unittest week5.new_implementation.test_plan_compiler -v
```

- [ ] **Step 3: Implement a small invariant abstraction**

```python
@dataclass(frozen=True)
class CompilationContext:
    question: str
    facts: tuple[SemanticFact, ...]
    resolution_context: ResolutionContext


@dataclass(frozen=True)
class PlanViolation:
    code: Literal[
        "invalid_schema",
        "ungrounded_constraint",
        "uncovered_fact",
        "contradiction",
        "answer_contract_mismatch",
        "unsupported_capability",
        "ambiguous_value",
    ]
    target: str
    message: str
    clarification_possible: bool = False


@dataclass(frozen=True)
class ConstraintProvenance:
    target_kind: Literal["filter", "measure", "predicate", "calculation", "group_by", "order_by", "limit", "entity"]
    field: str | None
    operator: FilterOperator | None
    values: tuple[str | float, ...]
    name: str | None
    direction: Literal["asc", "desc"] | None
    origin: EvidenceOrigin
    evidence_text: str


@dataclass(frozen=True)
class InvariantContext:
    compilation: CompilationContext
    proposal: PlannerProposal | None
    candidate_plan: QueryPlan
    provenance: tuple[ConstraintProvenance, ...]


class PlanInvariant(ABC):
    @abstractmethod
    def check(self, context: InvariantContext) -> tuple[PlanViolation, ...]:
        raise NotImplementedError
```

Register exactly these checks in deterministic order:

- `SchemaInvariant`
- `GroundingInvariant`
- `CoverageInvariant`
- `ContradictionInvariant`
- `AnswerContractInvariant`
- `CapabilityInvariant`

After defining those concrete classes, register them once:

```python
PLAN_INVARIANTS: tuple[PlanInvariant, ...] = (
    SchemaInvariant(),
    GroundingInvariant(),
    CoverageInvariant(),
    ContradictionInvariant(),
    AnswerContractInvariant(),
    CapabilityInvariant(),
)
```

Do not create an ABC for simple normalizers, model containers, or SQL fragments.

`SchemaInvariant` applies one visibility rule: an LLM-proposed field must be both allowed for its requested role and `planner_visible=True`; an internal non-visible field is accepted only when its provenance origin is `deterministic_default`.

`ContradictionInvariant` builds unordered predicate-incompatibility pairs from the registry, compares declared `incompatible_filters` against normalized executable filters, and evaluates each relationship once. Do not maintain a second `INCOMPATIBLE_BUSINESS_PREDICATE_SETS` table, require mirrored declarations, or branch on a named column/value in compiler code.

- [ ] **Step 4: Compile high-level choices into legacy execution fields**

`compile_proposal()` must:

1. reject `ready` without an answer contract;
2. verify every evidence substring with the shared `evidence_occurs()` helper;
3. canonicalize proposed values through `ResolverRegistry`;
4. compare proposal choices with independently detected strong facts;
5. derive executable mode from facts and compiled choices (`semantic` for semantic-only intent, `hybrid` for semantic intent plus structured constraints, otherwise `exact`) and derive `search_query` from the original question;
6. derive `aggregation` and `aggregation_field` from `measure` or `calculation`;
7. expand predicates and value concepts from registries;
8. validate group/order/limit roles against field metadata;
9. run all invariants;
10. keep the pre-validation object as plain `QueryPlan` while running invariants;
11. construct `ExecutableQueryPlan` only when no violation remains.

The compiler may repair spelling, casing, typed literals, registered aliases, and exact catalog casing. It must never silently change the selected field or discard an unsupported user fact. The model does not choose the backend mode or semantic search text. Detect `semantic_intent` through the existing registry-driven intent policy and free-text resolver, then apply the one mode rule above. For semantic/hybrid retrieval, use the stripped original question as the search query; do not add a second model-generated paraphrase or hardcoded rewrite table.

Apply one provenance-inheritance rule to all deterministic expansion:

- filters generated by a business predicate inherit that predicate's evidence and origin;
- aggregation fields generated by a measure inherit the measure's evidence and origin;
- members generated by a value concept inherit the concept phrase and origin;
- an ordering field and its direction share the cited ordering evidence, while provenance records the direction explicitly;
- an employee uniquely resolved from the current question retains `question` evidence; an explicit follow-up or previously selected employee uses `trusted_state`;
- `chunk_type` and other internal scopes use `deterministic_default`;
- relative-date filters retain the exact date phrase as question evidence.

No generated constraint is allowed to have empty provenance.

Add a contract test that `PLAN_INVARIANTS` contains each invariant class exactly once and in the documented order. This is the single standard validation pipeline used by both initial compilation and revalidation.

- [ ] **Step 5: Add immutable provenance and outcomes**

```python
class ConstraintCandidateData(BaseModel):
    field: str
    value: str
    label: str | None = None


class PendingConstraintData(BaseModel):
    field: str
    reference: str
    candidates: list[ConstraintCandidateData] = Field(default_factory=list)


@dataclass(frozen=True)
class PlanCompilationResult:
    executable_plan: ExecutableQueryPlan | None
    provenance: tuple[ConstraintProvenance, ...]
    violations: tuple[PlanViolation, ...]
    clarification: PendingConstraintData | None = None

    @property
    def ready(self) -> bool:
        return self.executable_plan is not None and not self.violations
```

Keep these clarification models in `plan_compiler.py` so pure compiler code has no upward dependency. `answer.py` may format them directly; do not duplicate equivalent models there.

- [ ] **Step 6: Run compiler, resolver, and schema tests**

```powershell
uv run python -m unittest week5.new_implementation.test_plan_compiler week5.new_implementation.test_semantic_resolution week5.new_implementation.test_attendance_schema -v
```

- [ ] **Step 7: Commit the compiler layer**

```powershell
git add week5/new_implementation/plan_compiler.py week5/new_implementation/test_plan_compiler.py week5/new_implementation/attendance_schema.py
git commit -m "feat: compile grounded executable query plans"
```

---

### Task 4: Add a registry-generated planner proposal path without changing production behavior

**Files:**
- Modify: `week5/new_implementation/answer.py:560-710`
- Modify: `week5/new_implementation/test_answer.py:142-515`

**Interfaces:**
- Produces: `propose_query(question: str, history: list[dict] | None = None, trusted_employees: list[EmployeeCandidate] | None = None, semantic_facts: tuple[SemanticFact, ...] = (), candidate_catalog: Mapping[str, tuple[str, ...]] | None = None) -> PlannerProposal`.
- Keeps temporarily: existing `plan_query() -> QueryPlan` production path until Task 6 passes the parity gate.

- [ ] **Step 1: Write failing prompt-source tests for `propose_query()`**

Patch LiteLLM, capture the prompt, and assert:

```python
self.assertIs(kwargs["response_format"], PlannerProposal)
self.assertIn("SEMANTIC REGISTRY", prompt)
self.assertEqual(prompt.count("SEMANTIC REGISTRY"), 1)
self.assertIn("Day_Type", prompt)
self.assertIn("Schedule_From_Date", prompt)
self.assertNotIn("record_json ->>", prompt)
self.assertNotIn('"name":"chunk_type"', prompt)
self.assertNotIn("For worked or attended days use", prompt)
self.assertNotIn("For explicit absent days use", prompt)
self.assertNotIn("If the user says", prompt)
self.assertNotIn("expected_sql", prompt)
```

Assert every field with `planner_visible=True` occurs in the rendered registry section and every field with `planner_visible=False` is absent. With no selected employee, assert employee names are absent. In separate tests, assert only trusted selected employees and explicitly supplied bounded `candidate_catalog` values appear—not the whole employee directory or unrelated catalog values. Complete high-cardinality catalogs must always be absent.

- [ ] **Step 2: Run the focused tests and confirm failure**

```powershell
uv run python -m unittest week5.new_implementation.test_answer.QueryPlannerSchemaTests -v
```

- [ ] **Step 3: Implement the new planner function alongside the legacy one**

Build the prompt from exactly four application-generated sections:

1. generic planner contract;
2. `render_planner_schema()` output;
3. deterministic date/fact context;
4. bounded relevant catalog and trusted-employee candidates.

Use only these generic planner rules:

```text
Return PlannerProposal, never SQL.
Use only definitions and values supplied by the semantic registry or candidate context.
Attach exact question evidence to every semantic choice.
Do not invent a field, value, predicate, measure, grouping, order, or limit.
Return ambiguous when evidence supports multiple meanings.
Return unsupported with controlled capability identifiers when the typed proposal cannot express the request.
```

Do not copy phrase-to-plan examples into the prompt. The schema registries already carry canonical meaning.

- [ ] **Step 4: Parse through the one proposal-model contract**

Use `PlannerProposal.model_validate_json()` and do not repeat its context-free rules in `answer.py`. Add tests proving JSON parsing, direct construction, and pending-state deserialization all reject the same invalid combinations defined in Task 1, including unknown extra keys such as `expected_sql`.

Keep context-dependent checks in `compile_proposal()`: an `ambiguous` status is valid only when interpretation candidates are present or a resolver actually returns ambiguity; evidence must occur in the question; and proposed fields, values, roles, and answer semantics must be grounded in the registry and detected facts. This separation gives one structural-validation path and one semantic-validation path.

- [ ] **Step 5: Run planner, date, and employee prompt tests**

```powershell
uv run python -m unittest week5.new_implementation.test_answer.QueryPlannerSchemaTests week5.new_implementation.test_answer.DateRangeResolutionTests week5.new_implementation.test_answer.EmployeeResolutionTests -v
```

The existing runtime still calls legacy `plan_query()` at this checkpoint.

- [ ] **Step 6: Commit the parallel planner path**

```powershell
git add week5/new_implementation/answer.py week5/new_implementation/test_answer.py
git commit -m "feat: add registry-generated planner proposals"
```

---

### Task 5: Build the all-column semantic parity gate before runtime migration

**Files:**
- Create: `week5/new_implementation/test_semantic_matrix.py`
- Modify: `week5/new_evaluation/test.py`
- Modify: `week5/new_evaluation/eval.py`
- Modify: `week5/new_evaluation/test_eval.py`

**Interfaces:**
- Produces: `generate_registry_cases() -> tuple[SemanticMatrixCase, ...]` in the test module.
- Adds optional evaluation expectations: `expected_violation_codes`, `expected_answer_contract`, `expected_unsupported_capabilities`.
- Does not change the production request path.

- [ ] **Step 1: Write a generated matrix test for every field category**

Define the test-only case type:

```python
@dataclass(frozen=True)
class SemanticMatrixCase:
    question: str
    proposal: PlannerProposal
    expected_field: str | None
    expected_operator: FilterOperator | None
    expected_value: object | None
```

Generate these cases from registry metadata:

- one representative positive filter case for every planner-visible filterable field, using its normalized canonical natural name and a type-correct value;
- every additional approved field natural name;
- every closed value;
- every approved value alias;
- every value concept;
- every measure natural name;
- every predicate natural name;
- every retrieval-intent natural name;
- every valid operator for each storage type;
- every groupable/orderable/aggregatable role.

For catalog fields, inject one deterministic catalog value per field into `ResolutionContext`; do not depend on the developer's live database. Free-text fields must prove semantic routing rather than fabricated equality. Add one rejection test showing a planner-proposed internal `planner_visible=False` field cannot compile.

Each generated case asserts that a matching proposal compiles and that the resulting executable field/operator/value agrees with registry metadata.

Keep generation bounded: one canonical field-name case per field, one case per declared alias/value/concept, and one operator-shape case per `(storage_type, operator)` pair. Do not create a Cartesian product of every field, value, operator, measure, and grouping.

- [ ] **Step 2: Add systematic negative mutations**

Generate one applicable negative mutation of each category per field or concept, rather than every possible combination:

- replace its field with a different allowed field;
- replace its value with a type-compatible valid value from another field;
- omit the strong fact;
- cite evidence absent from the question;
- use an invalid operator for the storage type;
- change the answer unit while retaining the calculation.

Each mutation must produce a stable violation and no executable plan.

Choose mutation inputs that pass basic Pydantic/type validation so the test reaches the intended semantic invariant. This prevents a schema-type error from falsely appearing to prove cross-column grounding.

- [ ] **Step 3: Add explicit cross-column collision cases**

Include at least:

```python
CROSS_COLUMN_CASES = (
    ("off days", "Day_Type", "Exception", "Absent"),
    ("authorized attendance records", "Status", "OT_Authorized", 1.0),
    ("leave type Annual Leave", "Leave_Type", "Leave_Hrs", 8.0),
    ("late hours over 2", "Lateness_Hrs", "Exception", "Lateness"),
)
```

The first tuple is a regression example; the assertions and compiler path remain generic.

- [ ] **Step 4: Run the matrix and confirm failures identify real gaps**

```powershell
uv run python -m unittest week5.new_implementation.test_semantic_matrix -v
```

Fix missing registry metadata in `attendance_schema.py`, not by adding case-specific compiler branches.

- [ ] **Step 5: Extend evaluation models without requiring private-corpus changes**

Add optional fields with empty defaults to `TestQuestion`. Update `BehaviorEval` to compare compiler violations and answer contracts deterministically. Existing JSONL lines remain valid.

- [ ] **Step 6: Run all pre-migration gates**

```powershell
uv run python -m unittest week5.new_implementation.test_attendance_schema week5.new_implementation.test_semantic_resolution week5.new_implementation.test_plan_compiler week5.new_implementation.test_semantic_matrix week5.new_evaluation.test_eval -v
```

Required result: all pass before any production call is switched from `plan_query()` to `propose_query()`.

- [ ] **Step 7: Commit the parity gate**

```powershell
git add week5/new_implementation/test_semantic_matrix.py week5/new_implementation/attendance_schema.py week5/new_evaluation/test.py week5/new_evaluation/eval.py week5/new_evaluation/test_eval.py
git commit -m "test: add all-column semantic plan matrix"
```

---

### Task 6: Migrate runtime and conversation state to the trusted plan boundary

**Files:**
- Modify: `week5/new_implementation/answer.py:180-260`
- Modify: `week5/new_implementation/answer.py:840-1030`
- Modify: `week5/new_implementation/answer.py:1460-2320`
- Modify: `week5/new_implementation/answer.py:3496-3690`
- Modify: `week5/new_implementation/answer.py:3920-4140`
- Modify: `week5/new_implementation/test_answer.py:725-3085`
- Modify: `week5/test_new_app.py`

**Interfaces:**
- Changes: `ContextFetchResult.plan` to `ExecutableQueryPlan`.
- Changes internal prepared input to `prepared_proposal: PlannerProposal | None` plus `prepared_facts: tuple[SemanticFact, ...]`.
- Removes the optional `fetch_context(..., prepared_plan=...)` bypass after migrating its test-only callers; repository search currently shows no production caller outside `answer.py`.
- Preserves the public four-item `fetch_context()` return value.
- Produces infrastructure adapter `load_attendance_catalog_candidates(question: str, proposed_filters: tuple[ProposedFilter, ...] = ()) -> dict[str, tuple[str, ...]]`.
- Adds the local boundary adapter `_employee_references(candidates: Sequence[EmployeeCandidate] | None) -> tuple[EmployeeReference, ...]`; the pure resolver layer must not import runtime UI models.
- Changes `ConversationState.pending_plan` to `pending_proposal` and `pending_facts`.
- Produces: `SemanticPlanValidationError(PlanValidationError)` carrying stable `violations` for deterministic evaluation and logging.

- [ ] **Step 1: Write failing boundary and no-retrieval tests**

Assert the old bypass is no longer accepted:

```python
with self.assertRaises(TypeError):
    answer.fetch_context(question, prepared_plan=plain_query_plan)
```

Mock `load_attendance_catalog_candidates()` with a bounded fixture and make `propose_query()` return `Exception=Absent` supported only by the phrase `off days`. Assert `PlanValidationError`; employee directory, attendance-record PostgreSQL execution, Chroma, aggregation, reranking, and final-answer mocks must remain uncalled. The bounded catalog-candidate adapter is allowed before compilation.

Add the successful generic value-concept case and assert the executable filter is:

```python
FilterCondition(
    field="Day_Type",
    operator="in",
    value=["OFF Day", "OFF Day (ZAS)"],
)
```

- [ ] **Step 2: Write failing multi-turn state tests**

Cover each existing clarification path:

- employee ambiguity;
- catalog-value ambiguity;
- interpretation ambiguity;
- selected employee follow-up;
- stale employee revalidation.

Assert state stores `pending_proposal`, never an executable or plain pending plan. On the follow-up turn, add the chosen value as a `trusted_state` fact, re-run compilation, and only then retrieve.

- [ ] **Step 3: Run safety and state tests and confirm failure**

```powershell
uv run python -m unittest week5.new_implementation.test_answer.ExecutablePlanSafetyTests week5.new_implementation.test_answer.ClarificationStateTests week5.test_new_app -v
```

- [ ] **Step 4: Add serializable pending planning state**

Use the frozen Pydantic `SemanticFact` created in Task 2 so Gradio/Pydantic state can serialize it. Update `ConversationState`:

```python
class ConversationState(BaseModel):
    selected_employees: list[EmployeeCandidate] = Field(default_factory=list)
    pending_question: str | None = None
    pending_proposal: PlannerProposal | None = None
    pending_facts: list[SemanticFact] = Field(default_factory=list)
    pending_candidates: list[EmployeeCandidate] = Field(default_factory=list)
    pending_constraint: PendingConstraintData | None = None
    pending_interpretations: list[InterpretationName] = Field(default_factory=list)
```

Remove `pending_plan` only after all state callers and tests migrate in this task.

- [ ] **Step 5: Enforce the new runtime order**

Inside `_fetch_context_result()`:

```python
trusted_access = _require_attendance_access(access_context)
_require_supported_attendance_question(question)
pre_catalog = load_attendance_catalog_candidates(question)
pre_context = ResolutionContext(
    catalog=pre_catalog,
    employees=_employee_references(default_employees),
)
initial_facts = tuple(prepared_facts) or detect_semantic_facts(question, pre_context)
proposal = prepared_proposal or propose_query(
    question,
    history,
    trusted_employees=default_employees,
    semantic_facts=initial_facts,
    candidate_catalog=pre_catalog,
)
if proposal.status == "unsupported":
    rejected = compile_proposal(
        proposal,
        CompilationContext(
            question=question,
            facts=initial_facts,
            resolution_context=pre_context,
        ),
    )
    raise SemanticPlanValidationError(rejected.violations)
if proposal.status == "ambiguous" and proposal.interpretation_candidates:
    raise InterpretationClarificationRequired(
        proposal=proposal,
        facts=initial_facts,
        candidates=proposal.interpretation_candidates,
    )
catalog = load_attendance_catalog_candidates(
    question,
    proposed_filters=tuple(proposal.filters),
)
resolution_context = ResolutionContext(
    catalog=catalog,
    employees=_employee_references(default_employees),
)
facts = merge_semantic_facts(
    initial_facts,
    detect_semantic_facts(question, resolution_context),
)
compilation = compile_proposal(
    proposal,
    CompilationContext(
        question=question,
        facts=facts,
        resolution_context=resolution_context,
    ),
)
if compilation.clarification is not None:
    raise ConstraintClarificationRequired(
        proposal=proposal,
        facts=facts,
        pending=compilation.clarification,
    )
if not compilation.ready:
    raise SemanticPlanValidationError(compilation.violations)
assert compilation.executable_plan is not None
plan = compilation.executable_plan
```

The access and basic-input checks must run before either catalog-candidate pass. `load_attendance_catalog_candidates()` searches only registry-declared, planner-visible catalog fields and uses bound lookup values plus `settings.constraint_candidate_limit`. Before the planner it queries fields explicitly named by the question; after the proposal it accepts a field only when its canonical name, catalog role, operator shape, and normalized evidence substring pass deterministic checks, then queries using the cited value. Unknown/internal fields never select a SQL expression. It never sends complete employee/name catalogs or unbounded distinct values to the LLM. An `unsupported` proposal stops after capability validation and does not trigger the second lookup.

Define the error without duplicating validation logic:

```python
class SemanticPlanValidationError(PlanValidationError):
    def __init__(self, violations: tuple[PlanViolation, ...]):
        self.violations = violations
        super().__init__(format_plan_violations(violations))
```

Only after successful compilation may full employee directory resolution, backend selection, `chunk_type` deterministic scoping, or attendance retrieval occur. If a name hint remains, resolve it through the existing employee stage, replace it with the verified `Employee_ID` constraint, clear the hint, and require `revalidate_executable_plan()` to return a final executable plan before choosing a backend. `format_plan_violations()` is a small user-safe formatter in `answer.py`. Any constraint added after compilation must carry `trusted_state` or `deterministic_default` provenance and pass the same revalidation pipeline.

- [ ] **Step 6: Migrate all clarification exceptions**

Use one consistent pending payload on every exception:

```python
class PlanningClarificationRequired(ValueError):
    def __init__(
        self,
        proposal: PlannerProposal,
        facts: tuple[SemanticFact, ...],
    ):
        self.proposal = proposal
        self.facts = facts
        super().__init__("planning clarification required")


class ConstraintClarificationRequired(PlanningClarificationRequired):
    def __init__(self, proposal, facts, pending: PendingConstraintData):
        super().__init__(proposal, facts)
        self.pending = pending


class InterpretationClarificationRequired(PlanningClarificationRequired):
    def __init__(self, proposal, facts, candidates: list[InterpretationName]):
        super().__init__(proposal, facts)
        self.candidates = candidates


class EmployeeClarificationRequired(PlanningClarificationRequired):
    def __init__(self, proposal, facts, resolution: EmployeeResolution):
        super().__init__(proposal, facts)
        self.resolution = resolution
```

On every catch, store `exc.proposal` and `exc.facts`. Follow-up selection modifies proposal choices, adds a `trusted_state` fact, and re-enters `compile_proposal()`; it must not directly mutate an executable filter and bypass validation. Clear `pending_proposal`, `pending_facts`, candidates, constraint, and interpretations together on success, terminal validation error, or cancelled clarification.

- [ ] **Step 7: Remove old runtime semantic sources in the same enforced change**

Delete production uses of:

- legacy `plan_query()`;
- `_CANONICAL_QUESTION_FILTERS`;
- `_STRUCTURED_QUESTION_FIELDS` when registry resolution replaces it;
- `_explicit_attendance_contract()`;
- `_apply_explicit_attendance_contract()`;
- `_filter_explicitly_selects_absence()`;
- `INCOMPATIBLE_BUSINESS_PREDICATE_SETS` after incompatibilities live on predicate definitions;
- phrase-specific sections of `_compile_required_constraints()`;
- the legacy `compile_business_intent()` after `plan_compiler.py` owns measure/predicate expansion and contradiction checks;
- prompt rules that map words to concrete measures/predicates/filters;
- legacy regex `SEMANTIC_INTENT_PATTERNS` after literal `RETRIEVAL_INTENT_DEFINITIONS` owns semantic-mode detection;
- `_CATALOG_FIELDS` after `resolution_kind` owns catalog policy;
- legacy `FieldDefinition.aliases` and `QUESTION_CONTEXT_FIELDS` after `natural_names` owns question-field matching and context selection.

Keep generic access, date parsing, finite numeric checks, type/operator validation, and SQL safety; move them into the new generic layers where appropriate.

- [ ] **Step 8: Run all runtime/state regressions**

```powershell
uv run python -m unittest week5.new_implementation.test_answer week5.test_new_app -v
```

- [ ] **Step 9: Commit the atomic runtime switch**

```powershell
git add week5/new_implementation/answer.py week5/new_implementation/attendance_schema.py week5/new_implementation/semantic_resolution.py week5/new_implementation/plan_compiler.py week5/new_implementation/test_answer.py week5/test_new_app.py
git commit -m "refactor: enforce semantic compilation before retrieval"
```

---

### Task 7: Make Python-generated SQL an explicit typed artifact

**Files:**
- Create: `week5/new_implementation/postgres_compiler.py`
- Create: `week5/new_implementation/test_postgres_compiler.py`
- Modify: `week5/new_implementation/answer.py:2427-2835`

**Interfaces:**
- Produces: `SqlFragment`, `CompiledPostgresQuery`, `compile_where()`, `compile_count_query()`, `compile_sample_query()`, `compile_aggregation_queries()`.
- Consumes: `ExecutableQueryPlan`, `FilterCondition`, `POSTGRES_FIELD_MAP`, trusted configured table name.
- Restricts: cursor execution receives only `CompiledPostgresQuery` objects created by this module.

- [ ] **Step 1: Write failing SQL boundary and parameterization tests**

```python
class PostgresCompilerTests(unittest.TestCase):
    def test_question_value_is_a_bound_parameter(self):
        fragment = compile_where([
            FilterCondition(
                field="Department",
                operator="eq",
                value="HR' OR 1=1 --",
            )
        ])
        self.assertNotIn("HR' OR 1=1 --", fragment.sql)
        self.assertEqual(fragment.params, ("HR' OR 1=1 --",))

    def test_plain_query_plan_is_rejected(self):
        with self.assertRaises(TypeError):
            compile_count_query(QueryPlan(mode="exact", search_query="records"))

    def test_planner_proposal_is_rejected(self):
        with self.assertRaises(TypeError):
            compile_count_query(PlannerProposal(
                status="unsupported",
                unsupported_capabilities=["nested_boolean_filters"],
            ))
```

Also test all operators, empty/invalid `in`, invalid aggregation fields, grouped calculations, percentages, ordering, limits, and malicious values. Assert unknown identifiers fail before any cursor is opened.

- [ ] **Step 2: Run the new tests and confirm import failure**

```powershell
uv run python -m unittest week5.new_implementation.test_postgres_compiler -v
```

- [ ] **Step 3: Implement immutable SQL artifacts**

```python
@dataclass(frozen=True)
class SqlFragment:
    sql: str
    params: tuple[object, ...]


@dataclass(frozen=True)
class CompiledPostgresQuery:
    sql: str
    params: tuple[object, ...]
    purpose: Literal["sample", "count", "aggregation", "coverage"]
    fingerprint: str
```

`fingerprint` is SHA-256 over normalized SQL without parameter values. Field and order expressions come only from `POSTGRES_FIELD_MAP`. Table names come only from validated application configuration.

- [ ] **Step 4: Move every exact-query construction path**

Move pure construction for:

- where clauses;
- record samples;
- matching-row counts;
- scalar aggregations;
- grouped aggregations;
- percentage numerator/denominator;
- coverage bounds.

`compile_aggregation_queries()` returns a tuple because percentage calculations require separate denominator and numerator queries. The executor maps returned rows to the existing calculation result shape.

- [ ] **Step 5: Keep database I/O in `answer.py`**

Retain connection creation, cursor lifetime, and:

```sql
SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY
```

Make `execute_exact_postgres(plan: ExecutableQueryPlan)` the only plan-level PostgreSQL entry point. Replace the current filter-accepting helpers with private row/scalar executors that accept `CompiledPostgresQuery`:

```python
def _execute_rows_query(query: CompiledPostgresQuery, connection) -> list[dict]:
    with connection.cursor() as cursor:
        cursor.execute(query.sql, query.params)
        return list(cursor.fetchall())


def _execute_scalar_query(query: CompiledPostgresQuery, connection):
    with connection.cursor() as cursor:
        cursor.execute(query.sql, query.params)
        return cursor.fetchone()
```

`fetch_exact_postgres()`, `count_exact_postgres()`, and `calculate_aggregation_postgres()` must no longer be alternate public paths that accept raw filters and rebuild SQL independently. Keep result-mapping helpers pure and private. Do not expose any `execute_sql(sql: str)` or equivalent function.

- [ ] **Step 6: Run SQL, result-parity, and injection tests**

```powershell
uv run python -m unittest week5.new_implementation.test_postgres_compiler week5.new_implementation.test_answer.PostgresResultParityTests week5.new_implementation.test_answer.PostgresResultTests -v
```

- [ ] **Step 7: Commit the SQL boundary**

```powershell
git add week5/new_implementation/postgres_compiler.py week5/new_implementation/test_postgres_compiler.py week5/new_implementation/answer.py
git commit -m "refactor: isolate trusted postgres query compilation"
```

---

### Task 8: Add semantic observability and unsupported-capability reporting

**Files:**
- Modify: `week5/new_implementation/answer.py:3496-3615`
- Modify: `week5/new_implementation/observability.py`
- Modify: `week5/new_implementation/test_observability.py`
- Modify: `week5/new_evaluation/test.py`
- Modify: `week5/new_evaluation/eval.py`
- Modify: `week5/new_evaluation/test_eval.py`

**Interfaces:**
- Adds events: `semantic_facts_detected`, `planner_proposal_received`, `proposal_rejected`, `executable_plan_compiled`, `postgres_query_compiled`.
- Extends behavior results with violation codes, answer contract, and unsupported capability comparisons.

- [ ] **Step 1: Write failing observability redaction tests**

Assert event payloads contain counts, plan mode, operation, stable violation codes, capability identifiers, and SQL fingerprint where applicable. Assert they never contain:

- full question or evidence text;
- employee names or IDs;
- catalog values selected by the user;
- SQL parameters;
- PostgreSQL DSN;
- complete raw SQL.

- [ ] **Step 2: Write failing unsupported-capability tests**

Use:

```python
PlannerProposal(
    status="unsupported",
    unsupported_capabilities=["nested_boolean_filters"],
    explanation="The request requires nested AND/OR grouping.",
)
```

Mock the bounded catalog-candidate adapter. Assert no employee directory, attendance-record PostgreSQL execution, Chroma, or final-answer LLM call occurs. Return a concise safe message explaining that the request cannot yet be represented safely.

- [ ] **Step 3: Run focused tests and confirm failure**

```powershell
uv run python -m unittest week5.new_implementation.test_observability week5.new_implementation.test_answer.ExecutablePlanSafetyTests week5.new_evaluation.test_eval -v
```

- [ ] **Step 4: Emit sanitized semantic lifecycle events**

Use stable payloads such as:

```python
event_logger.emit(
    "proposal_rejected",
    request_id=request_id,
    stage="semantic_validation",
    state="rejected",
    violation_codes=sorted({item.code for item in compilation.violations}),
    violation_count=len(compilation.violations),
)
```

For SQL, emit only purpose, fingerprint, and parameter count.

Treat `PlannerProposal.explanation` as untrusted diagnostic input: never show it directly to users and never include it in production events. User-facing unsupported and validation messages are selected by Python from controlled capability/violation codes.

- [ ] **Step 5: Extend deterministic behavior evaluation**

Add these optional fields to `TestQuestion` with empty defaults:

```python
expected_violation_codes: list[str] = Field(default_factory=list)
expected_answer_contract: dict | None = None
expected_unsupported_capabilities: list[str] = Field(default_factory=list)
```

Update `BehaviorEval` and `evaluate_behavior()` to compare them without an LLM judge. Preserve compatibility with every current JSONL row.

Catch `SemanticPlanValidationError` before the existing broad exception handler and read codes from `exc.violations`; do not parse human-readable exception text to determine behavior results.

- [ ] **Step 6: Report unsupported plan-language gaps offline**

Add a CLI behavior-report section that groups failed cases by controlled capability identifier and case index. It may include the stored evaluation question only in local console output when existing evaluation privacy policy allows it. It must never execute or store model-proposed SQL.

- [ ] **Step 7: Run observability and evaluation tests**

```powershell
uv run python -m unittest week5.new_implementation.test_observability week5.new_evaluation.test_eval week5.new_evaluation.test_benchmark -v
```

- [ ] **Step 8: Commit semantic diagnostics**

```powershell
git add week5/new_implementation/answer.py week5/new_implementation/observability.py week5/new_implementation/test_observability.py week5/new_evaluation/test.py week5/new_evaluation/eval.py week5/new_evaluation/test_eval.py
git commit -m "feat: report semantic planning failures safely"
```

---

### Task 9: Verify the private corpus, remove compatibility code, and prove final invariants

**Files:**
- Modify only files already named in Tasks 1-8 when verification identifies a defect or obsolete adapter.

**Interfaces:**
- Verifies all schema, planner, state, retrieval, PostgreSQL, Chroma, evaluation, and Gradio contracts.

- [ ] **Step 1: Add or verify the original regression in the authorized corpus**

When `week5/new_evaluation/tests.jsonl` is present, add or confirm a case for the A11017 September 2026 off-day question with:

- executable `Day_Type in [OFF Day, OFF Day (ZAS)]`;
- distinct count of `Date`;
- no `Exception=Absent`;
- answer fact `2` for the currently verified dataset;
- partial-period coverage wording.

The all-column generated matrix remains the primary proof that the architecture is general.

- [ ] **Step 2: Run the complete implementation suite**

```powershell
uv run python -m unittest discover -s week5/new_implementation -p "test_*.py" -v
```

- [ ] **Step 3: Run application and evaluation wiring**

```powershell
uv run python -m unittest week5.test_new_app week5.test_new_evaluator week5.new_evaluation.test_eval week5.new_evaluation.test_benchmark -v
```

- [ ] **Step 4: Verify dataset identity and all deterministic behavior cases**

```powershell
uv run python -m week5.new_evaluation.eval --verify-dataset
uv run python -m week5.new_evaluation.eval --behavior --all
```

Expected: dataset verification succeeds and the behavior report has zero failures. If private files are unavailable, record that fact and do not represent private-corpus verification as passed.

- [ ] **Step 5: Search for duplicated sources and forbidden model SQL**

```powershell
rg -n "_CANONICAL_QUESTION_FILTERS|_STRUCTURED_QUESTION_FIELDS|_explicit_attendance_contract|_apply_explicit_attendance_contract|_filter_explicitly_selects_absence|INCOMPATIBLE_BUSINESS_PREDICATE_SETS|compile_business_intent\(|SEMANTIC_INTENT_PATTERNS|_CATALOG_FIELDS|QUESTION_CONTEXT_FIELDS|definition\.aliases|For worked or attended days use|For explicit absent days use|expected_sql|custom_sql|execute_sql" week5/new_implementation week5/new_evaluation
```

Expected: no production business mapping or raw model-SQL execution path. Negative test assertions may contain these strings.

- [ ] **Step 6: Verify no obsolete plan/state boundary remains**

```powershell
rg -n "pending_plan|prepared_plan|ContextFetchResult.*QueryPlan|resolve_catalog_constraints\(|catalog_resolution" week5/new_implementation week5/new_evaluation week5/test_new_app.py
```

Expected: no `pending_plan` or `prepared_plan` bypass remains. Remove obsolete compatibility properties only when no production caller remains.

- [ ] **Step 7: Inspect final changes without disturbing prior user work**

```powershell
git status --short
git diff --check
git diff --stat
git diff --cached --check
git diff --cached
```

Expected: no whitespace errors, no unrelated files, and no overwritten pre-existing changes.

- [ ] **Step 8: Commit verification cleanup only when files changed**

```powershell
git add week5/new_implementation week5/new_evaluation week5/test_new_app.py
git commit -m "chore: complete schema-grounded query migration"
```

## Acceptance Criteria

1. The planner output type cannot be passed directly to any retrieval or SQL compiler function.
2. A valid field/value chosen without semantic support is rejected before employee-directory or attendance-record retrieval; only bounded catalog-candidate lookup is permitted beforehand.
3. Every strong field, value, employee, date, measure, predicate, grouping, ordering, or limit fact is covered or explicitly clarified.
4. Every registered column has complete natural-name, type, operator, role, and resolution metadata.
5. Every resolution kind is implemented by one registered strategy; no implementation is selected by column-specific branching.
6. Prompt schema and deterministic validation are generated from the same registry.
7. Phrase-to-filter and phrase-to-measure prompt rules are removed after the new gate is enforced.
8. Multi-turn clarification stores an untrusted pending proposal plus trusted facts and recompiles before retrieval.
9. The LLM returns `AnswerContract` and controlled unsupported capabilities, never executable SQL, a backend mode, or a separate semantic-query rewrite.
10. Unsupported requests fail closed and are reported for typed language evolution.
11. PostgreSQL identifiers come from trusted configuration/registry and values remain bound parameters.
12. The original off-day regression passes, plus generated and hand-written cross-column mutation tests pass.
13. Existing employee selection, partial-period coverage, Chroma fallback, deterministic formatting, evaluation, and Gradio behavior remain green.
