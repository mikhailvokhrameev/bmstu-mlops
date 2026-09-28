.PHONY: install generate bench inspect repro v1 v2 diff dag diversity contamination check check-data clean sample tokenize check-tokenize

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

check:
	bash tests/check.sh
	bash tests/check_anatomy.sh
	bash tests/check_data.sh
	bash tests/check_tokenize.sh

clean:
	rm -rf docs/bench.json docs/report.json metrics/tokenize.json docs/tokenize_report.md out1.txt out2.txt params.yaml.bak params.yaml.orig src/__pycache__ tests/__pycache__ data
