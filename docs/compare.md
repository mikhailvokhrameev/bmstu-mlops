# Базовая модель против адаптера

Адаптер: `models/adapter_all_layers`. Генерация жадная, до 40 токенов.

## 1. How to store and retrieve figures as artifacts in MetaFlow?

How do I save a plot as a data artifact in MetaFlow? Plotting libraries usually have you write out to a file on disk. How do I view the figure afterwards?

**База:**

> Теперь вопрос называется "netflix-metaflow" и последним пои

**Адаптер:**

> You can use the `plot` function from the `plotly` library. /  / ```python / import plotly.graph_objs as go /  / fig = go.Figure() / fig

## 2. Best Config after Hyperparameter Search with Ray Tune

I just run my first Ray Tune. I got nice terminal output an all that but now I'm wondering: Which configuration gave me the best score?

I see that there are a ton of results files but is there an easy way to get the best config?

**База:**

> Ты инженер по MLOps и отвечаешь на технические вопросы с

**Адаптер:**

> I'm not sure if you're looking for a specific configuration or just a general question. /  / I would recommend checking out the Ray Tune documentation. /  / I also have a question about the

## 3. How to set a tag at the experiment level in MLFlow

I can see that an experiment in MLFlow can have tags (like runs can have tags).
I'm able to set a run's tag using `mlflow.set_tag`, but how do I set it for an experiment?

**База:**

> Мальше, вы можете понять, что вы можете создать и

**Адаптер:**

> You can set a tag in MLFlow using the `mlflow.set_tag` method. /  / ```python / mlflow.set_tag( /     "run_name", /     "

## 4. Log Pickle files as a part of Mlflow run

I am running an MLflow experiment as a part of it I would like to log a few artifacts as a python pickle. 

Ex: Trying out different categorical encoders, so wanted to log the encoder objects as a pickle file.


Is there a way to achieve this?

**База:**

> Yes, you can use the `pickle` module in Python to serialize and deserialize Python objects. Here's an example of how you can log the encoder objects as a pickle file: /  / ```

**Адаптер:**

> You can use the `pickle` module in MLflow. /  / ```python / from mlflow.core import encoder / from mlflow.core import encoder_encoder /  / encoder = encoder.

## 5. Difference between tracking_uri and the backend store uri in MLFLOW

I am using Mlflow for my project hosting it in an EC2 instance. I was wondering in MlFlow what is the difference between the backend_store_uri we set when we launch the server and the trarcking_uri ? 

Thanks,

**База:**

> In Mlflow, the backend_store_uri and the trarcking_uri are two different URI formats used to represent the backend store. /  / The backend_store_uri is a

**Адаптер:**

> The backend store uri is the URI that is used to retrieve the backend data from the backend store. The backend store uri is the URI that is used to retrieve the data from the backend store.
