"""Serial, disk-reserved and resumable collection of the complete original plan.

Only never-started steps can be resumed automatically. Native requests and
their completion workers are the existing frozen producer's actual functions.
"""
import argparse
from datetime import datetime,timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from genlaw_pipeline.admission_wait import admitted_bounded,preserve_capacity_rejection
from genlaw_pipeline.cell_windows import checked_file,file_binding
from genlaw_pipeline.native_collection import folder,groups,group_command,seal_command
from genlaw_pipeline.native_stream import run as native_run,checked_request,command as native_command
from genlaw_pipeline.native_stream_metadata import receipt
from genlaw_pipeline.planar_resume import process_identity
from genlaw_pipeline.sparse_material import checked_resources
from genlaw_pipeline.run import write

WORKER=ROOT/'genlaw_pipeline/native_collection.py'
NATIVE=ROOT/'genlaw_pipeline/native_stream.py'
LOAD=lambda p:json.loads(Path(p).read_text())


def initialize(partition,build,out):
    out=out.resolve();out.relative_to(ROOT);out.mkdir(parents=True,exist_ok=False)
    plan=out/'plan.json'
    admitted_bounded(out,'initialize',[sys.executable,WORKER,'initialize','--partition',partition.resolve(),
        '--build',build.resolve(),'--output',plan])
    return plan


def pending(out,label,command,artifact):
    resource=out/(label+'-resources.json');log=out/(label+'.log');capacity=out/(label+'-capacity.json')
    if resource.exists():
        checked_resources(resource,command)
        if not artifact.is_file():raise ValueError('Successful worker lacks its declared artifact')
        return False
    if log.exists() or artifact.exists():raise ValueError('Started or partial collection step cannot be repeated')
    if capacity.exists():preserve_capacity_rejection(out,label)
    return True


def finish_native(plan,spec,out):
    """Continue an unchanged original request without rewriting its provenance."""
    if not out.exists():
        return native_run(checked_file(plan['context']['inputs']['project']),checked_file(plan['build']),
            spec['bounds'],out,plan['native_facets_per_file'])
    request,project,binary,env=checked_request(out/'request.json')
    if (request['inputs']!=plan['context']['inputs'] or request['build']!=plan['build']
            or request['window']!=spec['bounds'] or request['facets_per_file']!=plan['native_facets_per_file']):
        raise ValueError('Partial native request differs from the original collection plan')
    command=native_command(binary,project,spec['bounds'],out/'native',request['facets_per_file'])
    if pending(out,'native',command,out/'native/native.json'):
        if (out/'native').exists() and any((out/'native').iterdir()):
            raise ValueError('Unexplained partial native files; do not start another execution')
        admitted_bounded(out,'native',command,env=env)
    command=[sys.executable,NATIVE,'finish','--out',out]
    if pending(out,'validate',command,out/'result.json'):admitted_bounded(out,'validate',command)
    return LOAD(out/'result.json')


def run_window(plan_path,plan,spec,run_dir):
    index=spec['index'];out=folder(plan_path,index);out.mkdir(parents=True,exist_ok=True)
    free=shutil.disk_usage(ROOT).free;required=spec['storage']['required_free_bytes_before_window']
    write(run_dir/f'window-{index:04d}-storage.json',{'window_index':index,'free_bytes':free,
        'required_free_bytes':required,'storage':spec['storage'],'admitted':free>=required,
        'native_started_by_this_reservation':False,'source_code':file_binding(Path(__file__))})
    if free<required:raise RuntimeError('Window storage reserve is not available; completed outputs retained')
    finish_native(plan,spec,out/'native')
    native=receipt(out/'native/result.json',plan['context'])
    for number,indices in enumerate(groups(native['chunks'])):
        label=f'group-{number:04d}';target=out/'groups'/label
        command=group_command(plan_path,index,number)
        if pending(out,label,command,target/'result.json'):
            if target.exists() or any((out/'candidates'/f'chunk-{i:04d}').exists() for i in indices):
                raise ValueError('Partial candidate/archive group requires explicit investigation')
            admitted_bounded(out,label,command)
    command=seal_command(plan_path,index)
    if pending(out,'seal',command,out/'result.json'):admitted_bounded(out,'seal',command)
    result=LOAD(out/'result.json')
    print(json.dumps({'window_completed':index,'native_triangles':result['native_triangles'],
        'candidate_triangles':result['candidate_triangles'],'local_archive_bytes':result['local_archive_bytes']}),flush=True)


