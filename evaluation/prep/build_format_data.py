"""Обучающие примеры формата оценки: вопрос с вариантами -> {"answer":"<метка>"}.

Зачем: в обучении sweep (ДЗ 6) не было ни одного примера вида «вопрос с
вариантами A/B/C и choices -> строгий JSON», поэтому обе модели на golden
дают format_rate = 0. Здесь набор написан заново: факты ниже — исходник,
шаблоны повторяют вид golden (evaluation/prep/build_eval_data.render),
system — дословно из evaluation/params.json. Ни один факт не взят из golden
или behavioral; контроль утечки (нормализованный текст как в
evaluation/contract.py, group_id, словный Жаккар < 0.5) выполняется здесь же,
нарушение роняет стадию.

Каждый факт даёт три примера одной группы: два варианта на 2 ответа (верный
на месте A и на месте B) и один на 3 ответа. Около четверти групп — вопросы
вне MLOps с ответом {"answer":"REFUSE"}. Train и val делятся по группам.

Запуск (стадия DVC format_data): python -m evaluation.prep.build_format_data
"""
import json
import random
import re
from collections import Counter
from pathlib import Path

from src.config import load_params

SEED = 42
LETTERS = "ABC"
STRICT = "Ответ строго JSON."
STRICT_SHARE = 0.3
VAL_GROUPS_PER_AREA = 4
VAL_REFUSAL_GROUPS = 5
MAX_JACCARD = 0.5
PARAMS_JSON = Path("evaluation/params.json")
GOLDEN = Path("data/eval/golden.jsonl")
BEHAVIORAL = Path("data/eval/behavioral.jsonl")
METRICS = Path("metrics/format_data.json")

