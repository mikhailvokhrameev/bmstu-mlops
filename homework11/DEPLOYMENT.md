# Стенд ДЗ 11

Команды выполняются из корня репозитория. Основной backend — **simulated**: реальные CPU-вычисления, имитация локального кеша, NDJSON-поток. Образ содержит только CPU API и не устанавливает torch/transformers; существующие режимы ДЗ 10 запускаются прежним окружением.

## Предварительная проверка и запуск

Перед созданием стенда сохраните реальные сведения, не подставляйте оценку вместо измерения:

```sh
mkdir -p results/environment
 df -h . > results/environment/disk-before.txt
docker info > results/environment/docker-info.txt
docker system df > results/environment/docker-disk-before.txt
minikube version > results/environment/minikube-version.txt
kubectl version --client -o yaml > results/environment/kubectl-version.yaml
helm version > results/environment/helm-version.txt
minikube start -p hw11 --driver=docker --kubernetes-version=v1.31.0 --cpus=4 --memory=4096 --disk-size=12g
docker build -f homework11/Dockerfile -t bmstu-inference:hw11 .
minikube -p hw11 image load bmstu-inference:hw11
helm lint homework11/chart
helm upgrade --install hw11 homework11/chart --namespace hw11 --create-namespace --wait --timeout 5m
kubectl -n hw11 get pods,svc
```

Пример профиля: 4 CPU, 4 GiB RAM, диск VM 12 GiB. Лимит памяти пяти основных Pod — 1664 MiB при двух репликах API (API 2×256 MiB; мониторинг 3×384 MiB), при трёх API — 1920 MiB, плюс control plane, metrics-server, системные Pod, клиентские Pod и временная surge-реплика при rollout. Requests: 650m CPU и 480 MiB при двух API. Это план ёмкости, а не фактическое потребление. Retention Prometheus — 2 часа и 256 MB; его emptyDir ограничен 384 MiB, Grafana — 384 MiB. Том непостоянный: экспортируйте измерения до удаления. Образы закреплены тегами версий, зависимости — точными версиями в requirements.txt; для строгой идентичности байтов после первой загрузки сохраните imageID/digest из Pod и используйте registry с неизменяемыми тегами.

## Фактический размер выполненного стенда

В выполненном профиле использованы minikube v1.34.0 и Kubernetes v1.31.0; команда запуска выше закреплена на этой проверенной версии. Docker inspect подтверждает лимит контейнера профиля hw11: 4 CPU / 4 GiB RAM. До minikube наблюдалось 45 GiB свободного места хоста по df; это записано в игнорируемом git рабочем ledger `.superpowers/sdd/2026-11-17-homework11/progress.md`, а не в сохранённом raw snapshot df. Сопоставимого сохранённого `docker system df` до запуска также нет, поэтому прирост Docker-диска относительно исходного состояния не вычислен.

Read-only измерение после опытов сохранено в [size-inspection.json](../results/environment/size-inspection.json), включая argv, stdout/stderr и время:

- образ CPU API `hw11-service:local`: 163 901 359 байт, или 156,31 MiB по `docker image inspect`;
- выделенный том профиля hw11, смонтированный в `/var`: `du -skx /var` = 2 336 192 KiB, около 2,228 GiB; `-x` исключает повторный обход вложенных overlay mount;
- kicbase v0.0.45: 1 187 744 135 байт, около 1,106 GiB; writable layer контейнера профиля: 2 968 965 байт, около 2,83 MiB;
- грубая сумма выделенного `/var`, базового образа и writable layer: около 3,337 GiB. Это оценка состава собственного профиля, не точное число уникальных физических блоков хоста: слои могут разделяться, Docker VM и sparse-диск имеют собственные накладные расходы. API image уже содержится внутри данных `/var` и повторно к сумме не прибавлен.

