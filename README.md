## ДЗ 1 — Окружение, первый инференс SmolLM2, бенчмарк машины

### Что нужно сделать

1. Поднять окружение: `uv sync`, закоммитить `uv.lock`.
2. Заполнить `TODO` в коде.
3. **Найти и исправить четыре намеренных дефекта.** Симптомы описаны
   в задании; конкретные строки не указаны — искать нужно самим.
4. Прогнать `make bench`, определить свой профиль железа по таблице курса
   и записать выбранную модель в `params.yaml`.
5. Написать `docs/hardware.md`: результаты замера **и разбор каждого
   найденного дефекта** — в чём был, как проявлялся, как исправлен.
   Простое «починил» не засчитывается.
6. Согласовать с преподавателем тему своего датасета — понадобится в ДЗ 3.

## ДЗ 2 — Анатомия модели: параметры по слоям, хуки, профиль памяти

### Что нужно сделать

1. Прогнать `make inspect`: разбор модели, отчёт в `docs/anatomy.md`,
   график в `docs/activations.png`.
2. Прогнать `make check` — самопроверки ДЗ 1, 2 и 3 должны быть
   зелёными.
3. `docs/anatomy.md`: таблица «модуль → shape → параметров → доля»,
   график норм активаций, расчёт LoRA-параметров для обоих конфигов,
   три числа памяти (инференс / full fine-tune / LoRA) с условиями
   замера и разбором каждого из четырёх найденных дефектов.
4. `docs/anatomy-worksheet.xlsx` — заполненный, контрольные суммы
   сходятся.

## ДЗ 3 — Свой датасет, DVC, валидация схемы, честный сплит

### Что нужно сделать

1. Датасет — вопросы Stack Overflow с принятым ответом по 57 тегам
   MLOps-инструментов (mlflow, dvc, kubeflow, airflow, sagemaker и др.).
   Тег — тема и группа сплита. Стадия `fetch` собирает снимок через
   Stack Exchange API, `collect` превращает его в `data/raw.jsonl`.
2. Пайплайн `fetch → collect → clean → diversity → split` описан в
   `dvc.yaml`, данные версионирует DVC (локальный remote `dvc_remote/`),
   в git данных нет.
3. `make v1` / `make v2` переключают версию датасета. Версии отмечены
   git-тегами `v1` и `v2`, разница между ними —
   `make diff` (то же, что `dvc metrics diff v1 v2`; другие ревизии:
   `make diff A=<rev> B=<rev>`).
4. `make contamination` — отдельная проверка train/test, `make diversity` —
   гейт разнообразия.
5. `docs/datasheet.md` — источник, лицензия, объём, длины, устройство сплита,
   ограничения. `docs/defects.md` — разбор пяти найденных дефектов.

## ДЗ 4 — Препроцессинг: chat template, masking промпта, packing

### Что нужно сделать

1. Вход стадии `tokenize` — свой датасет из ДЗ 3, уже лежит в
   `data/train.jsonl` / `data/val.jsonl` (выход стадии `split`).
2. Прогнать `make tokenize`, затем `make check-tokenize` — самопроверка
   из 9 пунктов, часть из них красная до исправления дефектов.
3. **Найти и исправить четыре намеренных дефекта** в `src/tokenize_data.py`:
   немаскированный лосс, ненайденная граница промпт/ответ, не применённый
   `padding_side`, не посчитанная доля обрезки.
4. `docs/tokenize_defects.md`: разбор каждого дефекта — в чём был, как
   проявлялся, как исправлен, каким числом подтверждается.
5. Стадия `tokenize` объявлена в `dvc.yaml` после `split`.

## Запуск

```bash
uv sync
make generate     # ДЗ1: генерация
make bench        # ДЗ1: замеры
make inspect      # ДЗ2: разбор модели, отчёт в docs/
make repro        # ДЗ3: весь пайплайн данных
make v1           # ДЗ3: переключиться на v1 и пересчитать
make v2           # ДЗ3: переключиться на v2 и пересчитать
make diff         # ДЗ3: dvc metrics diff v1 v2
make contamination  # ДЗ3: проверка train/test
make diversity    # ДЗ3: гейт разнообразия
make sample       # ДЗ4: срез курса из parquet (только для преподавателя)
make tokenize     # ДЗ4: data/*.jsonl -> data/tokenized/ + метрики + отчёт
make check-tokenize  # ДЗ4: самопроверка стадии tokenize
make train        # ДЗ5: LoRA-обучение (all_layers + freeze14) и графики
make train-all    # ДЗ5: один вариант, адаптеры на всех слоях
make compare      # ДЗ5: базовая модель против адаптера на 5 промптах
make check-train  # ДЗ5: самопроверка обучения
make check        # самопроверка всех ДЗ — должна быть зелёной
```

