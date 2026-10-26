"""Тесты ДЗ 6: слияние стартового проекта hw67 и (в следующих задачах) дефекты."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestMerge(unittest.TestCase):
    def test_hw6_view_has_expected_sections(self):
        from src.common import load_params

        params = load_params()
        self.assertGreaterEqual(
            set(params),
            {"model", "data", "train", "lora", "sweep", "tracking", "registry", "generation"},
        )
        self.assertEqual(params["train"]["max_steps"], 4)
        self.assertEqual(params["train"]["grad_accum"], 2)
        self.assertEqual(len(params["sweep"]), 6)

    def test_older_homeworks_params_untouched(self):
        from src.config import load_params

        params = load_params()
        self.assertEqual(params["train"]["grad_accum"], 8)
        self.assertIsNone(params["train"]["max_steps"])
        self.assertEqual(params["lora_train"]["r"], 8)

    def test_model_section_merged(self):
        from src.common import load_params

        model = load_params()["model"]
        self.assertEqual(model["revision"], "main")
        self.assertEqual(model["name"], "Qwen/Qwen3-0.6B")


if __name__ == "__main__":
    unittest.main()
