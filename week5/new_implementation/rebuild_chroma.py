"""Build a collection for another embedding model from an existing collection."""

from __future__ import annotations

import argparse

from .chroma_client import create_chroma_client
from .config import settings
from .embedding import collection_name_for_model, get_embeddings, split_for_embedding
from .ingest import _build_split_identity_prefix


def rebuild_collection(
    client,
    source_name: str,
    target_name: str,
    embeddings,
    *,
    batch_size: int = 32,
    provider: str = "huggingface",
) -> int:
    if source_name == target_name or batch_size < 1:
        raise ValueError(
            "source and target collections must differ; batch size must be positive"
        )
    source = client.get_collection(source_name)
    target = client.get_or_create_collection(target_name)
    source_count = source.count()
    # A failed earlier attempt may have left a partial target. Keep the source
    # untouched while replacing those incomplete vectors.
    old_target_ids = target.get(include=[])["ids"]
    for start in range(0, len(old_target_ids), batch_size):
        target.delete(ids=old_target_ids[start : start + batch_size])

    expected_ids = set()
    seen_source_ids = set()
    vector_dimension = None

    for offset in range(0, source_count, batch_size):
        page = source.get(
            limit=batch_size,
            offset=offset,
            include=["documents", "metadatas"],
        )
        ids = page["ids"]
        seen_source_ids.update(ids)
        documents = page.get("documents") or []
        metadatas = page.get("metadatas") or []
        if (
            len(ids) != len(documents)
            or len(ids) != len(metadatas)
            or any(document is None for document in documents)
            or any(metadata is None for metadata in metadatas)
        ):
            raise RuntimeError("source collection has incomplete documents or metadata")
        new_ids, new_documents, new_metadatas = [], [], []
        for source_id, document, metadata in zip(ids, documents, metadatas):
            parts = split_for_embedding(
                document,
                _build_split_identity_prefix(metadata),
                embeddings,
                provider=provider,
            )
            for part_index, (part, _) in enumerate(parts, start=1):
                part_id = (
                    source_id
                    if len(parts) == 1
                    else f"{source_id}:embedding-part-{part_index}"
                )
                part_metadata = dict(metadata)
                part_metadata.update(
                    embedding_part=part_index,
                    embedding_parts_total=len(parts),
                    part_id=part_id,
                )
                new_ids.append(part_id)
                new_documents.append(part)
                new_metadatas.append(part_metadata)
        if expected_ids.intersection(new_ids):
            raise RuntimeError("rebuilt document IDs are not unique")
        expected_ids.update(new_ids)
        vectors = embeddings.embed_documents(new_documents)
        dimensions = {len(vector) for vector in vectors}
        if len(vectors) != len(new_ids) or len(dimensions) != 1:
            raise RuntimeError("embedding vectors have the wrong count or dimension")
        batch_dimension = dimensions.pop()
        if vector_dimension is not None and batch_dimension != vector_dimension:
            raise RuntimeError("embedding vector dimension changed during rebuild")
        vector_dimension = batch_dimension
        target.upsert(
            ids=new_ids,
            documents=new_documents,
            metadatas=new_metadatas,
            embeddings=vectors,
        )
        print(f"Re-embedded {offset + len(ids)}/{source_count} documents")

    source_ids = set(source.get(include=[])["ids"])
    target_ids = set(target.get(include=[])["ids"])
    if len(seen_source_ids) != source_count or seen_source_ids != source_ids:
        raise RuntimeError("source collection changed during rebuild")
    if target.count() != len(expected_ids) or target_ids != expected_ids:
        raise RuntimeError("rebuilt collection does not match the expected IDs")
    return source_count


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source-model", help="Existing model's collection to copy")
    source.add_argument("--source-collection", help="Existing collection name to copy")
    args = parser.parse_args(argv)
    source_name = args.source_collection or collection_name_for_model(
        settings.chroma_collection_name, args.source_model, "chunks"
    )
    target_name = collection_name_for_model(
        settings.chroma_collection_name, settings.embedding_model, "chunks"
    )
    count = rebuild_collection(
        create_chroma_client(),
        source_name,
        target_name,
        get_embeddings(settings.embedding_model, settings.embedding_provider),
        provider=settings.embedding_provider,
    )
    print(f"Rebuilt {count} source documents in {target_name}; {source_name} retained")


if __name__ == "__main__":
    main()
