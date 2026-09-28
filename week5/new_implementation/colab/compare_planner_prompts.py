"""Compare two SQL planner prompts on the same complex private cases in Colab."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from time import perf_counter


RUNTIME_CONFIG = Path("/content/.attendance_private_runtime.json")

LEAN_SQL_SYSTEM = """You plan one PostgreSQL SELECT query for an attendance chat.
Read the current question and updated_request with the full verified conversation
history. Use prior turns to resolve references, but let the current question decide
which facts to retrieve. Follow the application's verified employee and date scope.
subject_relationship describes employees, criteria, their union or intersection,
or all authorized employees. When null, determine the subject from the current
question and trusted context, or ask for clarification if a person is unresolved.
Apply the resolved date and user-requested category
predicates to every relevant branch of the query.
Table date coverage describes available rows. For a request without a date
period, do not turn the first and last observed row dates into SQL filters or
carry them as a user-requested interval into a follow-up.
Use scope_provenance to compare the current original question, previous original
question, and previous executed SQL before carrying a person or location forward.
A value only mentioned in a prior answer or result is not a user-requested filter.
If rewritten scope conflicts with a broad new request, plan for the current
request while retaining genuinely requested follow-up constraints.
Read the supplied schema field descriptions, standard values, and business meanings.
Choose the fields and predicates that match the user's meaning;
use typed columns when available and documented JSON expressions otherwise.
When the request asks for a recorded category, use that category's field and exact
stored value. A zero or NULL in a numeric measure is not evidence of a different
category. Combine alternative conditions only when the request asks for their union.
For a requested employee attendance report with overtime kinds, derive separate
Normal OT, Week Off OT, and Night OT values from the documented type/value pairs;
sum current OT_Value_1 and OT_Value_2 for current total overtime. The immutable
OT_Authorized field is only the original security/audit baseline after adjustments.
For an unqualified employee overtime request, include the current totals of all
three kinds and their overall current total over the requested period, or over
available records if no period was requested.
When a follow-up negates a recorded category, negate that category predicate while
preserving its NULL meaning; do not substitute a different measure such as positive
worked hours for a category's negation.

Return enough data to answer every requested part. Use a scalar aggregate for a
single count or total, grouped bounded rows for a ranking, and flat bounded rows
with record_id and COUNT(*) OVER() AS matched_count for record details. For employee
attributes, select distinct employee ID, name, and requested attributes, without
enumerating duplicate records. For identity, select the resolved ID and name.
Keep relevant context for follow-ups, including
the metric and eligible groups in a comparison. Use as_of_date for relative dates.
If the user selects a clarification option, answer the pending question about the
resolved employee; the selection alone does not request attendance details.

