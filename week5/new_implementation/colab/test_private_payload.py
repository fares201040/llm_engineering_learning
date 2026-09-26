from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import warnings
from zipfile import ZIP_DEFLATED, ZipFile

from week5.new_implementation.colab.private_payload import (
    DATASET_FINGERPRINT,
    build_payload_archive,
    validate_case_bytes,
    validate_payload_archive,
)


def _case_bytes(*, placeholder: bool = False) -> bytes:
    category = "synthetic_manifest" if placeholder else "attendance_count"
    return b"".join(
        (
            json.dumps(
                {
                    "question": f"Question {index}",
                    "keywords": [],
                    "reference_answer": f"Answer {index}",
                    "category": category,
                },
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        for index in range(311)
    )


def _attendance_bytes() -> bytes:
    dates = [f"2026-09-{day:02d}" for day in range(1, 8)]
    return b"".join(
        (
            json.dumps(
                {
                    "record_id": f"record-{index:04d}",
                    "employee_id": f"employee-{index % 568:03d}",
                    "attendance_date": dates[index % len(dates)],
                },
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        for index in range(3964)
    )


def _manifest(attendance: bytes, cases: bytes, **overrides) -> dict:
    value = {
        "version": 1,
        "dataset_fingerprint": DATASET_FINGERPRINT,
        "record_count": 3964,
        "employee_count": 568,
        "date_min": "2026-09-01",
        "date_max": "2026-09-07",
        "case_count": 311,
        "files": {
            "attendance_records.jsonl": sha256(attendance).hexdigest(),
            "tests.jsonl": sha256(cases).hexdigest(),
        },
    }
    value.update(overrides)
    return value


def _write_archive(path: Path, members: list[tuple[str, bytes]]) -> None:
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, data in members:
            archive.writestr(name, data)


class PrivatePayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.attendance = _attendance_bytes()
        cls.cases = _case_bytes()

    def test_placeholder_corpus_is_rejected_even_with_311_lines(self):
        with self.assertRaisesRegex(ValueError, "placeholder"):
            validate_case_bytes(_case_bytes(placeholder=True))

    def test_valid_case_corpus_reports_exact_count(self):
        self.assertEqual(validate_case_bytes(self.cases), 311)

    def test_builder_writes_only_allowlisted_members_and_no_credentials(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "payload.zip"

            build_payload_archive(
                output,
                attendance_bytes=self.attendance,
                case_bytes=self.cases,
                dataset_fingerprint=DATASET_FINGERPRINT,
            )

            with ZipFile(output) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {"attendance_records.jsonl", "tests.jsonl", "manifest.json"},
                )
                serialized = b"".join(archive.read(name) for name in archive.namelist())
            self.assertNotIn(b"POSTGRES_DSN", serialized)
            self.assertNotIn(b"postgresql://", serialized)

    def test_valid_archive_passes_hash_and_dataset_validation(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "payload.zip"
            digest = build_payload_archive(
                output,
                attendance_bytes=self.attendance,
                case_bytes=self.cases,
                dataset_fingerprint=DATASET_FINGERPRINT,
            )

            manifest = validate_payload_archive(output, expected_sha256=digest)

            self.assertEqual(manifest["record_count"], 3964)
            self.assertEqual(manifest["case_count"], 311)

    def test_archive_rejects_unsafe_unexpected_duplicate_and_secret_members(self):
        invalid_names = ("../tests.jsonl", "extra.json", ".env.postgres")
        with TemporaryDirectory() as directory:
            for index, invalid_name in enumerate(invalid_names):
                output = Path(directory) / f"invalid-{index}.zip"
                _write_archive(output, [(invalid_name, b"value")])
                with self.subTest(name=invalid_name):
                    with self.assertRaises(ValueError):
                        validate_payload_archive(output)

            duplicate = Path(directory) / "duplicate.zip"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                _write_archive(
                    duplicate,
                    [
                        ("tests.jsonl", self.cases),
                        ("tests.jsonl", self.cases),
                    ],
                )
            with self.assertRaisesRegex(ValueError, "duplicate"):
                validate_payload_archive(duplicate)

    def test_archive_rejects_wrong_outer_or_member_hash(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "payload.zip"
            build_payload_archive(
                output,
                attendance_bytes=self.attendance,
                case_bytes=self.cases,
                dataset_fingerprint=DATASET_FINGERPRINT,
            )
            with self.assertRaisesRegex(ValueError, "archive SHA-256"):
                validate_payload_archive(output, expected_sha256="0" * 64)

            bad_member = Path(directory) / "bad-member.zip"
            manifest = _manifest(self.attendance, self.cases)
            manifest["files"]["tests.jsonl"] = "0" * 64
            _write_archive(
                bad_member,
                [
                    ("attendance_records.jsonl", self.attendance),
                    ("tests.jsonl", self.cases),
                    ("manifest.json", json.dumps(manifest).encode("utf-8")),
                ],
            )
            with self.assertRaisesRegex(ValueError, "member SHA-256"):
                validate_payload_archive(bad_member)

    def test_archive_rejects_wrong_required_manifest_fact(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "wrong-manifest.zip"
            manifest = _manifest(self.attendance, self.cases, employee_count=567)
            _write_archive(
                output,
                [
                    ("attendance_records.jsonl", self.attendance),
                    ("tests.jsonl", self.cases),
                    ("manifest.json", json.dumps(manifest).encode("utf-8")),
                ],
            )

            with self.assertRaisesRegex(ValueError, "employee_count"):
                validate_payload_archive(output)


if __name__ == "__main__":
    unittest.main()
