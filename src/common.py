"""Общий контракт эксперимента: конфиг, версии входов и подключение."""
import hashlib
import json
import os
import subprocess
from pathlib import Path

import mlflow
from src import config


def load_params() -> dict:
    """Вид ДЗ 6: общий params.yaml; модель и данные берутся из общих секций,
    остальное — из секции hw6. Секции ДЗ 1-5 (train, lora) не затрагиваются."""
    base = config.load_params()
    hw6 = base["hw6"]
    return {
        "model": {**base["model"], **hw6.get("model", {})},
        "data": base["data"],
        **{k: v for k, v in hw6.items() if k != "model"},
    }


def connect(params):
    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", params["tracking"]["uri"]))
    mlflow.set_experiment(params["tracking"]["experiment"])


def digest(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def code_digest():
    h = hashlib.sha256()
    for p in sorted(Path("src").glob("*.py")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def provenance(params):
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    return {
        "git_sha": sha,
        "git_dirty": str(bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())),
        "code_sha256": code_digest(),
        "data_train_sha256": digest(params["data"]["train"]),
        "data_val_sha256": digest(params["data"]["val"]),
        "config_sha256": hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest(),
    }


def save_json(path, value):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
