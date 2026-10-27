# ДЗ 6 (MLflow): разбор дефектов sweep

## Дефект 1. Разные модели в одном run

### Симптом

Sweep обучает шесть разных LoRA-адаптеров (разные `r`, `lr`, `target_modules`),
но в MLflow они попадали в один и тот же run `all-candidates`. В итоге у шести
моделей один общий набор params и metrics: непонятно, какие значения относятся
к какой модели, и сравнивать кандидатов в UI нельзя.

### Причина

В `src/sweep.py` функция `main()` открывала один вложенный run на весь цикл:

```python
with mlflow.start_run(run_name="all-candidates", nested=True):
    for candidate in p["sweep"]:
        rows.append(run_candidate(p, candidate, versions))
```

Параметры в MLflow неизменяемы: `log_params` с другим значением для уже
записанного ключа вызывает ошибку. Поэтому в `run_candidate` стояло условие
`if not ...data.params:` — параметры писались только для первого кандидата, а у
остальных молча пропускались. Условие маскировало дефект: без него `log_params`
упал бы на втором кандидате.

### Исправление

Один дочерний run на кандидата, с именем кандидата; параметры логируются
безусловно:

```python
for candidate in p["sweep"]:
    with mlflow.start_run(run_name=candidate["name"], nested=True):
        rows.append(run_candidate(p, candidate, versions))
```

Структура: родитель `sweep` -> шесть дочерних runs. Если кандидат падает,
исключение не глотается, итоговый `metrics/sweep.json` не пишется.

### Доказательство «до/после»

Тест `TestSeparateRuns` в `tests/test_hw6_defects.py` подменяет `mlflow` и
проверяет имена вызовов `start_run(..., nested=True)`.

До исправления (RED):

```
test_each_candidate_gets_own_nested_run ... FAIL
AssertionError: Lists differ: ['all-candidates'] != ['c0', 'c1', 'c2', 'c3', 'c4', 'c5']
FAILED (failures=1)
```

После исправления (GREEN):

```
test_each_candidate_gets_own_nested_run ... ok
test_failed_candidate_aborts_without_summary ... ok
test_duplicate_or_too_few_candidates_rejected ... ok
Ran 6 tests in 7.265s
OK
```

Позже это подтвердится и в UI: шесть отдельных дочерних runs под `sweep`.

## Дефект 2. Артефакты теряются при замене контейнера

### Симптом

После `docker compose up --force-recreate mlflow` (то же происходит при
обновлении образа) runs и метрики в MLflow остаются, но файлы артефактов
пропадают. Клиент получает ошибку при скачивании:

```text
API request to http://127.0.0.1:5006/api/2.0/mlflow-artifacts/artifacts/1/<run_id>/artifacts/persistence/probe.txt
failed with exception ... ResponseError('too many 500 error responses')
make: *** [check-storage] Error 1
```

### Причина

MLflow хранит данные в двух местах. Postgres (volume `postgres`) держит только
метаданные: runs, params, metrics и пути к артефактам. Сами файлы сервер
пишет в каталог `--artifacts-destination=/mlartifacts` внутри своего
контейнера. В `compose.yaml` volume `artifacts` был объявлен, но сервису
`mlflow` не подключён, поэтому `/mlartifacts` жил в записываемом слое
контейнера. При замене контейнера слой удаляется вместе с файлами, а в БД
остаются ссылки на несуществующие объекты.

### Исправление

Сервису `mlflow` подключён именованный volume (точка монтирования совпадает с
`--artifacts-destination`):

```yaml
volumes:
  - artifacts:/mlartifacts
```

Volume не принадлежит контейнеру и переживает `--force-recreate` и
`make down` (удалит его только `down -v`). Теперь метаданные в Postgres, а
файлы в volume `artifacts`: оба хранилища переживают замену контейнера.

Тесты `TestCompose` проверяют, что монтирование есть и что путь
`--artifacts-destination` совпадает с точкой монтирования.

### Проверка

До исправления `make check-storage` падает (см. выше). После:

```text
PASS: новый контейнер вернул тот же артефакт, SHA256 совпал
```

Скрипт `tests/check_storage.py` логирует файл с уникальным содержимым,
пересоздаёт только сервис `mlflow`, скачивает файл обратно и сравнивает SHA256.
Результат записывается в `metrics/storage-check.json`.

## Дефект 3. В тегах runs нет версий train и val

### Симптом

