"""Prepare a private Colab runtime and launch the attendance chatbot."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
from zipfile import ZipFile


SOURCE_ARCHIVE = Path("/content/attendance_chatbot_source.zip")
SOURCE_ROOT = Path("/content/attendance_chatbot_source")
NEXT_PRIVATE_PAYLOAD = Path("/content/attendance_private_payload.next.zip")
PRIVATE_PAYLOAD = Path("/content/attendance_private_payload.zip")
PRIVATE_RUNTIME = Path("/content/.attendance_private_runtime.json")


def run(command: list[str], *, input_text: str | None = None) -> None:
    subprocess.run(command, input=input_text, text=True, check=True)


def extract_source() -> None:
    if not SOURCE_ARCHIVE.is_file():
        raise FileNotFoundError("The chatbot source archive was not uploaded")
    if SOURCE_ROOT.exists():
        shutil.rmtree(SOURCE_ROOT)
    SOURCE_ROOT.mkdir(parents=True)
    root = SOURCE_ROOT.resolve()
    with ZipFile(SOURCE_ARCHIVE) as archive:
        for member in archive.infolist():
            target = (root / member.filename).resolve()
            if not target.is_relative_to(root):
                raise ValueError("The source archive contains an unsafe path")
            lowered = {part.casefold() for part in Path(member.filename).parts}
            if "results" in lowered or any(part.startswith(".env") for part in lowered):
                raise ValueError("The source archive contains a private runtime file")
        archive.extractall(root)


def install_dependencies() -> None:
    run(["apt-get", "update", "-qq"])
    run(
        [
            "apt-get",
            "install",
            "-y",
            "curl",
            "postgresql",
            "postgresql-client",
            "zstd",
        ]
    )
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-q",
            "chromadb>=1.1.0",
            "gradio>=5.47.2,<6.0",
            "litellm>=1.77.5",
            "langchain-huggingface>=1.0.0",
            "psycopg[binary]>=3.2.0",
            "pydantic>=2",
            "python-dotenv>=1.1.1",
            "sqlglot>=30.19.0,<31",
            "sentence-transformers>=5.1.1",
        ]
    )
    run(["service", "postgresql", "start"])


def prepare_private_data() -> dict[str, str]:
    if not NEXT_PRIVATE_PAYLOAD.is_file():
        raise FileNotFoundError("The private attendance payload was not uploaded")
    if str(SOURCE_ROOT) not in sys.path:
        sys.path.insert(0, str(SOURCE_ROOT))
    os.environ["ATTENDANCE_PHASE2_SOURCE_ROOT"] = str(SOURCE_ROOT)

    if PRIVATE_RUNTIME.is_file():
        from week5.new_implementation.colab.cleanup_private_runtime import (
            main as cleanup_private_runtime,
        )

        cleanup_private_runtime()
    else:
        PRIVATE_PAYLOAD.unlink(missing_ok=True)
    NEXT_PRIVATE_PAYLOAD.replace(PRIVATE_PAYLOAD)

    from week5.new_implementation.colab.prepare_private_runtime import (
        main as prepare_private_runtime,
    )

    prepare_private_runtime()
    settings = json.loads(PRIVATE_RUNTIME.read_text(encoding="utf-8"))
    settings["LLM_REFERENCE_MODEL"] = "openai/gpt-4.1-mini"
    settings["LLM_PLANNER_MODEL"] = "openai/gpt-4.1-mini"
    PRIVATE_RUNTIME.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    PRIVATE_RUNTIME.chmod(0o600)
    return settings


def main() -> None:
    digest = os.getenv("ATTENDANCE_PRIVATE_PAYLOAD_SHA256", "")
    if len(digest) != 64:
        raise ValueError("The private payload SHA-256 was not provided")

    extract_source()
    install_dependencies()
    settings = prepare_private_data()
    os.environ.update(settings)
    os.environ["GRADIO_SHARE"] = "true"
    os.environ["GRADIO_PREVENT_THREAD_LOCK"] = "true"
    os.environ["GRADIO_AUTH_USER"] = "attendance"
    os.environ["GRADIO_AUTH_PASSWORD"] = secrets.token_urlsafe(12)
    os.chdir(SOURCE_ROOT)

    from week5.new_app import main as launch_chatbot

    print("Starting the APDC Attendance Assistant. Open the public Gradio URL below.")
    print("Login user: attendance")
    print(f"Login password: {os.environ['GRADIO_AUTH_PASSWORD']}")
    launch_chatbot()
    print("CHATBOT_READY")


if __name__ == "__main__":
    main()
