"""Gradio dashboard for the APDC attendance evaluation suite."""

from collections import defaultdict

import gradio as gr
import pandas as pd

try:
    from week5.new_evaluation import eval as apdc_evaluation
except ModuleNotFoundError as exc:
    if exc.name != "week5":
        raise
    from new_evaluation import eval as apdc_evaluation


def limit_cases(cases, maximum):
    """Return at most ``maximum`` cases; zero means the complete corpus."""
    cases = list(cases)
    maximum = max(0, int(maximum or 0))
    return cases if maximum == 0 else cases[:maximum]


def _update_progress(progress, completed, total, description):
    if progress is not None and total:
        progress(completed / total, desc=description)


def _category_frame(values, metric_name):
    rows = [
        {"Category": category, metric_name: sum(scores) / len(scores)}
        for category, scores in sorted(values.items())
        if scores
    ]
    return pd.DataFrame(rows, columns=["Category", metric_name])


def run_dataset_verification():
    try:
        manifest = apdc_evaluation.verify_dataset()
    except Exception as exc:
        return (
            "❌ Dataset verification failed "
            f"({type(exc).__name__}). Check the server logs for details."
        )
    return (
        "✅ **Dataset verified** — "
        f"{manifest['record_count']:,} records, "
        f"{manifest['employee_count']:,} employees, "
        f"{manifest['date_min']} through {manifest['date_max']}."
    )


def run_behavior_evaluation(maximum, progress=gr.Progress()):
    cases = limit_cases(apdc_evaluation.load_tests(), maximum)
    category_scores = defaultdict(list)
    details = []
    passed = 0

    for index, case in enumerate(cases, start=1):
        try:
            behavior_result = apdc_evaluation.evaluate_behavior(case)
            checks = behavior_result.model_dump()
            failed_checks = [name for name, value in checks.items() if not value]
            case_passed = not failed_checks
            diagnostic = apdc_evaluation.diagnose_behavior_result(
                index=index - 1,
                category=case.category,
                result=behavior_result,
            )
            passed += int(case_passed)
            category_scores[case.category].append(100.0 if case_passed else 0.0)
            details.append(
                {
                    "Case": index - 1,
                    "Category": case.category,
                    "Status": "Passed" if case_passed else "Failed",
                    "Failed checks": ", ".join(failed_checks),
                    "Cause": diagnostic.cause if diagnostic else "",
                }
            )
        except Exception as exc:
            category_scores[case.category].append(0.0)
            details.append(
                {
                    "Case": index - 1,
                    "Category": case.category,
                    "Status": f"Failed ({type(exc).__name__})",
                    "Failed checks": "evaluation_error",
                    "Cause": "provider_structural_failure",
                }
            )
        _update_progress(progress, index, len(cases), f"Behavior case {index}")

    total = len(cases)
    rate = passed / total * 100 if total else 0.0
    summary = f"### Behavior result: {passed} of {total} passed ({rate:.1f}%)"
    categories = _category_frame(category_scores, "Pass Rate")
    detail_frame = pd.DataFrame(
        details,
        columns=["Case", "Category", "Status", "Failed checks", "Cause"],
    )
    return summary, categories, detail_frame


def run_retrieval_evaluation(maximum, progress=gr.Progress()):
    cases = limit_cases(apdc_evaluation.load_tests(), maximum)
    category_mrr = defaultdict(list)
    details = []
    results = []

    for index, case in enumerate(cases, start=1):
        try:
            result = apdc_evaluation.evaluate_retrieval(case)
            results.append(result)
            category_mrr[case.category].append(result.mrr)
            details.append(
                {
                    "Case": index - 1,
                    "Category": case.category,
                    "MRR": result.mrr,
                    "nDCG": result.ndcg,
                    "Coverage %": result.keyword_coverage,
                    "Status": "Completed",
                }
            )
        except Exception as exc:
            details.append(
                {
                    "Case": index - 1,
                    "Category": case.category,
                    "MRR": None,
                    "nDCG": None,
                    "Coverage %": None,
                    "Status": f"Failed ({type(exc).__name__})",
                }
            )
        _update_progress(progress, index, len(cases), f"Retrieval case {index}")

    failures = len(cases) - len(results)
    if results:
        average_mrr = sum(result.mrr for result in results) / len(results)
        average_ndcg = sum(result.ndcg for result in results) / len(results)
        average_coverage = sum(result.keyword_coverage for result in results) / len(
            results
        )
        summary = (
            f"### Retrieval result: {len(results)} completed, {failures} failed  \n"
            f"MRR: {average_mrr:.4f} · nDCG: {average_ndcg:.4f} · "
            f"Keyword coverage: {average_coverage:.1f}%"
        )
    else:
        summary = f"### Retrieval result: 0 completed, {failures} failed"

    categories = _category_frame(category_mrr, "Average MRR")
    detail_frame = pd.DataFrame(
        details,
        columns=[
            "Case",
            "Category",
            "MRR",
            "nDCG",
            "Coverage %",
            "Status",
        ],
    )
    return summary, categories, detail_frame