У родительского и дочерних runs sweep в MLflow были теги `git_sha`,
`git_dirty`, `code_sha256`, `config_sha256`, но не было `data_train_sha256` и
`data_val_sha256`. По run нельзя было узнать, на каких именно данных он
обучался, а `promote.select()` и проверка 2 требуют все шесть ключей.

### Причина

В `src/sweep.py` (`main`) версии фильтровались и данные выбрасывались:

```python
versions = {k: v for k, v in provenance(p).items() if not k.startswith("data_")}
```

`provenance(p)` считает все шесть хэшей, но фильтр `not k.startswith("data_")`
убирал оба хэша данных до того, как словарь попадал в теги и в
`run_candidate`.

Почему sha данных важен: `git_sha` не покрывает `data/`. Каталог лежит в
`.gitignore` и версионируется через DVC, поэтому два запуска с одним и тем же
коммитом могут обучаться на разных `train.pt` и `val.pt`, а по `git_sha`
это не заметно. Без хэшей данных метрики двух runs нельзя честно сравнивать.

### Исправление

Фильтр убран, в теги идёт весь результат `provenance`:

```python
versions = provenance(p)
```

Теперь все шесть ключей (`git_sha`, `git_dirty`, `code_sha256`,
`data_train_sha256`, `data_val_sha256`, `config_sha256`) попадают в теги
родительского run и в `versions` каждого кандидата.

### Доказательство «до/после»

Тесты `TestVersions` подменяют `provenance` заглушкой с шестью ключами и
проверяют теги родительского `start_run` и аргумент `versions` в каждом
`run_candidate`.

До исправления (RED):

```
test_candidates_receive_data_hashes ... FAIL
test_parent_run_has_data_hashes ... FAIL
AssertionError: 'data_train_sha256' not found in {'git_sha': ..., 'git_dirty': ..., 'code_sha256': ..., 'config_sha256': ...}
FAILED (failures=2)
```

После исправления (GREEN):

```
test_candidates_receive_data_hashes ... ok
test_parent_run_has_data_hashes ... ok
Ran 10 tests in 7.772s
OK
```

Для справки, текущие хэши данных (`shasum -a 256`):

```text
34b9f134...  data/tokenized/train.pt
a478f26a...  data/tokenized/val.pt
```

Эти значения теперь будут записаны в теги при следующем sweep.

## Дефект 4. Порог по loss проверяется после регистрации модели

### Симптом

`make candidate` (через `evaluation.cli.prepare_candidate`, который вызывает
`promote`) регистрировал версию модели в реестре и вешал на неё alias вне
зависимости от качества. Тег `validation=passed` ставился безусловно, даже если
`final_val_loss` хуже `base_val_loss`, равен `NaN` или run не завершился
(`FAILED`). Флаг `--max-val-ratio` мог ослабить порог из конфига
(например, `5.0` вместо `1.0`) и пропустить заведомо худшую модель.

### Причина

`promote` считал `ratio` напрямую из метрик, нигде не сравнивая его с порогом
(на месте проверки стоял `TODO`), и сразу вызывал `mlflow.register_model`.
Готовая функция `gate`, которая бросает `ValueError` при плохих метриках, статусе
не `FINISHED` или превышении порога, в `promote` не использовалась. В `main`
значение флага просто подменяло значение из конфига в любую сторону.

Это важно из-за различия ролей. Candidate — версия, которая только претендует на
замену: её регистрируют, чтобы затем сравнить с champion на отложенных данных
(часть B). Но candidate обязан хотя бы не быть хуже базовой модели по val loss;
champion же — версия, которую реально используют, и она получается из candidate
только после оценки. Если в реестр попадают кандидаты без проверки порога, в
него попадает и мусор, а `validation=passed` перестаёт что-либо значить.

### Исправление

`promote` первой строкой вызывает `ratio = gate(run, threshold)`: при провале
`ValueError` бросается до `register_model`, поэтому в реестре не остаётся ни
версии, ни тегов. `validation=passed` ставится только после успешного gate.
Добавлена `effective_threshold(config_value, override)`: она возвращает
`min(config_value, override)`, то есть флаг CLI может только ужесточить порог.

```python
def effective_threshold(config_value, override):
    return config_value if override is None else min(config_value, override)


def promote(client, run, name, alias, threshold):
    ratio = gate(run, threshold)
    version = mlflow.register_model(f"runs:/{run.info.run_id}/model", name)
    ...
```

### Доказательство «до/после»