def run(plan_path,imports=(),max_new=None):
    plan_path=plan_path.resolve();plan_path.relative_to(ROOT);root=plan_path.parent
    if max_new is not None and (type(max_new) is not int or max_new<1):raise ValueError('Invalid batch count')
    with (root/'.collection.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        runs=root/'runs';runs.mkdir(exist_ok=True)
        previous=sorted(runs.glob('run-*/owner.json'))
        if previous:
            owner=LOAD(previous[-1])
            if process_identity(owner['process']['pid'])==owner['process']:
                raise ValueError('Previous collection owner is still live; observe it instead of restarting')
        run_dir=runs/f'run-{len(previous)+1:04d}';run_dir.mkdir()
        write(run_dir/'owner.json',{'process':process_identity(os.getpid()),'plan':file_binding(plan_path),
            'observed_live':True,'source_code':file_binding(Path(__file__))})
        try:
            admitted_bounded(run_dir,'before',[sys.executable,WORKER,'snapshot','--plan',plan_path,'--output',run_dir/'before.json'])
            plan=LOAD(plan_path);before=LOAD(run_dir/'before.json')
            completed={r['window_index'] for r in before['completed_windows']};specs={r['index']:r for r in plan['windows']}
            for source in imports:
                source=source.resolve();item=LOAD(source);native=LOAD(checked_file(item['native_result']))
                bounds=LOAD(checked_file(native['request']))['window']
                matching=[s['index'] for s in plan['windows'] if s['bounds']==bounds]
                if len(matching)!=1:raise ValueError('Import does not belong to this original full plan')
                index=matching[0]
                if index in completed:
                    row=LOAD(folder(plan_path,index)/'result.json')
                    if row['import_input']!=file_binding(source):raise ValueError('Import would replace completed window identity')
                    continue
                label=f'import-{index:04d}';command=[sys.executable,WORKER,'import','--plan',plan_path,'--input',source]
                if pending(root,label,command,folder(plan_path,index)/'result.json'):
                    if folder(plan_path,index).exists():raise ValueError('Partial import cannot be repeated')
                    admitted_bounded(root,label,command)
                completed.add(index)
            started=[]
            for index in plan['execution_order']:
                if index in completed:continue
                # Freeze checks are cheap identities here; the actual workers
                # independently recheck the complete partition and all sources.
                for binding in plan['sources']:checked_file(binding)
                checked_file(plan['partition']);checked_file(plan['build'])
                run_window(plan_path,plan,specs[index],run_dir);started.append(index);completed.add(index)
                if max_new is not None and len(started)>=max_new:break
            admitted_bounded(run_dir,'after',[sys.executable,WORKER,'snapshot','--plan',plan_path,'--output',run_dir/'after.json'])
            summary=LOAD(run_dir/'after.json')
            write(run_dir/'result.json',{'status':'run_finished','at_utc':datetime.now(timezone.utc).isoformat(),
                'plan':file_binding(plan_path),'owner':file_binding(run_dir/'owner.json'),
                'before':file_binding(run_dir/'before.json'),'after':file_binding(run_dir/'after.json'),
                'completed_count':summary['completed_window_count'],'required_count':summary['required_windows'],
                'newly_completed_indices':started,'requested_batch_limit':max_new,'entire_plan_preserved':True,
                'whole_stock_geometry_verified':False,'source_code':file_binding(Path(__file__))})
            print((run_dir/'after.log').read_text(),flush=True)
        except Exception as error:
            write(run_dir/'stopped.json',{'status':'stopped_without_retrying_started_failures',
                'error_type':type(error).__name__,'error':str(error),'owner':file_binding(run_dir/'owner.json'),
                'completed_outputs_retained':True,'source_code':file_binding(Path(__file__))})
            raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['initialize','run'])
    for name in ['partition','build','output','plan']:p.add_argument('--'+name,type=Path)
    p.add_argument('--imports',type=Path,nargs='*',default=[]);p.add_argument('--max-new',type=int);a=p.parse_args()
    if a.action=='initialize':initialize(a.partition,a.build,a.output)
    else:run(a.plan,a.imports,a.max_new)
