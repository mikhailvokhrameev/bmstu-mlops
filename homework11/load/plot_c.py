#!/usr/bin/env python3
"""Plot saved C evidence only; safely rerun while collectors are appending.

python3 homework11/load/plot_c.py --results results --output homework11/figures
Missing samples remain gaps. No cluster/network access, no inferred recovery.
"""
import argparse
from collections import defaultdict, Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile
from urllib.parse import urlparse, parse_qs
os.environ.setdefault('MPLCONFIGDIR', str(Path(tempfile.gettempdir())/'hw11-matplotlib'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


def stamp(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()


def body(value):
    if not isinstance(value,str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        # Backfill's HTTP collector preserves escaped JSON quotes in body.
        return json.loads(value.replace('\\"','"'))


def rows(path, audit):
    if not path.exists():
        audit.append({'source':str(path),'issue':'missing file'})
        return
    with path.open() as file:
        for number,line in enumerate(file,1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                audit.append({'source':str(path),'line':number,'issue':'invalid/partial JSON line skipped'})


def cores(value):
    for unit,scale in [('n',1e-9),('u',1e-6),('m',1e-3)]:
        if value.endswith(unit):
            return float(value[:-1])*scale
    return float(value)


def broken(points, max_gap):
    """Do not draw across collector outages. Duplicate timestamps use last value."""
    points=sorted(dict(points).items())
    x,y=[],[]
    for t,v in points:
        if x and t-x[-1]>max_gap:
            x.append(t-.001); y.append(float('nan'))
        x.append(t);y.append(v)
    return [datetime.fromtimestamp(t,timezone.utc) for t in x],y


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results',type=Path,default=Path('results'))
    parser.add_argument('--output',type=Path,default=Path('homework11/figures'))
    args=parser.parse_args();issues=[];cpu=defaultdict(list);backfill=defaultdict(list)
    replicas=defaultdict(list);alerts=defaultdict(list);pod_events=[];observed=[]
    for e in rows(args.results/'c-observer/evidence.jsonl',issues):
        t=e.get('wall_time')
        if t is None:
            continue
        observed.append(t)
        if e.get('kind')!='kubectl' or e.get('returncode')!=0:
            continue
        argv=e.get('argv',[])
        try:
            d=body(e.get('stdout',''))
            if 'deployment' in argv and d.get('metadata',{}).get('name')=='hw11-inference':
                # An absent status field is recorded as missing, never zero.
                status=d.get('status',{})
                for key,label in [('replicas','Deployment current'),('readyReplicas','Deployment ready')]:
                    if key in status:
                        replicas[label].append((t,status[key]))
            elif 'hpa' in argv:
                for key,label in [('currentReplicas','HPA current'),('desiredReplicas','HPA desired')]:
                    if key in d.get('status',{}):
                        replicas[label].append((t,d['status'][key]))
            elif '/apis/metrics.k8s.io/v1beta1/namespaces/hw11/pods' in argv:
                for item in d.get('items',[]):
                    pod=item['metadata']['name']
                    if not pod.startswith('hw11-inference-'):
                        continue
                    sample_time=stamp(item['timestamp'])
                    for container in item.get('containers',[]):
                        if container['name']=='api':
                            cpu[pod].append((sample_time,cores(container['usage']['cpu'])))
        except (ValueError,KeyError,TypeError) as error:
            issues.append({'source':'c-observer/evidence.jsonl','wall':t,'issue':str(error)})
    for e in rows(args.results/'c-transitions-live.jsonl',issues):
        try:
            t=stamp(e['time'])
            if e['kind']=='alert':
                alerts[e['name']].append((t,{'inactive':0,'pending':1,'firing':2}.get(e['state'],float('nan'))))
            elif e['kind']=='pod':
                pod_events.append(e)
        except (KeyError,ValueError) as error:
            issues.append({'source':'c-transitions-live.jsonl','issue':str(error)})
    # Alert states use dense observer rules, so gaps are not filled by transitions.
    dense_alerts=defaultdict(list)
    for e in rows(args.results/'c-observer/evidence.jsonl',issues):
        if e.get('kind')=='prometheus' and '/api/v1/rules' in e.get('url','') and e.get('status')==200:
            try:
                for group in body(e['body']).get('data',{}).get('groups',[]):
                    for rule in group.get('rules',[]):
                        if rule.get('type')=='alerting':
                            dense_alerts[rule['name']].append((e['wall_time'],{'inactive':0,'pending':1,'firing':2}.get(rule['state'],float('nan'))))
            except (ValueError,KeyError,TypeError) as error:
                issues.append({'source':'observer rules','issue':str(error)})
    for e in rows(args.results/'c-cadvisor-backfill.jsonl',issues):
        if e.get('name')!='cpu':
            continue
        try:
            for series in body(e['body']).get('data',{}).get('result',[]):
                pod=series.get('metric',{}).get('pod','unknown')
                for t,v in series.get('values',[]):
                    if math.isfinite(float(v)):
                        backfill[pod].append((float(t),float(v)))
        except (ValueError,KeyError,TypeError) as error:
            issues.append({'source':'c-cadvisor-backfill.jsonl','issue':str(error)})
    phases=[]
    for folder in sorted(args.results.glob('c-*')):
        if not folder.is_dir() or not list(folder.glob('client-*.jsonl')):
            continue
        attempts=[];origin=None;ends=0;clients=[]
        for file in sorted(folder.glob('client-*.jsonl')):
            events=list(rows(file,issues));starts=[e for e in events if e.get('event')=='run_start']
            clients.extend(starts)
            ends+=sum(e.get('event')=='run_end' for e in events)
            attempts.extend(e for e in events if e.get('event')=='attempt_start')
        if not attempts:
            continue
        times=[e['sent_wall'] for e in attempts]
        counts=Counter(math.floor(t/10)*10 for t in times)
        first,last=min(counts),max(counts)
        phases.append({'name':folder.name,'first_send':min(times),'last_send':max(times),
                       'run_end_count':ends,'complete_clients':ends==4,'attempts_observed':len(attempts),
                       'clients':clients,'arrival_10s':[(t,counts[t]/10) for t in range(first,last+1,10)]})
    phase_markers=list(rows(args.results/'c-phase-times.jsonl',issues))
    args.output.mkdir(parents=True,exist_ok=True)
    result={'conditions':'CPU resource-metrics API uses its own sample timestamp and averaging window; dashed CPU is cAdvisor rate[1m] backfill. Never sum these sources. Arrivals count observed attempt_start, including retries, in UTC-aligned 10s bins; edge bins can be partial.',
            'gap_policy':'Dense observer/replica samples disconnected after 20s; CPU API after 90s; cAdvisor after 15s. No extrapolation beyond last sample. Missing data never becomes zero.',
            'observer_first':min(observed) if observed else None,'observer_last':max(observed) if observed else None,
            'cpu_resource_api':dict(cpu),'cpu_cadvisor':dict(backfill),'replicas':dict(replicas),
            'alert_samples':dict(dense_alerts),'alert_transitions':dict(alerts),'pod_events':pod_events,
            'phases':phases,'phase_markers':phase_markers,'issues':issues}
    (args.output/'c-plot-data.json').write_text(json.dumps(result,indent=2)+'\n')
    fig,axes=plt.subplots(4,1,figsize=(13,12),sharex=True)
    colors={pod:plt.get_cmap('tab10')(i%10) for i,pod in enumerate(sorted(set(cpu)|set(backfill)))}
    for pod,points in sorted(cpu.items()):
        axes[0].plot(*broken(points,90),label=pod.rsplit('-',1)[-1]+' API',color=colors[pod],linewidth=1.3)
    for pod,points in sorted(backfill.items()):
        axes[0].plot(*broken(points,15),label=pod.rsplit('-',1)[-1]+' cAdvisor',color=colors[pod],linestyle='--',alpha=.65)
    axes[0].set_ylabel('CPU cores / Pod');axes[0].legend(fontsize=7,ncol=4)
    for i,phase in enumerate(phases):
        # Stored timestamps are bin starts. Post steps with the final boundary
        # show each observed count over its actual [t, t+10) interval.
        arrivals=phase['arrival_10s']
        bounded=arrivals+[(arrivals[-1][0]+10,arrivals[-1][1])] if arrivals else []
        x,y=broken(bounded,15)
        axes[1].step(x,y,where='post',label=phase['name']+(' [partial]' if not phase['complete_clients'] else ''))
        for ax in axes:
            ax.axvspan(datetime.fromtimestamp(phase['first_send'],timezone.utc),datetime.fromtimestamp(phase['last_send'],timezone.utc),alpha=.035,color=f'C{i%10}')
    known_phases={p['name'] for p in phases}
    for marker in phase_markers:
        if marker.get('phase') in known_phases or 'first_send' not in marker or 'last_send' not in marker:
            continue
        left=datetime.fromtimestamp(marker['first_send'],timezone.utc)
        right=datetime.fromtimestamp(marker['last_send'],timezone.utc)
        for ax in axes:
            ax.axvspan(left,right,facecolor='none',edgecolor='gray',hatch='//',alpha=.3)
        axes[1].annotate(marker.get('phase','phase')+' timing only; arrivals unavailable',
                         xy=(left,.96),xycoords=('data','axes fraction'),fontsize=7,rotation=8)
    axes[1].set_ylabel('Observed attempts/s\n10 s bins');axes[1].legend(fontsize=8,ncol=2)
    for name,points in sorted(replicas.items()):
        axes[2].step(*broken(points,20),where='post',label=name,linewidth=1.2)
    axes[2].set_ylabel('Replicas');axes[2].set_yticks([0,1,2,3,4]);axes[2].legend(fontsize=8,ncol=4)
    for name,points in sorted(dense_alerts.items()):
        axes[3].step(*broken(points,20),where='post',label=name,alpha=.8)
    axes[3].set_yticks([0,1,2],['inactive','pending','firing']);axes[3].set_ylabel('Alert state');axes[3].legend(fontsize=8)
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S',tz=timezone.utc))
    axes[-1].set_xlabel('UTC (date and exact sample epochs in c-plot-data.json)')
    for ax in axes:
        ax.grid(alpha=.2)
    fig.suptitle('C: observed CPU, arrivals, HPA/Ready replicas and alert states\nSaved evidence only; missing intervals remain gaps',fontsize=13)
    fig.text(.01,.01,'Solid CPU: resource metrics API; dashed: cAdvisor backfill. No data outside observed samples. Details/gaps: c-plot-data.json.',fontsize=8)
    fig.tight_layout(rect=(0,.025,1,.95));fig.savefig(args.output/'c-scaling-load.svg');plt.close(fig)
    print(json.dumps({'observer_records':len(observed),'replica_series':{k:len(v) for k,v in replicas.items()},'cpu_pods':len(cpu),'backfill_pods':len(backfill),'phases':[{k:v for k,v in p.items() if k not in ['clients','arrival_10s']} for p in phases],'issues':len(issues)},indent=2))

if __name__=='__main__':
    main()
