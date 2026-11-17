# Генератор и собственные проверки

Инструменты используют только Python stdlib; клиентский образ закреплён на
`python:3.12.8-slim-bookworm`. Создаются четыре обычных Pod без hostNetwork,
один общий ConfigMap, результаты сохраняются в stdout контейнеров и локально.
Проверьте доступность образа заранее, иначе общий момент старта может быть
пропущен. Просроченный запуск не скрывается: `lag_seconds` показывает задержку.

Из корня репозитория:

```sh
python homework11/load/runner.py schedule --duration 120 --rate 2 --seed 110 --output /tmp/uniform.jsonl
python homework11/load/runner.py schedule --duration 120 --rate 2 --seed 110 --skew --output /tmp/skew.jsonl
python homework11/load/cluster.py --namespace hw11 --name b-uniform-balanced-1 --schedule /tmp/uniform.jsonl --url http://inference-balanced:8092 --output results/b-uniform-balanced-1
python homework11/load/summarize.py results/b-uniform-balanced-1/requests.jsonl
```

Замените `inference` фактическим fullname Helm release. Для матрицы B повторите
каждое сочетание `{uniform,skew} × {balanced,sticky}` три раза с уникальными
именами запусков и каталогов. Сравниваемые политики используют один и тот же
файл расписания: его SHA-256 входит в `run_start`. При rate=2 за 120 секунд
равномерный сценарий даёт 60 запросов на клиента, skew — 192/16/16/16.
Каждый запрос содержит одинаково ограниченный вывод и явную историю.

Перед каждым повтором восстановите **одинаковое состояние**: 2 реплики,
выключенный HPA для B, одинаковые simulator config/ресурсы, завершённые прошлые
потоки, restart всех server Pod, ожидание Ready всех реплик. Не заполняйте
кеш служебными generate-запросами. Сохраните values, EndpointSlice, Service,
версию CNI/kube-proxy и IP Pod. Удаление и ожидание серверных Pod выполняет
оператор: cluster.py намеренно не меняет сервис и не сбрасывает кеш.

`--lead-seconds` по умолчанию 90. Расписание содержит 120 секунд **подачи**
запросов, затем клиент ожидает ответы; последние отправки в 119.5 секунды.
Начало фазы в `run_start.start_at`, монотонный origin каждого Pod — рядом.
Монотонные часы разных Pod не сопоставляйте напрямую: используйте смещение
от origin и wall-время для привязки к метрикам. Часы node должны быть
синхронизированы. Keep-alive явно выключен: отдельное TCP-соединение на
попытку, что зафиксировано в журнале. Это часть условий опыта, а не вывод о
поведении реального клиента с keep-alive. Kubernetes определяет source IP,
видимый affinity; POD_IP доказывает адрес клиента, но не отсутствие SNAT.

У worker pool 128 потоков на Pod. При насыщении запросы сохраняются в очереди
генератора, а lag растёт. Следите за lag_p95 и CPU клиента: такие прогоны
нельзя трактовать как точное соблюдение расписания. Общие timeout/retries
можно задавать прямому runner; cluster запускает 60 секунд / 0 повторов.
Каждый повтор, если включён, имеет отдельный request ID и попытку.
`timeout` задаёт общий дедлайн запроса. После подключения отдельный watchdog
вызывает shutdown сохранённого TCP-сокета в момент дедлайна: это ограничивает
ожидание заголовков, чтение фрагментов (включая медленную подачу по байтам)
и тела HTTP-ошибок даже при Connection: close. Сокет ответа сохраняется
отдельно от conn.sock. Ограничение: блокирующий системный DNS до подключения
не отменяется stdlib; установка TCP-соединения ограничена socket timeout,
по возвращении connect проверяется остаток общего бюджета. Это не realtime
гарантия при остановке процесса или нехватке CPU генератора.

События: planned, attempt_start, chunk (весь JSON фрагмента), stream_complete,
attempt_end и run_start/end. Отсутствие complete даже при HTTP 200 — aborted;
429/503 — rejected; тайм-аут и незавершённый поток учитываются одновременно
через outcome и incomplete_stream. p50/p95 считаются nearest rank только
по completed, с числом наблюдений. summary отдельно показывает запросы,
попытки, unsent/unfinished, ошибки, тайм-ауты, распределение и lag.
Для timeout ранняя TTFC всё равно сохраняется. Очередь сервера находится в
`stream_complete.data.queue_seconds`; inter-chunk — в каждом chunk.

## Проверки

```sh
python -m unittest discover -s tests -p test_homework11_load.py -v
```