Внутренний Docker профиля отдельно показывает 1,662 GB образов и 64,83 MB container writable layers; эти значения перекрываются с `/var`, а не добавляются к нему. Совокупный host `docker system df` после запуска (17,97 GB образов, 3,256 GB томов) включает чужие/предыдущие ресурсы и не является размером только этой работы. Объём временных клиентских Pod и метрик меняется по мере опытов; retention/лимиты томов выше остаются планом ограничения, не измерением занятого пространства.

## Resource metrics API и HPA

Установка resource metrics API отделена от chart: кластерный компонент не должен удаляться вместе с опытом. Версия upstream manifest закреплена:

```sh
curl -fL https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.7.2/components.yaml -o results/environment/metrics-server-v0.7.2.yaml
kubectl apply -f results/environment/metrics-server-v0.7.2.yaml
kubectl -n kube-system rollout status deployment/metrics-server
kubectl get --raw /apis/metrics.k8s.io/v1beta1/nodes > results/environment/resource-metrics.json
kubectl top pods -n hw11
helm upgrade hw11 homework11/chart -n hw11 --reuse-values --set hpa.enabled=true
kubectl -n hw11 get hpa -w
```

Если локальный kubelet использует сертификат без подходящего SAN, metrics-server может отказать в TLS-проверке. Только в изолированном учебном кластере добавьте `--kubelet-insecure-tls`, сохранив изменение в результаты:

```sh
kubectl -n kube-system patch deployment metrics-server --type=json -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
kubectl -n kube-system get deployment metrics-server -o yaml > results/environment/metrics-server-effective.yaml
```

HPA выключен по умолчанию для опытов A/B: две фиксированные реплики. В C включается диапазон 2–3, цель 60% CPU request (250m), downscale stabilization 120 с. При включённом HPA chart не устанавливает replicas, чтобы upgrade не сбрасывал решение контроллера. Для возврата к A/B:

```sh
helm upgrade hw11 homework11/chart -n hw11 --reuse-values --set hpa.enabled=false --set replicaCount=2
```

## Версия, прогрев и завершение

`artifact.version` задаёт JSON-артефакт ConfigMap. Helm вычисляет SHA-256 **точных байтов JSON**, сохраняет ожидаемый hash в simulator.json. Сервис читает файл при старте и проверяет SHA-256; загруженная версия и hash возвращаются в ответах. Изменение конфигурации меняет annotation checksum и запускает rollout. Весов и общих PV нет. У всех реплик одинаковые байты артефакта; кеш всегда локальный и пуст после замены Pod.

Startup `/startup` ожидает загрузку и прогрев (по умолчанию бюджет 120 с), readiness `/ready` управляет рабочими endpoints, liveness `/health` проверяет живость отдельно. При большем прогреве увеличьте startupProbe.failureThreshold и progressDeadlineSeconds в шаблоне. Для исходного дефекта используйте `--set simulator.premature_ready=true`, затем повторите с false. Для rollout A→B: `--set artifact.version=B`. Для неготовой B можно задать `--set simulator.warmup_seconds=3600`; восстановление `helm rollback hw11 <revision-A> -n hw11`. Сохраните `helm history` и проверяйте версию по фактическому ответу.

При удалении Pod Kubernetes исключает endpoint из нового трафика; preStop 5 с оставляет время на распространение, затем SIGTERM получает Python как PID 1. Uvicorn завершает активные ответы в пределах общего terminationGracePeriodSeconds=120. Для длительных потоков budget должен покрывать очередь, вычисления, распространение endpoints и запас. Нулевые ошибки rollout требуют фактического нагрузочного подтверждения; chart сам по себе их не доказывает.

## Сервисы и мониторинг

`hw11-inference-balanced:8092` — sessionAffinity None; `hw11-inference-sticky:8092` — ClientIP с timeout 10800 с. Обе политики выбирают одни Pod. Для измерений направляйте нагрузку из четырёх клиентских Pod; port-forward не воспроизводит распределение Service. Фиксируйте сеть, keep-alive и IP.

