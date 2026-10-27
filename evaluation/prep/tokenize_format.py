"""Токенизация примеров формата для sweep (стадия DVC format_tokenize).

Тот же код, что у стадии tokenize ДЗ 4 (src.tokenize_data.process_split:
шаблон чата, маска промпта, обрезка по max_seq_len), но вход и выход берутся
из вида ДЗ 6 src.common.load_params()["data"], а токенизатор закреплён на
hw6.model.revision — той же ревизии, что грузит sweep. Packing выключен:
sweep читает только examples. Файлы и метрики ДЗ 4 не трогаются.

Запуск: python -m evaluation.prep.tokenize_format
"""
import json
from pathlib import Path

import torch
from transformers import AutoTokenizer

from src import common, config
from src.tokenize_data import process_split

METRICS = Path("metrics/format_tokenize.json")


def main():
    base, hw6 = config.load_params(), common.load_params()
    model, data = hw6["model"], hw6["data"]
    params = {**base, "model": model, "packing": {"enabled": False}}
    tokenizer = AutoTokenizer.from_pretrained(model["name"], revision=model["revision"])
    tokenizer.padding_side = base["tokenize"]["padding_side"]
    splits = {}
    for name in ("train", "val"):
        examples, stats, _ = process_split(tokenizer, name, Path(data[f"{name}_jsonl"]), params)
        out = Path(data[name])
        out.parent.mkdir(parents=True, exist_ok=True)
        torch.save({
            "examples": examples,
            "model": model["name"],
            "max_seq_len": base["tokenize"]["max_seq_len"],
            "padding_side": tokenizer.padding_side,
            "pad_token_id": tokenizer.pad_token_id,
        }, out)
        splits[name] = stats
    METRICS.parent.mkdir(parents=True, exist_ok=True)
    METRICS.write_text(json.dumps({"model": model["name"], "revision": model["revision"],
                                   "max_seq_len": base["tokenize"]["max_seq_len"], "splits": splits},
                                  ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  -> {data['train']}, {data['val']}, {METRICS}")


if __name__ == "__main__":
    main()
