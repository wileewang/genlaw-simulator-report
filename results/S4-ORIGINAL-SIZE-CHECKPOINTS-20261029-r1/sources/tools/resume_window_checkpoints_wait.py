"""Resume admitted fine checkpoints without replacing completed native evidence."""
import argparse
import json
import math
from pathlib import Path
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'tools')]
from genlaw_pipeline.cell_windows import file_binding,checked_file,inspect_and_plan,run_window
from genlaw_pipeline.checkpoint_context import native_checkpoint,subset_policy,make_context,checked_context
from genlaw_pipeline.programming import simulation_project
from genlaw_pipeline.run import write
from genlaw_pipeline.admission_wait import admitted_bounded as bounded
LOAD=lambda p:json.loads(p.read_text())


def measure_shards(out,surface,reference,caches,output):
    worker=ROOT/'genlaw_pipeline/sharded_measure.py';plan=out/'sharded-measure-plan.json'
    bounded(out,'sharded-measure-plan',[sys.executable,worker,'plan','--surface',surface,
        '--reference',reference,'--caches',caches,'--output',plan])
    parts=out/'sharded-measure-parts';parts.mkdir()
    for i in range(len(LOAD(plan)['parts'])):
        bounded(out,f'sharded-measure-{i:03d}',[sys.executable,worker,'part','--plan',plan,
            '--index',str(i),'--output',parts/f'part-{i:03d}.json'])
        print(json.dumps({'measured_global_height_part':i+1}),flush=True)
    bounded(out,'sharded-measure-finish',[sys.executable,worker,'finish','--plan',plan,'--parts',parts,'--output',output])


def observe(case):
    worker=ROOT/'genlaw_pipeline/sharded_verification.py';out=case/'features';out.mkdir();plan=out/'plan.json'
    bounded(out,'plan',[sys.executable,worker,'plan','--measure-plan',case/'sharded-measure-plan.json',
        '--policy',case/'software-policy.json','--checkpoint-context',case/'context.json','--output',plan])
    data=LOAD(plan)
    for mode,rows in [('rays',data['ray_groups']),('section',data['heights'])]:
        for i in range(len(rows)):
            bounded(out,f'{mode}-{i:03d}',[sys.executable,worker,mode,'--plan',plan,'--index',str(i),
                '--output',out/f'{mode}-{i:03d}.json'])
    bounded(out,'finish',[sys.executable,worker,'finish','--plan',plan,'--directory',out,'--output',out/'verification.json'])
    return LOAD(out/'verification.json')


def run_admitted_window(plan,index,target,attempt):
    for number in range(20):
        try:return run_window(plan,index,target)
        except RuntimeError as error:
            if str(error)!='Capacity unavailable; camotics not started':raise
            preserve_unstarted(target,attempt/'native-admission'/f'{target.parent.parent.name}-{index:03d}-{number:02d}')
            print(json.dumps({'waiting_for_capacity':target.name,'attempt':number+1,'native_started':False}),flush=True)
            if number==19:raise RuntimeError('Capacity remained unavailable after 20 recorded admissions') from error
            time.sleep(15)


def validate_saved(case,start,end):
    from genlaw_pipeline.sharded_surface import require_bounded_worker,checked_result
    require_bounded_worker();checked_context(case/'context.json')
    plan=case/'windows/plan.json';count=len(LOAD(plan)['windows'])
    if not 0<=start<end<=count or end-start>8:raise ValueError('Invalid saved-window check batch')
    for i in range(start,end):
        path=case/'windows'/f'window-{i:03d}';record,_=checked_result(plan,path/'result.json')
        audit=LOAD(path/'audit.json');cache=LOAD(path/'sections.json')
        if audit['result']!=file_binding(path/'result.json') or cache['result']!=file_binding(path/'result.json'):
            raise ValueError('Saved postprocessing belongs to another native result')
        for key in ('surface','boundary_file','source_code'):checked_file(audit[key])
        for key in ('original_surface','array','source_code'):checked_file(cache[key])
        if audit['surface']!=record['surface'] or cache['original_surface']!=record['surface']:
            raise ValueError('Saved postprocessing uses another actual surface')
    print(json.dumps({'validated_saved_windows':[start,end]}),flush=True)