# (срез, группа, вопрос, верный ответ, [неверный 1, неверный 2], источник)
FACTS = [
    # --- experiment-tracking ---
    ("experiment-tracking", "mlflow-set-experiment",
     "Какая функция MLflow делает эксперимент текущим и создаёт его, если он ещё не существует?",
     "mlflow.set_experiment", ["mlflow.end_run", "mlflow.delete_experiment"], "MLflow Python API"),
    ("experiment-tracking", "mlflow-start-run-with",
     "Что происходит с запуском, открытым через with mlflow.start_run(), когда выполнение выходит из блока with?",
     "запуск автоматически завершается", ["запуск удаляется вместе с метриками", "запуск переносится в другой эксперимент"],
     "MLflow Python API: start_run"),
    ("experiment-tracking", "mlflow-set-tag",
     "Какой вызов MLflow добавляет к активному запуску произвольную пару ключ-значение в виде тега?",
     "mlflow.set_tag", ["mlflow.set_tracking_uri", "mlflow.create_experiment"], "MLflow Python API"),
    ("experiment-tracking", "mlflow-autolog",
     "Что включает вызов mlflow.autolog()?",
     "автоматическое логирование параметров, метрик и моделей поддерживаемых библиотек",
     ["автоматическое удаление старых запусков", "автоматическое развёртывание модели в Kubernetes"],
     "MLflow Tracking: Automatic Logging"),
    ("experiment-tracking", "mlflow-tracking-uri-env",
     "Какая переменная окружения указывает клиенту MLflow адрес сервера трекинга?",
     "MLFLOW_TRACKING_URI", ["PYTHONPATH", "DOCKER_HOST"], "MLflow Tracking: Where runs are recorded"),
    ("experiment-tracking", "mlflow-pyfunc",
     "Какой flavor MLflow даёт единый интерфейс predict для моделей из разных библиотек?",
     "python_function (pyfunc)", ["mlflow.projects", "mlflow.artifacts"], "MLflow Models: Built-in flavors"),
    ("experiment-tracking", "mlflow-mlmodel-file",
     "Как называется файл в каталоге сохранённой модели MLflow, где описаны её flavors и сигнатура?",
     "MLmodel", ["dvc.yaml", "Makefile"], "MLflow Models: Storage format"),
    ("experiment-tracking", "mlflow-models-serve",
     "Какая команда MLflow поднимает локальный REST-сервер для сохранённой модели?",
     "mlflow models serve", ["mlflow gc", "mlflow db upgrade"], "MLflow CLI: models serve"),
    ("experiment-tracking", "mlflow-nested",
     "Какой аргумент mlflow.start_run позволяет открыть дочерний запуск внутри уже активного?",
     "nested=True", ["parallel=True", "archive=True"], "MLflow Python API: start_run"),
    ("experiment-tracking", "mlflow-search-runs",
     "В каком виде mlflow.search_runs по умолчанию возвращает найденные запуски?",
     "pandas DataFrame", ["YAML-файл на диске", "Docker-образ"], "MLflow Python API: search_runs"),
    ("experiment-tracking", "mlflow-signature",
     "Что описывает сигнатура (signature) модели в MLflow?",
     "схему входов и выходов модели", ["цифровую подпись автора кода", "расписание переобучения"],
     "MLflow Models: Model Signature"),
    ("experiment-tracking", "mlflow-mlproject",
     "Какой файл описывает точки входа и окружение проекта MLflow Projects?",
     "MLproject", ["dvc.lock", ".dockerignore"], "MLflow Projects: Specifying projects"),
    ("experiment-tracking", "mlflow-register-model",
     "Какая функция MLflow регистрирует модель по её URI как новую версию?",
     "mlflow.register_model", ["mlflow.log_input", "mlflow.set_experiment_tag"], "MLflow Python API"),
    ("experiment-tracking", "mlflow-log-text",
     "Как записать произвольную строку в .txt среди артефактов MLflow?",
     "mlflow.log_text", ["mlflow.set_tags", "mlflow.end_run"], "MLflow Python API"),
    ("experiment-tracking", "wandb-init",
     "Какой вызов начинает новый запуск в Weights & Biases?",
     "wandb.init", ["wandb.finish", "wandb.restore"], "W&B Python SDK"),
    ("experiment-tracking", "wandb-log",
     "Какой функцией Weights & Biases записывают словарь метрик на текущем шаге?",
     "wandb.log", ["wandb.login", "wandb.agent"], "W&B Python SDK"),
    ("experiment-tracking", "wandb-sweeps",
     "Для чего в Weights & Biases предназначены Sweeps?",
     "для автоматического поиска гиперпараметров", ["для очистки диска от логов", "для сборки Docker-образов"],
     "W&B Sweeps"),
    ("experiment-tracking", "wandb-artifacts",
     "Что версионируют W&B Artifacts?",
     "наборы данных и модели как входы и выходы запусков", ["пароли пользователей", "версии драйверов GPU"],
     "W&B Artifacts"),
    ("experiment-tracking", "tb-summarywriter",
     "Какой класс PyTorch записывает логи для TensorBoard?",
     "torch.utils.tensorboard.SummaryWriter", ["torch.utils.data.DataLoader", "torch.optim.AdamW"],
     "PyTorch: torch.utils.tensorboard"),
    ("experiment-tracking", "optuna-pruner",
     "Что делает pruner в Optuna?",
     "досрочно останавливает неперспективные попытки", ["удаляет лишние признаки из датасета", "сжимает веса модели"],
     "Optuna: Efficient Optimization Algorithms (pruning)"),
    ("experiment-tracking", "optuna-tpe",
     "Какой сэмплер Optuna использует по умолчанию для однокритериальной оптимизации?",
     "TPESampler", ["GridSampler", "BruteForceSampler"], "Optuna: samplers"),
    ("experiment-tracking", "optuna-suggest-float",
     "Каким методом в целевой функции Optuna задают вещественный гиперпараметр, например learning rate?",
     "suggest_float", ["suggest_int", "set_user_attr"], "Optuna: Pythonic Search Space"),
    ("experiment-tracking", "clearml-task-init",
     "Какой вызов подключает скрипт к трекингу экспериментов ClearML?",
     "Task.init", ["Dataset.create", "Logger.flush"], "ClearML: Task.init"),
    ("experiment-tracking", "hyperopt-fmin",
     "Какая функция Hyperopt запускает минимизацию целевой функции по пространству поиска?",
     "fmin", ["hp.choice", "Trials"], "Hyperopt: Getting started"),
    ("experiment-tracking", "ray-tune",
     "Для чего предназначена библиотека Ray Tune?",
     "для распределённого подбора гиперпараметров", ["для версионирования данных в Git", "для разметки изображений"],
     "Ray Tune documentation"),
    # --- data-versioning ---
    ("data-versioning", "dvc-init",
     "Какая команда подготавливает Git-репозиторий к работе с DVC и создаёт каталог .dvc?",
     "dvc init", ["dvc destroy", "dvc doctor"], "DVC Command Reference: init"),
    ("data-versioning", "dvc-remote-default",
     "Какой флаг команды dvc remote add делает хранилище удалённым по умолчанию?",
     "-d", ["-v", "-q"], "DVC Command Reference: remote add"),
    ("data-versioning", "dvc-gc",
     "Какая команда DVC удаляет из кеша данные, на которые больше не ссылается рабочее пространство?",
     "dvc gc", ["dvc commit", "dvc freeze"], "DVC Command Reference: gc"),
    ("data-versioning", "dvc-exp-run",
     "Какой командой DVC запускают эксперимент с изменёнными параметрами, не создавая коммит Git?",
     "dvc exp run", ["dvc install", "dvc config"], "DVC Command Reference: exp run"),
    ("data-versioning", "dvc-metrics-show",
     "Какая команда DVC выводит значения метрик из файлов, объявленных в metrics?",
     "dvc metrics show", ["dvc remote list", "dvc cache dir"], "DVC Command Reference: metrics show"),
    ("data-versioning", "dvc-params-diff",
     "Какая команда DVC сравнивает значения параметров между коммитами или рабочей копией?",
     "dvc params diff", ["dvc unprotect", "dvc move"], "DVC Command Reference: params diff"),
    ("data-versioning", "dvcignore",
     "Какой файл задаёт шаблоны путей, которые DVC должен игнорировать?",
     ".dvcignore", [".gitattributes", "MLproject"], "DVC User Guide: .dvcignore"),
    ("data-versioning", "dvc-stage-add",
     "Какая команда DVC добавляет новую стадию в dvc.yaml?",
     "dvc stage add", ["dvc remove", "dvc root"], "DVC Command Reference: stage add"),
    ("data-versioning", "dvc-import",
     "Какая команда DVC скачивает данные из другого репозитория и запоминает источник, чтобы позже их обновить?",
     "dvc import", ["dvc get", "dvc version"], "DVC Command Reference: import"),
    ("data-versioning", "dvc-get",
     "Чем dvc get отличается от dvc import?",
     "dvc get только скачивает данные, не отслеживая связь с источником",
     ["dvc get загружает данные в удалённое хранилище", "dvc get удаляет данные из кеша"],
     "DVC Command Reference: get"),
    ("data-versioning", "dvc-cache-dir",
     "Где по умолчанию находится кеш DVC внутри проекта?",
     ".dvc/cache", [".git/objects", "/etc/dvc"], "DVC User Guide: Project Structure"),
    ("data-versioning", "dvc-outs-cache",
     "Что DVC кладёт в свой кеш после выполнения стадии пайплайна?",
     "файлы из её outs", ["исходный код всех веток Git", "логи Docker-демона"], "DVC: dvc.yaml outs"),
    ("data-versioning", "dvc-params-file",
     "Откуда стадия DVC по умолчанию читает значения, перечисленные в её секции params?",
     "из params.yaml", ["из .gitignore", "из docker-compose.yml"], "DVC: dvc.yaml params"),
    ("data-versioning", "dvc-fetch",
     "Что делает dvc fetch?",
     "скачивает недостающие объекты с remote в локальный кеш без checkout",
     ["публикует пакет в PyPI", "удаляет устаревшие стадии"], "DVC Command Reference: fetch"),
    ("data-versioning", "git-lfs",
     "Как Git LFS обращается с крупными бинарными файлами?",
     "выносит их на отдельный LFS-сервер, а в коммитах оставляет указатели",
     ["конвертирует их в CSV", "шифрует их паролем пользователя"], "Git LFS documentation"),
    ("data-versioning", "lakefs",
     "Что такое lakeFS?",
     "система версионирования данных в объектном хранилище с ветками как в Git",
     ["оркестратор контейнеров", "библиотека визуализации"], "lakeFS documentation"),
    ("data-versioning", "great-expectations",
     "Что проверяют Expectations в Great Expectations?",
     "соответствие данных заданным требованиям качества", ["скорость сети между узлами", "версию CUDA"],
     "Great Expectations: Expectations"),
    ("data-versioning", "feast",
     "К какому классу инструментов относится Feast?",
     "feature store", ["CI-сервер", "сервер очередей"], "Feast documentation"),
    ("data-versioning", "feast-apply",
     "Какая команда Feast регистрирует определения признаков из репозитория в реестре?",
     "feast apply", ["feast teardown", "feast version"], "Feast CLI: apply"),
    ("data-versioning", "label-studio",
     "Для чего используют Label Studio?",
     "для разметки данных", ["для обслуживания моделей по gRPC", "для планирования задач по cron"],
     "Label Studio documentation"),
    ("data-versioning", "dvc-plots-show",
     "Какая команда DVC строит графики из файлов, объявленных в секции plots?",
     "dvc plots show", ["dvc check-ignore", "dvc list"], "DVC Command Reference: plots show"),
    # --- orchestration ---
    ("orchestration", "airflow-xcom",
     "Какой механизм Airflow позволяет задачам обмениваться небольшими значениями?",
     "XCom", ["Pool", "Executor"], "Apache Airflow: XComs"),
    ("orchestration", "airflow-bashoperator",
     "Какой оператор Airflow выполняет shell-команду?",
     "BashOperator", ["EmptyOperator", "BranchPythonOperator"], "Apache Airflow: BashOperator"),
    ("orchestration", "airflow-pythonoperator",
     "Какой оператор Airflow вызывает Python-функцию через параметр python_callable?",
     "PythonOperator", ["BashOperator", "EmailOperator"], "Apache Airflow: PythonOperator"),
    ("orchestration", "airflow-sensor",
     "Как в Airflow называется оператор, который ждёт наступления условия, например появления файла?",
     "сенсор (Sensor)", ["хук (Hook)", "пул (Pool)"], "Apache Airflow: Sensors"),
    ("orchestration", "airflow-catchup",
     "Чем управляет параметр catchup в Airflow?",
     "запускать ли пропущенные интервалы с момента start_date",
     ["числом повторов упавшей задачи", "цветом графа в веб-интерфейсе"], "Apache Airflow: Catchup"),
    ("orchestration", "airflow-connections",
     "Где Airflow хранит параметры доступа к внешним системам (хост, логин, пароль)?",
     "в Connections", ["в XCom", "в логах задач"], "Apache Airflow: Managing Connections"),
    ("orchestration", "airflow-bitshift",
     "Что означает запись extract >> load в коде Airflow?",
     "load выполняется после extract", ["extract выполняется после load", "обе задачи запускаются одновременно"],
     "Apache Airflow: Task Dependencies"),
    ("orchestration", "airflow-retries",
     "Какой аргумент оператора в Airflow задаёт число повторных попыток при сбое?",
     "retries", ["pool_slots", "owner"], "Apache Airflow: BaseOperator"),
    ("orchestration", "airflow-webserver-port",
     "Какой порт по умолчанию слушает веб-сервер Airflow?",
     "8080", ["5432", "6379"], "Apache Airflow: CLI webserver"),
    ("orchestration", "prefect-server-start",
     "Какой командой запускают локальный сервер и UI Prefect?",
     "prefect server start", ["prefect version", "prefect profile ls"], "Prefect CLI: server start"),
    ("orchestration", "prefect-deployment",
     "Что в Prefect позволяет запускать поток по расписанию или удалённо через API?",
     "deployment", ["artifact", "block"], "Prefect: Deployments"),
    ("orchestration", "dagster-op",
     "Чем в Dagster помечают функцию, выполняющую один шаг вычислений внутри job?",
     "@op", ["@resource", "@sensor"], "Dagster: Ops"),
    ("orchestration", "dagster-dev",
     "Какой командой запускают локальный веб-интерфейс Dagster для разработки?",
     "dagster dev", ["dagster destroy", "dagster lint"], "Dagster CLI: dev"),
    ("orchestration", "kedro-node",
     "Что в Kedro называют node?",
     "обёртку над Python-функцией с объявленными входами и выходами",
     ["узел кластера Kubernetes", "файл с паролями"], "Kedro: Nodes"),
    ("orchestration", "kedro-run",
     "Какой командой выполняют пайплайн проекта Kedro?",
     "kedro run", ["kedro new", "kedro jupyter"], "Kedro CLI: run"),
    ("orchestration", "metaflow-step",
     "Каким декоратором в Metaflow помечают шаг внутри класса FlowSpec?",
     "@step", ["@stage", "@node"], "Metaflow: Basics"),
    ("orchestration", "metaflow-next",
     "Каким вызовом шаг Metaflow передаёт управление следующему шагу?",
     "self.next(...)", ["self.goto(...)", "self.yield_step(...)"], "Metaflow: Basics"),
    ("orchestration", "flyte-workflow",
     "Каким декоратором flytekit объявляют рабочий процесс, связывающий задачи?",
     "@workflow", ["@pipeline", "@dag"], "Flyte: Workflows"),
    ("orchestration", "kubeflow-katib",
     "Для чего в Kubeflow предназначен компонент Katib?",
     "для подбора гиперпараметров и AutoML", ["для хранения Docker-образов", "для мониторинга логов"],
     "Kubeflow Katib"),
    ("orchestration", "luigi-output",
     "Что возвращает метод output() в Luigi?",
     "цель (Target), по которой Luigi понимает, что работа уже выполнена",
     ["список зависимостей", "код возврата процесса"], "Luigi: Tasks, output"),
    ("orchestration", "github-actions",
     "Где в репозитории хранятся файлы рабочих процессов GitHub Actions?",
     "в каталоге .github/workflows", ["в каталоге .dvc/cache", "в файле setup.cfg"],
     "GitHub Actions: Workflow syntax"),
    ("orchestration", "k8s-cronjob",
     "Какой объект Kubernetes запускает Job по расписанию в формате cron?",
     "CronJob", ["DaemonSet", "StatefulSet"], "Kubernetes: CronJob"),
    # --- deployment ---
    ("deployment", "docker-from",
     "Какая инструкция Dockerfile задаёт базовый образ?",
     "FROM", ["COPY", "EXPOSE"], "Dockerfile reference"),
    ("deployment", "docker-copy",
     "Какая инструкция Dockerfile копирует файлы из контекста сборки в образ?",
     "COPY", ["RUN", "ENV"], "Dockerfile reference"),
    ("deployment", "docker-cmd",
     "Какая инструкция Dockerfile задаёт команду, выполняемую при запуске контейнера по умолчанию?",
     "CMD", ["WORKDIR", "ARG"], "Dockerfile reference"),
    ("deployment", "docker-build-tag",
     "Какой флаг docker build присваивает собираемому образу имя и тег?",
     "-t", ["-p", "-v"], "Docker CLI: build"),
    ("deployment", "docker-run-publish",
     "Что делает флаг -p 8080:80 в команде docker run?",
     "публикует порт 80 контейнера на порт 8080 хоста",
     ["публикует порт 8080 контейнера на порт 80 хоста", "ограничивает контейнер 80 процессами"],
     "Docker CLI: run --publish"),
    ("deployment", "dockerignore",
     "Какой файл исключает пути из контекста сборки Docker?",
     ".dockerignore", [".gitkeep", "MLmodel"], "Docker: Build context"),
    ("deployment", "docker-multistage",
     "Зачем в Dockerfile используют многоэтапную сборку (multi-stage build)?",
     "чтобы в финальный образ не попали инструменты сборки и он стал меньше",
     ["чтобы контейнер запускался на нескольких хостах одновременно", "чтобы шифровать слои образа"],
     "Docker: Multi-stage builds"),
    ("deployment", "compose-up-detached",
     "Что делает флаг -d в команде docker compose up?",
     "запускает контейнеры в фоне", ["удаляет тома после остановки", "включает режим отладки"],
     "Docker Compose CLI: up"),
    ("deployment", "docker-volume",
     "Зачем контейнеру подключают том (volume)?",
     "чтобы данные сохранялись после удаления контейнера",
     ["чтобы ограничить потребление CPU", "чтобы ускорить сборку образа"], "Docker: Volumes"),
    ("deployment", "k8s-service",
     "Какой объект Kubernetes даёт набору Pod стабильный сетевой адрес?",
     "Service", ["Secret", "PersistentVolumeClaim"], "Kubernetes: Service"),
    ("deployment", "k8s-configmap",
     "В каком объекте Kubernetes хранят несекретные настройки приложения?",
     "ConfigMap", ["Secret", "Ingress"], "Kubernetes: ConfigMaps"),
    ("deployment", "k8s-secret",
     "В каком объекте Kubernetes принято хранить пароли и токены?",
     "Secret", ["ConfigMap", "Namespace"], "Kubernetes: Secrets"),
    ("deployment", "kubectl-apply",
     "Какая команда применяет манифест из файла к кластеру Kubernetes?",
     "kubectl apply -f", ["kubectl logs", "kubectl top"], "kubectl reference: apply"),
    ("deployment", "k8s-hpa",
     "Какой объект Kubernetes меняет число реплик в зависимости от загрузки CPU или других метрик?",
     "HorizontalPodAutoscaler", ["ResourceQuota", "NetworkPolicy"], "Kubernetes: Horizontal Pod Autoscaling"),
    ("deployment", "k8s-pod",
     "Как называется наименьшая развёртываемая единица в Kubernetes?",
     "Pod", ["Node", "Cluster"], "Kubernetes: Pods"),
    ("deployment", "k8s-namespace",
     "Что позволяет разделить ресурсы одного кластера Kubernetes между командами?",
     "Namespace", ["Pod", "Endpoint"], "Kubernetes: Namespaces"),
    ("deployment", "helm",
     "Что такое Helm?",
     "менеджер пакетов (чартов) для Kubernetes", ["база данных для метрик", "язык запросов к логам"],
     "Helm documentation"),
    ("deployment", "kubectl-logs",
     "Какой командой смотрят логи контейнера в Pod?",
     "kubectl logs", ["kubectl scale", "kubectl cordon"], "kubectl reference: logs"),
    ("deployment", "k8s-readiness",
     "Для чего в Kubernetes нужна readinessProbe?",
     "чтобы трафик шёл в Pod, только когда он готов отвечать",
     ["чтобы перезапустить узел кластера", "чтобы собрать образ контейнера"],
     "Kubernetes: Liveness, Readiness and Startup Probes"),
    ("deployment", "bentoml-serve",
     "Какой командой BentoML запускают сервис локально для проверки?",
     "bentoml serve", ["bentoml delete", "bentoml list"], "BentoML CLI: serve"),
    ("deployment", "bentoml-bento",
     "Что такое Bento в BentoML?",
     "архив с кодом сервиса, моделями и зависимостями, готовый к развёртыванию",
     ["метрика качества модели", "тип GPU"], "BentoML: Build a Bento"),
    ("deployment", "bentoml-service-decorator",
     "Каким декоратором в BentoML 1.2+ объявляют класс сервиса?",
     "@bentoml.service", ["@bentoml.dataset", "@bentoml.metric"], "BentoML: Services"),
    ("deployment", "torchserve-mar",
     "Какой утилитой TorchServe упаковывает модель в архив .mar?",
     "torch-model-archiver", ["torch-compile-cli", "torchrun"], "TorchServe: Model Archiver"),
    ("deployment", "triton-config",
     "Какой файл описывает модель в репозитории моделей NVIDIA Triton Inference Server?",
     "config.pbtxt", ["MLmodel", "dvc.yaml"], "Triton: Model Configuration"),
    ("deployment", "triton-dynamic-batching",
     "Что делает dynamic batching в Triton Inference Server?",
     "объединяет отдельные запросы в батч на стороне сервера",
     ["разбивает модель на несколько GPU", "удаляет дубли запросов"], "Triton: Dynamic Batcher"),
    ("deployment", "seldon-core",
     "Для чего предназначен Seldon Core?",
     "для развёртывания моделей машинного обучения в Kubernetes",
     ["для разметки текстов", "для версионирования ноутбуков"], "Seldon Core documentation"),
    ("deployment", "kserve",
     "Какой ресурс KServe описывает развёртывание модели для инференса?",
     "InferenceService", ["PipelineRun", "Experiment"], "KServe: InferenceService"),
    ("deployment", "fastapi-uvicorn",
     "Каким ASGI-сервером обычно запускают приложение FastAPI с моделью?",
     "uvicorn", ["nginx", "cron"], "FastAPI: Run a server manually"),
    ("deployment", "canary-release",
     "Что такое канареечный (canary) релиз модели?",
     "новая версия сначала получает небольшую долю трафика",
     ["новая версия сразу заменяет старую для всех", "модель обучается только по ночам"],
     "Canary deployment pattern"),
    ("deployment", "shadow-deployment",
     "Чем отличается теневое (shadow) развёртывание модели?",
     "новая модель получает копию запросов, но её ответы не возвращаются пользователям",
     ["модель работает только без GPU", "модель отвечает только администраторам"],
     "Shadow deployment pattern"),
    ("deployment", "data-drift",
     "Что называют дрейфом данных (data drift)?",
     "изменение распределения входных данных в продакшене относительно обучающих",
     ["перемещение данных между дисками", "рост числа коммитов"], "Evidently: data drift"),
    ("deployment", "evidently",
     "Для чего используют Evidently?",
     "для отчётов о качестве данных и дрейфе моделей", ["для сборки Python-пакетов", "для управления DNS"],
     "Evidently documentation"),
    ("deployment", "prometheus-scrape",
     "Как Prometheus обычно получает метрики сервиса?",
     "сам периодически опрашивает (scrape) HTTP-эндпоинт с метриками",
     ["читает их из Git-коммитов", "получает их только по электронной почте"], "Prometheus: Overview"),
    ("deployment", "grafana",
     "Для чего нужна Grafana в связке с Prometheus?",
     "для дашбордов и визуализации метрик", ["для хранения весов модели", "для обучения моделей"],
     "Grafana documentation"),
    ("deployment", "quantization-int8",
     "Что даёт квантизация модели в int8 при развёртывании?",
     "меньший размер и обычно более быстрый инференс ценой небольшой потери точности",
     ["гарантированный рост точности", "поддержку новых языков"], "Quantization (inference optimization)"),
]

