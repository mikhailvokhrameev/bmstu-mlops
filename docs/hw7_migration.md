# ДЗ 7: сверка переноса hw67-broken с основным проектом

Что сделано: каждый файл шаблона `mlops26-hw6-7/hw67-broken/` сравнён с файлом по тому же
относительному пути в корне репозитория (`cmp -s` для статуса, `diff` для различий).
Ничего не запускалось: ни тесты, ни обучение, ни docker. Стартовый проект перенесён коммитом
`232bb91` (`chore(hw6): перенести стартовый проект hw67 в общий репозиторий`).

## Итог

В шаблоне 35 файлов (в плане разведки было указано 31, фактическое число другое).

| Статус  | Файлов | Что значит |
|---------|--------|------------|
| SAME    | 13     | байт в байт совпадает с шаблоном |
| DIFF    | 19     | файл есть, содержимое отличается |
| MISSING | 3      | по этому пути в корне файла нет |

Вывод: переносить нечего. Все рабочие файлы шаблона в корне есть. Три MISSING — пустые
заглушки `.gitkeep`, их задача сохранить пустую папку в Git; в корне эти папки заняты
реальным содержимым. Среди DIFF нет файлов, которые корень потерял; это правки под свой проект.

## Таблица «файл, статус, почему отличается»

| Файл | Статус | Почему отличается |
|------|--------|-------------------|
| `Dockerfile` | SAME | не менялся |
| `evaluation/__init__.py` | SAME | не менялся |
| `evaluation/cli.py` | SAME | обвязка оценки не менялась |
| `evaluation/contract.py` | SAME | контракт форматов не менялся |
| `examples/behavioral.example.jsonl` | SAME | не менялся |
| `examples/golden.example.jsonl` | SAME | не менялся |
| `examples/training-index.example.jsonl` | SAME | не менялся |
| `src/__init__.py` | SAME | не менялся |
| `src/runtime.py` | SAME | не менялся |
| `tests/check.py` | SAME | не менялся |
| `tests/check_storage.py` | SAME | не менялся |
| `tests/test_eval.py` | SAME | тесты оценки не ослаблялись |
| `tests/test_pipeline.py` | SAME | не менялся |
| `compose.yaml` | DIFF | в шаблоне на месте `TODO` нет volume; в корне добавлен именованный volume `artifacts:/mlartifacts` (см. ниже) |
| `evaluation/solution.py` | DIFF | в шаблоне заглушки с `NotImplementedError`; в корне реализованы `score` и `quality_gate` (см. ниже) |
| `evaluation/params.json` | DIFF | своя тема (MLOps вместо географии): `critical_slices`, `system` |
| `Makefile` | DIFF | к целям ДЗ 6+7 добавлены цели ДЗ 1–5; `check` занят проверками ДЗ 1–5, аналоги названы `check-hw6`, `check-hw6-defects`, `check-hw6-all`; `install` без `--frozen`; модель `my-smollm-adapter`; отчёт `src.sweep_report` (см. ниже) |
| `params.yaml` | DIFF | в корне один общий файл: секции ДЗ 1–5 плюс секция `hw6:` (модель SmolLM2 вместо Qwen3-0.6B, `revision` закреплён, реестр `my-smollm-adapter`, свои промпты) |
| `pyproject.toml` | DIFF | имя `mlops-hw`, зависимости всех ДЗ (dvc, matplotlib и др.), версии заданы нижней границей (`>=`), тогда как в шаблоне закреплены точно; `mlflow==2.22.2` и `sqlalchemy==2.0.43` сохранены |
| `uv.lock` | DIFF | следствие различий `pyproject.toml` (4096 строк против 2007) |
| `.gitignore` | DIFF | в корне исключены `mlops26-hw*`, `.venv`, `dvc_remote/` и т. д.; шаблон игнорировал всю папку `metrics/`, в корне только `metrics/train_*.json` и `metrics/compare_*.json`, поэтому метрики ДЗ 6 попадают в Git |
| `README.md` | DIFF | корневой README от ДЗ 1 (шаблонный README описывает ДЗ 6+7); описания маршрута ДЗ 6+7 в нём нет |
| `EVALUATION.md` | DIFF | «измерения Qwen» заменено на «измерения модели»; добавлен раздел «Состояние выполнения на SmolLM2-135M-Instruct» |
| `src/common.py` | DIFF | `load_params()` собирает вид ДЗ 6 из общего `params.yaml` (общие `model`/`data` + секция `hw6`) вместо чтения целого файла |
| `src/data.py` | DIFF | `torch.load(..., weights_only=False)` вместо `True` |
| `src/predict.py` | DIFF | пример URI: `my-smollm-adapter` вместо `my-qwen-adapter` |
| `src/promote.py` | DIFF | убрано `TODO` у порога: добавлена `effective_threshold`, проверка вызывает `gate(run, threshold)`; порог CLI может только ужесточать |
| `src/sweep.py` | DIFF | импорт `src.sweep_train`; `python_model="src/adapter_model.py"`, `code_paths=["src"]`; версии библиотек в `pip_requirements` берутся из окружения; параметры логируются без условия на пустой набор |
| `src/model.py` | DIFF | пути не совпадают по имени: в корне `src/model.py` — загрузка модели ДЗ 1–2, а MLflow-обёртка шаблона лежит в `src/adapter_model.py` (см. ниже) |
| `src/train.py` | DIFF | в корне `src/train.py` — цикл обучения ДЗ 5; шаблонный LoRA-ран лежит в `src/sweep_train.py` |
| `src/report.py` | DIFF | в корне `src/report.py` — отчёт ДЗ 2 (анатомия); шаблонный отчёт лежит в `src/sweep_report.py` |
| `tests/check.sh` | DIFF | в корне `tests/check.sh` — самопроверка ДЗ 1; шаблонный `check.sh` хранится дословно в `tests/hw67_check.sh`, отпечаток считает `tests/check_hw6.sh` |
| `data/eval/.gitkeep` | MISSING | папка есть и содержит `golden.jsonl`, `behavioral.jsonl`, `training-index.jsonl` |
| `data/tokenized/.gitkeep` | MISSING | папка есть и содержит `train.pt`, `val.pt` и упакованные варианты |
| `docs/.gitkeep` | MISSING | папка есть и содержит документы всех ДЗ |

