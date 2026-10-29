"""Fine-resolution material gates for native NC prefixes, with bounded shards."""
import argparse
import json
import math
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.cell_windows import file_binding,checked_file,candidate_engine,inspect_and_plan,run_window,expected_grid
from genlaw_pipeline.checkpoint_context import native_checkpoint,subset_policy,make_context,checked_context
from genlaw_pipeline.programming import simulation_project
from genlaw_pipeline.run import bounded,write
from genlaw_pipeline.sharded_measurement import measure_shards

LOAD=lambda p:json.loads(p.read_text())


def observe(case):
    worker=ROOT/'genlaw_pipeline/sharded_verification.py';out=case/'features';out.mkdir()
    plan=out/'plan.json'
    bounded(out,'plan',[sys.executable,worker,'plan','--measure-plan',case/'sharded-measure-plan.json',
        '--policy',case/'software-policy.json','--checkpoint-context',case/'context.json','--output',plan])
    data=LOAD(plan)
    for mode,rows in [('rays',data['ray_groups']),('section',data['heights'])]:
        for i in range(len(rows)):
            bounded(out,f'{mode}-{i:03d}',[sys.executable,worker,mode,'--plan',plan,'--index',str(i),
                '--output',out/f'{mode}-{i:03d}.json'])
    bounded(out,'finish',[sys.executable,worker,'finish','--plan',plan,'--directory',out,'--output',out/'verification.json'])
    return LOAD(out/'verification.json')


