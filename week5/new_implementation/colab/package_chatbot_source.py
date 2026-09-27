"""Package the current safe application source for the Colab chatbot launcher."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from .package_source import _source_archive_bytes


ARCHIVE_PATH = Path(__file__).resolve().parent / "attendance_chatbot_source.zip"


def build_chatbot_source_archive() -> str:
    archive_bytes = _source_archive_bytes()
    digest = sha256(archive_bytes).hexdigest()
    ARCHIVE_PATH.write_bytes(archive_bytes)
    print(f"Created {ARCHIVE_PATH.name}: {len(archive_bytes)} bytes, SHA-256 {digest}")
    return digest


if __name__ == "__main__":
    build_chatbot_source_archive()
