"""Wait for a complete original collection, then run the existing material audit.

The waiting coordinator performs no geometry work. Original native producers,
exact candidate proofs and the paged material auditor remain unchanged.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
LOAD=lambda p:json.loads(p.read_text())


def require_complete_progress(progress,indices):
    """A count alone cannot turn a partial collection into a complete handoff."""
    expected=sorted(indices)
    if (not expected or any(type(i) is not int for i in expected)
            or len(expected)!=len(set(expected))):raise ValueError('Invalid original window membership')
    rows=progress.get('completed_windows',[])
    ids=[r.get('window_index') for r in rows]
    if (progress.get('status')!='all_native_windows_collected'
            or any(type(i) is not int for i in ids) or ids!=expected
            or progress.get('pending_window_indices')!=[]
            or type(progress.get('completed_window_count')) is not int
            or progress['completed_window_count']!=len(expected)
            or type(progress.get('required_windows')) is not int
            or progress['required_windows']!=len(expected)):
        raise ValueError('Complete original window coverage is required before material auditing')
    counts=[progress.get('imported_windows'),progress.get('new_native_windows')]
    if any(type(n) is not int or n<0 for n in counts) or sum(counts)!=len(expected):
        raise ValueError('Original execution and reuse counts differ')
    flags={'metadata_revalidated':True,'all_payload_bytes_revalidated_in_this_snapshot':False,
        'bulk_archives_stored_locally':True,'global_geometry_verified':False,
        'whole_process_card_complete':False,'production_approved':False}
    if any(progress.get(k) is not v for k,v in flags.items()):
        raise ValueError('Collection handoff lost its scope or overstated geometric acceptance')
    return ids


def prerequisite(plan_path,run_path):
    from genlaw_pipeline.cell_windows import checked_file,file_binding
    from genlaw_pipeline.planar_resume import process_identity
    from genlaw_pipeline.sparse_material import checked_resources
    plan_path=plan_path.resolve();run_path=run_path.resolve();plan_path.relative_to(ROOT)
    run_path.relative_to(plan_path.parent/'runs')
    owner_path=run_path/'owner.json';owner=LOAD(owner_path);plan_binding=file_binding(plan_path)
    driver=file_binding(ROOT/'genlaw_pipeline/native_collection_entry.py')
    if (owner.get('plan')!=plan_binding or owner.get('source_code')!=driver
            or owner.get('observed_live') is not True):raise ValueError('Foreign or changed original collection owner')
    identity=owner['process']
    if process_identity(identity['pid'])==identity:
        return {'ready':False,'reason':'original_collection_owner_observed_live',
            'owner':file_binding(owner_path),'process':identity,'new_geometry_workers_started':0}
    if (run_path/'stopped.json').exists():raise ValueError('Original collection stopped; preserve and diagnose it')
    terminal=run_path/'result.json'
    if not terminal.is_file():raise ValueError('Original owner ended without a completed run result')
    row=LOAD(terminal)
    if (row.get('status')!='run_finished' or row.get('plan')!=plan_binding
            or row.get('owner')!=file_binding(owner_path) or row.get('source_code')!=driver
            or row.get('entire_plan_preserved') is not True
            or row.get('whole_stock_geometry_verified') is not False):
        raise ValueError('Original collection did not complete its declared run')
    after=checked_file(row['after'])
    if after!=run_path/'after.json':raise ValueError('Foreign collection completion snapshot')
    resource=run_path/'after-resources.json'
    checked_resources(resource,[sys.executable,ROOT/'genlaw_pipeline/native_collection.py','snapshot',
        '--plan',plan_path,'--output',after])
    progress=LOAD(after);plan=LOAD(plan_path)
    if progress.get('plan')!=plan_binding:raise ValueError('Completion snapshot belongs to another plan')
    require_complete_progress(progress,[r['index'] for r in plan['windows']])
    return {'ready':True,'reason':'original_owner_ended_and_all_windows_collected',
        'owner':file_binding(owner_path),'process':identity,'terminal_result':file_binding(terminal),
        'progress':file_binding(after),'completion_resources':file_binding(resource),
        'new_geometry_workers_started':0}


def handoff(plan_path,run_path,output):
    from genlaw_pipeline.cell_windows import checked_file,file_binding
    from genlaw_pipeline.native_collection import snapshot
    from genlaw_pipeline.run import write
    from step2gcode_demo.validation.local_resource_limits import require_bounded_worker
    require_bounded_worker();ready=prerequisite(plan_path,run_path)
    if not ready['ready']:raise ValueError('Original collection is still live')
    progress_path=output.parent/'collection-progress.json';snapshot(plan_path,progress_path)
    progress=LOAD(progress_path);plan=LOAD(plan_path)
    require_complete_progress(progress,[r['index'] for r in plan['windows']])
    if progress!=LOAD(checked_file(ready['progress'])):
        raise ValueError('Completed window identities changed after the original driver ended')
    results=[];candidates=[]
    for entry in progress['completed_windows']:
        row=LOAD(checked_file(entry['result']));results.append(row['native_result']);candidates.extend(row['candidates'])
    if len({r['path'] for r in results})!=len(results) or len({r['path'] for r in candidates})!=len(candidates):
        raise ValueError('Duplicate native or candidate identities in handoff')
    write(output,{'schema':'genlaw.collection-material-handoff.v1','status':'ready_for_complete_material_audit',
        'collection_plan':file_binding(plan_path),'prerequisite':ready,'progress':file_binding(progress_path),
        'project':plan['context']['inputs']['project'],'build':plan['build'],'partition':plan['partition'],
        'native_results':results,'candidates':candidates,'original_window_count':len(results),
        'new_native_executions':0,'payload_equivalence_replay':'performed by each existing material chunk worker',
        'global_geometry_verified':False,'whole_process_card_complete':False,'production_approved':False,
        'source_code':file_binding(Path(__file__))})


def run(plan_path,run_path,out):
    from genlaw_pipeline.cell_windows import checked_file,file_binding
    from genlaw_pipeline.planar_resume import process_identity
    from genlaw_pipeline.admission_wait import admitted_bounded
    from genlaw_pipeline.planar_directory_entry import assemble
    from genlaw_pipeline.run import write
    plan_path=plan_path.resolve();run_path=run_path.resolve();out=out.resolve();out.relative_to(ROOT)
    out.mkdir(parents=True,exist_ok=False)
    source=file_binding(Path(__file__));plan_binding=file_binding(plan_path)
    with (out/'.followup.lock').open('a') as own_lock:
        fcntl.flock(own_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        write(out/'owner.json',{'process':process_identity(os.getpid()),'collection_plan':plan_binding,
            'collection_run':str(run_path.relative_to(ROOT)),'source_code':source,'observed_live':True})
        try:
            waiting_recorded=False
            while True:
                checked_file(source);checked_file(plan_binding);ready=prerequisite(plan_path,run_path)
                if ready['ready']:break
                if not waiting_recorded:
                    write(out/'waiting.json',ready);waiting_recorded=True
                    print(json.dumps({'waiting_for_original_owner':ready['process']['pid'],
                        'new_geometry_workers_started':0}),flush=True)
                time.sleep(15)
            # The previous owner has actually ended. Keep completed collection
            # evidence stable while the next serial stage consumes it.
            with (plan_path.parent/'.collection.lock').open('a') as collection_lock:
                fcntl.flock(collection_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                write(out/'prerequisite.json',ready)
                transfer=out/'handoff.json'
                admitted_bounded(out,'handoff',[sys.executable,Path(__file__).resolve(),'handoff',
                    '--plan',plan_path,'--run',run_path,'--output',transfer])
                value=LOAD(transfer)
                surface=assemble(checked_file(value['project']),checked_file(value['build']),
                    [checked_file(r) for r in value['native_results']],
                    [checked_file(r) for r in value['candidates']],out/'material',checked_file(value['partition']))
                final=LOAD(surface)
                if (final.get('schema')!='genlaw.planar-native-surface.v1'
                        or final.get('status')!='closed_oriented_surface'
                        or final.get('native_execution_windows')!=value['original_window_count']
                        or final.get('all_global_cells_covered_once') is not True
                        or any(final.get(k) is not False for k in ['geometry_modified','artificial_caps',
                            'production_approved','whole_process_card_complete'])):
                    raise ValueError('Material auditor did not return the complete original surface')
                write(out/'result.json',{'status':'complete_original_material_audit_finished',
                    'handoff':file_binding(transfer),'surface':file_binding(surface),'new_native_executions':0,
                    'original_window_count':value['original_window_count'],'CAD_comparison_performed':False,
                    'whole_process_card_complete':False,'production_approved':False,'source_code':source})
                print(json.dumps({'complete_material_audit':str(surface),'CAD_comparison_performed':False}),flush=True)
        except Exception as error:
            write(out/'stopped.json',{'status':'stopped_without_repeating_started_work','error_type':type(error).__name__,
                'error':str(error),'existing_outputs_retained':True,'source_code':source})
            raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['check','handoff','run'])
    for name in ['plan','run']:p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--output',type=Path);a=p.parse_args()
    if a.action!='check' and a.output is None:p.error('--output is required for handoff/run')
    if a.action=='check':print(json.dumps(prerequisite(a.plan,a.run),ensure_ascii=False),flush=True)
    elif a.action=='handoff':handoff(a.plan.resolve(),a.run.resolve(),a.output.resolve())
    else:run(a.plan,a.run,a.output)
