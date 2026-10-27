"""ДЗ 7, задача 9A: обучающие примеры формата «вопрос с вариантами -> {"answer":"X"}».

Без модели и без MLflow: проверяется сам набор (строится в памяти тем же
кодом, что и стадия DVC format_data), контроль утечки относительно golden
и behavioral, переключение данных sweep в params.yaml и то, что sweep
читает именно params["data"].
"""
import json
import re
import sys
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.prep import build_eval_data  # noqa: E402
from evaluation.prep import build_format_data as fmt  # noqa: E402

INPUT_RE = re.compile(
    r"^.+[?.] A - .+, B - .+(, C - .+)?\. choices: A, B(, C)?, REFUSE\.( Ответ строго JSON\.)?$"
)


def normalize(text):
    # Та же нормализация, что в evaluation/contract.py.
    return " ".join(text.casefold().split())


class FormatData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.train, cls.val = fmt.build()
        cls.reference = build_eval_data.golden() + build_eval_data.behavioral()
        cls.system = json.loads((ROOT / "evaluation/params.json").read_text())["system"]

    def rows(self):
        return self.train + self.val

    # --- объём и схема ---
    def test_volume(self):
        self.assertGreaterEqual(len(self.train), 200)
        self.assertGreaterEqual(len(self.val), 40)

    def test_ids_unique(self):
        ids = [r["id"] for r in self.rows()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(isinstance(i, str) and i for i in ids))

    def test_messages_match_evaluation_prompt(self):
        for r in self.rows():
            roles = [m["role"] for m in r["messages"]]
            self.assertEqual(roles, ["system", "user", "assistant"], r["id"])
            self.assertEqual(r["messages"][0]["content"], self.system, r["id"])
            self.assertEqual(r["messages"][1]["content"], r["input"], r["id"])

    def test_assistant_is_exact_compact_json(self):
        for r in self.rows():
            answer = r["messages"][2]["content"]
            self.assertEqual(answer, '{"answer":"%s"}' % r["expected"], r["id"])
            self.assertEqual(json.loads(answer), {"answer": r["expected"]})
            self.assertIn(r["expected"], r["choices"])

    def test_input_format_like_golden(self):
        for r in self.rows():
            self.assertRegex(r["input"], INPUT_RE)
            labels = re.search(r"choices: ((?:[ABC], )+REFUSE)\.", r["input"]).group(1).split(", ")
            self.assertEqual(labels, r["choices"], r["id"])
        n_options = Counter(len(r["choices"]) - 1 for r in self.train)
        self.assertGreater(n_options[2], 0)
        self.assertGreater(n_options[3], 0)
        strict = sum(r["input"].endswith("Ответ строго JSON.") for r in self.train)
        self.assertTrue(0 < strict < len(self.train))

    def test_expected_points_to_true_option(self):
        right = {f"fmt-{slug}": answer for _, slug, _, answer, _, _ in fmt.FACTS}
        for r in self.rows():
            if r["expected"] == "REFUSE":
                self.assertNotIn(r["group_id"], right)
                continue
            options = dict(re.findall(r"([ABC]) - (.+?)(?=, [ABC] - |\. choices:)", r["input"]))
            self.assertEqual(options[r["expected"]], right[r["group_id"]], r["id"])

    def test_deterministic(self):
        self.assertEqual(fmt.build(), (self.train, self.val))

    # --- баланс меток и доля отказов ---
    def test_label_balance(self):
        for split in (self.train, self.val):
            answers = [r for r in split if r["expected"] != "REFUSE"]
            two = Counter(r["expected"] for r in answers if len(r["choices"]) == 3)
            self.assertLessEqual(abs(two["A"] - two["B"]), 0.1 * sum(two.values()))
            three = Counter(r["expected"] for r in answers if len(r["choices"]) == 4)
            for label in "ABC":
                self.assertGreaterEqual(three[label] / sum(three.values()), 0.2)
            # Не как в golden (25/21/4): ни одна метка не забирает половину ответов.
            share = Counter(r["expected"] for r in answers)
            self.assertLess(max(share.values()) / len(answers), 0.5)

    def test_refuse_share_about_quarter(self):
        for split in (self.train, self.val, self.train[:240], self.val[:40]):
            share = sum(r["expected"] == "REFUSE" for r in split) / len(split)
            self.assertGreaterEqual(share, 0.15)
            self.assertLessEqual(share, 0.35)

    # --- утечка ---
    def test_no_exact_or_group_overlap_with_eval(self):
        ref_text = {normalize(r["input"]) for r in self.reference}
        ref_groups = {r["group_id"] for r in self.reference}
        for r in self.rows():
            self.assertNotIn(normalize(r["input"]), ref_text, r["id"])
            self.assertNotIn(r["group_id"], ref_groups, r["id"])

    def test_max_jaccard_with_eval_below_half(self):
        stats = fmt.leakage(self.train, self.val, self.reference)
        self.assertLess(stats["max_jaccard"], 0.5, stats["max_jaccard_pair"])

    def test_train_and_val_disjoint(self):
        self.assertFalse({normalize(r["input"]) for r in self.train}
                         & {normalize(r["input"]) for r in self.val})
        self.assertFalse({r["group_id"] for r in self.train} & {r["group_id"] for r in self.val})

    def test_leakage_check_rejects_copy_of_golden(self):
        golden = build_eval_data.golden()
        leaked = dict(self.train[0], input=golden[0]["input"])
        with self.assertRaises(ValueError):
            fmt.leakage([leaked] + self.train[1:], self.val, self.reference)

    def test_files_on_disk_match_builder(self):
        params = fmt.data_paths()
        for path, rows in ((params["train_jsonl"], self.train), (params["val_jsonl"], self.val)):
            path = ROOT / path
            if not path.exists():
                self.skipTest(f"{path} не собран: dvc repro --single-item format_data")
            on_disk = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(on_disk, rows)


