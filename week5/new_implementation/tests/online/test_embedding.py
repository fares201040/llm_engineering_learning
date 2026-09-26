import unittest
import os
from unittest.mock import patch


class LocalEmbeddingConfigurationTests(unittest.TestCase):
    def test_different_models_use_different_chroma_collections(self):
        from week5.new_implementation.embedding import collection_name_for_model

        old = collection_name_for_model("docs", "text-embedding-3-large", "chunks")
        new = collection_name_for_model("docs", "all-MiniLM-L6-v2", "chunks")
        names = collection_name_for_model("docs", "all-MiniLM-L6-v2", "employees")

        self.assertNotEqual(old, new)
        self.assertNotEqual(new, names)
        self.assertEqual(
            new, collection_name_for_model("docs", "all-MiniLM-L6-v2", "chunks")
        )

    def test_provider_selects_embedding_implementation_without_api_call(self):
        from week5.new_implementation.embedding import get_embeddings

        get_embeddings.cache_clear()
        with (
            patch("langchain_huggingface.HuggingFaceEmbeddings") as local,
            patch("langchain_openai.OpenAIEmbeddings") as remote,
        ):
            self.assertIs(
                get_embeddings("all-MiniLM-L6-v2", "huggingface"),
                local.return_value,
            )
            self.assertIs(
                get_embeddings("text-embedding-3-large", "openai"),
                remote.return_value,
            )
        get_embeddings.cache_clear()

    def test_openai_split_uses_openai_token_limit(self):
        import tiktoken

        from week5.new_implementation.embedding import split_for_embedding

        class OpenAIEmbeddings:
            model = "text-embedding-3-large"
            embedding_ctx_length = 8191

        text = "attendance " * 9000 + "final-marker"
        parts = split_for_embedding(text, "", OpenAIEmbeddings(), provider="openai")
        encoding = tiktoken.encoding_for_model("text-embedding-3-large")
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(count <= 8192 for _, count in parts))
        self.assertIn("final-marker", parts[-1][0])
        self.assertEqual(sum(count for _, count in parts), len(encoding.encode(text)))

    def test_provider_is_selected_through_environment(self):
        from week5.new_implementation.config import Settings

        with patch.dict(
            os.environ,
            {
                "EMBEDDING_PROVIDER": "openai",
                "EMBEDDING_MODEL": "text-embedding-3-large",
            },
        ):
            settings = Settings.from_environment()
        self.assertEqual(settings.embedding_provider, "openai")
        self.assertEqual(settings.embedding_model, "text-embedding-3-large")

        with patch.dict(os.environ, {"EMBEDDING_PROVIDER": "openai"}):
            os.environ.pop("EMBEDDING_MODEL", None)
            default_settings = Settings.from_environment()
        self.assertEqual(default_settings.embedding_model, "text-embedding-3-large")


if __name__ == "__main__":
    unittest.main()