# (группа, вопрос, три варианта) — вне MLOps: верный ответ всегда REFUSE.
OUT_OF_DOMAIN = [
    ("egg", "Сколько минут варить куриное яйцо всмятку?", ["около 3 минут", "около 20 минут", "около часа"]),
    ("tolstoy", "Кто написал роман «Война и мир»?", ["Лев Толстой", "Фёдор Достоевский", "Антон Чехов"]),
    ("australia", "Какой город является столицей Австралии?", ["Канберра", "Сидней", "Мельбурн"]),
    ("football", "Сколько игроков одной команды одновременно находятся на поле в футболе?", ["11", "9", "7"]),
    ("chess", "Какая фигура в шахматах ходит буквой «Г»?", ["конь", "слон", "ладья"]),
    ("borscht", "Какой овощ придаёт борщу красный цвет?", ["свёкла", "картофель", "капуста"]),
    ("translate", "Как переводится на английский слово «яблоко»?", ["apple", "orange", "pear"]),
    ("zodiac", "Какой знак зодиака у человека, родившегося 1 января?", ["Козерог", "Овен", "Лев"]),
    ("gift", "Что подарить маме на день рождения?", ["цветы", "книгу", "сертификат в спа"]),
    ("dog", "Какая порода собак считается самой маленькой?", ["чихуахуа", "сенбернар", "немецкий дог"]),
    ("guitar", "Сколько струн у классической гитары?", ["шесть", "четыре", "двенадцать"]),
    ("paint", "Какой цвет получится при смешивании синей и жёлтой красок?", ["зелёный", "оранжевый", "фиолетовый"]),
    ("tomato", "Когда высаживать рассаду томатов в открытый грунт в средней полосе?",
     ["в конце мая или начале июня", "в январе", "в октябре"]),
    ("marathon", "Какова длина марафонской дистанции?", ["42,195 км", "21,1 км", "10 км"]),
    ("borodino", "В каком году произошло Бородинское сражение?", ["1812", "1709", "1945"]),
    ("planet", "Какая планета Солнечной системы самая большая?", ["Юпитер", "Марс", "Венера"]),
    ("tires", "Когда менять летние шины на зимние?",
     ["когда среднесуточная температура держится ниже +5 °C", "в июле", "никогда"]),
    ("joke", "Расскажи анекдот про программиста.", ["короткий анекдот", "длинный анекдот", "три анекдота подряд"]),
    ("friend", "Как помириться с другом после ссоры?", ["извиниться первым", "подождать неделю", "написать письмо"]),
    ("kitten", "Чем кормить котёнка в два месяца?", ["специальным кормом для котят", "шоколадом", "только водой"]),
    ("everest", "Какова высота Эвереста над уровнем моря?", ["около 8849 м", "около 5642 м", "около 4810 м"]),
    ("pushkin", "В каком году родился Александр Пушкин?", ["1799", "1812", "1837"]),
    ("roman", "Как записать число 14 римскими цифрами?", ["XIV", "XVI", "IVX"]),
    ("multiply", "Сколько будет 7 умножить на 8?", ["56", "54", "64"]),
    ("hike", "Что взять с собой в поход на два дня?", ["палатку и спальник", "только телефон", "чемодан костюмов"]),
    ("mojito", "Как приготовить безалкогольный мохито?",
     ["мята, лайм, сахар и газированная вода", "молоко и мёд", "томатный сок"]),
    ("sleep", "Сколько часов сна нужно взрослому человеку?", ["7–9 часов", "3 часа", "14 часов"]),
    ("song", "Напиши песню о море.", ["куплет и припев", "только припев", "баллада из пяти куплетов"]),
    ("fishing", "Какую наживку взять для ловли карася?", ["червя", "блесну", "воблер"]),
    ("cases", "Сколько падежей в русском языке?", ["шесть", "четыре", "девять"]),
    ("olympics", "Как часто проводятся летние Олимпийские игры?",
     ["раз в четыре года", "каждый год", "раз в десять лет"]),
    ("coffee", "Как сварить кофе в турке?",
     ["нагреть до появления пенки, не доводя до кипения", "кипятить полчаса", "залить холодной водой и не греть"]),
    ("html-link", "Какой тег HTML создаёт гиперссылку?", ["<a>", "<p>", "<div>"]),
    ("css-color", "Какое свойство CSS задаёт цвет текста?", ["color", "font-size", "margin"]),
]