class SweepUsesFormatData(unittest.TestCase):
    def test_hw6_view_points_to_format_files(self):
        from src.common import load_params

        data = load_params()["data"]
        self.assertEqual(data["train_jsonl"], "data/format/train.jsonl")
        self.assertEqual(data["val_jsonl"], "data/format/val.jsonl")
        self.assertEqual(data["train"], "data/format/tokenized/train.pt")
        self.assertEqual(data["val"], "data/format/tokenized/val.pt")

    def test_hw6_budget(self):
        from src.common import load_params

        p = load_params()
        t = p["train"]
        self.assertEqual((t["max_steps"], t["train_limit"], t["val_limit"], t["eval_every"]),
                         (60, 240, 40, 10))
        self.assertEqual((t["seed"], t["batch_size"], t["grad_accum"]), (42, 1, 2))
        self.assertEqual(len(p["sweep"]), 6)

    def test_older_homeworks_keep_their_data(self):
        from src.config import load_params

        data = load_params()["data"]
        self.assertEqual(data["train"], "data/tokenized/train.pt")
        self.assertEqual(data["val"], "data/tokenized/val.pt")

    def test_sweep_train_loads_paths_from_params_data(self):
        from src import sweep_train

        params = {"train": {"seed": 42}, "model": {"device": "cpu", "dtype": "float32"},
                  "data": {"train": "data/format/tokenized/train.pt",
                           "val": "data/format/tokenized/val.pt"}}
        seen = []

        def fake_load(path):
            seen.append(path)
            raise RuntimeError("stop")

        with mock.patch.object(sweep_train, "load_split", side_effect=fake_load):
            with self.assertRaises(RuntimeError):
                sweep_train.train(params, {}, "unused", print)
        self.assertEqual(seen, ["data/format/tokenized/train.pt"])


if __name__ == "__main__":
    unittest.main()