def control_windows(config):
    steps=expected_grid(config)['steps'];pivots=[n//2 for n in steps]
    return [[x0,y0,z0,x1,y1,z1] for x0,x1 in [(0,pivots[0]),(pivots[0],steps[0])]
        for y0,y1 in [(0,pivots[1]),(pivots[1],steps[1])]
        for z0,z1 in [(0,pivots[2]),(pivots[2],steps[2])]]


def compare_legacy(case,source):
    bounded(case,'legacy-facets',[sys.executable,ROOT/'genlaw_pipeline/sharded_surface.py','compare',
        '--surface',case/'surface.json','--canonical',source/'simulated.stl',
        '--output',case/'legacy-facets.json','--combined',case/'combined.stl'])
    old=LOAD(source/'software-verification.json');new=LOAD(case/'features/verification.json')
    project=lambda report:[(r['feature_id'],r['kind'],r['status'],r['criterion']) for r in report['software_regression']]
    old_metrics=LOAD(source/'metrics.json');new_metrics=LOAD(case/'metrics.json')
    if project(old)!=project(new):raise ValueError('Checkpoint geometry verdicts differ from original gates')
    delta=abs(old_metrics['iou']-new_metrics['iou'])
    if delta>1e-10:raise ValueError('Global sharded IoU differs from legacy material checkpoint')
    write(case/'legacy-comparison.json',{'status':'passed','old_verification':file_binding(source/'software-verification.json'),
        'old_metrics':file_binding(source/'metrics.json'),'new_verification':file_binding(case/'features/verification.json'),
        'new_metrics':file_binding(case/'metrics.json'),'absolute_iou_difference':delta,
        'criteria_and_verdicts_unchanged':True,'source_code':file_binding(Path(__file__))})


def run(parent,out,engine,regression,policy,legacy=False,checkpoint_regression=None):
    parent,out,engine,regression,policy=[p.resolve() for p in (parent,out,engine,regression,policy)]
    for p in (parent,out,engine,regression,policy):p.relative_to(ROOT)
    record=LOAD(regression)
    if (record.get('status')!='passed' or record.get('engine_manifest')!=file_binding(engine)
            or len(record.get('normal_cases',[]))!=4 or len(record.get('controls',[]))!=2
            or not all(r['regression_passed'] for r in record['normal_cases']+record['controls']+record['default_cases'])):
        raise ValueError('Fine checkpoint execution requires frozen complete-engine regression')
    if not legacy:
        if not checkpoint_regression:raise ValueError('Original-size run requires the small checkpoint regression')
        small=LOAD(checkpoint_regression)
        if (small.get('status')!='fine_checkpoints_passed' or not small.get('legacy_comparison_passed')
                or len(small['checkpoints'])!=2 or small['engine_manifest']!=file_binding(engine)
                or small['observer_source']!=file_binding(ROOT/'genlaw_pipeline/sharded_verification.py')
                or small['context_source']!=file_binding(ROOT/'genlaw_pipeline/checkpoint_context.py')
                or small['source_code']!=file_binding(Path(__file__))):
            raise ValueError('Small checkpoint regression does not bind the current observer')
        for row in small['checkpoints']:checked_file(row['result'])
    candidate_engine(engine)
    cam=LOAD(parent/'cam-result.json');keys=[r['id'] for r in cam['checkpoint_prefixes']]
    contexts=[native_checkpoint(parent,key,policy) for key in keys]
    if simulation_project(contexts[0]['plan'])['resolution']!=.025:raise ValueError('Expected frozen 0.025 mm fine checkpoint configuration')
    out.mkdir(parents=True,exist_ok=False)
    write(out/'intent.json',{'parent_cam':file_binding(parent/'cam-result.json'),'full_nc':file_binding(parent/'program.nc'),
        'engine_manifest':file_binding(engine),'engine_regression':file_binding(regression),
        'observer_source':file_binding(ROOT/'genlaw_pipeline/sharded_verification.py'),
        'context_source':file_binding(ROOT/'genlaw_pipeline/checkpoint_context.py'),
        'checkpoint_regression':file_binding(checkpoint_regression) if checkpoint_regression else None,
        'expected_checkpoints':keys,'resolution_mm':.025,'source_code':file_binding(Path(__file__))})
    records=[];stock=contexts[0]['plan']['stock']['bounds_mm'];volumes=[math.prod(b-a for a,b in zip(stock['min'],stock['max']))]
    for c in contexts:
        case=out/c['key'];case.mkdir()
        for name in ('program.nc','prefix-binding.json','native-geometry.json','target-display.stl','target-display.json'):
            shutil.copy2(c['source']/name,case/name)
        write(case/'software-policy.json',subset_policy(c));config=simulation_project(c['plan']);write(case/'simulation.camotics',config)
        make_context(parent,c['key'],policy,case,case/'context.json')
        plan=inspect_and_plan(case/'simulation.camotics',engine,case/'windows',control_windows(config) if legacy else None)
        windows=LOAD(plan)['windows']
        for index in range(len(windows)):
            target=case/'windows'/f'window-{index:03d}';run_window(plan,index,target)
            bounded(target,'postprocess',[sys.executable,ROOT/'tools/postprocess_window.py','--plan',plan,'--directory',target])
            print(json.dumps({'checkpoint':c['key'],'completed_windows':index+1,'total_windows':len(windows)}),flush=True)
        bounded(case,'surface-audit',[sys.executable,ROOT/'genlaw_pipeline/sharded_surface.py','aggregate',
            '--plan',plan,'--directory',case/'windows','--output',case/'surface.json'])
        measure_shards(case,case/'surface.json',case/'native-geometry.json',case/'windows',case/'metrics.json')
        report=observe(case);metrics=LOAD(case/'metrics.json');surface=LOAD(case/'surface.json')
        checked_context(case/'context.json')
        if not report['all_software_geometry_checks_passed']:raise ValueError('Checkpoint geometry gate failed: '+c['key'])
        if legacy:compare_legacy(case,c['source'])
        result={'schema':'genlaw.fine-window-checkpoint.v1','status':'software_geometry_passed','id':c['key'],
            'context':file_binding(case/'context.json'),'surface':file_binding(case/'surface.json'),
            'metrics':file_binding(case/'metrics.json'),'verification':file_binding(case/'features/verification.json'),
            'iou_percent':metrics['iou_percent'],'checks_passed':len(report['software_regression']),
            'actual_volume_mm3':surface['volume_mm3'],'window_count':len(windows),'resolution_mm':.025,
            'legacy_comparison':file_binding(case/'legacy-comparison.json') if legacy else None,
            'fine_checkpoint_acceptance':True,'production_approved':False}
        write(case/'checkpoint-result.json',result);records.append({'id':c['key'],'result':file_binding(case/'checkpoint-result.json')})
        volumes.append(surface['volume_mm3']);print(json.dumps({'checkpoint_passed':c['key'],'iou_percent':metrics['iou_percent'],'checks':result['checks_passed']}),flush=True)
    final_metrics=LOAD(parent/'metrics.json')
    volumes.append(final_metrics['actual_mesh']['volume_mm3'] if legacy else LOAD(parent/'surface.json')['volume_mm3'])
    if not all(a>b for a,b in zip(volumes,volumes[1:])):raise ValueError('Fine actual material volumes must strictly decrease')
    if [r['id'] for r in records]!=keys:raise ValueError('Missing or reordered material checkpoint')
    write(out/'summary.json',{'schema':'genlaw.fine-window-checkpoints.v1','status':'fine_checkpoints_passed',
        'intent':file_binding(out/'intent.json'),'engine_manifest':file_binding(engine),'checkpoints':records,
        'final_metrics':file_binding(parent/'metrics.json'),'full_nc':file_binding(parent/'program.nc'),
        'actual_volumes_mm3':volumes,'material_volume_monotonically_decreases':True,
        'original_stock_used':True,'expected_cad_used_as_stock':False,'resolution_mm':.025,
        'observer_source':file_binding(ROOT/'genlaw_pipeline/sharded_verification.py'),
        'context_source':file_binding(ROOT/'genlaw_pipeline/checkpoint_context.py'),
        'legacy_comparison_passed':legacy,'source_code':file_binding(Path(__file__)),
        'production_approved':False,'whole_process_card_complete':False})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('parent','output','engine','regression','policy'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--compare-legacy',action='store_true');p.add_argument('--checkpoint-regression',type=Path)
    a=p.parse_args();run(a.parent,a.output,a.engine,a.regression,a.policy,a.compare_legacy,a.checkpoint_regression)
