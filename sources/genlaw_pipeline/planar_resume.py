"""Resume only unstarted typed-material measurements after a known owner exits.

Completed commands and their artifacts are reused. A started failure, partial
artifact, live prior owner, or unexplained admission record stops recovery.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from genlaw_pipeline.admission_wait import admitted_bounded,preserve_capacity_rejection
from genlaw_pipeline.cell_windows import checked_file,file_binding
from genlaw_pipeline.planar_chamfer_entry import jobs,initial_command
from genlaw_pipeline.sparse_material import checked_resources
from genlaw_pipeline.run import write

LOAD=lambda p:json.loads(p.read_text())
ENTRY=ROOT/'genlaw_pipeline/planar_chamfer_entry.py'


def process_identity(pid):
    if type(pid) is not int or pid<=0:raise ValueError('Invalid prior process identity')
    path=Path('/proc')/str(pid)/'stat'
    try:text=path.read_text()
    except FileNotFoundError:return None
    fields=text[text.rfind(')')+2:].split()
    if fields[0]=='Z':return None
    return {'pid':pid,'start_ticks':int(fields[19]),
        'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}


def observe_owner(pid,plan_path,output):
    identity=process_identity(pid)
    if identity is None:raise ValueError('Can only record an owner observed live now')
    write(output,{'schema':'genlaw.planar-measurement-owner.v1','process':identity,
        'plan':file_binding(plan_path),'observed_live':True,'source_code':file_binding(Path(__file__))})


def owner_stopped(path,plan_path):
    row=LOAD(path)
    if (row.get('schema')!='genlaw.planar-measurement-owner.v1' or row.get('observed_live') is not True
            or row.get('plan')!=file_binding(plan_path) or row.get('source_code')!=file_binding(Path(__file__))):
        raise ValueError('Missing an actual observation of this plan owner')
    if process_identity(row['process']['pid'])==row['process']:
        raise ValueError('Previous measurement owner is still live; do not restart')


def job_state(out,label,command,artifact,plan_binding):
    resource=out/(label+'-resources.json');log=out/(label+'.log');cap=out/(label+'-capacity.json')
    if resource.exists():
        checked_resources(resource,command)
        row=LOAD(artifact)
        if row.get('plan')!=plan_binding:raise ValueError('Completed artifact belongs to another plan')
        if 'source_code' in row:checked_file(row['source_code'])
        for binding in row.get('sections',[]):checked_file(binding)
        return {'state':'completed','label':label,'resources':file_binding(resource),'artifact':file_binding(artifact)}
    if log.exists() or artifact.exists():raise ValueError('Ambiguous started or partial worker; cannot repeat it')
    if cap.exists():
        data=LOAD(cap)
        if data['eligible_cpus'] and data['mem_available_bytes']>3*1024**3 and data['disk_free_bytes']>4.5*1024**3:
            raise ValueError('Admission may have started a worker; cannot assume it never ran')
    return {'state':'unstarted','label':label}


def sequence(plan_path):
    yield from jobs(plan_path)
    yield 'complete',[sys.executable,ENTRY,'complete','--plan',plan_path,'--output',plan_path.parent/'result.json'],plan_path.parent/'result.json'


def validate(plan_path,owner,output):
    from genlaw_pipeline.planar_chamfer_measurement import checked,require_bounded_worker,result_status
    require_bounded_worker();owner_stopped(owner,plan_path);plan,*_=checked(plan_path)
    inputs=plan['inputs'];control=checked_file(inputs['test_control']) if inputs['test_only_guard_bypass'] else None
    first=initial_command(checked_file(plan['surface']),checked_file(plan['reference']),checked_file(plan['policy']),plan_path,control)
    checked_resources(plan_path.parent/'plan-resources.json',first)
    rows=[job_state(plan_path.parent,label,command,path,file_binding(plan_path)) for label,command,path in sequence(plan_path)]
    if all(r['state']=='completed' for r in rows) and control is None:
        result_status(plan_path,plan_path.parent/'metrics.json',plan_path.parent/'verification.json')
    write(output,{'plan':file_binding(plan_path),'owner':file_binding(owner),'jobs':rows,
        'source_code':file_binding(Path(__file__))})


def resume(plan_path,owner,out):
    plan_path=plan_path.resolve();owner=owner.resolve();out=out.resolve()
    for path in [plan_path,owner,out]:path.relative_to(ROOT)
    owner_stopped(owner,plan_path);out.mkdir(parents=True,exist_ok=False)
    with (plan_path.parent/'.resume.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        before=out/'before.json';after=out/'after.json'
        admitted_bounded(out,'before',[sys.executable,Path(__file__),'validate','--plan',plan_path,'--owner',owner,'--output',before])
        started=[];reused=[]
        for label,command,path in sequence(plan_path):
            state=job_state(plan_path.parent,label,command,path,file_binding(plan_path))
            if state['state']=='completed':reused.append(state);continue
            if (plan_path.parent/(label+'-capacity.json')).exists():preserve_capacity_rejection(plan_path.parent,label)
            admitted_bounded(plan_path.parent,label,command);started.append(label)
        admitted_bounded(out,'after',[sys.executable,Path(__file__),'validate','--plan',plan_path,'--owner',owner,'--output',after])
        if any(r['state']!='completed' for r in LOAD(after)['jobs']):raise ValueError('Recovery left unfinished measurements')
        write(out/'result.json',{'status':'measurement_complete','plan':file_binding(plan_path),'owner':file_binding(owner),
            'before':file_binding(before),'after':file_binding(after),'reused_jobs':reused,'newly_started_jobs':started,
            'started_failures_repeated':0,'new_native_jobs':0,'numerical_code_changed':False,
            'source_code':file_binding(Path(__file__))})
        print(json.dumps({'resumed_measurement_jobs':len(started),'verified_reused_jobs':len(reused)}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['observe','validate','resume'])
    p.add_argument('--plan',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--owner',type=Path);p.add_argument('--pid',type=int);a=p.parse_args()
    if a.action=='observe':observe_owner(a.pid,a.plan,a.output)
    elif a.action=='validate':validate(a.plan,a.owner,a.output)
    else:resume(a.plan,a.owner,a.output)
