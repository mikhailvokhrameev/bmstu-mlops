.PHONY: install generate bench inspect check clean

install:
	uv sync

generate:
	uv run python -m src.generate

bench:
	uv run python -m src.bench

inspect:
	uv run python -m src.inspect_model

check:
	bash tests/check.sh
	bash tests/check_anatomy.sh

clean:
	rm -rf docs/bench.json docs/report.json out1.txt out2.txt params.yaml.bak src/__pycache__ tests/__pycache__
