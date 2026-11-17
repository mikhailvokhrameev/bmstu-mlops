#!/usr/bin/env python3
"""Summarize requests separately from attempts, preserving stream failures."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path


def summarize(events):
    planned = [e for e in events if e['event']=='planned']
    starts = [e for e in events if e['event']=='attempt_start']
    ends = [e for e in events if e['event']=='attempt_end']
    counts = Counter(e['outcome'] for e in ends)
    def percentile(values, q):
        values=sorted(v for v in values if v is not None)
        return values[max(0,math.ceil(q*len(values))-1)] if values else None
    good = [e for e in ends if e['outcome']=='completed']
    sent_ids = {e['id'] for e in starts}
    return dict(planned=len(planned), sent_requests=len(sent_ids), unsent=len({e['id'] for e in planned}-sent_ids),
                attempts=len(starts), unfinished_attempts=len(starts)-len(ends), outcomes=dict(counts),
                incomplete_streams=sum(e['incomplete_stream'] for e in ends), successful_samples=len(good),
                attempt_error_fraction=(len(ends)-len(good))/len(ends) if ends else None,
                timeout_fraction=counts['timeout']/len(ends) if ends else None,
                ttfc_p50=percentile([e['ttfc_seconds'] for e in good],.5),ttfc_p95=percentile([e['ttfc_seconds'] for e in good],.95),
                total_p95=percentile([e['total_seconds'] for e in good],.95),
                pods=dict(Counter(e['identity'].get('pod','unknown') for e in ends)),
                lag_p95=percentile([e['lag_seconds'] for e in starts],.95))

if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('journal');a=p.parse_args()
    print(json.dumps(summarize([json.loads(line) for line in Path(a.journal).read_text().splitlines()]),indent=2))
