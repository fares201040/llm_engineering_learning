# APDC tolerant multilingual input boundary

## Purpose

The attendance assistant accepts concise, noisy, English, Arabic, and mixed-language input without allowing uncertain wording to weaken the verified query pipeline. Tolerance is limited to interpreting the user-facing surface. Executable meaning remains registry-owned and compiler-verified.

## Processing order

1. Verify attendance access and reject clearly out-of-domain input.
2. Preserve the original question and analyze its surface form.
3. Normalize only safe presentation differences: Unicode NFKC, casing, whitespace, punctuation, Arabic diacritics and tatweel, Arabic/Latin digits, and conservative Arabic letter variants.
4. Resolve employee identity against the authorized directory.
5. Pause for every non-exact employee name. Exact IDs and unique exact full names may proceed.
6. Promote only exact aliases or one fuzzy semantic candidate with score at least `0.90` and a margin of at least `0.08` over a candidate for the same source span.
7. Pause for lower-confidence semantic candidates and expose only finite registry-derived choices.
8. Verify that the request identifies a supported result operation. A predicate-only request may use exactly one entailed `INTERPRETATION_PRESETS` entry. A bare employee never retrieves records.
9. Assemble a proposal from strong facts, compile it to an `ExecutableQueryPlan`, revalidate all executable choices, and only then retrieve.
10. Render deterministic profiles, projections, and calculations in the preserved reply locale. Narrative answers receive a verified reply-language instruction and bounded evidence.

No unresolved request reaches catalog expansion, provider planning, retrieval, reranking, calculation, or final completion.

## Employee policy

Resolution precedence is exact employee ID, exact full name, duplicate exact name, then prefix/substring/reordered/transliterated/fuzzy matching. Every match in the last group requires confirmation, even when there is only one candidate.

Choices contain only `Name — Employee ID`, are bounded by the configured constraint candidate limit, and are ordered by match quality, normalized name, and employee ID. Arabic-Indic selection numbers are accepted. `all` and `both` are accepted only if the saved original request explicitly requested multiple employees.

The original request, reply locale, strong facts, safe prepared proposal when one exists, and displayed finite choices are retained in a discriminated `PendingClarification`. The older public `ConversationState` fields remain mirrored for compatibility, but new clarification metadata is stored in the typed union. A confirmed choice is revalidated against the current directory before execution. A complete new attendance question cancels the pending choice.

## Employee profiles

`employee_profile` is a registered result intent. It may expose only:

- `Employee_ID`
- `Name`
- `Department`
- `Position`
- `Work_Location`

The compiler derives this projection from the registry; the provider cannot author or alter it. PostgreSQL uses a parameterized `SELECT DISTINCT` produced from an `ExecutableQueryPlan`. Chroma uses the same verified filter and projection semantics. Multiple recorded values are preserved and rendered in deterministic sorted order instead of silently selecting one value. Profile rendering never calls the final LLM.

## Localization

Arabic is selected when Arabic alphabetic tokens equal or outnumber Latin alphabetic tokens; otherwise English is selected. The locale is retained across clarification turns. Employee clarifications, semantic and catalog clarifications, missing-intent prompts, profiles, projections, aggregations, and safe interpretation errors have deterministic localized forms.

Material automatic corrections may be disclosed, for example: `I understood “wokred days” as “worked days.”` Harmless casing, spacing, punctuation, and digit normalization are not announced.

## Privacy-safe observability

Input events may contain only request ID, stage/state, reply locale, understanding or clarification kind, candidate/correction counts, and match-method counts. Questions, source evidence, employee names, employee IDs, candidate labels, catalog values, scores, SQL, and SQL parameters are not emitted.

The local evaluator may display its separately requested `Question` column. That UI behavior is not part of diagnostic serialization and is intentionally kept in separate uncommitted work in the original checkout.

## Preserved fail-closed boundaries

The feature does not add raw SQL, model-generated SQL, complete-question templates, typo dictionaries, employee-specific rules, expected-answer mappings, silent fallbacks, or partial execution. Nested Boolean filters, HAVING, window calculations, cross-period comparisons, grouped percentages, unsupported calculations, and genuine multi-stage aggregation remain unsupported and fail closed.

## Current limitations

- Supported reply/input languages in this phase are English, Arabic, and mixtures of the two.
- Transliteration is conservative and heuristic; uncertain transliterations always require confirmation.
- Low-confidence wording is clarified rather than guessed, so some uncommon paraphrases still require one user choice.
- Arabic business aliases cover registered attendance concepts; unsupported advanced operations remain unsupported regardless of language.
- Provider-backed quality checks and private-corpus checks remain separate from deterministic correctness and require their authorized runtime data and provider health.