## Проверка файлов, перенесённых под другим именем

Три шаблонных файла в корне названы иначе, потому что имена `model.py`, `train.py`, `report.py`
уже были заняты ДЗ 1–5. Сравнение шаблонного файла с корневым аналогом:

| Шаблон | Корневой аналог | Расхождение |
|--------|-----------------|-------------|
| `src/model.py` | `src/adapter_model.py` | 2 строки: сборка промпта через `src.prompt.build_chat_text` вместо `apply_chat_template(..., enable_thinking=...)` |
| `src/train.py` | `src/sweep_train.py` | 1 строка: `from src.adapter_model import AdapterModel` |
| `src/report.py` | `src/sweep_report.py` | совпадают |
| `tests/check.sh` | `tests/hw67_check.sh` | совпадают |

## Ключевые различия

### compose.yaml: volume для артефактов

```diff
-    # TODO: где хранятся файлы после замены контейнера?
+    # Postgres хранит только метаданные (runs, params, metrics, пути к артефактам).
+    # Сами ФАЙЛЫ артефактов лежат в /mlartifacts, и без монтирования это слой
+    # контейнера: после --force-recreate он исчезает, а в БД остаются «висячие»
+    # ссылки. Именованный volume `artifacts` переживает замену контейнера.
+    volumes:
+      - artifacts:/mlartifacts
```

### evaluation/solution.py: заглушки заменены реализацией

Шаблон (`diff` показывает 190 изменённых строк, здесь только суть):

```diff
-"""Реализуйте две функции. Контракт и примеры: EVALUATION.md и tests/test_eval.py."""
+"""Оценка ответов и допуск в champion. Контракт: EVALUATION.md, tests/test_eval.py."""
...
-    raise NotImplementedError('ДЗ 6+7: реализуйте score; запустите make check-eval')
+    (строгий разбор ответа, проверка id, подсчёт overall и срезов)
```

Добавлены: константы `MIN_GOLDEN = 50`, `MIN_SLICES = 3`, `MIN_BEHAVIORAL = 3`, строгий разбор
`_parse` (JSON-объект с единственным полем `answer`, строка из `choices`, дубли ключей
отвергаются), `_stats`. `score` бросает `ValueError` на пустой набор и на несовпадающие или
дублирующиеся id; плохие ответы остаются в знаменателе. Реализация `quality_gate` лежит в том
же файле, после `score`.

### Makefile: цели и имена

```diff
-.PHONY: up down sweep promote predict check check-storage
+.PHONY: up down sweep promote predict check-storage report candidate ... check-hw6 check-hw6-defects check-hw6-all ...
-	uv sync --frozen
+	uv sync
-	... --uri models:/my-qwen-adapter@champion ...
-check:
-	bash tests/check.sh
+	... --uri models:/my-smollm-adapter@champion ...
+check-hw6:
+	bash tests/check_hw6.sh
+check-hw6-defects:
+	uv run python -m unittest discover -s tests -p 'test_hw6_defects.py' -v
-	uv run python -m src.report
+	uv run python -m src.sweep_report
-check-all: check check-eval
+check-hw6-all: check-hw6 check-eval
```

Следствие: цели `check` и `check-all` в смысле задания ДЗ 6+7 в корне отсутствуют
(`check` запускает проверки ДЗ 1–5). Это расхождение 1 из плана, оно решается отдельной задачей.

## Подтверждение: обвязка оценки не ослаблялась

| Файл | `cmp` с шаблоном | История в Git |
|------|------------------|---------------|
| `evaluation/cli.py` | SAME | единственный коммит `232bb91` (перенос) |
| `evaluation/contract.py` | SAME | единственный коммит `232bb91` (перенос) |
| `tests/test_eval.py` | SAME | единственный коммит `232bb91` (перенос) |

Все три файла совпадают с шаблоном побайтно и после переноса не правились. Меняла
поведение оценки только реализация в `evaluation/solution.py`, которую шаблон требует написать.

## Отпечаток tests/

Скрипт `tests/check_hw6.sh` считает sha256 по пяти файлам: `check.py`, `check_storage.py`,
`test_eval.py`, `test_pipeline.py` и `check.sh` (в корне его роль играет `tests/hw67_check.sh`).
Я воспроизвёл этот алгоритм отдельным скриптом (сам `check_hw6.sh` не запускался) на файлах
шаблона и на файлах корня. Оба раза результат `c42ac7788026`, то есть тот, что указан
у преподавателя. Значение `e39a04315254` получается, если хешировать все файлы папки `tests/`
корня: там лежат ещё проверки ДЗ 1–5, которых в шаблоне нет.

## Что не измерялось

Здесь сравнивается только содержимое файлов. Работоспособность кода, прохождение тестов и
согласованность с хешами runs не проверялись: по условиям задачи тесты и обучение не запускались.
