# Опыт A: подготовленный протокол, без результатов

**Не запускать до окончания B и выгрузки его данных.** Здесь нет результатов
и нет автоматически выполняемых изменений кластера. Все команды ниже — ручной
план последующего запуска из корня репозитория. Используются release/namespace
`hw11`; сверить реальные имена. Не запускать C одновременно.

## Общее состояние и доказательства

Сохранить отдельный новый каталог для каждой фазы, UTC timestamps команд,
stdout/stderr и exit codes, `helm get values hw11 -n hw11 -a`,
`helm get manifest hw11 -n hw11`, `helm history hw11 -n hw11`, image IDs,
контрольные суммы расписания и runner. Проверить HPA выключен, 2 Ready Pod,
образ доступен, ресурсы позволяют surge +1. Не начинать без доступного A.
Полный штатный прогон должен содержать все попытки, без фильтрации ошибок.

Перед каждым прогоном запустить в отдельном терминале:

```sh
python3 homework11/experiments/a_observe.py --output results/a-rollout-observer --duration 1200
```

Это только GET Kubernetes и локальная запись в новый каталог. Сохраняются Pod
UID, Ready conditions/transition times, deletionTimestamp, restartCount,
Deployment, ReplicaSet, EndpointSlice conditions ready/serving/terminating,
события. Интервал 1s — пауза после пяти последовательных запросов; фактический
период длиннее. Снимки не атомарны, краткие переходы могут быть пропущены.
Ошибки API сохраняются и не означают отсутствие endpoints. При необходимости
параллельно сохранить `kubectl get pods -w -o json` и EndpointSlice watch.

## Штатный rollout A → B

Для воспроизводимого длинного ответа используем 16 чанков по 0.5 CPU-секунды.
Это **CPU**, не гарантированные 8 секунд wall time: подтвердить фактический
первый chunk до обновления. Grace=120s, preStop=5s, клиентский timeout=60s.
Нагрузка низкая: один запрос каждые 20s, четыре клиента. Результаты относятся
только к этой конфигурации и ресурсам. Сначала подготовить A и дождаться Ready:

```sh
helm upgrade hw11 homework11/chart -n hw11 --reuse-values --set hpa.enabled=false --set replicaCount=2 --set artifact.version=A --set simulator.premature_ready=false --set simulator.warmup_seconds=20 --set simulator.chunks=16 --set-json simulator.chunk_cpu_seconds=0.5 --set terminationGracePeriodSeconds=120 --set preStopSeconds=5
kubectl -n hw11 rollout status deployment/hw11-inference --timeout=240s
python3 homework11/load/runner.py schedule --duration 240 --rate 0.05 --seed 111 --output /tmp/a-rollout.jsonl
python3 - <<'PY'
import json
from pathlib import Path
p=Path('/tmp/a-rollout.jsonl')
rows=[json.loads(line) for line in p.read_text().splitlines()]
for row in rows:
    row['payload']['max_new_tokens']=16
p.write_text(''.join(json.dumps(row)+'\n' for row in rows))
PY
python3 homework11/load/cluster.py --namespace hw11 --name a-rollout-1 --schedule /tmp/a-rollout.jsonl --url http://hw11-inference-balanced:8092 --output results/a-rollout-1
```

Последняя команда блокируется до выгрузки. Во втором терминале смотреть
`kubectl -n hw11 logs -f a-rollout-1-0` (если первый клиент другой, выбрать Pod
с текущим запросом). **Только после фактического первого `chunk` A**, пока нет
его `stream_complete`, записать UTC и выполнить:

```sh
helm upgrade hw11 homework11/chart -n hw11 --reuse-values --set artifact.version=B
kubectl -n hw11 rollout status deployment/hw11-inference --timeout=240s
```