def run_answer_evaluation(maximum, progress=gr.Progress()):
    cases = limit_cases(apdc_evaluation.load_tests(), maximum)
    category_accuracy = defaultdict(list)
    details = []
    results = []

    for index, case in enumerate(cases, start=1):
        try:
            result, diagnostic = apdc_evaluation.evaluate_answer_with_diagnostic(
                case, index=index - 1
            )
            results.append(result)
            category_accuracy[case.category].append(result.accuracy)
            details.append(
                {
                    "Case": index - 1,
                    "Category": case.category,
                    "Accuracy": result.accuracy,
                    "Completeness": result.completeness,
                    "Relevance": result.relevance,
                    "Status": "Completed",
                    "Cause": diagnostic.cause if diagnostic else "",
                }
            )
        except Exception as exc:
            details.append(
                {
                    "Case": index - 1,
                    "Category": case.category,
                    "Accuracy": None,
                    "Completeness": None,
                    "Relevance": None,
                    "Status": f"Failed ({type(exc).__name__})",
                    "Cause": "provider_structural_failure",
                }
            )
        _update_progress(progress, index, len(cases), f"Answer case {index}")

    failures = len(cases) - len(results)
    if results:
        accuracy = sum(result.accuracy for result in results) / len(results)
        completeness = sum(result.completeness for result in results) / len(results)
        relevance = sum(result.relevance for result in results) / len(results)
        summary = (
            f"### Answer result: {len(results)} completed, {failures} failed  \n"
            f"Accuracy: {accuracy:.2f}/5 · Completeness: {completeness:.2f}/5 · "
            f"Relevance: {relevance:.2f}/5"
        )
    else:
        summary = f"### Answer result: 0 completed, {failures} failed"

    categories = _category_frame(category_accuracy, "Average Accuracy")
    detail_frame = pd.DataFrame(
        details,
        columns=[
            "Case",
            "Category",
            "Accuracy",
            "Completeness",
            "Relevance",
            "Status",
            "Cause",
        ],
    )
    return summary, categories, detail_frame


def build_app():
    theme = gr.themes.Soft(font=["Inter", "system-ui", "sans-serif"])
    with gr.Blocks(title="APDC Attendance Evaluation", theme=theme) as app:
        gr.Markdown(
            "# 📊 APDC Attendance Evaluation\n"
            "Evaluate the current `new_implementation` against the APDC corpus."
        )
        maximum = gr.Number(
            label="Maximum cases per run (0 = all)",
            value=10,
            minimum=0,
            precision=0,
        )
        gr.Markdown(
            "Answer evaluation calls the configured model for every case and may "
            "take time or incur provider usage."
        )

        with gr.Tab("Dataset"):
            verify_button = gr.Button("Verify APDC Dataset", variant="primary")
            dataset_result = gr.Markdown(
                "Click the button to verify dataset integrity."
            )
            verify_button.click(run_dataset_verification, outputs=dataset_result)

        with gr.Tab("Behavior"):
            behavior_button = gr.Button("Run Behavior Evaluation", variant="primary")
            behavior_summary = gr.Markdown()
            behavior_chart = gr.BarPlot(
                x="Category",
                y="Pass Rate",
                title="Behavior pass rate by category",
                y_lim=[0, 100],
                height=360,
            )
            behavior_details = gr.Dataframe(interactive=False)
            behavior_button.click(
                run_behavior_evaluation,
                inputs=maximum,
                outputs=[behavior_summary, behavior_chart, behavior_details],
            )

        with gr.Tab("Retrieval"):
            retrieval_button = gr.Button("Run Retrieval Evaluation", variant="primary")
            retrieval_summary = gr.Markdown()
            retrieval_chart = gr.BarPlot(
                x="Category",
                y="Average MRR",
                title="Average MRR by category",
                y_lim=[0, 1],
                height=360,
            )
            retrieval_details = gr.Dataframe(interactive=False)
            retrieval_button.click(
                run_retrieval_evaluation,
                inputs=maximum,
                outputs=[retrieval_summary, retrieval_chart, retrieval_details],
            )

        with gr.Tab("Answer Quality"):
            answer_button = gr.Button("Run Answer Evaluation", variant="primary")
            answer_summary = gr.Markdown()
            answer_chart = gr.BarPlot(
                x="Category",
                y="Average Accuracy",
                title="Average answer accuracy by category",
                y_lim=[1, 5],
                height=360,
            )
            answer_details = gr.Dataframe(interactive=False)
            answer_button.click(
                run_answer_evaluation,
                inputs=maximum,
                outputs=[answer_summary, answer_chart, answer_details],
            )

    return app


def main():
    build_app().queue().launch(inbrowser=True)


if __name__ == "__main__":
    main()
