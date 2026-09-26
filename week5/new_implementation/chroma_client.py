"""Central construction point for the local Chroma client."""

from .config import settings


def create_chroma_client():
    from chromadb import PersistentClient
    from chromadb.config import Settings as ChromaSettings

    return PersistentClient(
        path=str(settings.chroma_db_path),
        settings=ChromaSettings(
            anonymized_telemetry=settings.chroma_anonymized_telemetry,
        ),
    )