Если ответ успел закончиться до команды — этот прогон не доказывает пересечение,
повторить с новым именем/каталогом. Найти request_id, у которого первый chunk A
раньше начала upgrade, complete A позже начала upgrade. Сопоставить UID его Pod,
удаление/завершение старой реплики и длительность grace. Сохранить и ответы B.
Поток, завершившийся до удаления своего Pod, подтверждает пересечение rollout,
но сам по себе не доказывает drain активного ответа при SIGTERM.

```sh
python3 homework11/load/summarize.py results/a-rollout-1/requests.jsonl
```

Проверить число planned/attempt_start/attempt_end и завершение всех четырёх
клиентов, ноль status>=500, ноль incomplete_stream, timeout/error/aborted,
все потоки имеют complete. Любое нарушение опровергает требуемый нулевой итог;
не скрывать его за последующим успешным retry. Cluster runner по умолчанию
retries=0 и timeout=60; не считать 429 успешным обслуживанием.

## Заведомо неготовая B → управляемый откат A

Сначала вернуть A с прогревом 20s той же командой подготовки и дождаться Ready.
Сохранить **номер текущей Helm revision A** из `helm history`. Начать отдельный
observer и то же расписание под новым именем `a-bad-b-1`; нагрузку сохранить до
конца отката. После начала запросов:

```sh
helm upgrade hw11 homework11/chart -n hw11 --reuse-values --set artifact.version=B --set simulator.warmup_seconds=3600
kubectl -n hw11 get pods -o wide
kubectl -n hw11 get endpointslices -l kubernetes.io/service-name=hw11-inference-balanced -o yaml
```

Не использовать `--atomic`: нужен наблюдаемый управляемый откат. При
maxUnavailable=0 ожидаются две доступные A и одна B не Ready. Сохранить минимум
30–40s наблюдения до отката; startupProbe здесь допускает примерно 120s до
перезапуска, поэтому откатить раньше. Проверить по UID/IP B, что она отсутствует
с ready=true в EndpointSlice, и по ответам — что обслуживала A. Само отсутствие
ответов B без Ready/Endpoints линии недостаточно. Не вызывать B напрямую.

```sh
# Подставить сохранённый числовой номер revision A, не копировать заглушку.
helm rollback hw11 REVISION_A -n hw11 --wait --timeout 240s
kubectl -n hw11 rollout status deployment/hw11-inference --timeout=240s
```

Подтвердить A фактическими `model_id`/runtime_sha256 ответов после завершения
rollback, а не только Helm values. Shared ConfigMap меняется при upgrade;
процесс читает config при старте, поэтому обязательно сохранить config/checksum
и идентичность ответов каждого Pod, не выводить версию только из имени Pod.

## Отдельное принудительное завершение

После штатного опыта и отката восстановить 2 Ready A, сохранить отдельные
observer/расписание/выгрузку с именем `a-kill-1`. Применить ту же низкую нагрузку.
По текущему `chunk.data.pod` выбрать **именно Pod активного потока**, записать
request_id и UTC. Пока этот request_id не завершился, выполнить в другом
терминале (подставить реальное имя Pod):

```sh
# В данном Docker/minikube hw11: сначала сохранить Pod JSON и точный api containerID.
# Подставить полный ID без docker://; сверить имя/UID активного Pod.
docker exec hw11 docker kill --signal=KILL EXACT_API_CONTAINER_ID
```

Это отдельный разрушительный сценарий, только после B. Он убивает процесс
контейнера без preStop/grace. `kubectl delete --force --grace-period=0` не
равнозначен: удаление API-объекта само по себе не гарантирует остановку процесса.
Сигнал отправляется runtime из ancestor namespace. В текущем кластере
`kubectl exec ... os.kill(1,SIGKILL)` вернул 0, но процесс не остановился
(restartCount остался 0): такой запуск сохранён отдельно и не считается
успехом. Проверять завершение по lastState exit137/finishedAt, смене
containerID и обрыву потока; один exit code команды не доказывает kill.

