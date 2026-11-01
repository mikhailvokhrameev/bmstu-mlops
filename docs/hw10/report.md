# ДЗ10 — результат реализации, 2026-11-01

## Статус и границы

Реализован FastAPI API, общий backend-контракт, строгая реальная загрузка, валидация, прогрев, лимит вычислений, версии и длительности. Выполнены ASGI-тесты и настоящий локальный HTTP сценарий A → B → A с перезапусками. Измерения ниже **только STUB ONLY**, не доказательство качества или скорости реальной модели. Реальный smoke принятой модели и реальные измерения **не выполнены**, поскольку принятого строго проверяемого выпуска нет. Видео не записывалось: ниже сценарий для записи пользователем. Коммит не создавался в рамках этой реализации.

Изучены разделы C/D и связанные требования `mlops26-hw8-9-10/homework8-9-10.html`. Исходный `service.py` оставлен совместимым с регрессионными тестами учебного прототипа. Итоговый FastAPI запускается из `api_service.py`.

## Что реализовано

- `GET /health` подтверждает жизнь процесса. `GET /ready` сообщает фактически загруженную пару `model_id`/`runtime_sha256` только после загрузки и реального вызова прогрева. Lifespan startup блокирующий: до завершения startup HTTP не обслуживается, что разрешено учебной спецификацией. Ошибка загрузки останавливает startup; готовность не выдаётся.
- `POST /generate`: непустой prompt до 4096 символов; строгий integer лимит (boolean не допускается), неизвестные поля запрещены. Stub разрешает 1–32 токена. Real разрешает только точный `max_new_tokens` из оценённого runtime: меньший лимит тоже не наследует допуск автоматически. По умолчанию используется именно этот лимит. Проверки API выполняются до `backend.generate`.
- Общий `Backend` protocol, `StubBackend`, `TransformersBackend`. Real: только `release.load_verified`, инвентаризация/хеши/исходная оценка/quality gate проверяются до загрузки; локальная exact-revision база, tokenizer и PEFT для adapter; prompt template/system/enable_thinking/seed/dtype/device/generation взяты из runtime. Неподдерживаемые поля runtime закрывают startup. Сеть при загрузке не нужна (`local_files_only=True`). Реальный backend поддерживает строго проверенные adapter и base releases. Base загружается из проинвентаризированного локального snapshot без PEFT; adapter — как pinned база + PEFT.
- Ответ: `text`, `request_id`, фактически загруженные `model_id`, `runtime_sha256`, `output_tokens`, эффективный `max_new_tokens`, `queue_seconds`, `generation_seconds`, `total_seconds`. Указатель читается один раз при startup. Обновление файла не меняет объект в памяти.
- Semaphore ограничивает число вычисляющих worker по умолчанию одним; event loop свободен для health/ready. При отмене клиента слот удерживается до фактического завершения вычисления. CLI `--max-parallel` позволяет явно выбрать предел, но замер выполнен с одним слотом.
- Ограничение контекста дополнительно проверяется после токенизации перед model.generate. Это входит во время backend; char limit HTTP проверяется раньше. KV-cache не включается между запросами.
- Новые зависимости FastAPI/Uvicorn/HTTPX закреплены в отдельном `requirements-hw8910.txt`; существующий Prefect pin сохранён. Transformers/PEFT уже определены основным pyproject/lock.

## Команды и проверки

Среда: macOS arm64, Python 3.12.4. `python3` имеет FastAPI 0.117.1, Uvicorn 0.36.0. `.venv` содержит Transformers и PEFT; Prefect отсутствует в обеих проверенных средах. Подробности: `metrics/hw10/real-availability.json`.

