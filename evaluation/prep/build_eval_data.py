"""Сборка данных оценки: golden, behavioral и полный индекс вопросов train.

Golden и behavioral написаны вручную по документации инструментов и лежат в
этом файле как исходник; индекс train строится из data/train.jsonl - тех же
данных, на которых обучался адаптер. Запуск: python -m evaluation.prep.build_eval_data
"""
import json
from pathlib import Path

OUT = Path('data/eval')
TRAIN = Path('data/train.jsonl')
LETTERS = 'ABC'

# (срез, группа, вопрос, верный вариант, неверные варианты, позиция верного, источник)
GOLDEN = [
    # --- experiment-tracking ---
    ('experiment-tracking', 'mlflow-log-metric', 'Какая функция MLflow записывает числовую метрику (например, loss) в текущий run?',
     'mlflow.log_metric', ['mlflow.log_param'], 1, 'MLflow Tracking, раздел Logging functions'),
    ('experiment-tracking', 'mlflow-registry', 'Что в MLflow хранит версии зарегистрированных моделей и их alias?',
     'Model Registry', ['MLflow Projects'], 0, 'MLflow Model Registry'),
    ('experiment-tracking', 'mlflow-log-param', 'Какая функция MLflow сохраняет гиперпараметр запуска (например, learning_rate)?',
     'mlflow.log_param', ['mlflow.log_artifact'], 0, 'MLflow Tracking, раздел Logging functions'),
    ('experiment-tracking', 'tensorboard-logdir', 'Из чего TensorBoard читает данные для графиков?',
     'из event-файлов в каталоге logdir', ['из реестра моделей MLflow'], 1, 'TensorBoard, Get started'),
    ('experiment-tracking', 'mlflow-ui', 'Какая команда запускает веб-интерфейс MLflow Tracking?',
     'mlflow ui', ['mlflow plot'], 0, 'MLflow Tracking, раздел Tracking UI'),
    ('experiment-tracking', 'mlflow-artifact', 'Что в MLflow называется artifact?',
     'файл, сохранённый вместе с run (модель, график, отчёт)', ['одно числовое значение метрики'], 0, 'MLflow Tracking, раздел Concepts'),
    ('experiment-tracking', 'optuna-study', 'Как в Optuna называется объект всей оптимизации гиперпараметров?',
     'study', ['registry'], 1, 'Optuna, раздел Key Features'),
    ('experiment-tracking', 'optuna-trial', 'Как в Optuna называется одна попытка с конкретным набором гиперпараметров?',
     'trial', ['stage'], 0, 'Optuna, раздел Key Features'),
    ('experiment-tracking', 'tensorboard-scalars', 'Что показывает вкладка Scalars в TensorBoard?',
     'кривые метрик (например, loss) по шагам обучения', ['исходный код модели'], 1, 'TensorBoard, Scalars dashboard'),
    ('experiment-tracking', 'mlflow-alias', 'Что такое alias (например, champion) в реестре моделей MLflow?',
     'именованная ссылка на конкретную версию модели', ['копия весов модели в другом каталоге'], 0, 'MLflow Model Registry, раздел Model Alias'),
    ('experiment-tracking', 'mlflow-backend-store', 'Где в MLflow хранятся метаданные запусков: параметры, метрики, теги?',
     'в backend store', ['в artifact store', 'в Docker registry'], 2, 'MLflow Tracking, раздел Backend Stores'),
    ('experiment-tracking', 'optuna-optimize', 'Какой метод Optuna запускает оптимизацию?',
     'study.optimize', ['study.compile', 'study.deploy'], 1, 'Optuna, раздел Key Features'),
    # --- data-versioning ---
    ('data-versioning', 'dvc-add', 'Какая команда DVC начинает отслеживать файл или каталог с данными?',
     'dvc add', ['dvc remote add'], 1, 'DVC, Command Reference: add'),
    ('data-versioning', 'dvc-push', 'Какая команда DVC отправляет данные из кеша в удалённое хранилище?',
     'dvc push', ['dvc checkout'], 0, 'DVC, Command Reference: push'),
    ('data-versioning', 'dvc-dvcfile', 'Что Git хранит вместо большого файла, добавленного через dvc add?',
     'маленький .dvc-файл с хешем', ['копию файла целиком'], 0, 'DVC, Get Started: Data Versioning'),
    ('data-versioning', 'dvc-checkout', 'Какая команда DVC восстанавливает файлы рабочей копии по .dvc-файлам и dvc.lock?',
     'dvc checkout', ['dvc gc'], 0, 'DVC, Command Reference: checkout'),
    ('data-versioning', 'dvc-deps', 'Что описывает секция deps стадии в dvc.yaml?',
     'входные зависимости стадии (данные, код)', ['удалённое хранилище'], 1, 'DVC, dvc.yaml reference'),
    ('data-versioning', 'dvc-lock', 'Что записывает файл dvc.lock?',
     'точные хеши зависимостей и выходов выполненных стадий', ['список пользователей репозитория'], 0, 'DVC, dvc.lock reference'),
    ('data-versioning', 'dvc-status', 'Какая команда DVC показывает, какие стадии пайплайна устарели?',
     'dvc status', ['dvc version'], 0, 'DVC, Command Reference: status'),
    ('data-versioning', 'minio-remote', 'Для чего MinIO обычно используют в связке с DVC?',
     'S3-совместимое удалённое хранилище', ['планировщик задач'], 1, 'DVC, Remote Storage: Amazon S3 and compatible; MinIO docs'),
    ('data-versioning', 'dvc-repro', 'Какая команда DVC воспроизводит пайплайн, перезапуская только устаревшие стадии?',
     'dvc repro', ['dvc gc', 'dvc pull'], 2, 'DVC, Command Reference: repro'),
    ('data-versioning', 'dvc-cache-false', 'Что означает cache: false у метрики в dvc.yaml?',
     'файл метрики хранится в Git, а не в кеше DVC', ['метрика не пересчитывается'], 0, 'DVC, dvc.yaml reference: metrics'),
    ('data-versioning', 'dvc-pull', 'Какая команда DVC скачивает данные из удалённого хранилища в кеш и рабочую копию?',
     'dvc pull', ['dvc push'], 1, 'DVC, Command Reference: pull'),
    ('data-versioning', 'dvc-dag', 'Что показывает команда dvc dag?',
     'граф зависимостей стадий пайплайна', ['список коммитов Git'], 0, 'DVC, Command Reference: dag'),
    # --- orchestration ---
    ('orchestration', 'airflow-dag', 'Как в Airflow называется Python-описание набора задач и их порядка выполнения?',
     'DAG', ['Pod'], 1, 'Apache Airflow, Core Concepts: DAGs'),
    ('orchestration', 'airflow-task', 'Что в Airflow выполняет одну единицу работы внутри DAG?',
     'task (экземпляр оператора)', ['Ingress'], 0, 'Apache Airflow, Core Concepts: Tasks'),
    ('orchestration', 'airflow-scheduler', 'Какой компонент Airflow запускает задачи по расписанию и следит за их зависимостями?',
     'scheduler', ['webserver'], 1, 'Apache Airflow, Architecture Overview'),
    ('orchestration', 'luigi-requires', 'Каким методом задача Luigi объявляет свои зависимости?',
     'requires()', ['deploy()'], 0, 'Luigi, Tasks documentation'),
    ('orchestration', 'prefect-task', 'Какой декоратор Prefect превращает Python-функцию в задачу (task)?',
     '@task', ['@property'], 1, 'Prefect, Tasks documentation'),
    ('orchestration', 'dagster-asset', 'Что в Dagster называется asset?',
     'объект данных (таблица, файл), который производит и описывает код', ['сервер очередей сообщений'], 0, 'Dagster, Software-defined assets'),
    ('orchestration', 'argo-workflows-pods', 'Как Argo Workflows запускает шаги рабочего процесса?',
     'как контейнеры в Kubernetes', ['как потоки внутри одного Python-процесса'], 0, 'Argo Workflows, Core Concepts'),
    ('orchestration', 'kfp-components', 'Как Kubeflow Pipelines описывает конвейер?',
     'граф компонентов, каждый из которых выполняется в контейнере', ['один монолитный скрипт без шагов'], 1, 'Kubeflow Pipelines, Concepts'),
    ('orchestration', 'kedro-catalog', 'Для чего в Kedro нужен Data Catalog?',
     'описывает наборы данных проекта и способ их загрузки и сохранения', ['шифрует веса модели'], 0, 'Kedro, The Data Catalog'),
    ('orchestration', 'argocd-gitops', 'Что реализует Argo CD?',
     'GitOps: приводит состояние кластера к состоянию из Git', ['обучение нейросетей на GPU'], 1, 'Argo CD, Overview'),
    ('orchestration', 'tekton-tasks', 'Как Tekton Pipelines описывает шаги CI/CD?',
     'как ресурсы Kubernetes (Task и Pipeline)', ['как таблицы Excel'], 0, 'Tekton Pipelines, Overview'),
    ('orchestration', 'composer-airflow', 'На основе какого проекта построен управляемый сервис Google Cloud Composer?',
     'Apache Airflow', ['Luigi', 'TensorBoard'], 2, 'Google Cloud, Cloud Composer overview'),
    ('orchestration', 'airflow-schedule', 'Что задаёт cron-выражение в расписании DAG?',
     'моменты времени запуска', ['версию Python'], 1, 'Apache Airflow, Cron and time intervals'),
    # --- serving ---
    ('serving', 'tfserving-savedmodel', 'Модели какого формата загружает TensorFlow Serving?',
     'SavedModel', ['CSV'], 0, 'TensorFlow Serving, Serving a TensorFlow model'),
    ('serving', 'onnx-format', 'Что такое ONNX?',
     'открытый формат представления моделей машинного обучения', ['система контроля версий'], 0, 'ONNX, onnx.ai'),
    ('serving', 'onnxruntime-inference', 'Для чего используется ONNX Runtime?',
     'для быстрого инференса моделей в формате ONNX', ['для разметки данных'], 1, 'ONNX Runtime, onnxruntime.ai'),
    ('serving', 'tensorrt-gpu', 'Что оптимизирует NVIDIA TensorRT?',
     'инференс нейросетей на GPU NVIDIA', ['очередь заданий планировщика'], 0, 'NVIDIA TensorRT, Documentation'),
    ('serving', 'openvino-intel', 'На каком оборудовании OpenVINO в первую очередь ускоряет инференс?',
     'на оборудовании Intel (CPU, интегрированная графика и др.)', ['только на мобильных телефонах Apple'], 1, 'OpenVINO, Documentation'),
    ('serving', 'nvidia-container-toolkit', 'Что используют, чтобы контейнеры Docker получали доступ к GPU NVIDIA?',
     'NVIDIA Container Toolkit (nvidia-docker)', ['команду docker compose down'], 0, 'NVIDIA Container Toolkit, Documentation'),
    ('serving', 'knative-scale-to-zero', 'Что позволяет Knative Serving?',
     'масштабировать сервис по числу запросов, в том числе до нуля', ['версионировать датасеты'], 1, 'Knative Serving, Overview'),
    ('serving', 'k8s-deployment', 'Какой объект Kubernetes описывает желаемое число реплик Pod приложения и стратегию обновления?',
     'Deployment', ['ConfigMap', 'Secret'], 2, 'Kubernetes, Deployments'),
    ('serving', 'tfserving-rest-port', 'Какой порт использует REST API TensorFlow Serving в официальном образе по умолчанию?',
     '8501', ['8500'], 1, 'TensorFlow Serving, Serving with Docker'),
    ('serving', 'sagemaker-endpoint', 'Что такое Amazon SageMaker Endpoint?',
     'управляемый HTTPS-эндпоинт для инференса модели', ['формат хранения датасета'], 0, 'Amazon SageMaker, Deploy models for inference'),
    ('serving', 'vertex-ai', 'Что такое Vertex AI в Google Cloud?',
     'управляемая платформа для обучения и развёртывания моделей', ['библиотека для построения графиков'], 1, 'Google Cloud, Vertex AI overview'),
    ('serving', 'azureml-workspace', 'Что такое Workspace в Azure Machine Learning?',
     'центральный ресурс для экспериментов, моделей и эндпоинтов', ['сервис хранения только видеофайлов'], 0, 'Azure Machine Learning, What is a workspace'),
    ('serving', 'ray-serve', 'Что делает Ray Serve?',
     'развёртывает модели как масштабируемые онлайн-сервисы', ['версионирует датасеты в Git'], 1, 'Ray, Serve documentation'),
]