Пять локальных проверок: точное расписание/skew, равномерность, корректное
завершение с монотонными длительностями, HTTP-отказ, оборванный NDJSON-поток
после первой части. Последние три используют настоящий loopback HTTP-сервер.

Kubernetes проверки запускайте **в клиентском Pod** с этим файлом, не через
порт-форвардинг. Файл можно добавить в ConfigMap и смонтировать или передать
через stdin `kubectl exec -i <client> -- python - ...`:

```sh
kubectl -n hw11 exec -i <running-client> -- python - --check version --url http://inference-balanced:8092 --version A < homework11/load/check_service.py
kubectl -n hw11 exec -i <running-client> -- python - --check replacement --url http://inference-balanced:8092 --version A --old-pod <deleted-pod> --expected-pod <new-pod> < homework11/load/check_service.py
kubectl -n hw11 exec -i <running-client> -- python - --check isolation --url http://inference-balanced:8092 < homework11/load/check_service.py
kubectl -n hw11 exec -i <running-client> -- python - --check warmup --url http://<new-pod-ip>:8092 < homework11/load/check_service.py
```

Для version сначала выполните rollout undo и дождитесь версии A. Для
replacement сохраните имя удаляемой реплики, удалите её, дождитесь замены;
история передаётся заново и проверяется в ответе. Проверка warmup идёт прямо
к новому Pod во время прогрева (Service исправно исключает его): ожидаются
503 от ready и generate. На исходном premature_ready она должна упасть,
после исправления — пройти. Дополнительно сохраните Service EndpointSlice,
Ready и журнал нагрузки, чтобы доказать исключение Pod из рабочего трафика.
Эти Kubernetes проверки здесь подготовлены, фактические результаты получает
оператор стенда. Для замены всей реплики доказательство включает Kubernetes
UID/timestamps; одна проверка имени сама по себе не доказывает факт удаления.

После сбора cluster.py печатает команды удаления собственных клиентских Pod
и ConfigMap. Не удаляйте их до выгрузки. Логи и emptyDir ограничены ресурсами
узла: при очень высокой частоте контролируйте ротацию container logs и
размеры. Каждый запуск сохраняет manifest.json, schedule.jsonl, pods.json и
сырые журналы; каталог результата создаётся только новым.

## Уточнения проверки и сохранности данных

Каждое событие немедленно записывается и flush-ится в stdout и файл. Поэтому
при SIGKILL/OOM клиента уже записанные planned/start/chunk доступны через
`kubectl logs`, а summary видит незавершённые попытки. Сохранность зависит от
container log rotation и сохранности узла. `wire_line` содержит время,
исходные байты hex и строку UTF-8 **до** проверки JSON/схемы, включая
повреждённые фрагменты.

Для replacement требуется `--expected-pod <точное-имя-нового-pod>` вместе с
`--old-pod`. Ответ другой старой реплики не проходит. Через обычный Service
новая реплика может не попасться с первой попытки: повторяйте проверку с
сохранением вывода каждой попытки, пока Service не направит запрос на нужную
реплику. Сопоставьте новое имя с UID и временем создания из Kubernetes.

Автоматическая проверка прогрева с управляющего компьютера:

```sh
python homework11/load/check_warmup_cluster.py --namespace hw11 --client <running-python-client> --pod <warming-server-pod> --service inference-balanced --samples 5 --output results/warmup-check.json
```

Она сохраняет сырые Pod/EndpointSlice и ответы; проверяет Pod Ready=false,
отсутствие готового endpoint нового Pod, 503 при прямом обращении и успешный
запрос через Service на другой Pod. Запускайте, когда HTTP нового Pod уже
доступен, а прогрев ещё идёт; выбирайте достаточно длинный прогрев для всех
samples. В исходном premature_ready результат обязан быть отрицательным.
Проверка наблюдений конечна и дополняет непрерывный журнал нагрузки.

Для ограниченного ожидания маршрутизации на replacement добавьте
`--attempts 20 --interval 0.2`. По умолчанию одна попытка. Каждый ответ,
включая другие реплики и транспортные ошибки, печатается немедленно отдельной
JSON-строкой; итог содержит все наблюдения. Сохраняйте stdout даже при
ненулевом завершении. Это конечное число запросов, а не гарантия попадания
на новую реплику при любой сетевой реализации.

`check_warmup_cluster.py` требует samples > 0 и сохраняет argv, returncode,
stdout/stderr каждой команды до проверки её успешности, в том числе
неуспешного `kubectl exec`.