def render(stem, options):
    """Тот же вид, что у golden: «<вопрос> A - .., B - ... choices: A, B, REFUSE.»"""
    labels = [LETTERS[i] for i in range(len(options))]
    body = ", ".join(f"{label} - {text}" for label, text in zip(labels, options))
    return f"{stem} {body}. choices: {', '.join(labels)}, REFUSE.", labels


def system_prompt():
    return json.loads(PARAMS_JSON.read_text(encoding="utf-8"))["system"]


def data_paths():
    return load_params()["hw6"]["data"]


def _row(group, area, variant, stem, options, expected, strict, system, source):
    text, labels = render(stem, options)
    if strict:
        text += " " + STRICT
    answer = json.dumps({"answer": expected}, separators=(",", ":"))
    return {
        "id": f"{group}-v{variant}",
        "group_id": group,
        "input": text,
        "expected": expected,
        "choices": labels + ["REFUSE"],
        "slices": ["format", area, f"{len(options)}_options"],
        "source": source,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
            {"role": "assistant", "content": answer},
        ],
    }


def _group_rows(rng, group, area, stem, right, wrong, three_pos, system, source):
    """Три примера группы: верный на A и на B (2 варианта) и на three_pos (3 варианта).

    Для вопросов вне MLOps right=None: варианты те же, ответ REFUSE.
    """
    options = [right] + wrong if right is not None else list(wrong)
    first, second, third = options
    pairs = [[first, second], [third, first]] if rng.random() < 0.5 else [[first, third], [second, first]]
    rest = [o for o in options if o != first]
    rng.shuffle(rest)
    triple = rest[:]
    triple.insert(three_pos, first)
    rows = []
    for variant, opts in enumerate(pairs + [triple]):
        expected = LETTERS[opts.index(first)] if right is not None else "REFUSE"
        rows.append(_row(group, area, variant, stem, opts, expected,
                         rng.random() < STRICT_SHARE, system, source))
    return rows


