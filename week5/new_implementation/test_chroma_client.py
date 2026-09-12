import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from week5.new_implementation import chroma_client


class ChromaClientModuleTests(unittest.TestCase):
    def test_central_chroma_client_module_exists(self):
        spec = importlib.util.find_spec("week5.new_implementation.chroma_client")
        self.assertIsNotNone(spec)

    def test_module_exposes_client_factory(self):
        self.assertTrue(hasattr(chroma_client, "create_chroma_client"))

    def test_factory_passes_path_and_native_telemetry_setting(self):
        configured = SimpleNamespace(
            chroma_db_path=Path("configured-chroma"),
            chroma_anonymized_telemetry=False,
        )
        native_settings = object()

        with (
            patch.object(chroma_client, "settings", configured, create=True),
            patch("chromadb.config.Settings", return_value=native_settings) as settings,
            patch("chromadb.PersistentClient", return_value="client") as persistent,
        ):
            result = chroma_client.create_chroma_client()

        self.assertEqual(result, "client")
        settings.assert_called_once_with(anonymized_telemetry=False)
        persistent.assert_called_once_with(
            path="configured-chroma",
            settings=native_settings,
        )