## ДЗ 5 — LoRA-обучение

Интегрировано: стадии DVC `train_all` / `train_freeze` / `compare`
(`src/train.py`, `src/compare.py`, `src/plot.py`, `src/data.py`,
`src/runtime.py`), параметры в `params.yaml` (`train`, `lora_train`,
`compare`), самопроверка `tests/check_train.sh`. Пять дефектов цикла
обучения исправлены отдельными коммитами, разбор — в
[docs/train_defects.md](docs/train_defects.md).

```bash
make train        # all_layers + freeze14 + графики
make compare      # базовая модель против адаптера на 5 MLOps-вопросах
make check-train  # самопроверка ДЗ 5
make check        # все ДЗ
```

Результаты: `docs/curves.png`, `docs/compare.md`, числа — в
[docs/train_defects.md](docs/train_defects.md). Обучение SmolLM2-135M на машине с 8 ГБ: 99 шагов
на вариант (примеры длиннее 768 токенов отбрасываются, остаётся 799 из 1324),
чистое обучение 3347 с у `all_layers` (около 56 минут, 128,5 ток/с) и 1339 с у `freeze14`
(около 22 минут, 321,3 ток/с), плюс 685 и 345 с на замеры val (прогон, зафиксированный в
`dvc.lock`). Более ранний чистый прогон дал 1172 / 1062 с и 367 / 405 ток/с: разброс, вероятно,
от параллельной нагрузки на машине; с Qwen3-0.6B выходило около часа на вариант.
Вариант `freeze14` замораживает первые 14 из 30 слоёв, то есть адаптеры стоят на
16 слоях (53%), а не на «половине». Проверять ДЗ 5 нужно
`make check-train`: полный `make check` запускает `dvc repro` и удаляет артефакты обучения.

## ДЗ 6 — MLflow: трекинг экспериментов и реестр моделей

Стартовый проект `hw67-broken` перенесён в общий репозиторий; код ДЗ 1–5 не менялся.
Маршрут: `make up` (MLflow + Postgres, порт 5006, compose project `mlops-hw6`) →
`make sweep` (шесть кандидатов LoRA) → `make report` (`docs/comparison.csv`) →
`make candidate` → `make check-storage` → `make check-hw6`. Модель в реестре — `my-smollm-adapter`.
Отдельно: `make check-hw6-defects` (юнит-тесты без сервера), `make check-hw6-all`.

На машине с 8 ГБ `make sweep` с настройками MPS по умолчанию падает по OOM в первой
оценке базы (лимит аллокатора около 2,7 ГиБ при последовательностях до 1536 токенов).
Запускать так: `PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.8 PYTORCH_MPS_LOW_WATERMARK_RATIO=0.6 make sweep`.
Пик памяти при этом 3,7–4,1 ГБ в попытке 1 и 1,5–2,6 ГБ в попытке 2.

Результат на SmolLM2-135M-Instruct.

- Попытка 1, обучение на StackOverflow, бюджет 4 шага и 32 примера train. Val loss базы 2.5915
  (8 примеров val), лучший кандидат `all-r8-high` — 2.5831 (run `56bfe730`), версия 4.
  Результаты сохранены в `metrics/attempt1-v4/`.
- Попытка 2 (текущая), обучение на примерах формата оценки (`data/format/`, стадии DVC
  `format_data` и `format_tokenize`), бюджет 60 шагов, train[:240], val[:40]. Val loss базы 2.0490,
  лучший кандидат `qv-r8-high` — 0.1503 (run `370a4f53`), остальные 0.1730–0.6436.

В реестре `my-smollm-adapter` alias `candidate` указывает на версию 5. Версии 1–4 остались без алиаса.
Оценка ответов (часть B ДЗ 7) выполнена: accuracy кандидата 0.46 против 0.0 у базы, format_rate 1.0,
но behavioral 3/5, поэтому gate отказал и `champion` не выставлен. Подробности — в разделе «ДЗ 6+7» ниже и
[docs/conclusion.md](docs/conclusion.md); разбор дефектов — [docs/defects.md](docs/defects.md)
и [docs/hw6_defects.md](docs/hw6_defects.md).

Конфиг ДЗ 6 лежит в секции `hw6:` файла `params.yaml` и собирается функцией
`src.common.load_params()` (общие `model` и `data` + секции из `hw6`). Корневые `train`/`lora` — ДЗ 5/ДЗ 2.

