"""Стадия fetch: Stack Exchange API → снимок источника data/source/*.jsonl.

Источник ДЗ 3 — вопросы Stack Overflow по MLOps-инструментам с принятым ответом.
Тег вопроса становится темой (topic), а значит и группой сплита.

Сеть — отдельная стадия, а не часть collect. У API лимит 300 запросов в сутки
на IP, и тратить его на каждый `dvc repro` нельзя: снимок берётся один раз,
версионируется DVC и дальше воспроизводится из кэша. collect работает уже
с ним и в сеть не ходит.

Снимок делится на шарды по рангу вопроса внутри тега: шард 0 — самые
популярные, шард 1 — следующие. Версия v1 читает первый шард, v2 — оба.
"""

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from src.config import load_params

BATCH = 100        # потолок API: id в одном запросе и вопросов на странице
RETRIES = 3
# Материалы, опубликованные с 2018-05-02, распространяются под CC BY-SA 4.0,
# более ранние — под 3.0. Для вопросов API поле content_license не отдаёт.
LICENSE_4_FROM = 1525219200


class FetchError(RuntimeError):
    """API ответил ошибкой или недоступен."""


class Client:
    """Клиент Stack Exchange API: считает запросы и уважает backoff."""

    def __init__(self, cfg: dict) -> None:
        self.base = cfg["api"]
        self.site = cfg["site"]
        self.filter = cfg["filter"]
        self.pause = cfg["pause_seconds"]
        self.calls = 0
        self.quota_remaining: int | None = None

    def get(self, endpoint: str, **query) -> dict:
        query = {"site": self.site, "filter": self.filter, **query}
        url = f"{self.base}/{endpoint}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
        for attempt in range(1, RETRIES + 1):
            try:
                with urllib.request.urlopen(request, timeout=30) as resp:
                    raw = resp.read()
                    if resp.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                break
            except urllib.error.HTTPError as exc:
                # Тело ошибки — JSON с error_message: без него видно только «400».
                detail = exc.read().decode("utf-8", errors="replace")[:200]
                raise FetchError(f"{endpoint}: HTTP {exc.code} — {detail}") from exc
            except urllib.error.URLError as exc:
                if attempt == RETRIES:
                    raise FetchError(f"{endpoint}: {exc.reason}") from exc
                time.sleep(2 * attempt)
        payload = json.loads(raw)
        self.calls += 1
        self.quota_remaining = payload.get("quota_remaining", self.quota_remaining)
        time.sleep(max(self.pause, payload.get("backoff", 0)))
        return payload


def question_license(question: dict) -> str:
    """Лицензия вопроса: из ответа API, а если её нет — по дате публикации."""
    if question.get("content_license"):
        return question["content_license"]
    return "CC BY-SA 4.0" if question["creation_date"] >= LICENSE_4_FROM else "CC BY-SA 3.0"


def top_questions(client: Client, tag: str, pool: int) -> list[dict]:
    """Самые популярные вопросы тега, у которых есть принятый ответ."""
    payload = client.get(
        "search/advanced",
        tagged=tag, accepted="True", sort="votes", order="desc", pagesize=pool, page=1,
    )
    return [q for q in payload["items"] if q.get("accepted_answer_id")]


def fetch_answers(client: Client, ids: list[int]) -> dict[int, dict]:
    """Тексты принятых ответов пачками по BATCH — один запрос на сотню id."""
    found: dict[int, dict] = {}
    for start in range(0, len(ids), BATCH):
        chunk = ids[start : start + BATCH]
        payload = client.get(
            f"answers/{';'.join(str(i) for i in chunk)}", pagesize=BATCH,
        )
        found.update({a["answer_id"]: a for a in payload["items"]})
    return found


def main() -> None:
    params = load_params()
    cfg = params["fetch"]
    paths = params["paths"]
    per_shard = cfg["per_shard"]
    shards = cfg["shards"]
    started = time.perf_counter()
    client = Client(cfg)

    # Теги идут от редких к частым: вопрос с несколькими тегами достаётся первому
    # из них, и редкий тег не остаётся пустым из-за того, что airflow забрал всё.
    seen: set[int] = set()
    chosen: dict[str, list[dict]] = {}
    for tag in cfg["tags"]:
        fresh = [q for q in top_questions(client, tag, cfg["pool"]) if q["question_id"] not in seen]
        chosen[tag] = fresh[: per_shard * shards]
        seen.update(q["question_id"] for q in chosen[tag])
        print(f"fetch: {tag}: {len(chosen[tag])} вопросов (запросов {client.calls})")

    answers = fetch_answers(client, [q["accepted_answer_id"] for rows in chosen.values() for q in rows])

    out_dir = Path(paths["source_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_per_shard = [0] * shards
    missing_answers = 0
    handles = [
        (out_dir / f"stackoverflow_{k:04d}.jsonl").open("w", encoding="utf-8") for k in range(shards)
    ]
    try:
        for tag, rows in chosen.items():
            for rank, q in enumerate(rows):
                answer = answers.get(q["accepted_answer_id"])
                if answer is None:
                    missing_answers += 1
                    continue
                shard = rank // per_shard
                record = {
                    "question_id": q["question_id"],
                    "tag": tag,
                    "title": q["title"],
                    "question": q["body_markdown"],
                    "question_score": q["score"],
                    "question_license": question_license(q),
                    "question_link": q["link"],
                    "accepted_answer_id": q["accepted_answer_id"],
                    "answer_id": answer["answer_id"],
                    "answer_question_id": answer["question_id"],
                    "answer_is_accepted": answer["is_accepted"],
                    "answer": answer["body_markdown"],
                    "answer_score": answer["score"],
                    "answer_license": answer["content_license"],
                }
                handles[shard].write(json.dumps(record, ensure_ascii=False) + "\n")
                rows_per_shard[shard] += 1
    finally:
        for fh in handles:
            fh.close()

    metrics = {
        "site": cfg["site"],
        "fetched_at": date.today().isoformat(),
        "tags": len(cfg["tags"]),
        "tags_with_rows": sum(1 for rows in chosen.values() if rows),
        "questions": sum(len(rows) for rows in chosen.values()),
        "rows_per_shard": rows_per_shard,
        "answers_missing": missing_answers,
        "api_calls": client.calls,
        "quota_remaining": client.quota_remaining,
        "seconds": round(time.perf_counter() - started, 2),
    }
    mpath = Path(paths["metrics_fetch"])
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        f"fetch: {metrics['tags_with_rows']} тегов, {metrics['questions']} вопросов, "
        f"шарды {rows_per_shard}, запросов к API {client.calls} "
        f"(осталось в квоте {client.quota_remaining}), {metrics['seconds']} с → {out_dir}"
    )


if __name__ == "__main__":
    main()