def preserve_unstarted(path,resume):
    """Only a failed admission is eligible; never discard partial native output."""
    if {p.name for p in path.iterdir()}!={'window-budget.json','camotics-capacity.json'}:
        raise ValueError('Unfinished native attempt needs inspection: '+str(path))
    cap=LOAD(path/'camotics-capacity.json')
    if cap['eligible_cpus'] and cap['mem_available_bytes']>3*1024**3 and cap['disk_free_bytes']>4.5*1024**3:
        raise ValueError('No recorded resource admission failure')
    saved=resume/'unstarted'/path.parent.parent.name/path.name;saved.parent.mkdir(parents=True,exist_ok=True)
    entries={p.name:file_binding(p) for p in path.iterdir()}
    path.rename(saved)
    write(saved/'relocation.json',{'original_directory':str(path.relative_to(ROOT)),
        'files_before_move':entries,'native_execution_started':False,
        'preserved_files':{n:file_binding(saved/n) for n in entries}})


def resume_run(out):
    out=out.resolve();out.relative_to(ROOT);intent=LOAD(out/'intent.json')
    if (out/'summary.json').exists():raise ValueError('Completed run does not need resuming')
    for k in ('parent_cam','full_nc','engine_manifest','engine_regression','observer_source','context_source','source_code'):
        checked_file(intent[k])
    regression=LOAD(checked_file(intent['checkpoint_regression']))
    if (regression['status']!='fine_checkpoints_passed' or not regression['legacy_comparison_passed']
            or regression['observer_source']!=intent['observer_source'] or regression['context_source']!=intent['context_source']):
        raise ValueError('Frozen small regression differs')
    parent=checked_file(intent['parent_cam']).parent
    request=LOAD(parent/'request.json');policy=Path(request['input']).parent/'software-policy.json'
    contexts=[native_checkpoint(parent,k,policy) for k in intent['expected_checkpoints']]
    root=out/'resumes';root.mkdir(exist_ok=True);number=len(list(root.glob('attempt-*')))+1
    attempt=root/f'attempt-{number:02d}';attempt.mkdir()
    write(attempt/'intent.json',{'original_intent':file_binding(out/'intent.json'),
        'source_code':file_binding(Path(__file__)),
        'admission_wait_source':file_binding(ROOT/'genlaw_pipeline/admission_wait.py'),
        'rules_and_native_limits_unchanged':True})
    records=[];bounds=contexts[0]['plan']['stock']['bounds_mm']
    volumes=[math.prod(b-a for a,b in zip(bounds['min'],bounds['max']))]
    for c in contexts:
        case=out/c['key']
        if not case.exists():
            case.mkdir()
            for name in ('program.nc','prefix-binding.json','native-geometry.json','target-display.stl','target-display.json'):
                shutil.copy2(c['source']/name,case/name)
            write(case/'software-policy.json',subset_policy(c));write(case/'simulation.camotics',simulation_project(c['plan']))
            make_context(parent,c['key'],policy,case,case/'context.json')
            inspect_and_plan(case/'simulation.camotics',checked_file(intent['engine_manifest']),case/'windows')
        checked_context(case/'context.json');plan=case/'windows/plan.json';count=len(LOAD(plan)['windows'])
        saved=[]
        for i in range(count):
            if (case/'windows'/f'window-{i:03d}'/'result.json').exists():saved.append(i)
        if saved!=list(range(len(saved))):raise ValueError('Saved windows are not one complete prefix')
        for start in range(0,len(saved),8):
            bounded(attempt,f'validate-{c["key"]}-{start:03d}',[sys.executable,Path(__file__),'--validate',case,
                '--start',str(start),'--end',str(min(start+8,len(saved)))])
        print(json.dumps({'checkpoint':c['key'],'verified_reused_windows':len(saved)}),flush=True)
        result_path=case/'checkpoint-result.json'
        if result_path.exists():
            result=LOAD(result_path)
            for key in ('context','surface','metrics','verification'):checked_file(result[key])
            if (len(saved)!=count or not result['fine_checkpoint_acceptance']
                    or not LOAD(checked_file(result['verification']))['all_software_geometry_checks_passed']):
                raise ValueError('Saved checkpoint is incomplete')
        else:
            for i in range(len(saved),count):
                target=case/'windows'/f'window-{i:03d}'
                if target.exists():preserve_unstarted(target,attempt)
                run_admitted_window(plan,i,target,attempt)
                bounded(target,'postprocess',[sys.executable,ROOT/'tools/postprocess_window.py','--plan',plan,'--directory',target])
                print(json.dumps({'checkpoint':c['key'],'completed_windows':i+1,'total_windows':count}),flush=True)
            bounded(case,'surface-audit',[sys.executable,ROOT/'genlaw_pipeline/sharded_surface.py','aggregate',
                '--plan',plan,'--directory',case/'windows','--output',case/'surface.json'])
            measure_shards(case,case/'surface.json',case/'native-geometry.json',case/'windows',case/'metrics.json')
            report=observe(case);checked_context(case/'context.json')
            if not report['all_software_geometry_checks_passed']:raise ValueError('Checkpoint geometry failed')
            metrics=LOAD(case/'metrics.json');surface=LOAD(case/'surface.json')
            result={'schema':'genlaw.fine-window-checkpoint.v1','status':'software_geometry_passed','id':c['key'],
                'context':file_binding(case/'context.json'),'surface':file_binding(case/'surface.json'),
                'metrics':file_binding(case/'metrics.json'),'verification':file_binding(case/'features/verification.json'),
                'iou_percent':metrics['iou_percent'],'checks_passed':len(report['software_regression']),
                'actual_volume_mm3':surface['volume_mm3'],'window_count':count,'resolution_mm':.025,
                'legacy_comparison':None,'fine_checkpoint_acceptance':True,'production_approved':False,
                'resume_intent':file_binding(attempt/'intent.json')}
            write(result_path,result)
        records.append({'id':c['key'],'result':file_binding(result_path)});volumes.append(result['actual_volume_mm3'])
        print(json.dumps({'checkpoint_passed':c['key'],'iou_percent':result['iou_percent'],'checks':result['checks_passed']}),flush=True)
    volumes.append(LOAD(parent/'surface.json')['volume_mm3'])
    if not all(a>b for a,b in zip(volumes,volumes[1:])):raise ValueError('Actual material volumes do not decrease')
    write(out/'summary.json',{'schema':'genlaw.fine-window-checkpoints.v1','status':'fine_checkpoints_passed',
        'intent':file_binding(out/'intent.json'),'engine_manifest':intent['engine_manifest'],'checkpoints':records,
        'final_metrics':file_binding(parent/'metrics.json'),'full_nc':intent['full_nc'],'actual_volumes_mm3':volumes,
        'material_volume_monotonically_decreases':True,'original_stock_used':True,'expected_cad_used_as_stock':False,
        'resolution_mm':.025,'observer_source':intent['observer_source'],'context_source':intent['context_source'],
        'legacy_comparison_passed':False,'source_code':file_binding(Path(__file__)),
        'original_runner':intent['source_code'],'resume_intent':file_binding(attempt/'intent.json'),
        'production_approved':False,'whole_process_card_complete':False})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path);p.add_argument('--validate',type=Path)
    p.add_argument('--start',type=int);p.add_argument('--end',type=int);a=p.parse_args()
    if a.validate:validate_saved(a.validate,a.start,a.end)
    elif a.output:resume_run(a.output)
    else:p.error('Specify output or validation batch')