| Было в hw67 | Стало | Почему |
|---|---|---|
| `src/train.py` | `src/sweep_train.py` | `src/train.py` — обучение ДЗ 5, участвует в отпечатке стадии dvc |
| `src/model.py` | `src/adapter_model.py` | `src/model.py` — загрузка модели ДЗ 1; pyfunc-файл должен быть автономным |
| `src/report.py` | `src/sweep_report.py` | `src/report.py` — отчёт анатомии ДЗ 2 |
| `tests/check.sh` | `tests/check_hw6.sh` (+ дословная копия `tests/hw67_check.sh` для отпечатка) | `tests/check.sh` — проверки ДЗ 1 |
| Makefile `check` / `check-all` | `check-hw6` (девять проверок MLflow) / `check-all` (= `check-hw6` + `check-eval`; `check-hw6-all` — синоним) | `check` — проверки ДЗ 1–5 |
| `params.yaml` hw67 | секция `hw6:` | не пересекается с корневыми `train`/`lora` |

## ДЗ 6+7 — маршрут, решение и состав сдачи

**Маршрут команд** (MLflow на `http://127.0.0.1:5006`, compose project `mlops-hw6`):

```text
make up                 # MLflow + Postgres
make sweep              # шесть кандидатов, каждый в своём run (см. запуск с MPS выше)
make report             # docs/comparison.csv
make candidate          # версия в реестре + alias candidate
make baseline-init      # один раз; baseline не перезаписывать
make evaluate           # ответы baseline и candidate на golden и behavioral
make eval               # отчёты, eval-diff.json, quality gate; отказ даёт код возврата != 0
make check-negative     # испорченная копия ответов должна быть отклонена
make check-storage      # артефакт переживает пересоздание контейнера
make check-eval         # 57 юнит-тестов (score, quality_gate, данные формата), без модели
make check-all          # девять проверок MLflow (check-hw6) + check-eval
```

**Решение.** `make promote` не выполнялся, `champion` не назначен: quality gate отказал в обеих
попытках.

Итог: отказ принят, `champion` не назначен, `make promote` не выполняется, третья попытка не планируется.
Основание: accuracy 0.46 не доказывает знание фактов (интервал Уилсона [0.33; 0.60], правило «самый длинный
вариант» дало бы около 0.70), а честный отказ допустим по заданию.

- Попытка 1 (версия 4): «Behavioral: прошли 0 из 5», accuracy и format_rate 0.0 у обеих моделей.
  Причина — рассогласование обучения и оценки.
- Попытка 2 (версия 5, обучение на данных формата): accuracy 0.46, format_rate 1.0, критический срез
  4/12 против 0/12 у базы, но «Behavioral: прошли 3 из 5»: на вопрос по теме про `@flow` в Prefect
  модель ложно отвечает REFUSE.

Подробности в [docs/conclusion.md](docs/conclusion.md).

**Что сдавать** (веса, тензоры, `.venv`, volumes и секреты в Git не входят):

- `docs/comparison.csv`, `docs/defects.md`, один `docs/conclusion.md`;
- `evaluation/params.json`, `evaluation/baseline.json`, данные оценки под DVC (стадия `eval_data`:
  `data/eval/golden.jsonl` — 50, `behavioral.jsonl` — 5, `training-index.jsonl` — 1330);
- `metrics/{baseline,candidate}-{outputs,eval}.json`, `eval-diff.json`, `quality-gate.json`,
  `negative-gate.json`, `storage-check.json` (все в Git: `.gitignore` исключает из `metrics/` только
  `train_*.json` и `compare_*.json` от ДЗ 5). Результаты попытки 1 лежат в `metrics/attempt1-v4/`;
- ссылки на runs в MLflow: sweep `f4a4ddf971cd47419766b96582079c8e` и шесть дочерних runs, probe-run
  хранения `16b2d85fae834766a52533fd0bc40fb4`, run оценки `2d82968b4f044e3bad82b9cd187d8ecd`
  (эксперимент `hw67-evaluation`). Run оценки попытки 1 — `89c1fd76efac4270a3882c3032ced549`.

**Сценарий видео (5–8 минут).** 1) UI MLflow: родитель `sweep` и шесть дочерних runs, лучший по loss;
2) реестр: `my-smollm-adapter` версия 5 и alias `candidate`, `champion` нет; 3) `make eval`: результаты по
срезам, пары ответов и отказ gate (behavioral 3/5); 4) `make check-negative`: отклонение испорченной копии;
5) `make check-storage` и `storage-check.json`; 6) `make check-eval` и `make check-all`; 7) вывод:
попытка 1 (0.0, рассогласование) → попытка 2 (данные формата, 0.46, ложные REFUSE), почему отказ —
честный итог.