Сохранить `kubectl logs ACTIVE_POD -c api --previous`, Pod status с
lastState.terminated/reason/exitCode/finishedAt/restartCount, Ready/Endpoints
переходы, error/aborted исходного request_id и последующие complete после
прогрева перезапущенного процесса. Автоматический рестарт обычно сохраняет Pod
UID; это отличается от замены Pod Deployment. Обнаружение отказа измерять от
зафиксированной команды/finishedAt до первого наблюдения Ready=false и
исключения endpoint; восстановление — до Ready=true, рабочего endpoint и
первого успешного ответа после отказа. Для каждого времени назвать источник и
дискретность. Если краткий отказ пропущен — так и записать.

Повторы при необходимости включать отдельным клиентским запуском runner с
`--retries 1`; cluster.py не предоставляет этот флаг. Он сохраняет отдельные
attempt_start/attempt_end и request_id с `-a1`, `-a2`. Не склеивать попытки в
один успех, и не добавлять повторы скрытым HTTP-прокси. Базовый прогон retries=0
сохраняет исходный обрыв как самостоятельный результат.

## Исходный дефект и исправление

Если эта часть A ещё не сохранена, провести отдельные два одинаковых прогона:
`simulator.premature_ready=true` и затем false, warmup=20. Нагрузку подать
непосредственно в окно старта новых Pod; обычный cluster.py с lead=90s после
готовности пропустит дефект. Подготовить клиентов заранее, начать rollout в
заранее отмеченный момент подачи. Подтвердить ранний Ready, endpoint и реальный
503/unready с идентичностью непрогретого backend; затем исчезновение этого
поведения при исправлении на том же расписании. Если трафик не попал в новое
окно, это отсутствие наблюдения, а не опровержение дефекта.

## Локальная проверка подготовки

```sh
python3 homework11/experiments/a_observe.py --help
python3 -m py_compile homework11/experiments/a_observe.py
```

Эти проверки не обращаются к кластеру. Observer не собирает HTTP-попытки сам:
обязательны клиентские JSONL, включая wire_line, и логи сервера. Ничего в этом
файле не подтверждает уже выполненный опыт; численные итоги заполняются только
после фактического запуска и проверки сырых данных.

## Дополнительная проверка drain именно удаляемого Pod

Пересечение начала upgrade недостаточно для доказательства drain. Выполнить
отдельный прогон с observer `results/a-drain-observer` и новым именем нагрузки
`a-drain-1`, после завершения остальных прогонов. Подготовить Ready A командой
подготовки выше, заменив `simulator.chunks=16` на `simulator.chunks=32` и сохранив
`--set-json simulator.chunk_cpu_seconds=0.5`, grace=120s/preStop=5s. Создать
расписание duration=240/rate=0.025 (интервал 40s), затем тем же Python-фрагментом
заменить в **новом** файле `/tmp/a-drain.jsonl` max_new_tokens на 32. Запустить
cluster.py с новым schedule/name/output и balanced Service. При idle CPU поток
составляет около 16 CPU-секунд; CPU throttling/конкуренция удлиняют wall time.
Клиентский timeout остаётся 60s, поэтому проверить реальную длительность на
baseline и не добавлять параллельную нагрузку. Нельзя считать CPU-секунды
гарантированным временем ответа.

Конкретные команды подготовки и нагрузки (observer запустить до нагрузки):

```sh
helm upgrade hw11 homework11/chart -n hw11 --reuse-values --set hpa.enabled=false --set replicaCount=2 --set artifact.version=A --set simulator.premature_ready=false --set simulator.warmup_seconds=20 --set simulator.chunks=32 --set-json simulator.chunk_cpu_seconds=0.5 --set terminationGracePeriodSeconds=120 --set preStopSeconds=5
kubectl -n hw11 rollout status deployment/hw11-inference --timeout=240s
python3 homework11/load/runner.py schedule --duration 240 --rate 0.025 --seed 111 --output /tmp/a-drain.jsonl
python3 - <<'PY_DRAIN'
import json
from pathlib import Path
p=Path('/tmp/a-drain.jsonl')
rows=[json.loads(line) for line in p.read_text().splitlines()]
for row in rows:
    row['payload']['max_new_tokens']=32
p.write_text(''.join(json.dumps(row)+'\n' for row in rows))
PY_DRAIN
python3 homework11/load/cluster.py --namespace hw11 --name a-drain-1 --schedule /tmp/a-drain.jsonl --url http://hw11-inference-balanced:8092 --output results/a-drain-1
```

