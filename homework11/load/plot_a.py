#!/usr/bin/env python3
"""Render saved A snapshots and rollout, with no invented sample intervals.

python3 homework11/load/plot_a.py
Read-only with respect to cluster; rerunnable during/after collection.
"""
import argparse
from collections import defaultdict,Counter
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import tempfile
os.environ.setdefault('MPLCONFIGDIR',str(Path(tempfile.gettempdir())/'hw11-matplotlib'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from plot_c import rows,stamp,broken


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results',type=Path,default=Path('results'))
    parser.add_argument('--rollout',default='a-real-rollout')
    parser.add_argument('--output',type=Path,default=Path('homework11/figures'))
    args=parser.parse_args();folder=args.results/args.rollout;issues=[]
    samples=defaultdict(list);pod_points=defaultdict(list);pod_metadata={}
    for e in rows(folder/'observer/evidence.jsonl',issues):
        if e.get('returncode')!=0:
            continue
        try:
            d=json.loads(e['stdout']);t=stamp(e['observed_at']);argv=e.get('argv',[])
            if 'pods' in argv:
                ready=0
                for pod in d.get('items',[]):
                    if pod.get('metadata',{}).get('labels',{}).get('app.kubernetes.io/name')!='inference':
                        continue
                    name=pod['metadata']['name'];condition=next((c for c in pod.get('status',{}).get('conditions',[]) if c['type']=='Ready'),{})
                    value=condition.get('status')=='True';ready+=value
                    pod_points[name].append((t,int(value)))
                    pod_metadata[name]={'created':pod['metadata'].get('creationTimestamp'),'last_seen':t,'ready_condition':condition,'deleting':pod['metadata'].get('deletionTimestamp')}
                samples['Pod Ready count'].append((t,ready))
            elif 'endpointslices' in argv or 'endpointslice' in argv:
                for service in ['hw11-inference-balanced','hw11-inference-sticky']:
                    slices=[s for s in d.get('items',[]) if s.get('metadata',{}).get('labels',{}).get('kubernetes.io/service-name')==service]
                    if slices:
                        # Count unique Pod identities, including terminating endpoints only
                        # when their actual ready flag is true.
                        endpoints={ep.get('targetRef',{}).get('name',str(ep.get('addresses'))):ep for sl in slices for ep in sl.get('endpoints',[])}
                        samples[service+' ready endpoints'].append((t,sum(ep.get('conditions',{}).get('ready') is True for ep in endpoints.values())))
        except (ValueError,KeyError,TypeError) as error:
            issues.append({'source':'rollout observer','issue':str(error)})
    markers=list(rows(folder/'markers.jsonl',issues));events=list(rows(folder/'load/requests.jsonl',issues))
    if not events:
        for p in sorted((folder/'load').glob('client-*.jsonl')):
            events.extend(rows(p,issues))
    starts={e['request_id']:e for e in events if e.get('event')=='attempt_start'}
    ends={e['request_id']:e for e in events if e.get('event')=='attempt_end'}
    chunks=defaultdict(list)
    for e in events:
        if e.get('event')=='chunk':
            chunks[e['request_id']].append(e)
    streams=[]
    for request_id,start in sorted(starts.items(),key=lambda x:x[1]['sent_wall']):
        end=ends.get(request_id);parts=chunks.get(request_id,[])
        part_times=[start['sent_wall']+e['received_monotonic']-start['sent_monotonic'] for e in parts]
        streams.append({'request_id':request_id,'sent_wall':start['sent_wall'],
                        'end_wall':start['sent_wall']+end['total_seconds'] if end else None,
                        'observed_chunk_times':part_times,'outcome':end.get('outcome') if end else 'unfinished',
                        'status':end.get('status') if end else None,
                        'identity':end.get('identity',{}) if end else (parts[0]['data'] if parts else {})})
    warm=[]
    for filename,label in [('warmup-defect.json','defect'),('warmup-fixed.json','fixed attempt 1'),('warmup-fixed-retry.json','fixed retry'),('warmup-fixed-success.json','fixed service success')]:
        path=args.results/filename
        if not path.exists():
            issues.append({'source':str(path),'issue':'missing warmup evidence'});continue
        try:
            for e in json.loads(path.read_text()):
                if 'pod' not in e:
                    continue
                pod=e['pod'];name=pod['metadata']['name'];created=stamp(pod['metadata']['creationTimestamp'])
                ready=next((c['status']=='True' for c in pod.get('status',{}).get('conditions',[]) if c['type']=='Ready'),None)
                endpoint=next((ep.get('conditions',{}).get('ready') for sl in e.get('endpoint_slices',{}).get('items',[]) for ep in sl.get('endpoints',[]) if ep.get('targetRef',{}).get('name')==name),None)
                warm.append({'source':str(path),'label':label,'pod':name,'wall':e['wall'],'age_seconds':e['wall']-created,'ready':ready,'endpoint_ready':endpoint})
        except (ValueError,KeyError,TypeError) as error:
            issues.append({'source':str(path),'issue':str(error)})
    commands=[]
    for e in rows(folder/'commands.jsonl',issues):
        argv=e.get('argv',[])
        if 'upgrade' in argv:
            commands.append(e)
    args.output.mkdir(parents=True,exist_ok=True)
    audit={'sources':[str(folder/'observer/evidence.jsonl'),str(folder/'markers.jsonl'),str(folder/'load/requests.jsonl')],
           'conditions':'Warmup snapshots are separate early checks (60s setting). Rollout upgrade argv below is authoritative; actual rollout configured 20s warmup, 32 chunks, 0.5 CPU-seconds per chunk, grace120s/preStop5s. Snapshot Pod age includes scheduling/startup and is not elapsed backend warmup.',
           'gap_policy':'Rollout lines broken after 10s without observations; no extension beyond last sample. Warmup shows discrete snapshots only. Stream bars cover attempts; chunk dots prove received data. Wall time reconstructed from client monotonic deltas.',
           'warmup_snapshots':warm,'rollout_series':dict(samples),'pod_points':dict(pod_points),'pod_metadata':pod_metadata,'markers':markers,'streams':streams,'upgrade_commands':commands,'issues':issues}
    (args.output/'a-plot-data.json').write_text(json.dumps(audit,indent=2)+'\n')
    fig,axes=plt.subplots(4,1,figsize=(13,13),gridspec_kw={'height_ratios':[1.1,1,1.6,1.8]})
    for i,e in enumerate(warm):
        if e['ready'] is not None:axes[0].scatter(e['age_seconds'],i-.08,marker='o',color='C2' if e['ready'] else 'C3',label='Pod Ready' if i==0 else None)
        if e['endpoint_ready'] is not None:axes[0].scatter(e['age_seconds'],i+.08,marker='x',color='C2' if e['endpoint_ready'] else 'C3',label='Endpoint ready' if i==0 else None)
    axes[0].set_yticks(range(len(warm)),[e['label'] for e in warm]);axes[0].set_xlabel('Observed Pod age (seconds); separate snapshots, no continuous warmup inference')
    axes[0].set_title('Early warmup checks: green=True / red=False; service failures remain in raw evidence',fontsize=10);axes[0].legend(fontsize=8)
    for name,points in sorted(samples.items()):
        axes[1].step(*broken(points,10),where='post',label=name)
    axes[1].set_ylabel('Observed count');axes[1].legend(fontsize=8,ncol=3)
    for i,(name,points) in enumerate(sorted(pod_points.items())):
        # Each dot is an actual observation; disappearance is not assigned a time.
        axes[2].scatter([datetime.fromtimestamp(t,timezone.utc) for t,v in points],[i]*len(points),c=['#218352' if v else '#b34232' for t,v in points],s=8)
    axes[2].set_yticks(range(len(pod_points)),[p.rsplit('-',1)[-1] for p in sorted(pod_points)])
    axes[2].set_ylabel('Pod suffix');axes[2].set_title('Per-Pod observed Ready: green=True / red=False; blank=not observed',fontsize=10)
    for i,e in enumerate(streams):
        begin=datetime.fromtimestamp(e['sent_wall'],timezone.utc)
        last=e['end_wall'] or (max(e['observed_chunk_times']) if e['observed_chunk_times'] else e['sent_wall'])
        color='#17678a' if e['identity'].get('model_id')=='A' else '#b45d28'
        axes[3].plot([begin,datetime.fromtimestamp(last,timezone.utc)],[i,i],color=color,linewidth=3,alpha=.4)
        axes[3].scatter([datetime.fromtimestamp(t,timezone.utc) for t in e['observed_chunk_times']],[i]*len(e['observed_chunk_times']),s=7,color=color)
        if e['end_wall'] is not None:axes[3].scatter(datetime.fromtimestamp(e['end_wall'],timezone.utc),i,marker='|' if e['outcome']=='completed' else 'x',color=color,s=70)
    axes[3].set_yticks(range(len(streams)),[e['request_id'].split('-r')[-1]+' '+str(e['identity'].get('model_id','?'))+' '+e['outcome'] for e in streams]);axes[3].set_ylabel('Stream attempt')
    axes[3].set_title('Stream bars: sent→end; dots: received chunks; A=blue / B=brown; unfinished ends not inferred',fontsize=10)
    for ax in axes[1:]:
        for marker in markers:
            if marker.get('kind') in ['upgrade_B_start','upgrade_B_ready']:
                t=datetime.fromtimestamp(marker['wall'],timezone.utc);ax.axvline(t,color='#665d84',linestyle='--',alpha=.8)
                if ax==axes[1]:ax.annotate(marker['kind'],xy=(t,.97),xycoords=('data','axes fraction'),fontsize=8,rotation=10)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S',tz=timezone.utc));ax.set_xlabel('Rollout observation time UTC')
    bounds=[t for points in samples.values() for t,v in points]+[e['sent_wall'] for e in streams]+[e['end_wall'] for e in streams if e['end_wall']]
    if bounds:
        for ax in axes[1:]:ax.set_xlim(datetime.fromtimestamp(min(bounds)-1,timezone.utc),datetime.fromtimestamp(max(bounds)+1,timezone.utc))
    for ax in axes:ax.grid(alpha=.15)
    fig.suptitle('A: recorded warmup readiness, rollout Ready/Endpoints and long streams',fontsize=14)
    fig.text(.01,.008,'Conditions, full Pod/request IDs and sampling gaps: a-plot-data.json. Early checks warmup60s; rollout warmup20s / 32 chunks / 0.5 CPU-s per chunk.',fontsize=8)
    fig.tight_layout(rect=(0,.03,1,.96));fig.savefig(args.output/'a-warmup-rollout.svg');plt.close(fig)
    print(json.dumps({'warmup_snapshots':len(warm),'rollout_series':{k:len(v) for k,v in samples.items()},'pod_count':len(pod_points),'streams':len(streams),'outcomes':dict(Counter(e['outcome'] for e in streams)),'issues':issues},indent=2))

if __name__=='__main__':main()