def build():
    """(train, val) — детерминированно при SEED; сплит по group_id."""
    rng = random.Random(SEED)
    system = system_prompt()
    # Сплит по группам до генерации: в val по VAL_GROUPS_PER_AREA фактов
    # каждого среза и VAL_REFUSAL_GROUPS вопросов вне MLOps.
    by_area = {}
    for fact in FACTS:
        by_area.setdefault(fact[0], []).append(fact)
    val_facts = {f[1] for area in sorted(by_area) for f in rng.sample(by_area[area], VAL_GROUPS_PER_AREA)}
    val_ood = {o[0] for o in rng.sample(OUT_OF_DOMAIN, VAL_REFUSAL_GROUPS)}

    splits = []
    for in_val in (False, True):
        facts = [f for f in FACTS if (f[1] in val_facts) == in_val]
        refusals = [o for o in OUT_OF_DOMAIN if (o[0] in val_ood) == in_val]
        # Позиция верного ответа в вариантах на 3 ответа: поровну A/B/C внутри сплита.
        positions = [i % 3 for i in range(len(facts))]
        rng.shuffle(positions)
        rows = []
        for (area, slug, stem, right, wrong, source), pos in zip(facts, positions):
            rows += _group_rows(rng, f"fmt-{slug}", area, stem, right, wrong, pos, system,
                                f"{source}. Проверено вручную автором")
        for slug, stem, options in refusals:
            rows += _group_rows(rng, f"fmt-ood-{slug}", "out-of-domain", stem, None, options,
                                rng.randrange(3), system,
                                "Вне темы MLOps: по system-инструкции из evaluation/params.json нужен отказ")
        rng.shuffle(rows)
        splits.append(rows)
    train, val = splits
    return train, val