```sh
python3 -m unittest tests_hw8910.test_api
# 4 tests, OK
python3 -m unittest tests_hw8910.test_api tests_hw8910.test_incidents tests_hw8910.test_release tests_hw8910.test_real_release -v
# 39 tests, OK; сохранено в metrics/hw10/tests.log
PYTHONPATH=. python3 hw10_experiment.py
# 3 реальных запуска uvicorn + A/B/A; 8+8 HTTP запросов; exit 0
python3 -m unittest discover -s tests_hw8910 -v
# 40 tests: первоначальный запуск в sandbox — 1 failure (нет Prefect),
# 3 errors (bind loopback Operation not permitted).
# metrics/hw10/tests-environment-failure.log
```

Loopback первоначально был запрещён sandbox. Для настоящих HTTP-проверок получено автоматическое разрешение эскалации, после чего 39 выбранных тестов и эксперимент завершились успешно. Полный Prefect-тест не объявляется прошедшим: недостающая зависимость зафиксирована. Новые 4 теста проверяют A/B/A и неизменность loaded версии, invalid без вычисления, отказ legacy metadata в real, 503/health и фактический максимум одного compute при четырёх одновременных клиентах. Тесты работают без весов. Изначальные ошибки импорта при разработке тестов не считаются доказательством найденного дефекта исходного прототипа.

Воспроизведение после установки зависимостей:

```sh
.venv/bin/python -m pip install -r requirements-hw8910.txt
.venv/bin/python -m unittest discover -s tests_hw8910 -v
PYTHONPATH=. .venv/bin/python hw10_experiment.py
# Для существующего проверенного реального выпуска:
.venv/bin/python api_service.py --backend transformers --pointer accepted.json --port 8092
# После успешного startup/ready:
.venv/bin/python benchmark_api.py --url http://127.0.0.1:8092 --output metrics/hw10-real
```

`hw10_experiment.py` создаёт отдельный временный synthetic workspace, не меняет рабочий accepted и запускает три отдельных процесса. Проверяет ответы до/после изменения указателя и после перезапуска; `metrics/hw10/stub-version-scenario.json` содержит ready и ответы A/B/A. Все fixture model IDs/тексты явно помечены. Прогрев происходит внутри startup; каждый сервер готов до опытов.

## Измерение

Одинаковые 8 prompt, одинаковый лимит 32, последовательно concurrency 1 и 2, сервер с одним compute слотом, по одному выходному stub токену. Каждая попытка сохранена в `attempts.jsonl`: UUID попытки, режим, индекс, prompt, status/error, клиентская задержка, request_id, output_tokens, generation/queue/total, loaded модель и runtime. Ошибки и таймауты сохраняются, не фильтруются. Результаты и полная таблица в `summary.json`.

| Клиенты | n | Успехи | Ошибки | Пакет, с | Успешные req/s | Средняя успешная задержка, с | Макс., с |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 8 | 8 | 0 | 0.103400 | 77.370 | 0.012848 | 0.013211 |
| 2 | 8 | 8 | 0 | 0.097586 | 81.979 | 0.022735 | 0.025549 |

Небольшое отличие throughput обусловлено расходами HTTP и планированием; это не ускорение модели. При двух клиентах второй запрос ожидает semaphore, поэтому клиентская задержка выше. `queue_seconds` — серверное ожидание слота после валидации; `generation_seconds` — весь worker backend (включает токенизацию, model.generate, decode и передачу в thread), а не отдельно GPU kernel. `client_seconds` также включает HTTP/сериализацию и планирование. Измерение не разделяет prefill/decode: при едином JSON нет наблюдаемого TTFT и ITL. На восьми примерах устойчивый p95 не заявляется.

Prefill обрабатывает весь вход и строит KV-cache; decode последовательно получает новые токены, переиспользуя ключи/значения attention. KV-cache растёт с длиной входа/выхода, числом слоёв и параллельных последовательностей; это ограничивает concurrency по памяти. Последовательный Transformers/PEFT worker не реализует continuous batching SGLang/vLLM, поэтому эти числа не доказывают производительность таких движков. Stub sleep не моделирует prefill, decode или KV-cache.

## Проверка реальных артефактов и честная блокировка

