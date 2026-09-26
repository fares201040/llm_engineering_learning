import unittest


class Collection:
    def __init__(self, rows=()):
        self.rows = {key: (document, metadata) for key, document, metadata in rows}
        self.vector_dimensions = None

    def count(self):
        return len(self.rows)

    def get(self, *, limit=None, offset=0, include=()):
        rows = list(self.rows.items())
        if limit is not None:
            rows = rows[offset : offset + limit]
        return {
            "ids": [key for key, _ in rows],
            "documents": [value[0] for _, value in rows],
            "metadatas": [value[1] for _, value in rows],
        }

    def upsert(self, *, ids, documents, metadatas, embeddings):
        dimensions = {len(vector) for vector in embeddings}
        assert len(dimensions) == 1
        self.vector_dimensions = dimensions.pop()
        for key, document, metadata in zip(ids, documents, metadatas):
            self.rows[key] = (document, metadata)

    def delete(self, *, ids):
        for key in ids:
            self.rows.pop(key, None)


class Client:
    def __init__(self):
        self.collections = {
            "docs": Collection(
                (
                    ("one", "Synthetic one", {"domain": "attendance"}),
                    ("two", "Synthetic two", {"domain": "attendance"}),
                )
            )
        }
        self.deleted = []

    def get_collection(self, name):
        return self.collections[name]

    def get_or_create_collection(self, name):
        return self.collections.setdefault(name, Collection())

    def delete_collection(self, name):
        self.deleted.append(name)
        del self.collections[name]


class Tokenizer:
    def num_special_tokens_to_add(self, *, pair):
        return 2

    def encode(self, text, *, add_special_tokens, verbose=False):
        return list(text.encode())

    def decode(self, tokens, *, skip_special_tokens, clean_up_tokenization_spaces):
        return bytes(tokens).decode()


class Model:
    tokenizer = Tokenizer()
    max_seq_length = 32


class Embeddings:
    _client = Model()

    def embed_documents(self, texts):
        return [[float(len(text))] * 384 for text in texts]


class RebuildTests(unittest.TestCase):
    def test_source_collection_is_preserved_for_switching_back(self):
        from week5.new_implementation.rebuild_chroma import rebuild_collection

        client = Client()
        rebuild_collection(client, "docs", "docs_minilm", Embeddings(), batch_size=1)

        self.assertEqual(client.deleted, [])
        self.assertEqual(client.collections["docs"].count(), 2)
        self.assertEqual(set(client.collections["docs_minilm"].rows), {"one", "two"})
        self.assertEqual(
            client.collections["docs_minilm"].rows["one"],
            (
                "Synthetic one",
                {
                    "domain": "attendance",
                    "embedding_part": 1,
                    "embedding_parts_total": 1,
                    "part_id": "one",
                },
            ),
        )

    def test_long_document_is_split_without_discarding_its_end(self):
        from week5.new_implementation.rebuild_chroma import rebuild_collection

        client = Client()
        original = "abcdefghijklmnopqrstuvwx" * 3
        client.collections["docs"] = Collection((("long", original, {}),))
        rebuild_collection(client, "docs", "docs_minilm", Embeddings())
        rows = client.collections["docs_minilm"].rows
        self.assertGreater(len(rows), 1)
        self.assertIn("long:embedding-part-1", rows)
        self.assertIn("x", rows[next(reversed(rows))][0])

    def test_switching_to_openai_accepts_its_vector_dimension(self):
        from week5.new_implementation.rebuild_chroma import rebuild_collection

        class OpenAIEmbeddings:
            model = "text-embedding-3-large"
            embedding_ctx_length = 8191

            def embed_documents(self, texts):
                return [[0.1] * 3072 for _ in texts]

        client = Client()
        rebuild_collection(
            client,
            "docs",
            "docs_openai",
            OpenAIEmbeddings(),
            provider="openai",
        )
        self.assertEqual(client.collections["docs_openai"].vector_dimensions, 3072)
        self.assertEqual(client.collections["docs"].count(), 2)

    def test_embedding_failure_keeps_old_collection(self):
        from week5.new_implementation.rebuild_chroma import rebuild_collection

        class BrokenEmbeddings(Embeddings):
            def embed_documents(self, _texts):
                raise RuntimeError("embedding failed")

        client = Client()
        with self.assertRaisesRegex(RuntimeError, "embedding failed"):
            rebuild_collection(client, "docs", "docs_minilm", BrokenEmbeddings())
        self.assertEqual(client.deleted, [])
        self.assertEqual(client.collections["docs"].count(), 2)


if __name__ == "__main__":
    unittest.main()
