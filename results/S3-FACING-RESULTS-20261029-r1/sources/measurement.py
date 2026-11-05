"""Schedule sectional IoU in bounded jobs without deleting geometric detail."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]


def measure_bounded(out,label,reference,actual,output,display=False):
    from .run import bounded
    worker=ROOT/'genlaw_pipeline/measure_worker.py'
    plan_path=out/(label+'-plan.json')
    bounded(out,label+'-plan',[sys.executable,worker,'--run',reference,'--actual',actual,'--output',plan_path,'--plan-only'])
    plan=json.loads(plan_path.read_text())
    command=[sys.executable,worker,'--run',reference,'--actual',actual,'--output',output]
    if display:command.append('--display')
    if plan['levels']>192:
        directory=out/(label+'-parts');directory.mkdir(exist_ok=False)
        for i,(start,end) in enumerate(plan['parts']):
            bounded(out,label+'-part-'+str(i),[sys.executable,worker,'--run',reference,'--actual',actual,
                '--output',directory/('part-%03d.json'%i),'--interval-start',str(start),'--interval-end',str(end)])
        command+=['--parts',directory]
    return bounded(out,label,command)
