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
