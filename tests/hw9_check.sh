#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python="${PYTHON:-.venv/bin/python}"
"$python" -m unittest discover -s tests_hw8910 -p 'test_release.py' -v
"$python" -m unittest discover -s tests_hw8910 -p 'test_real_release.py' -v
"$python" -m unittest discover -s tests -p 'test_eval.py' -v
"$python" -m unittest discover -s tests -p 'test_pipeline.py' -v
"$python" -m unittest discover -s tests -p 'test_hw6_defects.py' -v