Проверены существующие `models/adapter_all_layers`, `models/adapter_freeze14`, `evaluation/baseline.json`, baseline/candidate eval, quality-gate, local HF snapshot и наличие accepted. Сохранены вычисленные факты в `real-availability.json`:

- Gate кандидата `passed: false`, причина: behavioral прошли 3 из 5. Он не публиковался и не выдавался за принятую модель.
- У обоих старых eval отсутствует `release_binding`. Дописать binding задним числом не докажет условия исходной оценки.
- У обоих локальных adapters нет `manifest.json`; вызов `release.artifact_identity` отказывает `Incomplete adapter/tokenizer inventory`.
- `accepted.json` отсутствует. Реальный вызов `release.load_verified(Path('accepted.json'))` даёт `FileNotFoundError`. Smoke API не запускал генерацию без проверенного допуска.
- Baseline указан как base `HuggingFaceTB/SmolLM2-135M-Instruct`, точная ревизия `12fd25f77366fa6b3b4b768ec3050bf629380bac`; локальный HF snapshot содержит model.safetensors/config/tokenizer/generation config. Наличие весов подтверждено, принятие и runtime binding — нет.
- Строгий контракт расширен для base snapshot без требования adapter/training manifest. Проверка исходной оценки, raw outputs, runtime binding и прежнего quality gate сохранена. Один `baseline.json` не доказывает принятие. Поэтому **обслуживание принятой baseline не выполнено**, а не подменено synthetic release.

Безопасное восстановление: получить исходный training manifest и проверяемую provenance адаптера из прежнего run/хранилища, не создавать фиктивный; проверить runtime; повторить оценку без переобучения, если исходная не связывала веса/runtime. Для existing adapter после восстановления:

```sh
.venv/bin/python -m evaluation.cli evaluate
.venv/bin/python -m evaluation.cli gate
# Gate остаётся обязательным. При отказе не публиковать candidate.
.venv/bin/python -m evaluation.release_evidence --runtime PATH_TO_EVALUATED_RUNTIME.json --publish accepted.json
.venv/bin/python api_service.py --pointer accepted.json --backend transformers
```

Runtime JSON должен происходить от реально выполненной оценки, а не угадываться ради публикации. Для base baseline теперь реализован strict snapshot контракт и backend без PEFT. Всё ещё требуется существующий проверяемый принятый envelope либо полноценная новая связанная оценка, прошедшая неизменённый gate; старый baseline-eval без release_binding сам по себе недостаточен. Ни новые пороги, ни новое обучение не выполнялись. После восстановления обязательны real API smoke и такой же 8+8 benchmark, отдельные от stub данных.

## Видео 5–8 минут (для записи пользователем)

0:00–0:45 — требования, семь дефектов и исправленный pipeline по сохранённым материалам ДЗ8–9.
0:45–2:00 — Prefect сбой/восстановление, cache/invalidation, gate отказ и неизменность accepted.
2:00–3:00 — зелёные быстрые проверки, отрицательный отчёт и ограничения реального выпуска.
3:00–4:30 — FastAPI health/ready/generate, invalid запрос до backend; A → pointer B → всё ещё A → restart B → rollback A по сохранённому JSON.
4:30–5:45 — таблица 8+8 и per-attempt журнал, queue vs compute, prefill/decode и отсутствие TTFT/ITL.
5:45–6:30 — реальная модель: после восстановления выпуска показать ready и реальный ответ; до восстановления прямо показать blocked availability, не выдавая stub за real.
6:30–7:00 — артефакты/команды воспроизведения, ограничения и план отката. Реальный smoke обязателен для полной сдачи.

## Изменённые/добавленные файлы

`api_service.py`, `benchmark_api.py`, `hw10_experiment.py`, `tests_hw8910/test_api.py`, `requirements-hw8910.txt`; `metrics/hw10/attempts.jsonl`, `summary.json`, `stub-version-scenario.json`, `real-availability.json`, `tests.log`, `tests-environment-failure.log`, `experiment.log`; `docs/hw10/report.md`.

