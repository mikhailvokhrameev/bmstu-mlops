"""Стадия collect: снимок источника → data/raw.jsonl.

Контракт стадии, а не её внутренности, держит остальной пайплайн:
на выходе JSONL со строками {"id", "topic", "messages": [system, user, assistant]}.

Снимок Stack Overflow (стадия fetch) сам по себе сдачей не является. Поэтому
стадия не перекладывает его в JSONL один в один, а делает четыре вещи, и каждая
видна числом в metrics/collect.json:

  1. сужает набор до перечисленных тегов (collect.topics), если это нужно задаче;
  2. сверяет принятый ответ с разметкой источника (collect.verify_answer) —
     расхождение выбрасывается, а не переносится в обучение;
  3. отсекает шум по голосам и длине ответа (min_*_score, max_answer_chars);
  4. добавляет системную реплику, которой в источнике нет, и разводит её
     на варианты (collect.system_prompts), чтобы модель не заучила формулировку.
"""

import hashlib
import html
import json
import re
import time
from pathlib import Path

from src.config import load_params, source_files

COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def pick_prompt(example_id: str, variants: list[str]) -> str:
    """Детерминированно выбрать вариант инструкции по id примера.

    Именно sha1, а не встроенный hash(): тот солится на каждый запуск процесса,
    и raw.jsonl переставал бы быть воспроизводимым.
    """
    digest = hashlib.sha1(example_id.encode("utf-8")).hexdigest()
    return variants[int(digest, 16) % len(variants)]


def tidy(text: str) -> str:
    """Markdown из API → чистый текст: сущности раскрыты, служебные комментарии убраны.

    <!-- language: lang-py --> — метка подсветки редактора Stack Overflow,
    к смыслу вопроса она не относится, а в обучающем тексте только мусорит.
    """
    text = html.unescape(text).replace("\r\n", "\n")
    return COMMENT.sub("", text).strip()


def answer_matches_source(row: dict) -> bool:
    """Совпадает ли ответ с разметкой источника.

    Ответ должен быть отмечен принятым, относиться к этому вопросу и быть
    тем самым, на который ссылается вопрос. Всё, что не так, — битая выгрузка.
    """
    return (
        bool(row["answer_is_accepted"])
        and row["answer_question_id"] == row["question_id"]
        and row["answer_id"] == row["accepted_answer_id"]
    )


def main() -> None:
    params = load_params()
    cfg = params["collect"]
    paths = params["paths"]
    n_rows = cfg["n_rows"]
    variants = cfg["system_prompts"]
    if not variants:
        raise SystemExit("collect.system_prompts пуст: инструкцию брать неоткуда")
    topics = cfg["topics"]
    wanted = set(topics) if topics else None

    out = Path(paths["raw"])
    out.parent.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    scanned = written = dropped_topic = dropped_answer = dropped_score = dropped_length = 0
    prompts_used: set[str] = set()

    with out.open("w", encoding="utf-8") as fh:
        for src in source_files(params):
            taken = 0
            with src.open(encoding="utf-8") as rows:
                for line in rows:
                    if taken >= n_rows:
                        break
                    row = json.loads(line)
                    scanned += 1
                    # Фильтры применяются ДО отсечки n_rows: иначе «первые 3000 строк»
                    # и «3000 строк по теме» — разные вещи.
                    if wanted is not None and row["tag"] not in wanted:
                        dropped_topic += 1
                        continue
                    if cfg["verify_answer"] and not answer_matches_source(row):
                        dropped_answer += 1
                        continue
                    if (
                        row["question_score"] < cfg["min_question_score"]
                        or row["answer_score"] < cfg["min_answer_score"]
                    ):
                        dropped_score += 1
                        continue
                    answer = tidy(row["answer"])
                    if len(answer) > cfg["max_answer_chars"]:
                        dropped_length += 1
                        continue
                    example_id = f"so_{row['question_id']}"
                    prompt = pick_prompt(example_id, variants)
                    prompts_used.add(prompt)
                    user = f"Тема:\n{row['tag']}\n\nВопрос:\n{tidy(row['title'])}\n\n{tidy(row['question'])}"
                    record = {
                        "id": example_id,
                        "topic": row["tag"],
                        "messages": [
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": user},
                            {"role": "assistant", "content": answer},
                        ],
                    }
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                    taken += 1
                    written += 1

    metrics = {
        "version": cfg["version"],
        "files": len(source_files(params)),
        "rows_scanned": scanned,
        "rows_written": written,
        "dropped_topic_filter": dropped_topic,
        "dropped_answer_mismatch": dropped_answer,
        "dropped_score": dropped_score,
        "dropped_answer_length": dropped_length,
        "topics_filter": len(wanted) if wanted else 0,
        "system_prompt_variants": len(prompts_used),
        "seconds": round(time.perf_counter() - started, 2),
    }
    mpath = Path(paths["metrics_collect"])
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        f"collect: версия {cfg['version']}, файлов {metrics['files']}, "
        f"просмотрено {scanned}, записано {written} "
        f"(фильтр тем -{dropped_topic}, расхождение с разметкой -{dropped_answer}, "
        f"голоса -{dropped_score}, длинные ответы -{dropped_length}), "
        f"вариантов инструкции {len(prompts_used)}, "
        f"{metrics['seconds']} с → {out}"
    )


if __name__ == "__main__":
    main()