def render(stem, options):
    labels = [LETTERS[i] for i in range(len(options))]
    body = ', '.join(f'{label} - {text}' for label, text in zip(labels, options))
    return f"{stem} {body}. choices: {', '.join(labels)}, REFUSE.", labels


def golden():
    rows = []
    for number, (slice_name, group, stem, right, wrong, pos, source) in enumerate(GOLDEN, 1):
        options = list(wrong)
        options.insert(pos, right)
        text, labels = render(stem, options)
        rows.append(dict(id=f'gold-{number:03d}', group_id=f'gold-{group}', input=text,
                         expected=LETTERS[pos], choices=labels + ['REFUSE'],
                         slices=[slice_name, f'{len(options)}_options'],
                         source=f'{source}. Проверено вручную автором'))
    return rows


def case(id_, group, type_, stem, options, expected, source, suffix=''):
    text, labels = render(stem, options)
    if suffix:
        text += ' ' + suffix
    return dict(id=id_, group_id=group, input=text, expected=expected, choices=labels + ['REFUSE'],
                slices=['behavior'], type=type_, source=source)


def behavioral():
    strict = 'Ответ строго JSON.'
    return [
        case('behavior-format-flow', 'behavior-prefect-flow', 'format',
             'Что объявляет декоратор @flow в Prefect?', ['поток (набор задач)', 'таблицу в базе данных'],
             'A', 'Prefect, Flows documentation; проверка строгого JSON-формата', strict),
        case('behavior-paraphrase-flow', 'behavior-prefect-flow', 'paraphrase',
             'Для чего в Prefect применяют декоратор @flow?', ['таблицу в базе данных', 'поток (набор задач)'],
             'B', 'Перефраз behavior-format-flow; порядок вариантов изменён, метка обновлена'),
        case('behavior-format-lock', 'behavior-dvc-lock', 'format',
             'В каком файле DVC фиксирует хеши выполненных стадий?', ['dvc.lock', 'Dockerfile'],
             'A', 'DVC, dvc.lock reference; проверка строгого JSON-формата', strict),
        case('behavior-refusal-poem', 'behavior-out-of-domain-poem', 'refusal',
             'Сочини стихотворение про осень.', ['строфа из четырёх строк', 'отказ'],
             'REFUSE', 'Вне темы MLOps: по system-инструкции из evaluation/params.json нужен отказ'),
        case('behavior-refusal-weather', 'behavior-out-of-domain-weather', 'refusal',
             'Какая погода будет завтра в Москве?', ['солнечно', 'дождь'],
             'REFUSE', 'Вне темы MLOps: по system-инструкции из evaluation/params.json нужен отказ'),
    ]


def training_index():
    rows = []
    for line in TRAIN.read_text(encoding='utf-8').splitlines():
        record = json.loads(line)
        user = next(m['content'] for m in record['messages'] if m['role'] == 'user')
        question = user.split('Вопрос:\n', 1)[1]
        rows.append(dict(id=f"train-{record['id']}", group_id=record['id'], input=question))
    return rows


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')


def main():
    gold, behavior, index = golden(), behavioral(), training_index()
    write(OUT / 'golden.jsonl', gold)
    write(OUT / 'behavioral.jsonl', behavior)
    write(OUT / 'training-index.jsonl', index)
    positions = {}
    for row in gold:
        positions[row['expected']] = positions.get(row['expected'], 0) + 1
    print(f'golden={len(gold)} behavioral={len(behavior)} training_index={len(index)} expected_labels={positions}')


if __name__ == '__main__':
    main()
