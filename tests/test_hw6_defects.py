"""Тесты ДЗ 6: слияние стартового проекта hw67 и (в следующих задачах) дефекты."""
import sys
import unittest
from pathlib import Path
from unittest import mock

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
        self.assertEqual(model["revision"], "12fd25f77366fa6b3b4b768ec3050bf629380bac")
        self.assertEqual(model["name"], "HuggingFaceTB/SmolLM2-135M-Instruct")


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


class TestVersions(unittest.TestCase):
    """Дефект 3: версии train/val попадают в теги родительского и дочерних runs."""

    KEYS = ("git_sha", "git_dirty", "code_sha256", "data_train_sha256",
            "data_val_sha256", "config_sha256")

    def _run(self):
        from unittest import mock

        from src import sweep

        names = [f"c{i}" for i in range(6)]
        params = {"sweep": [{"name": n} for n in names],
                  "train": {k: 1 for k in ("max_steps", "train_limit", "val_limit",
                                           "batch_size", "grad_accum", "eval_every")}}
        prov = {k: f"v-{k}" for k in self.KEYS}
        received = []

        def start_run(run_name=None, nested=False, tags=None, **kwargs):
            if not nested:
                parent_tags.update(tags or {})
            run = mock.MagicMock()
            run.__enter__.return_value.info.run_id = "id"
            return run

        parent_tags = {}
        mlflow = mock.MagicMock()
        mlflow.start_run.side_effect = start_run
        with mock.patch.object(sweep, "mlflow", mlflow), \
                mock.patch.object(sweep, "load_params", return_value=params), \
                mock.patch.object(sweep, "connect"), \
                mock.patch.object(sweep, "provenance", return_value=prov), \
                mock.patch.object(sweep, "save_json"), \
                mock.patch.object(sweep, "run_candidate",
                                  lambda p, c, v: received.append(v) or {"name": c["name"]}):
            sweep.main()
        return parent_tags, received

    def test_parent_run_has_data_hashes(self):
        parent_tags, _ = self._run()
        for key in self.KEYS:
            self.assertIn(key, parent_tags)

    def test_candidates_receive_data_hashes(self):
        _, received = self._run()
        self.assertEqual(len(received), 6)
        for versions in received:
            for key in self.KEYS:
                self.assertIn(key, versions)


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


class TestPromoteGate(unittest.TestCase):
    """Дефект 4: порог по loss проверяется до регистрации версии в реестре."""

    @staticmethod
    def _run(final=1.0, base=2.0, status="FINISHED", drop=None):
        metrics = {"final_val_loss": final, "base_val_loss": base}
        metrics.pop(drop, None)
        run = mock.MagicMock()
        run.data.metrics = metrics
        run.info.status = status
        run.info.run_id = "rid"
        return run

    def _promote(self, run, threshold):
        from src import promote

        client = mock.MagicMock()
        mlflow = mock.MagicMock()
        with mock.patch.object(promote, "mlflow", mlflow):
            try:
                promote.promote(client, run, "m", "candidate", threshold)
            finally:
                self.registered = mlflow.register_model.called
                self.client = client

    def test_zero_threshold_rejected(self):
        with self.assertRaises(ValueError):
            self._promote(self._run(final=1.0, base=2.0), 0.0)
        self.assertFalse(self.registered)

    def test_ratio_equal_threshold_accepted(self):
        self._promote(self._run(final=2.0, base=2.0), 1.0)
        self.assertTrue(self.registered)
        tags = {c.args[2]: c.args[3] for c in self.client.set_model_version_tag.call_args_list}
        self.assertEqual(tags["val_ratio"], "1.0")
        self.assertEqual(tags["gate.max_val_ratio"], "1.0")
        self.assertEqual(tags["validation"], "passed")

    def test_worse_than_base_rejected(self):
        with self.assertRaises(ValueError):
            self._promote(self._run(final=3.0, base=2.0), 1.0)
        self.assertFalse(self.registered)

    def test_missing_nan_or_unfinished_rejected(self):
        cases = {
            "no_base": self._run(drop="base_val_loss"),
            "nan_final": self._run(final=float("nan")),
            "failed": self._run(status="FAILED"),
        }
        for name, run in cases.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    self._promote(run, 1.0)
                self.assertFalse(self.registered)

    def test_cli_cannot_loosen_threshold(self):
        from src.promote import effective_threshold

        self.assertEqual(effective_threshold(1.0, 5.0), 1.0)
        self.assertEqual(effective_threshold(1.0, 0.5), 0.5)
        self.assertEqual(effective_threshold(1.0, None), 1.0)


if __name__ == "__main__":
    unittest.main()