For an unsupported concept or invalid literal, return one SELECT with a
request-specific explanation as a text literal named unsupported_capability.
Keep the literal brief and in user terms; avoid hypothetical tables, example SQL,
and join instructions unless the user requested implementation details.
For unresolved ambiguity, return one SELECT with a precise question as a text
literal named clarification_required. Both have no FROM clause. If
sql_execution_failure is supplied, correct that SQL
against the unchanged request and schema. Return exactly one raw SELECT or WITH
statement and nothing else."""


def _complexity(case: object) -> int:
    score = 0
    score += 4 if getattr(case, "turns", None) else 0
    score += 3 if getattr(case, "expected_group_values", None) else 0
    score += 3 if getattr(case, "expected_calculation", None) else 0
    score += 2 if getattr(case, "expected_plan", None) else 0
    score += min(len(getattr(case, "expected_answer_facts", ())), 3)
    return score


def _choose_cases(cases: list[object], limit: int = 8) -> list[int]:
    ranked = sorted(range(len(cases)), key=lambda i: (-_complexity(cases[i]), i))
    chosen: list[int] = []
    categories: set[str] = set()
    for index in ranked:
        category = str(getattr(cases[index], "category", ""))
        if _complexity(cases[index]) < 3 or category in categories:
            continue
        chosen.append(index)
        categories.add(category)
        if len(chosen) == limit:
            return chosen
    for index in ranked:
        if index not in chosen and _complexity(cases[index]) >= 3:
            chosen.append(index)
        if len(chosen) == limit:
            break
    return chosen


def main() -> int:
    settings = json.loads(RUNTIME_CONFIG.read_text(encoding="utf-8"))
    if not settings.get("OPENAI_API_KEY"):
        raise RuntimeError("GPT planner credentials are unavailable")
    source = Path(settings["ATTENDANCE_PHASE2_SOURCE_ROOT"])
    os.environ.update(settings)
    sys.path.insert(0, str(source))

    from week5.new_evaluation import eval as evaluation
    from week5.new_evaluation.test import load_tests
    from week5.new_implementation.online import pipeline, planner

    cases = load_tests(test_file=Path(settings["ATTENDANCE_PRIVATE_CASE_FILE"]))
    selected = _choose_cases(cases)
    if requested_indices := os.environ.get("PROMPT_CASE_INDICES"):
        selected = [int(value.strip()) for value in requested_indices.split(",")]
        if any(index < 0 or index >= len(cases) for index in selected):
            raise ValueError("PROMPT_CASE_INDICES contains an invalid case index")
    requested = os.environ.get("PROMPT_VARIANT", "").casefold()
    if requested not in {"guided", "lean"}:
        raise ValueError("PROMPT_VARIANT must be guided or lean")
    report_suffix = "-diagnostic" if requested_indices else ""
    report_path = Path(f"/content/attendance-prompt-{requested}{report_suffix}.json")
    report = {"selected_indices": selected, "runs": []}
    original_prompt = planner._SYSTEM
    for version, prompt in (("guided", original_prompt), ("lean", LEAN_SQL_SYSTEM)):
        if version != requested:
            continue
        planner._SYSTEM = prompt
        for index in selected:
            started = perf_counter()
            turns: list[dict[str, object]] = []
            sql_attempts: list[dict[str, object]] = []
            original_run_turn = evaluation.run_turn
            original_planner = pipeline.DEPENDENCIES.planner

            def traced_planner(**kwargs):
                attempt: dict[str, object] = {
                    "number": kwargs.get("attempt", 1),
                    "retry_feedback": kwargs.get("sql_execution_failure"),
                }
                try:
                    sql = original_planner(**kwargs)
                except Exception as exc:
                    attempt["error_type"] = type(exc).__name__
                    attempt["failure_code"] = getattr(exc, "code", None)
                    sql_attempts.append(attempt)
                    raise
                attempt["sql"] = sql
                sql_attempts.append(attempt)
                return sql

            def traced_turn(*args, **kwargs):
                first_attempt = len(sql_attempts)
                outcome = original_run_turn(*args, **kwargs)
                last = (
                    outcome.state.verified_turns[-1]
                    if outcome.state.verified_turns
                    else None
                )
                turns.append(
                    {
                        "question": args[0].question,
                        "kind": outcome.kind,
                        "sql": last.executed_sql
                        if outcome.kind == "answered" and last
                        else None,
                        "reply": outcome.reply,
                        "failure_code": getattr(outcome, "code", None),
                        "sql_attempts": sql_attempts[first_attempt:],
                    }
                )
                return outcome

            evaluation.run_turn = traced_turn
            pipeline.DEPENDENCIES.planner = traced_planner
            try:
                result = evaluation.evaluate_behavior(cases[index])
                entry = {
                    "version": version,
                    "index": index,
                    "category": cases[index].category,
                    "complexity": _complexity(cases[index]),
                    "passed": result.passed,
                    "checks": result.model_dump(),
                    "turns": turns,
                }
            except Exception as exc:
                entry = {
                    "version": version,
                    "index": index,
                    "category": cases[index].category,
                    "passed": False,
                    "error_type": type(exc).__name__,
                    "turns": turns,
                }
            finally:
                evaluation.run_turn = original_run_turn
                pipeline.DEPENDENCIES.planner = original_planner
            entry["duration_seconds"] = round(perf_counter() - started, 2)
            report["runs"].append(entry)
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(
                f"{version} case {index}: {'PASS' if entry['passed'] else 'FAIL'} "
                f"({entry['duration_seconds']}s)",
                flush=True,
            )
    planner._SYSTEM = original_prompt
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
