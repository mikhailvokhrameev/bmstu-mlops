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
чистое обучение 1172 с у `all_layers` и 1062 с у `freeze14` (около 18–20 минут),
плюс около 4,6–4,7 минуты на замеры val; с Qwen3-0.6B выходило около часа на вариант.
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
Пик памяти при этом 3,7–3,8 ГБ.

Результат на SmolLM2-135M-Instruct: val loss базы 2.5915 (8 примеров val), лучший
кандидат `all-r8-high` — 2.5831 (run `c5a75077`), остальные 2.5867–2.5896; бюджет — 4 шага,
32 примера train, поэтому разница небольшая. В реестре `my-smollm-adapter` версия 1 с алиасом
`candidate`; алиас `champion` не выставлен: эталонного набора `data/eval/golden.jsonl`
в репозитории нет, `make evaluate` и `make promote` не выполнены (подробности —
[docs/hw6_defects.md](docs/hw6_defects.md)).

Конфиг ДЗ 6 лежит в секции `hw6:` файла `params.yaml` и собирается функцией
`src.common.load_params()` (общие `model` и `data` + секции из `hw6`). Корневые `train`/`lora` — ДЗ 5/ДЗ 2.

| Было в hw67 | Стало | Почему |
|---|---|---|
| `src/train.py` | `src/sweep_train.py` | `src/train.py` — обучение ДЗ 5, участвует в отпечатке стадии dvc |
| `src/model.py` | `src/adapter_model.py` | `src/model.py` — загрузка модели ДЗ 1; pyfunc-файл должен быть автономным |
| `src/report.py` | `src/sweep_report.py` | `src/report.py` — отчёт анатомии ДЗ 2 |
| `tests/check.sh` | `tests/check_hw6.sh` (+ дословная копия `tests/hw67_check.sh` для отпечатка) | `tests/check.sh` — проверки ДЗ 1 |
| Makefile `check` / `check-all` | `check-hw6` / `check-hw6-all` | `check` — проверки ДЗ 1–5 |
| `params.yaml` hw67 | секция `hw6:` | не пересекается с корневыми `train`/`lora` |
