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


class TestSeparateRuns(unittest.TestCase):
    """Дефект 1: каждый кандидат sweep — отдельный вложенный MLflow-run."""

    def _params(self, names):
        return {"sweep": [{"name": n} for n in names],
                "train": {k: 1 for k in ("max_steps", "train_limit", "val_limit",
                                         "batch_size", "grad_accum", "eval_every")}}

    def _run_main(self, names, run_candidate=None):
        from unittest import mock

        from src import sweep

        nested_names = []

        def start_run(run_name=None, nested=False, **kwargs):
            if nested:
                nested_names.append(run_name)
            run = mock.MagicMock()
            run.__enter__.return_value.info.run_id = "id"
            return run

        mlflow = mock.MagicMock()
        mlflow.start_run.side_effect = start_run
        save_json = mock.Mock()
        calls = []
        default = lambda p, c, v: calls.append(c["name"]) or {"name": c["name"]}
        with mock.patch.object(sweep, "mlflow", mlflow), \
                mock.patch.object(sweep, "load_params", return_value=self._params(names)), \
                mock.patch.object(sweep, "connect"), \
                mock.patch.object(sweep, "provenance", return_value={}), \
                mock.patch.object(sweep, "save_json", save_json), \
                mock.patch.object(sweep, "run_candidate", run_candidate or default):
            try:
                sweep.main()
            finally:
                self.last_save_json = save_json
        return nested_names

    NAMES = [f"c{i}" for i in range(6)]

    def test_each_candidate_gets_own_nested_run(self):
        nested = self._run_main(self.NAMES)
        self.assertEqual(nested, self.NAMES)
        self.assertNotIn("all-candidates", nested)

    def test_failed_candidate_aborts_without_summary(self):
        count = []

        def failing(p, c, v):
            count.append(c["name"])
            if len(count) == 3:
                raise RuntimeError("boom")
            return {"name": c["name"]}

        with self.assertRaises(RuntimeError):
            self._run_main(self.NAMES, failing)
        self.last_save_json.assert_not_called()

    def test_duplicate_or_too_few_candidates_rejected(self):
        with self.assertRaises(SystemExit):
            self._run_main(self.NAMES[:5])
        with self.assertRaises(SystemExit):
            self._run_main(["a", "b", "c", "d", "e", "a"])


class TestCompose(unittest.TestCase):
    """Дефект 2: файлы артефактов должны лежать в volume, а не в контейнере."""

    @staticmethod
    def _mlflow_service():
        import yaml

        path = Path(__file__).resolve().parents[1] / "compose.yaml"
        return yaml.safe_load(path.read_text(encoding="utf-8"))["services"]["mlflow"]

    def test_mlflow_mounts_artifacts_volume(self):
        self.assertIn("artifacts:/mlartifacts", self._mlflow_service().get("volumes", []))

    def test_artifacts_destination_matches_mount(self):
        service = self._mlflow_service()
        prefix = "--artifacts-destination="
        destinations = [a[len(prefix):] for a in service["command"] if a.startswith(prefix)]
        self.assertEqual(len(destinations), 1)
        mounts = [v.split(":")[1] for v in service.get("volumes", [])]
        self.assertIn(destinations[0], mounts)


if __name__ == "__main__":
    unittest.main()