Чужие изменения `.gitignore` и `metrics/storage-check.json` не изменялись и не добавлялись в индекс. Веса, секреты, кеши и окружения не добавлялись. `git commit` не выполнялся. Частичный статус, ограничения real и неуспешный полный прогон зафиксированы в этом отчёте.


## Исправления после ревью: строгая base baseline

`release.base_artifact_identity(root, model)` инвентаризирует все файлы materialized snapshot: config, tokenizer config, словарь, монолитные веса либо полный shard index с наличием каждого shard. Требуются непустое имя и точный 40-символьный commit. Symlink запрещены; HF snapshot перед инвентаризацией копируется с разыменованием в отдельный временный каталог. Все байты веса, tokenizer и generation config входят в SHA-256 identity; дополнительные/изменённые файлы меняют identity.

`verify_artifacts` различает прежний adapter identity и `kind: base`. Для base проверяется совпадение name/revision/model_spec/runtime, URI точного локального каталога, generation runtime с pinned протоколом. Дальше применяется **тот же** путь из четырёх eval/output evidence: пересчёт golden/behavioral результатов, проверка binding, protocol/golden hash и неизменённый `evaluation.solution.quality_gate`. Ветка base не пропускает допуск и не объявляет оценку baseline автоматически принятой. Условия `passed is True`, complete status и атомарная публикация прежние. `load_verified` повторяет проверки на загрузке. В `TransformersBackend` base загружается непосредственно из проверенного root; PEFT импортируется и применяется только для adapter.

Добавлены 9 адресных synthetic тестов в `tests_hw8910/test_base_release.py`: публикация/загрузка base, изменённые веса при load, отсутствие binding, неверные revision/kind, runtime/protocol mismatch, сохранение ранее принятого base при rejected следующем выпуске, реальный behavioral gate refusal несмотря на report passed=True, неполный tokenizer/unpinned revision, symlink, а также загрузчик base без доступного PEFT. Некоторые проверки объединены в одном тесте. Эти fixture не считаются реальной оценкой.

```sh
python3 -m unittest tests_hw8910.test_base_release tests_hw8910.test_real_release tests_hw8910.test_api -v
# 23 tests, OK — metrics/hw10/base-tests.log
python3 -m unittest tests_hw8910.test_api tests_hw8910.test_base_release tests_hw8910.test_incidents tests_hw8910.test_release tests_hw8910.test_real_release -v
# 48 tests, OK — metrics/hw10/tests.log; localhost разрешён средой
.venv/bin/python check_baseline_availability.py --diagnostic-load
# metrics/hw10/base-availability.json и base-availability.log
```

До исправления roundtrip base отказывал как `Incomplete adapter/tokenizer inventory`; после исправления тот же roundtrip и проверки изменений проходят. Первоначальный журнал сохранён в `metrics/hw10/base-tests-before-fix.log`; он также содержит ошибки ещё отсутствовавшего нового helper и импорта PEFT, которые не считаются самостоятельным доказательством behavioral gate дефекта.

Реальный локальный snapshot успешно материализован во временном каталоге, все 8 файлов хешированы, реальная база загружена на CPU float32 и выполнила диагностическую генерацию 32 токенов. Последний запуск: загрузка 1.189 с, полный вызов генерации 1.072 с; текст и SHA-256 файлов сохранены в `base-availability.json`. Это прямая проверка доступности весов и вычислительных ресурсов, **не smoke принятого API**, не новая оценка и не benchmark принятой модели. В результате явно `accepted_release: false`. Временная копия весов удалена после проверки; веса не добавлены в Git.

Блокер принятия подтверждён точнее: у существующего baseline-eval overall accuracy/format_rate 0.0/0.0, behavioral **0 из 5**, binding отсутствует. Диагностическая проверка этого сохранённого baseline отчёта прежним gate (без новой генерации и без публикации) отказывает `Behavioral: прошли 0 из 5`. Даже наличие реальных весов не даёт основания принять эту версию. У candidate behavioral 3 из 5 и прежний отказ. `accepted.json` по-прежнему отсутствует; ни baseline, ни candidate не опубликованы фиктивно. Полные real API smoke и замер 8+8 остаются заблокированы отсутствием проверяемого принятия; качество ради них не ослаблялось.

