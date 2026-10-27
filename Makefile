.PHONY: up down sweep promote predict check-storage report candidate baseline-init evaluate eval check-eval check-negative check-hw6 check-hw6-defects check-hw6-all check-all baseline-update predict-candidate train train-all train-freeze plot compare check-train install generate bench inspect repro v1 v2 diff dag diversity contamination check check-data clean sample tokenize check-tokenize

install:
	uv sync

generate:
	uv run python -m src.generate

bench:
	uv run python -m src.bench

inspect:
	uv run python -m src.inspect_model

repro:
	uv run dvc repro

v1:
	uv run python -m src.set_version v1
	uv run dvc repro

v2:
	uv run python -m src.set_version v2
	uv run dvc repro

A ?= v1
B ?= v2

diff:
	uv run dvc metrics diff $(A) $(B)

dag:
	uv run dvc dag

diversity:
	uv run python -m src.diversity

contamination:
	uv run python -m src.check_contamination

check-data:
	bash tests/check_data.sh

sample:
	uv run python -m src.make_sample

tokenize:
	uv run python -m src.tokenize_data

check-tokenize:
	bash tests/check_tokenize.sh

check-train:
	bash tests/check_train.sh

# Два прогона: адаптеры на всех слоях и с замороженными первыми 14.
train: train-all train-freeze plot

train-all:
	uv run python -m src.train --variant all_layers

train-freeze:
	uv run python -m src.train --variant freeze14

plot:
	uv run python -m src.plot

# Базовая модель против адаптера на пяти фиксированных промптах.
compare:
	uv run python -m src.compare --variant all_layers

check:
	bash tests/check.sh
	bash tests/check_anatomy.sh
	bash tests/check_data.sh
	bash tests/check_tokenize.sh
	bash tests/check_train.sh

clean:
	rm -rf docs/bench.json docs/report.json metrics/tokenize.json docs/tokenize_report.md out1.txt out2.txt params.yaml.bak params.yaml.orig src/__pycache__ tests/__pycache__ data models metrics/train_*.json metrics/compare_*.json docs/curves.png docs/compare.md .check_*.log

# --- ДЗ 6: MLflow-эксперименты (сервер mlops-hw6, порт 5006) ---
up:
	docker compose -p mlops-hw6 up --build -d --wait
down:
	docker compose -p mlops-hw6 down
sweep:
	uv run python -m src.sweep
promote:
	uv run python -m evaluation.cli promote
predict:
	uv run python -m src.predict --uri models:/my-smollm-adapter@champion --prompt "Какой показатель отражает запасы железа?"
check-hw6:
	bash tests/check_hw6.sh
check-hw6-defects:
	uv run python -m unittest discover -s tests -p 'test_hw6_defects.py' -v
check-storage:
	uv run python tests/check_storage.py
report:
	uv run python -m src.sweep_report
candidate:
	uv run python -m evaluation.cli candidate
baseline-init:
	uv run python -m evaluation.cli baseline-init
evaluate:
	uv run python -m evaluation.cli evaluate
eval:
	uv run python -m evaluation.cli gate
check-eval:
	uv run python -m unittest discover -s tests -p 'test_*.py' -v
check-negative:
	uv run python -m evaluation.cli negative
check-hw6-all: check-hw6 check-eval
check-all: check-hw6 check-eval
baseline-update:
	uv run python -m evaluation.cli baseline-update
predict-candidate:
	uv run python -m src.predict --uri models:/my-smollm-adapter@candidate --prompt "Выберите правильный вариант."