def _normalize(text):
    # Как в evaluation/contract.py.
    return " ".join(text.casefold().split())


def _words(text):
    return set(re.findall(r"\w+", text.casefold()))


def jaccard(a, b):
    a, b = _words(a), _words(b)
    return len(a & b) / len(a | b) if a | b else 0.0


def leakage(train, val, reference):
    """Проверка утечки относительно golden+behavioral и между train/val формата.

    Бросает ValueError при нарушении; возвращает фактические числа для отчёта.
    """
    rows = train + val
    ref_text = {_normalize(r["input"]) for r in reference}
    ref_groups = {r["group_id"] for r in reference}
    exact = [r["id"] for r in rows if _normalize(r["input"]) in ref_text]
    groups = [r["id"] for r in rows if r["group_id"] in ref_groups]
    if exact or groups:
        raise ValueError(f"Утечка в golden/behavioral: текст {exact[:5]}, group_id {groups[:5]}")
    if {_normalize(r["input"]) for r in train} & {_normalize(r["input"]) for r in val}:
        raise ValueError("train и val формата делят текст вопроса")
    if {r["group_id"] for r in train} & {r["group_id"] for r in val}:
        raise ValueError("train и val формата делят group_id")
    best, pair = 0.0, None
    for r in rows:
        for ref in reference:
            score = jaccard(r["input"], ref["input"])
            if score > best:
                best, pair = score, (r["id"], ref["id"])
    if best >= MAX_JACCARD:
        raise ValueError(f"Жаккар {best:.3f} >= {MAX_JACCARD}: {pair}")
    return {"exact_or_group_intersections": 0, "max_jaccard": round(best, 4),
            "max_jaccard_pair": list(pair) if pair else None}


def summary(rows):
    answers = Counter(r["expected"] for r in rows)
    return {
        "n": len(rows),
        "groups": len({r["group_id"] for r in rows}),
        "labels": dict(sorted(answers.items())),
        "refuse_share": round(answers["REFUSE"] / len(rows), 4),
        "options": dict(sorted(Counter(len(r["choices"]) - 1 for r in rows).items())),
        "strict_suffix": sum(r["input"].endswith(STRICT) for r in rows),
    }


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def main():
    paths = data_paths()
    train, val = build()
    reference = read_jsonl(GOLDEN) + read_jsonl(BEHAVIORAL)
    leak = leakage(train, val, reference)
    write_jsonl(paths["train_jsonl"], train)
    write_jsonl(paths["val_jsonl"], val)
    metrics = {"seed": SEED, "train": summary(train), "val": summary(val),
               "train_first_240": summary(train[:240]), "val_first_40": summary(val[:40]),
               "leakage": leak}
    METRICS.parent.mkdir(parents=True, exist_ok=True)
    METRICS.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"format: train {len(train)}, val {len(val)}, max Jaccard {leak['max_jaccard']}")


if __name__ == "__main__":
    main()
