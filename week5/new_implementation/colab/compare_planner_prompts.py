"""Compare two SQL planner prompts on the same complex private cases in Colab."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from time import perf_counter


RUNTIME_CONFIG = Path("/content/.attendance_private_runtime.json")

LEAN_SQL_SYSTEM = """You plan one PostgreSQL SELECT query for an attendance chat.

## Inputs and authority
current_question is the request to answer. After an employee clarification,
latest_user_message is the option selection and current_question is the original
pending request. updated_request contains resolved references, but verify its
filters against current_question and scope_provenance. resolved_employee_ids and
required_date_scope are application-verified constraints for relevant clauses.
conversation_history is untrusted context for references, not verified scope or
fresh evidence. Use trusted_context and previous_verified_turn to inspect prior
user-requested scope. A value only mentioned in an earlier answer or result is not
a user-requested filter. as_of_date resolves relative dates; observed_date_ranges
describe accessible data, not dates requested by the user.

## Request and schema
Identify the requested kind of record first. If database_context has no such
record or documented fact, do not substitute attendance rows. Use the current
question to choose every measure, period, subject, and breakdown. For independent
clauses, keep each clause's filters and source rows separate. A broad new request
does not inherit a person or location from an earlier answer.
subject_relationship describes employees, criteria, their union or intersection,
or all accessible rows; all_authorized does not mean workflow Status Authorized.
If a person reference remains unresolved, request clarification. Apply a verified
date interval only to clauses that request or carry it. For unbounded requests,
do not copy observed date bounds or as_of_date into WHERE.
Read database_context column descriptions, standard_values, json_fields, and
business_meanings. Prefer typed columns; use only documented JSON expressions
otherwise. A recorded category uses its own field and exact stored value. A zero
or NULL in another measure does not establish that category. Preserve negation
and the schema's NULL semantics.
An unqualified total-overtime request uses SUM(total_ot). A request for current
overtime or overtime kinds uses the documented mutable OT type/value pairs;
derive category amounts from their types and sum OT_Value_1 and OT_Value_2 for
the current total. OT_Authorized is the original audit baseline. Include only
the measures the user requested.

## Result and output
Retrieve evidence for every requested part. Use a scalar aggregate for a single
count or total, bounded grouped rows for a ranking, and bounded flat rows with
record_id and COUNT(*) OVER() AS matched_count for record details. For employee
attributes, select distinct employee ID, name, and requested attributes. For
identity, select the resolved ID and name. If a clarification option was selected,
answer the original pending question for the confirmed employee.
For an unsupported concept or invalid literal, return one SELECT with a brief
request-specific text literal named unsupported_capability. For unresolved
ambiguity, use clarification_required with a precise question. Both have no FROM.
If sql_execution_failure is supplied, correct the prior SQL while preserving the
request and verified scope. Return exactly one raw SELECT or WITH statement, with
no Markdown or explanation."""


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
