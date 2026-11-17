# Опыт C — план запуска и сырые доказательства

**Эти файлы подготовлены локально; результатов опыта C здесь нет.** Не выполнять
команды изменения состояния и нагрузки, пока опыт B не завершён и его данные
не выгружены. `observe_c.py` выполняет только GET через kubectl и HTTP GET к
Prometheus; не создаёт Pod, не изменяет HPA и не отправляет generate-запросов.

## Настройки и предпосылки

В текущем chart: HPA выключен по умолчанию, autoscaling/v2, min=2/max=3,
CPU target=60%, request api=250m (порог в среднем примерно 150m/Pod), limit=1 CPU.
Scale-up: +1 Pod/30s без стабилизации; scale-down: окно 120s, -1 Pod/60s.
Прогрев 20s, readiness каждые 1s, minReadySeconds=5. Последний параметр
влияет на Deployment Available, а Ready фиксируется отдельно.

Алерты вычисляются каждые 5s, scrape тоже 5s:

* `InferenceHighTTFC`: p95 успешных TTFC >1.5s и >=20 успешных завершений
  за окно 1m; `for: 30s`.
* `InferenceRefusalRate`: доля terminal outcomes
  `rejected|queue_timeout|unready|aborted` >0.1 и >=20 terminal outcomes
  за 1m; `for: 30s`.

Коллектор сохраняет точный активный Prometheus config и `/api/v1/rules`:
сравнивать с chart обязательно. Нужны доступный metrics-server, CPU requests,
синхронизированные часы узлов и управляющего компьютера. RBAC должен разрешать
чтение pods/deployments/hpa/events/endpointslices/configmaps/services и resource
metrics API. Ошибки сохраняются как evidence и не означают успешный сбор.

## Запуск после завершения B

Примеры используют release `hw11`, namespace `hw11`; подставить реальные имена.
Сохранить `helm get values hw11 -n hw11 -a`, `helm get manifest`, параметры kind,
состояние кеша и контрольные суммы образа/расписания в каталог опыта.
Сначала восстановить 2 Ready реплики и одинаковое начальное состояние, без
незавершённых потоков. Изменение HPA выполнять только после выгрузки B:

```sh
helm upgrade hw11 homework11/chart -n hw11 --reuse-values --set hpa.enabled=true --set hpa.minReplicas=2 --set hpa.maxReplicas=3
kubectl -n hw11 get hpa hw11-inference -o yaml
kubectl get --raw /apis/metrics.k8s.io/v1beta1/nodes
kubectl -n hw11 port-forward svc/hw11-prometheus 9090:9090
```

В отдельном терминале запустить наблюдение **до** нагрузки:

```sh
python3 homework11/experiments/observe_c.py --release hw11 --namespace hw11 --duration 1800 --output results/c-balanced-observer
```

Коллектор создаёт только новый локальный каталог. `evidence.jsonl` сохраняет
каждый ответ kubectl (argv, returncode, stdout/stderr), raw Prometheus JSON,
Pod UID/время создания/Ready condition. При Ctrl-C выгружает range queries
за весь наблюдавшийся интервал и события Kubernetes. При SIGKILL финальной
выгрузки не будет; уже записанные строки останутся. Интервал 2s — пауза между
циклами; сами запросы увеличивают период. При медленном API наблюдения могут
пропускать краткие pending; ориентироваться также на 5s range `ALERTS`.

### Фазы нагрузки

Сохранять UTC timestamps начала подачи и окончания **последнего запроса**,
а также `run_start.start_at`, `attempt_start`, `attempt_end` клиента.
Подача и завершение потоков не совпадают. Каждая фаза — отдельный каталог,
уникальное имя Pod/ConfigMap. Базовый пример вычислительной нагрузки:

```sh
python3 homework11/load/runner.py schedule --duration 240 --rate 6 --seed 111 --output /tmp/c-high.jsonl
python3 homework11/load/cluster.py --namespace hw11 --name c-balanced-high-1 --schedule /tmp/c-high.jsonl --url http://hw11-inference-balanced:8092 --output results/c-balanced-high-1
python3 homework11/load/summarize.py results/c-balanced-high-1/requests.jsonl
```

Rate=6 — стартовая калибровка, **не гарантия** масштабирования или firing.
Выполнить baseline, high, затем полностью прекратить подачу (без нового
schedule), дождаться завершения запросов и возвращения 3→2. Сохранить не менее
120s baseline и достаточно длинную high-фазу, чтобы наблюдать новую Ready
реплику и alert `for`. При необходимости повышать rate отдельным сохранённым
прогоном, контролируя lag клиента/CPU/отказы. Для отказов можно повысить rate
дополнительно; это не гарантирует одновременного firing задержки: гистограмма
считает успешные ответы, а при перегрузе их может быть меньше 20/мин.
Продолжать наблюдение после остановки минимум 5–10 минут: 120s стабилизация
не является обещанием точного времени scale-down, CPU окно, HPA sync и
завершение запросов добавляют задержки. Recovery определяется `inactive` в
rules после firing, а не отсутствием данных или недоступностью Prometheus.

Повторить отдельный полный прогон с `--skew` и URL
`http://hw11-inference-sticky:8092`, восстановив 2 реплики и одинаковый кеш.
Не менять конфигурацию между фазами одного прогона. Если горячая реплика
не вызывает scale-up, это валидное наблюдение: HPA усредняет CPU двух Pod;
для перехода 2→3 средняя utilization должна превысить target с учётом
алгоритма tolerance и доступных метрик. ClientIP affinity сохраняется на
10800s, поэтому новый Pod может почти не получить существующих клиентов.
Проверять реальные per-Pod CPU и запросы, а не выводить распределение из
количества реплик.

## Что извлечь в отчёт

1. Наблюдённые `HPA.status.currentReplicas/desiredReplicas/currentMetrics`
   и conditions, Deployment spec/status, Pod UID, creationTimestamp и
   `Ready.lastTransitionTime` новой реплики. Не путать время первого
   наблюдения с временем Kubernetes transition.
2. UTC окончания подачи, последнего attempt_end, первого наблюдения desired=2,
   фактического удаления третьего Pod и восстановления двух Ready. Вычислить
   длительности от обоих моментов прекращения нагрузки; указать дискретность.
3. Для каждого алерта сохранить `pending → firing → inactive`, `activeAt`,
   численные значения выражений и минимального объёма наблюдений. Пустой
   ALERTS после firing — только дополнительное подтверждение при успешном
   API и наличии rule в inactive. Нет перехода — записать «не наблюдался».
4. Raw histogram buckets, counters, CPU, rules, range queries и клиентский
   JSONL сопоставить в одном wall-time окне. Prometheus p95 интерполирует
   histogram, клиентский p95 — nearest-rank; множества успешных завершений и
   окон должны совпадать. TTFC имитатора не называть TTFT реальной модели.

Глобальные PromQL текущего chart агрегируют все `simulated_*` в этом
Prometheus. Не запускать параллельно другие inference-нагрузки/релизы без
проверки scrape targets. Не использовать отсутствие серии как нулевой CPU
или доказательство восстановления. Ошибки сборщика видны в `kind=error`.

Локальные проверки (не обращаются к кластеру):

```sh
python3 -m unittest discover -s homework11/experiments -p 'test_*.py' -v
python3 homework11/experiments/observe_c.py --help
```
