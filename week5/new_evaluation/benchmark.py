"""Repeatable APDC benchmark utilities and JSON CLI."""

from argparse import ArgumentParser
from pathlib import Path
from statistics import median
from time import perf_counter
import json
import math

from ..new_implementation.config import settings


def percentile(samples, percent):
    if not samples:
        return None
    ordered = sorted(samples)
    index = max(0, math.ceil(percent / 100 * len(ordered)) - 1)
    return ordered[index]


def summarize_samples(samples, *, failures=0, skipped=0):
    return {
        "runs": len(samples),
        "median": median(samples) if samples else None,
        "p95": percentile(samples, 95),
        "failures": failures,
        "skipped": skipped,
    }


def run_benchmark(callable_, *, warmups=2, runs=10):
    warmup_failures = 0
    for _ in range(warmups):
        try:
            callable_()
        except Exception:
            warmup_failures += 1
    samples = []
    failures = 0
    for _ in range(runs):
        started = perf_counter()
        try:
            callable_()
        except Exception:
            failures += 1
        else:
            samples.append(perf_counter() - started)
    result = summarize_samples(samples, failures=failures)
    result["warmups"] = warmups
    result["warmup_failures"] = warmup_failures
    return result


def build_benchmark_report(*, warmups=2, runs=10, manifest_path: Path | None = None):
    """Measure real, read-only APDC components without touching live sinks."""
    from ..new_implementation.answer import (
        POSTGRES_DSN,
        QueryPlan,
        Result,
        _postgres_enabled,
        _question_specific_context_fields,
        _select_context_content,
        calculate_aggregation_chroma,
        execute_exact_postgres,
        normalize_query_plan,
    )
    from ..new_implementation.attendance_schema import FilterCondition

    question = "What is average lateness by department?"
    grouped_plan = QueryPlan(
        mode="exact",
        search_query="average lateness",
        aggregation="average",
        aggregation_field="Lateness_Hrs",
        group_by=["Department"],
    )
    rows = [
        Result(
            page_content=(
                f"Employee_ID: E-{index}\nDepartment: {department}\n"
                f"Lateness_Hrs: {lateness}\nTotal_OT: 99"
            ),
            metadata={
                "Employee_ID": f"E-{index}",
                "Department": department,
                "Lateness_Hrs": lateness,
                "Total_OT": 99,
            },
        )
        for index, (department, lateness) in enumerate(
            (("Operations", 0.1), ("Operations", 0.3), ("Finance", 0.2)),
            start=1,
        )
    ]
    fields = _question_specific_context_fields(question, grouped_plan)

    stages = {
        "plan_normalization": lambda: normalize_query_plan(question, grouped_plan),
        "python_grouped_calculation": lambda: calculate_aggregation_chroma(
            grouped_plan, rows
        ),
        "context_projection": lambda: [
            _select_context_content(row.page_content, fields) for row in rows
        ],
    }
    results = {
        name: run_benchmark(callable_, warmups=warmups, runs=runs)
        for name, callable_ in stages.items()
    }

    if _postgres_enabled():
        sql_plan = QueryPlan(
            mode="exact",
            search_query="synthetic employee attendance",
            filters=[
                FilterCondition(field="Employee_ID", operator="eq", value="E00001"),
                FilterCondition(
                    field="chunk_type", operator="eq", value="attendance_record"
                ),
            ],
            aggregation="count",
        )
        results["postgres_exact_snapshot"] = run_benchmark(
            lambda: execute_exact_postgres(sql_plan), warmups=warmups, runs=runs
        )
    else:
        results["postgres_exact_snapshot"] = summarize_samples([], skipped=runs)

    manifest_path = manifest_path or Path(__file__).with_name("dataset_manifest.json")
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else {}
    )
    return {
        "benchmark": "apdc_attendance_components",
        "dataset_fingerprint": manifest.get("fingerprint"),
        "dataset_state": ("available" if manifest else "private_fixture_unavailable"),
        "cache_state": "warm_after_warmups",
        "backend_flags": {
            "postgres": _postgres_enabled(),
            "provider": False,
        },
        "models": {
            "answer": settings.rag_model,
            "embedding": settings.embedding_model,
        },
        "provider_stages": {"state": "skipped", "reason": "offline benchmark"},
        "postgres_dsn_configured": bool(POSTGRES_DSN),
        "stages": results,
    }


def main(argv=None):
    parser = ArgumentParser()
    parser.add_argument("--warmups", type=int, default=settings.benchmark_warmups)
    parser.add_argument("--runs", type=int, default=settings.benchmark_runs)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    report = build_benchmark_report(warmups=args.warmups, runs=args.runs)
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    for name, result in report["stages"].items():
        median_value = result.get("median")
        p95_value = result.get("p95")
        if median_value is None:
            print(f"{name}: skipped={result['skipped']} failures={result['failures']}")
        else:
            print(
                f"{name}: median={median_value:.6f}s p95={p95_value:.6f}s "
                f"failures={result['failures']}"
            )
    local_names = {
        "plan_normalization",
        "python_grouped_calculation",
        "context_projection",
    }
    return int(any(report["stages"][name]["failures"] for name in local_names))


if __name__ == "__main__":
    raise SystemExit(main())
