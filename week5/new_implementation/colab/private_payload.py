"""Private attendance-evaluation payload contract shared by local and Colab tools."""

from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from week5.new_evaluation.test import TestQuestion


DATASET_FINGERPRINT = (
    "6860e7657deb91023d6f199c230edf9b7d40cd3e402a1ad23ccea39f3487dde9"
)
EXPECTED_FACTS = {
    "version": 1,
    "dataset_fingerprint": DATASET_FINGERPRINT,
    "record_count": 3964,
    "employee_count": 568,
    "date_min": "2026-09-01",
    "date_max": "2026-09-07",
    "case_count": 311,
}
PAYLOAD_MEMBERS = frozenset(
    {"attendance_records.jsonl", "tests.jsonl", "manifest.json"}
)


def _json_lines(data: bytes, *, label: str):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must be UTF-8 JSONL") from exc
    lines = text.splitlines()
    if not lines or any(not line.strip() for line in lines):
        raise ValueError(f"{label} must contain non-empty JSONL records")
    for line_number, line in enumerate(lines, start=1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label} line {line_number} is not valid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{label} line {line_number} must be a JSON object")
        yield line_number, value


def validate_case_bytes(data: bytes) -> int:
    """Validate the private corpus and reject the sanitized placeholder manifest."""
    count = 0
    for line_number, value in _json_lines(data, label="evaluation cases"):
        if str(value.get("category", "")).casefold() == "synthetic_manifest":
            raise ValueError(
                "The generated placeholder corpus cannot be used for evaluation"
            )
        try:
            TestQuestion.model_validate(value)
        except Exception as exc:
            raise ValueError(
                f"evaluation case line {line_number} does not match the case schema"
            ) from exc
        count += 1
    if count != EXPECTED_FACTS["case_count"]:
        raise ValueError(
            f"case_count must be {EXPECTED_FACTS['case_count']}, received {count}"
        )
    return count


def attendance_summary(data: bytes) -> dict[str, object]:
    """Return the bounded dataset facts derived from exported attendance rows."""
    employees: set[str] = set()
    dates: list[date] = []
    count = 0
    for line_number, value in _json_lines(data, label="attendance records"):
        employee_id = value.get("employee_id")
        attendance_date = value.get("attendance_date")
        record_id = value.get("record_id")
        if not all(isinstance(item, str) and item for item in (record_id, employee_id)):
            raise ValueError(
                f"attendance record line {line_number} lacks its required identifiers"
            )
        try:
            parsed_date = date.fromisoformat(str(attendance_date))
        except ValueError as exc:
            raise ValueError(
                f"attendance record line {line_number} has an invalid attendance_date"
            ) from exc
        employees.add(employee_id)
        dates.append(parsed_date)
        count += 1
    return {
        "record_count": count,
        "employee_count": len(employees),
        "date_min": min(dates).isoformat() if dates else None,
        "date_max": max(dates).isoformat() if dates else None,
    }


def _validate_required_facts(manifest: dict) -> None:
    for key, expected in EXPECTED_FACTS.items():
        if manifest.get(key) != expected:
            raise ValueError(
                f"{key} must be {expected!r}, received {manifest.get(key)!r}"
            )


def _zip_info(name: str) -> ZipInfo:
    info = ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o100600 << 16
    return info


def build_payload_archive(
    path: Path,
    *,
    attendance_bytes: bytes,
    case_bytes: bytes,
    dataset_fingerprint: str,
) -> str:
    """Write a deterministic payload containing only the two private inputs."""
    summary = attendance_summary(attendance_bytes)
    case_count = validate_case_bytes(case_bytes)
    manifest = {
        "version": 1,
        "dataset_fingerprint": dataset_fingerprint,
        **summary,
        "case_count": case_count,
        "files": {
            "attendance_records.jsonl": sha256(attendance_bytes).hexdigest(),
            "tests.jsonl": sha256(case_bytes).hexdigest(),
        },
    }
    _validate_required_facts(manifest)
    manifest_bytes = (
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr(_zip_info("attendance_records.jsonl"), attendance_bytes)
        archive.writestr(_zip_info("tests.jsonl"), case_bytes)
        archive.writestr(_zip_info("manifest.json"), manifest_bytes)
    return sha256(path.read_bytes()).hexdigest()


def validate_payload_archive(
    path: Path, expected_sha256: str | None = None
) -> dict[str, object]:
    """Validate archive identity, structure, hashes, corpus, and dataset facts."""
    archive_bytes = path.read_bytes()
    digest = sha256(archive_bytes).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256.casefold():
        raise ValueError("private payload archive SHA-256 mismatch")
    with ZipFile(path) as archive:
        members = archive.infolist()
        names = [member.filename for member in members]
        if len(names) != len(set(names)):
            raise ValueError("private payload contains a duplicate archive member")
        for name in names:
            item = PurePosixPath(name)
            lowered = name.casefold()
            if item.is_absolute() or ".." in item.parts:
                raise ValueError(f"unsafe private payload member: {name}")
            if item.name.startswith(".env") or any(
                marker in lowered for marker in ("credential", "password", "dsn")
            ):
                raise ValueError(f"credential-shaped private payload member: {name}")
        if set(names) != PAYLOAD_MEMBERS:
            raise ValueError(
                "private payload members must be exactly "
                f"{sorted(PAYLOAD_MEMBERS)}, received {sorted(names)}"
            )
        attendance_bytes = archive.read("attendance_records.jsonl")
        case_bytes = archive.read("tests.jsonl")
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("private payload manifest is not valid UTF-8 JSON") from exc
    if not isinstance(manifest, dict):
        raise ValueError("private payload manifest must be a JSON object")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != {
        "attendance_records.jsonl",
        "tests.jsonl",
    }:
        raise ValueError("private payload manifest has an invalid files map")
    for name, data in (
        ("attendance_records.jsonl", attendance_bytes),
        ("tests.jsonl", case_bytes),
    ):
        if files.get(name) != sha256(data).hexdigest():
            raise ValueError(f"private payload member SHA-256 mismatch: {name}")
    derived = {
        "version": manifest.get("version"),
        "dataset_fingerprint": manifest.get("dataset_fingerprint"),
        **attendance_summary(attendance_bytes),
        "case_count": validate_case_bytes(case_bytes),
    }
    _validate_required_facts(derived)
    for key, value in derived.items():
        if manifest.get(key) != value:
            raise ValueError(
                f"manifest {key} does not match payload: {manifest.get(key)!r} != {value!r}"
            )
    return manifest


__all__ = [
    "DATASET_FINGERPRINT",
    "EXPECTED_FACTS",
    "PAYLOAD_MEMBERS",
    "attendance_summary",
    "build_payload_archive",
    "validate_case_bytes",
    "validate_payload_archive",
]
