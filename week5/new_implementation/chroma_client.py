"""Central construction point for the local Chroma client."""

from pathlib import Path

from .config import settings


def create_chroma_client(path: Path | str | None = None):
    from chromadb import PersistentClient
    from chromadb.config import Settings as ChromaSettings

    return PersistentClient(
        path=str(path if path is not None else settings.chroma_db_path),
        settings=ChromaSettings(
            anonymized_telemetry=settings.chroma_anonymized_telemetry,
        ),
    )
