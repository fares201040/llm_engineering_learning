from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from week5.new_implementation.colab.prepare_synthetic_runtime import (
    model_stage_timeout_seconds,
)


class CpuRuntimeTimeoutTests(unittest.TestCase):
    def test_cpu_session_allows_longer_model_calls(self):
        with patch("shutil.which", return_value=None):
            self.assertEqual(model_stage_timeout_seconds(), "900")

    def test_t4_session_retains_shorter_model_timeout(self):
        probe = subprocess.CompletedProcess(["nvidia-smi", "-L"], 0, stdout="GPU 0: T4")
        with (
            patch("shutil.which", return_value="/usr/bin/nvidia-smi"),
            patch("subprocess.run", return_value=probe),
        ):
            self.assertEqual(model_stage_timeout_seconds(), "240")
