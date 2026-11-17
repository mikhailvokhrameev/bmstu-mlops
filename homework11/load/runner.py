#!/usr/bin/env python3
"""Dependency-free open-loop NDJSON stream load client. Times are client monotonic."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import random
import socket
import sys
import threading
import time
from urllib.parse import urlsplit


def schedule(duration=120, rate=2, skew=False, seed=110):
    if duration <= 0 or rate <= 0:
        raise ValueError('duration and rate must be positive')
    rng = random.Random(seed)
    owners = [0]*12 + [1, 2, 3] if skew else [0, 1, 2, 3]
    rows = []
    for i in range(math.ceil(duration*rate)):
        # Each complete 15-request block has exactly 80% from client 0.
        if i % len(owners) == 0:
            block = owners[:]
            rng.shuffle(block)
        client = block[i % len(owners)]
        rows.append(dict(id=f'r{i:06}', client=client, offset_seconds=i/rate,
                         payload=dict(prompt=f'Continue dialogue {i%16}', history=[dict(role='user', content=f'CONTROL-{i%16:02} '+ 'prefix '*64)], max_new_tokens=8)))
    return rows


class Journal:
    def __init__(self, path):
        self.file = open(path, 'w', buffering=1)
        self.lock = threading.Lock()
    def emit(self, event, **fields):
        with self.lock:
            line = json.dumps(dict(event=event, **fields), ensure_ascii=False)+'\n'
            self.file.write(line)
            sys.stdout.write(line)
            sys.stdout.flush()


def attempt(row, url, journal, scheduled, timeout=60, number=1, run='run', keepalive=False, connection=None):
    parsed = urlsplit(url)
    if parsed.scheme != 'http':
        raise ValueError('Only HTTP inside the cluster is supported')
    started = time.monotonic()
    request_id = f'{run}-{row["id"]}-a{number}'
    base = dict(id=row['id'], request_id=request_id, client=row['client'], attempt=number,
                scheduled_monotonic=scheduled, sent_monotonic=started, sent_wall=time.time(),
                lag_seconds=started-scheduled)
    journal.emit('attempt_start', **base)
    conn = connection or http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout)
    chunks = 0
    first = previous = None
    status = None
    identity = {}
    outcome = 'error'
    error = None
    complete = False
    response = None
    deadline = started + timeout
    expired = threading.Event()
    timer = None
    wire_socket = None
    def expire():
        expired.set()
        if wire_socket is not None:
            try: wire_socket.shutdown(socket.SHUT_RDWR)
            except OSError: pass
    def remaining(set_socket=True):
        seconds = deadline-time.monotonic()
        if expired.is_set() or seconds <= 0: raise TimeoutError('Total request deadline')
        # Content-Length EOF may release the final socket reference before the
        # next loop checks its deadline. Buffered EOF needs no socket update.
        if set_socket and wire_socket.fileno() >= 0: wire_socket.settimeout(seconds)
        return seconds
    try:
        if conn.sock is None: conn.connect()
        # Retain the live socket: getresponse detaches conn.sock for Connection: close.
        wire_socket = conn.sock
        timer = threading.Timer(remaining(), expire)
        timer.daemon = True
        timer.start()
        conn.request('POST', parsed.path.rstrip('/')+'/generate/stream', json.dumps(row['payload']),
                     {'Content-Type':'application/json', 'X-Request-ID':request_id,
                      'Connection':'keep-alive' if keepalive else 'close'})
        response = conn.getresponse()
        remaining()
        status = response.status
        if status != 200:
            remaining()
            body = response.read(65536).decode(errors='replace')
            remaining(False)
            try:
                identity = json.loads(body).get('detail', {})
                if not isinstance(identity, dict): identity = {}
            except ValueError:
                pass
            outcome = 'rejected' if status in (429, 503) else 'http_error'
            journal.emit('http_error', **base, status=status, body=body)
        else:
            while True:
                remaining()
                line = response.readline(1024*1024)
                remaining(False)
                if not line: break
                now = time.monotonic()
                journal.emit('wire_line', **base, received_monotonic=now, raw_utf8=line.decode('utf-8', errors='replace'), raw_hex=line.hex())
                event = json.loads(line)
                if complete: raise ValueError('Data after completion')
                for key in ('pod','model_id','runtime_sha256','backend_mode'):
                    if key in identity and identity[key] != event.get(key): raise ValueError('Stream identity changed')
                    identity[key] = event.get(key)
                if event.get('request_id') != request_id: raise ValueError('Request identity mismatch')
                if event.get('event') == 'chunk':
                    if event['index'] != chunks: raise ValueError('Chunk index mismatch')
                    first = now if first is None else first
                    journal.emit('chunk', **base, received_monotonic=now, index=chunks,
                                 inter_chunk_seconds=None if previous is None else now-previous, data=event)
                    previous = now
                    chunks += 1
                elif event.get('event') == 'complete':
                    if event.get('chunks') != chunks or chunks == 0: raise ValueError('Completion count mismatch')
                    complete = True
                    journal.emit('stream_complete', **base, received_monotonic=now, data=event)
                else: raise ValueError('Unknown stream event')
            outcome = 'completed' if complete else 'aborted'
    except (TimeoutError, socket.timeout) as exc:
        outcome, error = 'timeout', str(exc)
    except Exception as exc:
        outcome, error = ('timeout' if expired.is_set() or time.monotonic() >= deadline else 'aborted' if status == 200 else 'error'), repr(exc)
    finally:
        if timer is not None: timer.cancel()
        if response is not None: response.close()
        if not keepalive or outcome != 'completed': conn.close()
        ended = time.monotonic()
        journal.emit('attempt_end', **base, ended_monotonic=ended, status=status, outcome=outcome,
                     chunks=chunks, stream_complete=complete, incomplete_stream=status == 200 and not complete,
                     ttfc_seconds=None if first is None else first-started, total_seconds=ended-started,
                     identity=identity, error=error)
    return outcome


def run(args):
    raw = Path(args.schedule).read_bytes()
    rows = [json.loads(line) for line in raw.splitlines() if line]
    journal = Journal(args.output)
    origin = time.monotonic() + args.start_at-time.time()
    journal.emit('run_start', client=args.client, client_ip=os.getenv('POD_IP', socket.gethostbyname(socket.gethostname())),
                 schedule_sha256=hashlib.sha256(raw).hexdigest(), start_at=args.start_at,
                 origin_monotonic=origin, url=args.url, timeout=args.timeout, workers=args.workers,
                 retries=args.retries, keepalive=False, run=args.run)
    selected = [row for row in rows if row['client'] == args.client]
    for row in selected:
        journal.emit('planned', **row, scheduled_monotonic=origin+row['offset_seconds'])
    def send(row):
        for number in range(1, args.retries+2):
            result = attempt(row, args.url, journal, origin+row['offset_seconds'], args.timeout, number, args.run)
            if result == 'completed': break
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = []
        for row in selected:
            time.sleep(max(0, origin+row['offset_seconds']-time.monotonic()))
            futures.append(pool.submit(send, row))
        for future in futures: future.result()
    journal.emit('run_end', ended_monotonic=time.monotonic(), planned=len(selected))
    journal.file.close()


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    make = sub.add_parser('schedule')
    make.add_argument('--duration', type=float, default=120)
    make.add_argument('--rate', type=float, default=2)
    make.add_argument('--skew', action='store_true')
    make.add_argument('--seed', type=int, default=110)
    make.add_argument('--output', required=True)
    client = sub.add_parser('run')
    client.add_argument('--schedule', required=True)
    client.add_argument('--client', type=int, choices=range(4), required=True)
    client.add_argument('--start-at', type=float, required=True)
    client.add_argument('--url', required=True)
    client.add_argument('--output', required=True)
    client.add_argument('--run', default='run')
    client.add_argument('--timeout', type=float, default=60)
    client.add_argument('--workers', type=int, default=128)
    client.add_argument('--retries', type=int, default=0)
    args = parser.parse_args()
    if args.command == 'schedule':
        Path(args.output).write_text(''.join(json.dumps(row)+'\n' for row in schedule(args.duration,args.rate,args.skew,args.seed)))
    else: run(args)

if __name__ == '__main__': main()