Если преподаватель/владелец артефактов предоставит прежний строгий base accepted envelope с доступными исходными evidence и неизменёнными snapshot файлами, восстановление выполняется через тот же проверяющий интерфейс:

```sh
.venv/bin/python - <<'PYCODE'
from pathlib import Path
from release import load_verified, publish_verified
manifest, report = load_verified('/trusted/path/base-accepted.json')
publish_verified(manifest, report, Path('accepted.json'))
PYCODE
.venv/bin/python api_service.py --backend transformers --pointer accepted.json --port 8092
curl --fail http://127.0.0.1:8092/ready
curl --fail -H 'Content-Type: application/json' -d '{"prompt":"Что такое MLflow?"}' http://127.0.0.1:8092/generate
.venv/bin/python benchmark_api.py --url http://127.0.0.1:8092 --output metrics/hw10-real
```

Путь `/trusted/path/...` — предоставляемое внешнее доказательство, которого в текущем репозитории нет. Snapshot и evidence принятого выпуска должны храниться неизменно в отдельном каталоге: новый candidate eval не должен перезаписывать файлы, на которые ссылается принятый baseline; иначе load_verified честно откажет из-за изменившихся хешей. Подмена старого eval добавленным вручную binding запрещена. Если такой provenance нет, требуется новая полноценная связанная оценка и её честный допуск; исходный base baseline с 0/5 behavioral не проходит имеющееся правило. Для видео показать реальную диагностическую загрузку отдельно от отсутствующего принятого API и сохранить это ограничение в итоговой сдаче.

Дополнительные файлы после ревью: `release.py`, `tests_hw8910/test_base_release.py`, `check_baseline_availability.py`, `metrics/hw10/base-availability.json`, `base-availability.log`, `base-tests.log`, `base-tests-before-fix.log`. Чужие `.gitignore` и `metrics/storage-check.json` не изменялись; коммит не создавался.


## Второе исправление после ревью: end-to-end base evaluation и evidence

Ранее base verifier/loader существовали, но штатный producer evidence был adapter-only. Теперь `python -m evaluation.cli evaluate-base` — самостоятельный штатный путь без MLflow candidate lookup и adapter manifest. Обязательные параметры: `--snapshot` (materialized локальная база), `--runtime`, `--output-dir` (новый, ещё не существующий каталог). Команда оценивает **закреплённый base из evaluation/baseline.json** как новую base-кандидатуру; другой baseline reference/adapter под этим именем не подставляется. Новое принятие не выводится из самого наличия baseline.json.

До генерации фиксируются полная artifact identity, точный runtime и snapshot протокола/golden/training-index/кода. Runtime обязан совпадать с именем/revision закреплённой базы и параметрами протокола, `do_sample=False`. Для baseline и candidate независимо создаётся backend и выполняется полный проход генерации по 50 golden + 5 behavioral; ответы не копируются между ролями и не берутся из старых отчётов. Оба отчёта содержат release_binding одного фактически загруженного snapshot/runtime. После каждого прохода все файлы snapshot хешируются повторно; изменение закрывает оценку. Ошибки генерации сохраняются по каждому примеру и не превращаются в успех. Новый каталог evidence не перезаписывает старый, в том числе при ошибках или отказе gate.

`python -m evaluation.release_evidence --base-evaluation DIR --runtime ... --output ... [--publish ...]` собирает base manifest/report из этих исходных файлов. Повторно проверяются оба binding, точный runtime, snapshot bytes, model_spec/protocol, raw outputs и прежний quality gate; `publish_verified/load_verified` остаются единственными реальными путями публикации/загрузки. Проверка base reference в verifier теперь также требует совпадения kind/name/revision с snapshot. Прежние adapter-команды не изменены. Никакая проверка не была удалена ради base.

