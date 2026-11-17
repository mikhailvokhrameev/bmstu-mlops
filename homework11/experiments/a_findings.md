# Опыт A: фактические наблюдения 2026-11-17

Все времена ниже UTC. Сырые материалы в `results/a-real-*`: commands.jsonl,
пронумерованные stdout/stderr, markers.jsonl, observer/evidence.jsonl,
load/requests.jsonl, load/client-N.jsonl и analysis.json. Неудавшаяся первая
инъекция не удалена. Контекст hw11, HPA отключён, 2 реплики, warmup 20s,
32 chunks ×0.5 CPU-секунды, grace 120s/preStop 5s. В каждой фазе 4 разных
клиентских Pod/IP, 6 запросов за 120s, rate 0.05/s, retries 0/timeout 60s.
C закончился до начала A; caffeinate использовался во всех оркестраторах.

| Фаза | Каталог | Фактический итог |
|---|---|---|
| A→B | results/a-real-rollout |6 planned/start/end,6 complete,0 HTTP 5xx,0 incomplete; A=3/B=3|
| Неготовая B, rollback A | results/a-real-bad-b |6/6 complete,0 HTTP 5xx/incomplete,всеA|
| Неудачная exec-инъекция | results/a-real-kill |SIGKILL PID1 из того же namespace не остановил процесс;6/6 complete; **не доказательство forced kill**|
| Runtime SIGKILL | results/a-real-kill-runtime |6start/end,1 aborted/incomplete и5 complete,0 HTTP 5xx|
| Graceful delete | results/a-real-drain |6/6 complete,0 HTTP 5xx/incomplete,всеA|

В каждой фазе имеются4 run_end и нет attempt_start без attempt_end. Нулевой итог
штатного rollout относится только к этой сохранённой нагрузке и конфигурации.
Все ответы указывают backend_mode=simulated: это имитатор, не реальная LLM.

## Rollout и rollback

Первый chunk A r000000 получен14:15:22.872; upgrade B старт14:15:22.948;
complete того же потока/Pod14:15:38.591. Rollout B Ready14:16:18.865.
Это доказывает пересечение начала rollout активным потоком; drain проверялся
отдельно, а не выводился из одного этого пересечения.

Неготовая B: hw11-inference-78c8d4855d-nqjzb/IP10.244.0.126, Ready=False.
В 29 EndpointSlice-снимках только ready=false/serving=false, в том числе при
terminating. Две A оставались Ready. Rollback Helm revision 9:
14:19:33.863→14:19:34.520. Четыре попытки, начавшиеся после rollback_ready,
завершились с model_id=A. Ответов B нет. Это совокупное доказательство исключения
неReady B из рабочего трафика в наблюдённом интервале.

## Принудительное завершение и восстановление

Первая exec-инъекция возвратила 0, но restartCount 0/Ready True и отсутствие
previous container показали, что остановки не было. Повтор выполнен через
`docker exec hw11 docker kill --signal=KILL EXACT_CONTAINER_ID`.

14:24:45: исходный container 52e9dd… завершился exit 137/Error; появился новый
e507b… с restartCount 1 при том же Pod UID 06e3a69f-8b14-4562-a85a-f318ec9c6a22.
Активный r000000 получил 1 chunk, затем EOF без complete: aborted/incomplete,
HTTP 200, total 0.969s. Последующие 5 attempt завершились; повторов не было.
Ready transition False14:24:45→True14:25:06 (21s).
Наблюдения EndpointSlice ready=false14:24:46.986→true14:25:07.020.
Клиентские успешные завершения после сбоя содержат A и идентичность Pod;
сохраняющий UID рестарт контейнера отличается от замены Pod в следующей фазе.

## Graceful drain и замена Pod

Удалялся именно Pod активного потока hw11-inference-c4f57b9c4-mrqx9,
UID efef625d-7cb5-4453-9a43-d36a3f91c792. Команда delete началась14:27:59.156.
Первый snapshot Terminating14:28:00.745. В Kubernetes deletionTimestamp
оказался **будущим deadline 14:29:59Z**, deletionGracePeriodSeconds 120:
это не начало удаления. Поэтому нельзя требовать завершения после этого
будущего timestamp и одновременно в пределах grace.

После первого Terminating-снимка получены 28 chunks того же потока; complete
14:28:14.613, до deadline. EndpointSlice14:28:00.995:
ready=false/serving=true/terminating=true;14:28:05.639:
ready=false/serving=false/terminating=true. Старый TCP-поток завершился, хотя
endpoint уже исключался из нового трафика. Без серверного timestamp SIGTERM
это доказательство graceful deletion, а не отдельное доказательство момента
обработки SIGTERM. Новая Ready реплика: hw11-inference-c4f57b9c4-48b4m,
UID 6b105871-dbe1-4b07-8ecb-abde5f79085c; старый UID отсутствует в финальном PodList.

Часы API/клиентов сопоставлены через sent_wall/sent_monotonic. Максимальная
ошибка синхронизации отдельно не измерялась; collector опрашивает пять ресурсов
последовательно, поэтому период больше 1s и короткие переходы могли пропускаться.

## Дополнительные проверки внутри Kubernetes через Service

`results/a-final-checks/result.json`: client Pod a-final-checks-173200 Succeeded.
Полный итоговый stdout: `results/a-final-checks/0023/stdout.txt`, команды и их
метаданные — в пронумерованных каталогах; manifest содержит точный клиентский код.

* Version: HTTP 200, model_id=A, backend_mode=simulated; ответ от новой 48b4m.
* Replacement: первая Service-попытка попала в qjrvc, вторая — именно новую
  hw11-inference-c4f57b9c4-48b4m; ответ A содержит PERSISTED-CONTROL-123.
  Все Service-попытки сохранены отдельно. UID новой реплики подтверждён Pod JSON.
* Isolation: два последовательных запроса с DIALOGUE-ALPHA-1729 и
  DIALOGUE-BETA-2718 возвратили собственный маркер и не содержали чужого;
  check=isolation passed. Это проверка данного сценария имитатора.

Runtime SHA256 A в ответах:
`3be60248867b330e3ed7c93b080dacda0485dabd2e108094bff17d4b1bead14f`.
После опыта оставлены две Ready A (48b4m и qjrvc), HPA выключен. Настройки
длительного потока chunks=32/chunk_cpu_seconds=0.5 сохранены в Helm values;
незаметного возврата к другой конфигурации не выполнялось. Все клиентские Pod
закончились; их объекты и ConfigMap оставлены для воспроизводимой проверки.
