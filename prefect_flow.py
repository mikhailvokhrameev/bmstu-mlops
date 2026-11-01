"""Prefect orchestration of the synthetic release lab, without model weights.

Preparation uses lab's durable, content-addressed files rather than Prefect's
input cache: hashing only the workspace path would hide changed file contents.
The remaining tasks always run so validation and publication reconciliation
cannot be bypassed by a cached task state. Only small descriptors cross tasks.
"""
import argparse
from pathlib import Path

from prefect import flow, task

import lab
import release


@task(name='prepare-data', cache_policy=None)
def prepare_data(root: Path):
    return lab.prepare(root)


@task(name='get-artifact', cache_policy=None)
def get_artifact(root: Path, report_name: str):
    return lab.read(root / 'manifest-B.json'), lab.read(root / report_name)


@task(name='validate-release', cache_policy=None)
def check_release(artifact):
    manifest, report = artifact
    release.validate_release(manifest, report)
    return artifact


@task(name='publish-release', cache_policy=None)
def publish_release(root: Path, prepared, artifact):
    manifest, report = artifact
    lab.publish_prepared(root, prepared, manifest, report)


@flow(name='synthetic-release')
def release_flow(root: Path, report_name: str = 'report-B.json', fail_after_prepare: bool = False):
    lab.start_attempt(root, report_name)
    prepared = prepare_data(root)
    if fail_after_prepare:
        raise RuntimeError('Инъекция сбоя после подготовки')
    artifact = get_artifact(root, report_name)
    accepted = check_release(artifact)
    publish_release(root, prepared, accepted)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, default=Path('.lab'))
    parser.add_argument('--report', default='report-B.json')
    parser.add_argument('--fail-after-prepare', action='store_true')
    args = parser.parse_args()
    release_flow(args.workspace, args.report, args.fail_after_prepare)


if __name__ == '__main__':
    main()