### Реальная новая связанная оценка выполнена

Использованы реальные локальные веса SmolLM2-135M-Instruct pinned revision, материализованные из HF cache в `/private/tmp/bmstu-hw10-base-humye_2t/snapshot`; этот каталог находится вне Git. Runtime CPU/float32, seed42, max_new_tokens32, thinkingFalse, do_sampleFalse взят из `metrics/hw10/base-runtime.json` и совпадает с протоколом. Команды, которые реально выполнены:

```sh
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 .venv/bin/python -m evaluation.cli evaluate-base --snapshot /private/tmp/bmstu-hw10-base-humye_2t/snapshot --runtime metrics/hw10/base-runtime.json --output-dir metrics/hw10/base-evaluation-real
# exit 1: Behavioral: прошли 0 из 5; все ответы и полный отказ сохранены
.venv/bin/python -m evaluation.release_evidence --runtime metrics/hw10/base-evaluation-real/evaluated-runtime.json --base-evaluation metrics/hw10/base-evaluation-real --output metrics/hw10/base-evaluation-real/release-report.json
# exit 1: тот же неизменённый quality gate; публикация не запрашивалась
```

Run ID: `base-c1ab7438bde14743a18f0e40482f7631`. В обеих ролях accuracy0.0, format_rate0.0, behavioral0/5; по 55 сохранённых попыток, **ошибок исполнения генерации 0**. Время всех generation-вызовов по журналу: baseline43.9997с, candidate41.3923с. Это длительности оценки, не API throughput/TTFT/p95 и не benchmark принятой модели. Обе роли имеют реальный snapshot/runtime binding, которого не было у старых eval. Сводка: `metrics/hw10/base-e2e-real-summary.json`; исходные отчёты, 110 сырых ответов, binding, protocol, runtime и gate: `metrics/hw10/base-evaluation-real/`. Логи команд: `base-evaluation-real.log`, `base-release-attempt.log`.

Таким образом, возможность штатно создать **честное связанное base evidence** доказана реальной генерацией; результатом оказался отказ качества. `accepted.json` отсутствует и не создан. **Real smoke принятого API и его 8+8 benchmark остаются открытыми**: принятого артефакта нет. Новый base gate не принимается автоматически даже при сравнении одной pinned базы с ней самой: behavioural требование 5/5 остаётся обязательным и здесь отказывает 0/5.

Для воспроизведения после очистки временного snapshot материализовать реальные файлы без symlink в новый каталог, затем использовать новый output-dir:

```sh
.venv/bin/python - <<'PYCODE'
import json, shutil
from pathlib import Path
base=json.loads(Path('evaluation/baseline.json').read_text())
cache=Path.home()/'.cache/huggingface/hub'/('models--'+base['name'].replace('/','--'))/'snapshots'/base['revision']
# Выберите новый каталог; copytree не перезаписывает существующий.
shutil.copytree(cache,Path('/private/tmp/hw10-base-reproduction'),symlinks=False)
PYCODE
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 .venv/bin/python -m evaluation.cli evaluate-base --snapshot /private/tmp/hw10-base-reproduction --runtime metrics/hw10/base-runtime.json --output-dir metrics/hw10/base-evaluation-reproduction
.venv/bin/python -m evaluation.release_evidence --runtime metrics/hw10/base-evaluation-reproduction/evaluated-runtime.json --base-evaluation metrics/hw10/base-evaluation-reproduction --output metrics/hw10/base-evaluation-reproduction/release-report.json --publish accepted.json
# Последняя команда создаст accepted ТОЛЬКО при реальном PASS; текущая модель не проходит.
```

На опубликованном через эту команду реальном PASS выполняются команды API smoke и benchmark из предыдущего раздела. Веса не включать в Git. Snapshot и evaluation evidence принятого выпуска должны сохраняться неизменными и быть доступны при каждой загрузке; `/private/tmp` используется здесь для отклонённого опыта, не как долговременное хранилище принятого выпуска.

