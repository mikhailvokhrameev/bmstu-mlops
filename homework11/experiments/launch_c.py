#!/usr/bin/env python3
"""Reuse cluster collector with create: large schedules must not double in annotations."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'load'))
import cluster
original_run = cluster.subprocess.run

def create_instead_of_apply(argv, **kwargs):
    if argv[:1] == ['kubectl'] and 'apply' in argv:
        argv = [value if value != 'apply' else 'create' for value in argv]
    return original_run(argv, **kwargs)

cluster.subprocess.run = create_instead_of_apply
cluster.main()
