# Базовая модель против адаптера

Адаптер: `models/adapter_all_layers`. Генерация жадная, до 40 токенов.

## 1. How to store and retrieve figures as artifacts in MetaFlow?

How do I save a plot as a data artifact in MetaFlow? Plotting libraries usually have you write out to a file on disk. How do I view the figure afterwards?

**База:**

> In **MetaFlow**, figures (plots) can be stored and retrieved as **data artifacts**. Here's how you can do it: /  / --- /  / ### **1. Store a Plot as a Data Artifact

**Адаптер:**

> You can use the `MetaFlow` class to create a new `Artifact` object, and then use the `save()` method to save the figure to a file. For example: /  / ```python

## 2. Best Config after Hyperparameter Search with Ray Tune

I just run my first Ray Tune. I got nice terminal output an all that but now I'm wondering: Which configuration gave me the best score?

I see that there are a ton of results files but is there an easy way to get the best config?

**База:**

> Вот, что можно сделать, чтобы определить лучшую конфигурацию после выполнения поиска параметров с **Ray Tune**: /  / ### 1. **П

**Адаптер:**

> I think the best way to get the best configuration is to run the following command: /  / `tune run --config-file config.yaml --num-workers 1 --num-threads 1 --num-

## 3. How to set a tag at the experiment level in MLFlow

I can see that an experiment in MLFlow can have tags (like runs can have tags).
I'm able to set a run's tag using `mlflow.set_tag`, but how do I set it for an experiment?

**База:**

> In MLFlow, you can set a **tag** at the **experiment level** using the `mlflow.set_experiment_tag` function. This allows you to assign a tag to an entire experiment,

**Адаптер:**

> You can set an experiment's tag using `mlflow.set_experiment_tag`: /  /     mlflow.set_experiment_tag("tag1", "value1") /     mlflow.set_experiment_tag("tag2",

## 4. Log Pickle files as a part of Mlflow run

I am running an MLflow experiment as a part of it I would like to log a few artifacts as a python pickle. 

Ex: Trying out different categorical encoders, so wanted to log the encoder objects as a pickle file.


Is there a way to achieve this?

**База:**

> Да, вы можете сохранить pickle-файлы в MLOps, используя инструменты MLOps, таких как `mlflow.log_artifacts` или `

**Адаптер:**

> You can use the `mlflow.log_artifacts` function to log the pickle files.  /  / For example: /  / ```python / from mlflow import log_artifacts /  / log_artifacts( /     artifact_path

## 5. Difference between tracking_uri and the backend store uri in MLFLOW

I am using Mlflow for my project hosting it in an EC2 instance. I was wondering in MlFlow what is the difference between the backend_store_uri we set when we launch the server and the trarcking_uri ? 

Thanks,

**База:**

> В MLOps, `tracking_uri` и `backend_store_uri` — это два разных аспекта, связанных с **множеством хранилищ** в систем

**Адаптер:**

> The backend store URI is the URL where MLFlow will store the data that you want to track. The tracking URI is the URL where MLFlow will store the data that you want to track.