```sh
kubectl -n hw11 port-forward svc/hw11-prometheus 9090:9090
# В другом терминале:
kubectl -n hw11 port-forward svc/hw11-grafana 3000:3000
```

Grafana доступна локально на localhost:3000 с anonymous Viewer; не публикуйте этот учебный стенд наружу. Dashboard экспортирован в `monitoring/dashboard.json`, та же версия включена в chart/files. При другом release/namespace измените константы namespace/deployment в dashboard. Данные собираются напрямую с каждого Pod, включая неготовые Pod; discovery ограничен namespace и release. cAdvisor поступает через API server node proxy, Ready-реплики — kube-state-metrics. CPU/RAM панели выбирают родительский Pod cgroup по конечному компоненту `id`, содержащему `pod`, и исключают дочерние container cgroup: CPU использует `cpu="total"`, агрегация `max by (pod)` устраняет дубли scrape без сложения родительской и дочерних метрик. Это проверено на cgroupfs minikube; иной формат `id` или отсутствие Pod-level series потребуют отдельной адаптации. ServiceAccount мониторинга имеет read-only cluster discovery и node proxy; это учебная конфигурация с кластерными правами, устанавливать её следует в отдельном локальном кластере.

Алерты доступны в Prometheus `/alerts`: p95 TTFC >1.5 с при ≥20 завершениях за минуту; доля отказов/обрывов >10% при ≥20 терминальных попытках за минуту. Оба удерживают условие 30 с; оценка каждые 5 с. Успешные histogram наблюдения пишутся только после завершения: сравнивайте их с завершёнными клиентскими ответами в том же окне. Отказы — rejected/queue_timeout/unready/aborted; invalid включён в знаменатель терминальных исходов. Alertmanager и доставка уведомлений не нужны для наблюдения pending/firing и не включены.

Пример PromQL для сравнения с клиентом: `histogram_quantile(0.95, sum by (le) (increase(simulated_ttfc_seconds_bucket[120s])))`; p50 заменяет 0.95 на 0.50. Нужны одинаковый интервал, набор завершённых ответов, достаточное число scrape и явный учёт приближения histogram. Request/session ID в метрики не добавляются.

## Сохранение и удаление

```sh
helm get values hw11 -n hw11 --all > results/environment/helm-values.yaml
helm get manifest hw11 -n hw11 > results/environment/helm-manifest.yaml
kubectl -n hw11 get pods -o json > results/environment/pods.json
kubectl -n hw11 get hpa,deploy,rs,endpointslices -o yaml > results/environment/kubernetes.yaml
kubectl -n hw11 logs -l app.kubernetes.io/name=inference --all-containers --prefix > results/environment/api.log
kubectl version -o yaml > results/environment/kubernetes-version.yaml
docker system df > results/environment/docker-disk-after.txt
minikube -p hw11 ssh -- df -h > results/environment/cluster-disk.txt
helm uninstall hw11 -n hw11
kubectl delete namespace hw11
minikube delete -p hw11
docker image rm bmstu-inference:hw11
```

Удаляется только выделенный профиль hw11. Если использовался общий кластер, вместо удаления minikube удалите только установленный вами metrics-server через сохранённый manifest; чужой metrics-server не удаляйте. Реальные графики, requests.jsonl, события rollout/HPA, firing алертов и объём диска заполняются только после прогона.

## Результаты и публикация

Сырые результаты упакованы в `homework11/results.tar.gz`. После клонирования распакуйте архив **из корня репозитория** командой `tar -xzf homework11/results.tar.gz`: она восстановит `results/` и ссылки отчёта. Распаковка может перезаписать одноимённые файлы; если `results/` уже существует, сначала сохраните его отдельно. Локальная распакованная директория `results/` исключена из Git, чтобы тысячи промежуточных журналов не попадали в список изменений. Отчёт `homework11/report.html` не игнорируется. Архив содержит объединённый JSONL и исходные журналы; `results/requests-manifest.json` содержит контрольные суммы источников.
