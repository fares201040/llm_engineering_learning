"""Build a deterministic, sanitized source snapshot for the Colab test notebook."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
COLAB_DIRECTORY = Path(__file__).resolve().parent
ARCHIVE_PATH = COLAB_DIRECTORY / "attendance_phase2_source.zip"
NOTEBOOK_PATH = COLAB_DIRECTORY / "attendance_phase2_tests.ipynb"
SYNTHETIC_CASE_COUNT = 311

SOURCE_PATHS = (
    "week5/new_app.py",
    "week5/test_new_app.py",
    "week5/new_implementation/__init__.py",
    "week5/new_implementation/answer.py",
    "week5/new_implementation/chroma_client.py",
    "week5/new_implementation/config.py",
    "week5/new_implementation/test_config.py",
    "week5/new_implementation/colab/package_source.py",
    "week5/new_implementation/colab/prepare_synthetic_runtime.py",
    "week5/new_implementation/colab/run_acceptance_turn.py",
    "week5/new_implementation/online/__init__.py",
    "week5/new_implementation/online/answering.py",
    "week5/new_implementation/online/context.py",
    "week5/new_implementation/online/execution.py",
    "week5/new_implementation/online/pipeline.py",
    "week5/new_implementation/online/planner.py",
    "week5/new_implementation/online/provider.py",
    "week5/new_implementation/online/reference.py",
    "week5/new_implementation/online/state.py",
    "week5/new_implementation/tests/__init__.py",
    "week5/new_implementation/tests/online/__init__.py",
    "week5/new_implementation/tests/online/test_execution.py",
    "week5/new_implementation/tests/online/test_pipeline.py",
    "week5/new_implementation/tests/online/test_planning.py",
    "week5/new_implementation/tests/online/test_query.py",
    "week5/new_evaluation/__init__.py",
    "week5/new_evaluation/acceptance.py",
    "week5/new_evaluation/eval.py",
    "week5/new_evaluation/test.py",
    "week5/new_evaluation/test_acceptance.py",
    "week5/new_evaluation/test_eval.py",
)


def _source_archive_bytes() -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for relative_path in SOURCE_PATHS:
            path = (REPOSITORY_ROOT / relative_path).resolve()
            if not path.is_relative_to(REPOSITORY_ROOT):
                raise ValueError(f"source path escapes the repository: {relative_path}")
            if not path.is_file():
                raise FileNotFoundError(path)
            lowered_parts = {part.casefold() for part in path.parts}
            if any(part.startswith(".env") for part in lowered_parts):
                raise ValueError(
                    f"environment files cannot be packaged: {relative_path}"
                )
            if "results" in lowered_parts:
                raise ValueError(
                    f"result artifacts cannot be packaged: {relative_path}"
                )
            info = ZipInfo(relative_path, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())

        synthetic_manifest = "".join(
            json.dumps(
                {
                    "question": f"Synthetic fixture case {index:03d}",
                    "keywords": [],
                    "reference_answer": "Synthetic count-only manifest entry.",
                    "category": "synthetic_manifest",
                },
                separators=(",", ":"),
            )
            + "\n"
            for index in range(1, SYNTHETIC_CASE_COUNT + 1)
        )
        info = ZipInfo(
            "week5/new_evaluation/tests.jsonl", date_time=(2020, 1, 1, 0, 0, 0)
        )
        info.compress_type = ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, synthetic_manifest.encode("utf-8"))
    return buffer.getvalue()


def _update_notebook_hash(digest: str) -> None:
    notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    target = 'expected_archive_sha256 = "'
    found = False
    for cell in notebook.get("cells", []):
        source = "".join(cell.get("source", []))
        if target not in source:
            continue
        source, count = re.subn(
            r'expected_archive_sha256 = "[0-9a-f]{64}"',
            f'expected_archive_sha256 = "{digest}"',
            source,
            count=1,
        )
        if count != 1:
            raise ValueError("the notebook archive hash entry is malformed")
        cell["source"] = source.splitlines(keepends=True)
        found = True
        break
    if not found:
        raise ValueError("the notebook has no expected archive hash entry")
    NOTEBOOK_PATH.write_text(
        json.dumps(notebook, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )


def build_source_archive() -> str:
    archive_bytes = _source_archive_bytes()
    digest = sha256(archive_bytes).hexdigest()
    ARCHIVE_PATH.write_bytes(archive_bytes)
    _update_notebook_hash(digest)
    print(
        f"Created {ARCHIVE_PATH.name}: {len(SOURCE_PATHS) + 1} files, "
        f"{len(archive_bytes)} bytes, SHA-256 {digest}"
    )
    print(
        f"The {SYNTHETIC_CASE_COUNT}-line manifest contains generated placeholders "
        "only; the private evaluation corpus is not packaged."
    )
    return digest


if __name__ == "__main__":
    build_source_archive()