Тесты `TestPromoteGate` подменяют `mlflow` в `src.promote` и проверяют, что
`register_model` не вызывался при отказе. Граница `ratio == порог` принимается
(`final == base`, порог `1.0`).

До исправления (RED):

```
FAIL: test_zero_threshold_rejected - ValueError not raised
FAIL: test_worse_than_base_rejected - ValueError not raised
FAIL: test_missing_nan_or_unfinished_rejected [nan_final], [failed] - ValueError not raised
ERROR: test_missing_nan_or_unfinished_rejected [no_base] - KeyError: 'base_val_loss'
ERROR: test_cli_cannot_loosen_threshold - cannot import name 'effective_threshold'
FAILED (failures=4, errors=2)
```

После исправления (GREEN):

```
Ran 15 tests in 7.3s
OK
```

## Состояние запуска на SmolLM2-135M-Instruct

Sweep, отчёт и регистрация кандидата выполнены на `HuggingFaceTB/SmolLM2-135M-Instruct`
(ревизия `12fd25f77366fa6b3b4b768ec3050bf629380bac`). Что сделано и что нет:

- Попытка 1: `make sweep`: шесть кандидатов, 4 шага, val loss базы 2.5915, лучший `all-r8-high`
  2.58313 (`qv-r8-high` 2.58669, `all-r8-low` 2.58856, `qv-r8-low` 2.58898,
  `qv-r4-high` 2.58924, `qv-r4-low` 2.58963). Все шесть чуть лучше базы; разброс
  невелик, ведь бюджет учебный (32 примера, 8 примеров val). Пик памяти попытки 1 3702–4062 МБ (`metrics/attempt1-v4/sweep.json`).
- Первый запуск `make sweep` упал по MPS OOM в первой оценке базы (лимит аллокатора
  около 2,67 ГиБ при настройках по умолчанию, последовательности до 1536 токенов).
  Повтор с `PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.8` и `PYTORCH_MPS_LOW_WATERMARK_RATIO=0.6`
  прошёл без правок кода. Упавший запуск оставил в MLflow один FAILED-run `qv-r4-low` и
  один FAILED-run `sweep`; в MLflow также остались FAILED-runs от прежних попыток
  на Qwen3-0.6B. Ничего из этого не удалялось.
- Попытка 1: `make report`, `make candidate`: `docs/comparison.csv`, в реестре
  `my-smollm-adapter` версия 4 (run `56bfe730`) с алиасом `candidate`; версии 1-3 от прежних sweep остались без алиаса. Модель
  `my-qwen-adapter` в реестре не существовала.
- Попытка 1: оценка выполнена: `make evaluate` и `make eval` дали accuracy и format_rate 0.0 у baseline и candidate,
  behavioral 0/5, `metrics/attempt1-v4/quality-gate.json`: `passed: false`, причина «Behavioral: прошли 0 из 5».
  `make promote` не выполнялся, алиас `champion` не выставлен.
- Попытка 1: причина нуля: не обрезка и не язык промпта (диагностика: RU/EN system, 32/96 токенов, везде 0.0), а
  рассогласование обучения и оценки: в train нет формата «вариант -> JSON», кандидат выбран по loss на
  StackOverflow. Адаптер загружен корректно, 47 из 55 ответов совпадают с baseline. Golden и пороги не
  менялись; диагностика на том же golden, не независимый аудит. Этот путь (данные в формате оценки
  и переобучение) выполнен как попытка 2, см. следующий пункт. Файлы попытки 1 лежат в
  `metrics/attempt1-v4/`.
- Попытка 2 (ДЗ 7): sweep обучен на данных формата (`hw6.data` → `data/format/tokenized/{train,val}.pt`,
  60 шагов, train[:240], val[:40]), sweep `f4a4ddf9`. Val loss базы 2.0490, лучший `qv-r8-high`
  0.1503 (run `370a4f53`), остальные 0.1730–0.6436. Пик памяти 1476–2588 МБ, запуск с теми же
  переменными MPS прошёл без OOM. В реестре версия 5 получила алиас `candidate`. Оценка: accuracy 0.46
  (база 0.0), format_rate 1.0, behavioral 3/5, gate отказал («Behavioral: прошли 3 из 5»).
  `make promote` не выполнялся.
- `make check-all` после попытки 2: `tests/check.py` 9/9, отпечаток `c42ac7788026`, `check-eval`
  57 тестов OK; `make check-storage` и `make check-negative` прошли. Итоги — в `docs/conclusion.md`.
- `make check-storage` и `make check-hw6-defects` (15 тестов) проходят.
