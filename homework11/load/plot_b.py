#!/usr/bin/env python3
"""Render observed B data; never include interrupted or unfinished runs.

Usage: python3 homework11/load/plot_b.py --results results --output homework11/figures
Requires matplotlib. Writes figures plus a machine-readable audit of inclusions.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

os.environ.setdefault('MPLCONFIGDIR', str(Path(tempfile.gettempdir()) / 'hw11-matplotlib'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def quantile(values, q):
    values = sorted(x for x in values if x is not None)
    return values[max(0, math.ceil(q * len(values)) - 1)] if values else None


def read_run(folder):
    """Client journals are authoritative; a partial final JSON line is incomplete."""
    match = re.fullmatch(r'b-(uniform|skew)-(balanced|sticky)-(\d+)', folder.name)
    if not match:
        raise ValueError('interrupted or unrecognized run name')
    raw = (folder / 'schedule.jsonl').read_bytes()
    schedule = [json.loads(line) for line in raw.splitlines() if line]
    events = []
    starts = []
    for client in range(4):
        client_events = [json.loads(line) for line in (folder / f'client-{client}.jsonl').read_text().splitlines() if line]
        run_starts = [e for e in client_events if e['event'] == 'run_start']
        run_ends = [e for e in client_events if e['event'] == 'run_end']
        if len(run_starts) != 1 or len(run_ends) != 1:
            raise ValueError(f'client {client}: missing/duplicate run_start or run_end')
        start = run_starts[0]
        if start['client'] != client or start['schedule_sha256'] != hashlib.sha256(raw).hexdigest():
            raise ValueError(f'client {client}: identity/schedule mismatch')
        expected = {r['id'] for r in schedule if r['client'] == client}
        planned = [e for e in client_events if e['event'] == 'planned']
        attempts = [e for e in client_events if e['event'] == 'attempt_start']
        ends = [e for e in client_events if e['event'] == 'attempt_end']
        if {e['id'] for e in planned} != expected or {e['id'] for e in attempts} != expected:
            raise ValueError(f'client {client}: unsent/missing planned requests')
        if Counter(e['request_id'] for e in attempts) != Counter(e['request_id'] for e in ends):
            raise ValueError(f'client {client}: unfinished attempts')
        if any(n != 1 for n in Counter(e['request_id'] for e in ends).values()):
            raise ValueError(f'client {client}: duplicate attempt')
        if run_ends[0]['planned'] != len(expected):
            raise ValueError(f'client {client}: run_end planned mismatch')
        events.extend(client_events)
        starts.append(start)
    if len({s['start_at'] for s in starts}) != 1 or len({s['url'] for s in starts}) != 1:
        raise ValueError('clients have different origins or target services')
    offsets = sorted(r['offset_seconds'] for r in schedule)
    # Our fixed-rate schedule omits the right endpoint: [0, duration).
    steps = [b-a for a,b in zip(offsets, offsets[1:])]
    if not steps or max(steps)-min(steps) > 1e-6:
        raise ValueError('cannot infer fixed-rate schedule duration')
    duration = offsets[-1] + steps[0] - offsets[0]
    if duration < 120 - 1e-6:
        raise ValueError(f'measurement schedule too short: {duration:g}s')
    ends = [e for e in events if e['event'] == 'attempt_end']
    good = [e for e in ends if e['outcome'] == 'completed']
    completions = {e['request_id']: e['data'] for e in events if e['event'] == 'stream_complete'}
    if any(e['request_id'] not in completions or not e.get('stream_complete') for e in good):
        raise ValueError('completed attempt lacks observed stream completion')
    pods = {}
    for e in good:
        pod = e['identity']['pod']
        item = pods.setdefault(pod, {'ttfc_seconds': [], 'queue_seconds': []})
        item['ttfc_seconds'].append(e['ttfc_seconds'])
        # Server queue is observed only for completed streams; never substitute zero.
        data = completions.get(e['request_id'], {})
        if data.get('queue_seconds') is not None:
            item['queue_seconds'].append(data['queue_seconds'])
    return dict(name=folder.name, traffic=match[1], policy=match[2], repeat=int(match[3]),
                duration_seconds=duration, rate_rps=len(schedule)/duration,
                schedule_sha256=hashlib.sha256(raw).hexdigest(), clients=starts,
                outcomes=dict(Counter(e['outcome'] for e in ends)), planned=len(schedule),
                attempts=len(ends), successful=len(good),
                error_fraction=(len(ends)-len(good))/len(ends) if ends else None,
                ttfc_p50_seconds=quantile([e['ttfc_seconds'] for e in good], .5),
                ttfc_p95_seconds=quantile([e['ttfc_seconds'] for e in good], .95),
                pod_samples=pods, evidence=str(folder))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, default=Path('results'))
    parser.add_argument('--output', type=Path, default=Path('homework11/figures'))
    args = parser.parse_args()
    included, skipped = [], []
    for folder in sorted(args.results.glob('b-*')):
        if not folder.is_dir() or folder.name == 'b-config':
            continue
        try:
            included.append(read_run(folder))
        except (OSError, ValueError, KeyError, TypeError) as error:
            skipped.append(dict(name=folder.name, reason=str(error)))
    args.output.mkdir(parents=True, exist_ok=True)
    audit = dict(method='Nearest-rank quantiles; successful completed streams only. Queue is server-reported; TTFC is client-observed. No unfinished/interrupted runs.',
                 conditions='CPU simulated backend; per-run schedule/client settings and source paths below. Check saved Helm values and b-config before comparing.',
                 included=included, skipped=skipped)
    (args.output / 'b-plot-data.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2)+'\n')
    plt.rcParams.update({'font.size': 10, 'svg.fonttype': 'none'})
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
    for ax, traffic in zip(axes, ['uniform', 'skew']):
        for policy, shift, color in [('balanced', -.06, '#17678a'), ('sticky', .06, '#bd592e')]:
            runs = sorted([r for r in included if r['traffic']==traffic and r['policy']==policy], key=lambda r:r['repeat'])
            for metric, marker, label in [('ttfc_p50_seconds', 'o', 'p50'), ('ttfc_p95_seconds', '^', 'p95')]:
                valid = [r for r in runs if r[metric] is not None]
                ax.scatter([r['repeat']+shift for r in valid], [r[metric] for r in valid], color=color, marker=marker, label=f'{policy} {label}')
        ax.set(title=f'{traffic}: observed completed runs', xlabel='Repeat (missing runs left blank)', xticks=[1,2,3], xlim=(.6,3.4))
        ax.grid(axis='y', alpha=.2)
    axes[0].set_ylabel('Client TTFC (seconds)')
    axes[1].legend(fontsize=8)
    fig.suptitle('B: policy comparison — nearest-rank quantiles of successful streams')
    fig.text(.02,.01,'Conditions and sample/error counts: b-plot-data.json. Only complete ≥120 s schedules; interrupted runs excluded.', fontsize=9)
    fig.tight_layout(rect=(0,.04,1,.94))
    fig.savefig(args.output / 'b-policy-repeats.svg')
    plt.close(fig)
    # One row per run preserves the independent repeats instead of pooling them.
    fig, axes = plt.subplots(max(1,len(included)), 2, figsize=(12,max(3,2.5*len(included))), squeeze=False)
    for row, run in enumerate(included):
        for col, metric in enumerate(['ttfc_seconds','queue_seconds']):
            ax=axes[row,col]
            pods=sorted(run['pod_samples'])
            labels=[p.rsplit('-',1)[-1] for p in pods]
            samples=[run['pod_samples'][p][metric] for p in pods]
            positions=[i+1 for i,v in enumerate(samples) if v]
            if positions:
                ax.boxplot([v for v in samples if v], positions=positions, whis=(5,95), showfliers=False)
            ax.set_xticks(range(1,len(pods)+1), [f'{p}\nn={len(v)}' for p,v in zip(labels,samples)])
            ax.set_ylabel('Seconds')
            ax.set_title(f"{run['name']} · {'client TTFC' if col==0 else 'server queue'} · errors {run['attempts']-run['successful']}/{run['attempts']}", fontsize=9)
            ax.grid(axis='y',alpha=.2)
    if not included:
        for ax in axes.flat:
            ax.text(.5,.5,'No complete eligible runs',ha='center',va='center')
    fig.suptitle('B: per-Pod successful-stream latency — box median/IQR, whiskers p5–p95')
    fig.tight_layout(rect=(0,.01,1,.98))
    fig.savefig(args.output / 'b-pod-latency-queue.svg')
    plt.close(fig)
    print(json.dumps({'included':[r['name'] for r in included], 'skipped':skipped, 'output':str(args.output)}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