После фактического первого chunk A выбрать имя **его** Pod и немедленно начать
штатное удаление именно этой реплики (отдельный drain-сценарий, без SIGKILL):

```sh
# ACTIVE_POD взять из текущего chunk.data.pod, записать request_id и UTC.
kubectl -n hw11 delete pod ACTIVE_POD --wait=false
```

Не указывать `--force` или `--grace-period=0`. Deployment создаст замену A.
Для доказательства сохранить UID/имя исходного Pod, его deletionTimestamp,
EndpointSlice ready/serving/terminating и непрерывный JSONL того же request_id.
В текущем API deletionTimestamp выставлен как будущий deadline (delete UTC +
120s), а не момент начала удаления. Требуются chunk после первого наблюдения
непустого deletionTimestamp (начала Terminating), затем stream_complete того же
потока/Pod и успешный attempt_end ДО этого deadline. Сохранить также
deletionGracePeriodSeconds и время команды delete; не прибавлять grace к
уже будущему deletionTimestamp второй раз.
Сопоставить UTC API и client wall time; client received_monotonic перевести
через пару sent_monotonic/sent_wall данного attempt. При ненадёжной
синхронизации часов указать погрешность; сравнение разных часов без неё не
доказывает порядок. Зафиксировать, что endpoint удаляется из нового трафика,
а существующее TCP-соединение завершается. По возможности параллельно сохранить
Pod watch и серверные логи до удаления: после исчезновения Pod логи могут быть
недоступны. Если процесс дождался завершения ответа в preStop до SIGTERM,
это подтверждает graceful deletion, но не ожидание ответа обработчиком SIGTERM;
для последнего нужны серверный timestamp SIGTERM и complete после него.

Нулевой итог для этого прогона устанавливается только после проверки всех
запланированных запросов и попыток, как для rollout выше. Если поток завершился
до первого наблюдения Terminating или не был пойман Pod snapshot — повторить в новом
каталоге и сохранить прежний прогон как не давший нужного доказательства.

## Локальное окружение оператора

В текущем окружении Helm находится в `/private/tmp/darwin-arm64/helm`.
Перед реальными командами **после сигнала об окончании C**:

```sh
export PATH="/private/tmp/darwin-arm64:$PATH"
export KUBECTL_CONTEXT=hw11
kubectl config current-context
# При отличии контекста остановиться и проверить; все мутации выполнять с --context hw11.
```

`KUBECTL_CONTEXT` сам по себе не переключает kubectl; это только напоминание.
Collector и cluster.py используют текущий kube context, поэтому проверить его
до запуска, сохранить kubeconfig context в evidence и не переключать во время
прогона. Для защиты от сна держать отдельный процесс `caffeinate -dimsu` на время
всех фаз; завершить его после выгрузки. Наличие caffeinate не подтверждает
успешность эксперимента.

Каждую существенную команду можно выполнить через журналирующую обёртку:

```sh
python3 homework11/experiments/a_command.py --output results/a-EXPERIMENT/COMMAND -- kubectl --context hw11 -n hw11 get pods -o json
```

Она создаёт только новый каталог и сохраняет argv, UTC/wall начало/конец,
stdout/stderr и exit code; при переданном helm/kubectl mutation действительно
выполняет изменение. Не вызывать её с мутациями до сигнала координатора.
Локально проверен `helm template` c chunks=32 и --set-json chunk_cpu_seconds=0.5:
в JSON simulator получены число 0.5 и целое 32. Это проверка шаблона, а не
результат Kubernetes-прогона.
