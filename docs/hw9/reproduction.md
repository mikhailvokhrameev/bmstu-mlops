# ДЗ9: воспроизведение допуска

Python 3.12, локальный `.venv`; проверки не загружают веса. Запуск: `bash tests/hw9_check.sh`. Синтетические fixtures проверяют программные контракты, но не качество модели. `check-success.log` содержит успешный общий запуск; `check-negative.log` — отказ настоящего quality gate на сохранённых реальных ответах, exit_code=1 (behavioral 3/5).

| Изменение | Команды | Повторная оценка |
|---|---|---|
| Только unit tests/документация | `bash tests/hw9_check.sh` | Нет, если не меняется протокол |
| Код оценки/промпт/decoding/runtime | `make evaluate`, `make eval` | Да |
| Golden/behavioral/training index | Проверить leakage; `make evaluate`, `make eval` | Да |
| Веса/токенизатор/manifest адаптера или base revision | `make candidate`, `make evaluate`, `make eval` | Да |
| Публикация без изменений | `python -m evaluation.release_evidence --runtime runtime.json --publish accepted.json` | Существующая полная связанная оценка; gate пересчитывается |

Перед реальной оценкой runtime JSON получают из `evaluation.release_evidence.evaluation_runtime(training_manifest, evaluation_config)`. Оценка сохраняет тот же runtime и полный inventory адаптера в `candidate-eval.json:release_binding`. Старые результаты без этой привязки не допускаются: требуется повторная оценка, не переобучение. Результат `evaluation_runtime` отражает HW6_DEVICE/HW6_DTYPE, поэтому их изменение требует повторной оценки.

Реальный flow: `prefect_flow.evaluated_release_flow(runtime_path, target)`. Все задачи validation/publication имеют `cache_policy=None`. `release.publish_verified` перепроверяет файлы непосредственно перед атомарной заменой. `release.load_verified` повторяет проверку перед загрузкой. Указатель включает manifest/report. Старый `publish` и `synthetic-release` сохраняются только для учебного лабораторного сценария и приложенных тестов; production использует verified API.

Абсолютные локальные пути в evidence/inventory должны оставаться доступны процессу публикации/загрузки. Это локальный проверяемый выпуск, не переносимый удалённый bundle. Изменение абсолютного корня требует новой связанной оценки. Не предназначено для конкурентной злонамеренной записи файлов между проверкой и чтением модели; артефакт должен быть read-only для других writers.
