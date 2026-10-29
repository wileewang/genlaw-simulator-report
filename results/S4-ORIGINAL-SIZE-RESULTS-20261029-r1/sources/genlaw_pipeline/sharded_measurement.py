"""Schedule complete global IoU without a large monolithic mesh or native job."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]


def measure_shards(out,surface,reference,caches,output):
    from .run import bounded
    worker=ROOT/'genlaw_pipeline/sharded_measure.py'
    plan=out/'sharded-measure-plan.json'
    bounded(out,'sharded-measure-plan',[sys.executable,worker,'plan','--surface',surface,
        '--reference',reference,'--caches',caches,'--output',plan])
    parts=out/'sharded-measure-parts';parts.mkdir(exist_ok=False)
    for i,_ in enumerate(json.loads(plan.read_text())['parts']):
        bounded(out,f'sharded-measure-{i:03d}',[sys.executable,worker,'part','--plan',plan,'--index',str(i),
            '--output',parts/f'part-{i:03d}.json'])
        print(json.dumps({'measured_global_height_part':i+1}),flush=True)
    bounded(out,'sharded-measure-finish',[sys.executable,worker,'finish','--plan',plan,
        '--parts',parts,'--output',output])