### Проверки после end-to-end исправления

Добавлены 8 адресных тестов `tests_hw8910/test_base_evaluation.py`: обе роли bound; полный evaluate→evidence→publish→load roundtrip; честный behavioural gate reject с сохранением попыток; изменение snapshot во время генерации; смена runtime; tampering raw outputs; запрет перезаписи evidence; CLI dispatch без adapter lookup; CLI публикация строго проверенного base fixture. Некоторые проверки объединены в одном методе. Только генерация в этих unit fixtures синтетическая; реальная оценка описана отдельно выше.

```sh
python3 -m unittest tests_hw8910.test_base_evaluation tests_hw8910.test_base_release tests_hw8910.test_real_release tests_hw8910.test_api -v
# 31 tests OK — metrics/hw10/base-e2e-tests.log
python3 -m unittest tests_hw8910.test_api tests_hw8910.test_base_release tests_hw8910.test_base_evaluation tests_hw8910.test_incidents tests_hw8910.test_release tests_hw8910.test_real_release -v
# 56 tests OK — финальный metrics/hw10/tests.log (заменяет исторические 39/48-test прогоны)
python3 -m compileall -q evaluation api_service.py release.py tests_hw8910/test_base_evaluation.py
git diff --check
# оба exit0
```

Проверки Prefect, как и раньше, относятся к отдельному полному прогону и ограничены отсутствием Prefect в проверенной среде. Чужие `.gitignore` и `metrics/storage-check.json` не изменялись, коммит не создавался. Дополнительные исходники: `evaluation/base_evaluation.py`, изменения `evaluation/cli.py`, `evaluation/release_evidence.py`, `release.py`, `tests_hw8910/test_base_evaluation.py`. Для видео вместо обещания будущей связанной оценки теперь показать сохранённые реальные ответы обоих проходов, bindings и отказ0/5, затем отдельно объяснить, почему accepted API не запускается.


## Финальное исправление после ревью: устройство входит в строгую runtime identity

Закрыта возможность публиковать `device:auto` и выбирать CPU/MPS/CUDA заново на startup с прежним runtime hash. Для **любого реального** base/adapter release `verify_artifacts` теперь требует canonical concrete `device`: `cpu`, `mps` либо `cuda:N` с явным индексом. `auto`, bare `cuda`, missing device и неканонические aliases запрещены в публикуемом runtime. Legacy STUB путь сохранён и не считается реальным выпуском.

Для adapter evaluation `evaluation_runtime` разрешает пользовательский auto **до оценки** через resolve_device; если выбран CUDA, фиксирует `torch.cuda.current_device()` как `cuda:N`. Затем это конкретное значение передаётся в оба вызова evaluator `generate`, а не разрешается повторно из env или train config. После `model.to(...)` проверяются фактические устройства всех parameters и buffers. Binding и runtime SHA-256 относятся к этому конкретному устройству. Base evaluator использует общий backend и уже требует конкретный device до генерации.

В `TransformersBackend` проверка runtime происходит до загрузки. Выбранное resolve_device обязано точно соответствовать pinned значению; после `.to(...).eval()` проверяется каждый parameter/buffer. Любое расхождение, meta/unverifiable tensor или отсутствие проверяемых tensors останавливает startup до прогрева и ready. Недоступность закреплённого устройства приводит к ошибке, автоматического CPU/MPS/CUDA fallback нет. При успешной загрузке `loaded_device` подтверждает проверенное устройство.

Добавлены 6 регрессионных тестов `tests_hw8910/test_runtime_device.py`: полностью согласованные hashes/evidence с auto не публикуются ни для base, ни для adapter; cuda требует индекс; фактические tensors на MPS при runtime CPU отклоняются; startup с таким несовпадением закрывается; adapter auto преобразуется в concrete до binding. До исправления согласованный base `device:auto` успешно публиковался (зафиксированный FAIL в `device-tests-before-fix.log`); после исправления тест отказывает. Ошибки ещё отсутствовавших helper в первоначальном журнале отдельно не считаются behavioral доказательством. Существующая synthetic strict adapter fixture теперь явно указывает CPU вместо отсутствующего device; отрицательные тесты не ослаблены.

