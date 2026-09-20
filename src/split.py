"""Стадия split: разбиение на train/val/test."""

import json
import random
import time
from collections import Counter
from pathlib import Path

from src.config import load_params
from src.contamination import is_clean, report
from src.schema import Example, dump, iter_examples
from src.textnorm import normalize_group


def group_split(keys: list[str], ratios: dict[str, float], seed: int) -> list[str]:
    """Раздать строкам метки сплита так, чтобы группа целиком попала в один сплит.

    Резать по строкам нельзя: вопросы одной группы близки друг к другу, и при
    случайном сплите они оказываются и в train, и в test. Тест перестаёт быть
    незнакомым, а метрика на нём — честной.

    Группы перемешиваются по seed, затем крупные идут первыми, и каждая
    достаётся сплиту с наибольшим недобором до целевой доли. Так доли выходят
    близкими к заданным, а результат детерминирован.
    """
    sizes = Counter(keys)
    groups = sorted(sizes)
    random.Random(seed).shuffle(groups)
    groups.sort(key=lambda g: -sizes[g])    # сортировка устойчивая: ничьи остаются в порядке shuffle
    total = len(keys)
    filled = {name: 0 for name in ratios}
    owner: dict[str, str] = {}
    for group in groups:
        name = max(ratios, key=lambda n: ratios[n] * total - filled[n])
        owner[group] = name
        filled[name] += sizes[group]
    empty = [name for name, count in filled.items() if not count]
    if empty:
        raise SystemExit(
            f"групп слишком мало ({len(groups)}): сплиты {empty} остались пустыми"
        )
    return [owner[key] for key in keys]


def main() -> None:
    params = load_params()
    paths = params["paths"]
    cfg = params["split"]
    started = time.perf_counter()

    examples: list[Example] = list(iter_examples(paths["clean"]))
    if cfg["group_key"] != "topic":
        raise SystemExit(f"неизвестный split.group_key: {cfg['group_key']!r}")

    keys = [normalize_group(ex.topic) for ex in examples]
    sizes = Counter(keys)

    labels = group_split(keys, cfg["ratios"], cfg["seed"])
    buckets: dict[str, list[Example]] = {name: [] for name in cfg["ratios"]}
    for label, ex in zip(labels, examples):
        buckets[label].append(ex)

    for name, rows in buckets.items():
        out = Path(paths[name])
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            for ex in rows:
                fh.write(dump(ex) + "\n")

    nd = params["clean"]["near_dup"]
    rep = report(
        buckets["train"],
        buckets["test"],
        shingle_words=nd["shingle_words"],
        num_perm=nd["num_perm"],
        threshold=params["contamination"]["threshold"],
    )

    metrics = {
        "version": params["collect"]["version"],
        "seed": cfg["seed"],
        "group_key": cfg["group_key"],
        "groups_total": len(sizes),
        "sizes": {name: len(rows) for name, rows in buckets.items()},
        "groups": {
            name: len({normalize_group(ex.topic) for ex in rows}) for name, rows in buckets.items()
        },
        "ratios_actual": {
            name: round(len(rows) / len(examples), 4) for name, rows in buckets.items()
        },
        "contamination": rep,
        "seconds": round(time.perf_counter() - started, 2),
    }
    mpath = Path(paths["metrics_split"])
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        "split: "
        + ", ".join(f"{name} {len(rows)}" for name, rows in buckets.items())
        + f" (групп {len(sizes)}, {metrics['seconds']} с)"
    )

    # Метрики записаны выше — по ним видно, что именно протекло. Но стадия
    # обязана упасть: молча оставленный протекающий test доехал бы до обучения.
    if not is_clean(rep):
        leaks = ", ".join(
            f"{name} {rep[name]}"
            for name in ("id_overlap", "text_overlap", "group_overlap", "near_dup_pairs")
            if rep[name]
        )
        raise SystemExit(f"split: контаминация train/test ({leaks}) — метрики на test будут завышены")


if __name__ == "__main__":
    main()
