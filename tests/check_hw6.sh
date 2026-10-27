#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."
uv run python tests/check.py
status=$?
# Отпечаток считается ТОЛЬКО по пяти файлам приёмки hw67. Исходный check.sh хранится
# дословно в tests/hw67_check.sh и хэшируется под именем check.sh, как в оригинале.
uv run python - <<'PY'
import hashlib
from pathlib import Path
h = hashlib.sha256()
files = {n: Path('tests') / n for n in ('check.py', 'check_storage.py', 'test_eval.py', 'test_pipeline.py')}
files['check.sh'] = Path('tests/hw67_check.sh')
for name in sorted(files):
    h.update(name.encode())
    h.update(files[name].read_bytes())
print('Отпечаток пяти файлов hw67 (tests/):', h.hexdigest()[:12])
PY
exit "$status"