```sh
python3 -m unittest tests_hw8910.test_runtime_device tests_hw8910.test_base_evaluation tests_hw8910.test_base_release tests_hw8910.test_real_release tests_hw8910.test_api -v
# 37 tests OK — metrics/hw10/device-tests.log
python3 -m unittest tests_hw8910.test_api tests_hw8910.test_base_release tests_hw8910.test_base_evaluation tests_hw8910.test_runtime_device tests_hw8910.test_incidents tests_hw8910.test_release tests_hw8910.test_real_release -v
# 62 tests OK — финальный metrics/hw10/tests.log, заменяет прежний 56-test этап
python3 -m compileall -q evaluation api_service.py release.py tests_hw8910/test_runtime_device.py
git diff --check
# exit0
```

Старая реальная отказная base evidence **не изменялась**: она уже фиксировала CPU. Проверена совместимость новым assembler с прежними runtime/protocol/raw files:

```sh
.venv/bin/python -m evaluation.release_evidence --runtime metrics/hw10/base-evaluation-real/evaluated-runtime.json --base-evaluation metrics/hw10/base-evaluation-real --output /tmp/hw10-post-device-release-report.json
# exit1, прежняя причина Behavioral: прошли 0 из 5, а не ошибка устройства
```

Журнал: `metrics/hw10/device-refusal-compatibility.log`. Дополнительно реально загружен тот же локальный base snapshot новым backend; после `.to(cpu)` все parameters и buffers имеют `cpu`, `loaded_device=cpu`. Результат: `metrics/hw10/device-real-diagnostic.json`, явно `accepted_release:false`. Это проверка actual-device на реальных tensors, **не** smoke принятого API. Воспроизводится отдельно от serving, как диагностическая загрузка ещё не принятой модели:

```sh
.venv/bin/python - <<'PYCODE'
import json
from pathlib import Path
import release
from api_service import TransformersBackend
source=json.loads(Path('metrics/hw10/base-evaluation-real/candidate-eval.json').read_text())
binding=source['release_binding']
assert release.base_artifact_identity(binding['artifact']['root'],binding['artifact']['base_model']) == binding['artifact']
backend=TransformersBackend(dict(artifact=binding['artifact'],runtime=binding['runtime'],model_id=source['model']['run_id'],runtime_sha256=release.sha256(release.canonical(binding['runtime']))))
assert backend.loaded_device == 'cpu'
print(sorted({str(p.device) for p in backend.model.parameters()}))
print(sorted({str(p.device) for p in backend.model.buffers()}))
PYCODE
```

Проверка требует сохранённого materialized snapshot из реального отказного опыта; после его удаления требуется воспроизведение новой оценки, а не переписывание архивного binding. Фактический CUDA/MPS запуск не заявляется: на них протестированы строгие правила/отказы на fixtures, реальный hardware check здесь CPU. Старые отказные evidence с auto можно читать как исторические JSON, но конвертировать их в новый accepted выпуск без новой concrete-device оценки нельзя. Gate не менялся. Принятый real API smoke/8+8 benchmark остаются открытыми по прежнему отказу качества и отсутствию accepted; фиктивный release и коммит не создавались.


Дополнительно после device fix проверена совместимость прежней pipeline fixture без adapter artifact: `python3 -m unittest discover -s tests -p test_pipeline.py -v` — **5 tests OK**, `metrics/hw10/device-pipeline-compatibility.log`. Для этой неподписываемой fixture сохранён четырёхаргументный вызов generate; реальная bound adapter-оценка всегда получает concrete runtime явным аргументом. Существующие pipeline tests не изменялись. Повторные compileall и diff-check успешны.
