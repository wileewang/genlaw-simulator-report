"""Execute the frozen connection reference in separately recorded sequential stages."""
import argparse
import json
import math
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tools')]
from genlaw_pipeline.admission_wait import admitted_bounded as bounded
from genlaw_pipeline.cell_windows import (file_binding,checked_file,candidate_engine,
    expected_grid,partition,window_budget,inspect_and_plan,run_window)
from genlaw_pipeline.preparation import prepare
from genlaw_pipeline.programming import checked_plan,check_nc,simulation_project,verify_binding
from genlaw_pipeline.profile_window_qualification import checked_qualification
from genlaw_pipeline.checkpoint_context import native_checkpoint,subset_policy,make_context,checked_context
from genlaw_pipeline.sharded_measurement import measure_shards
from genlaw_pipeline.run import write,FREECAD,workspace_environment,camotics_budget
import genlaw_pipeline.run as pipeline_run
import run_window_checkpoints as checkpoint_runner
from run_profile_window_regression import observe as observe_final

LOAD=lambda p:json.loads(p.read_text())
INPUT=ROOT/'validation/feature-inputs/S4-CONNECTION-REFERENCE-20261029-r2/connection-100mm/input.json'
ENGINE=ROOT/'engines/camotics/releases/1.2.0-genlaw-window-r3/manifest.json'
BASE=ROOT/'validation/runs/GRID-WINDOW-REGRESSION-20261029-r1/summary.json'
QUALIFICATION=ROOT/'validation/runs/PROFILE-WINDOW-REGRESSION-20261029-r3/summary.json'


def source_files():
    return [file_binding(p) for p in sorted((ROOT/'genlaw_pipeline').glob('*.py'))]+[
        file_binding(ROOT/name) for name in ['tools/connection_reference_stages.py',
            'tools/run_window_checkpoints.py','tools/run_profile_window_regression.py','tools/postprocess_window.py']]


def checked(run,programmed=True):
    intent=LOAD(run/'intent.json')
    for entry in intent['sources']:checked_file(entry)
    checked_file(intent['input']);checked_file(intent['base_engine_regression'])
    checked_qualification(checked_file(intent['profile_window_regression']),checked_file(intent['engine_manifest']))
    plan=checked_plan(INPUT,run/'preparation-result.json')
    if programmed:
        generated=LOAD(run/'programmed.json')
        for key in ('cam','nc','project','window_plan','reference'):checked_file(generated[key])
        cam=LOAD(checked_file(generated['cam']))
        verify_binding(run/'program.nc',cam['nc_sha256']);check_nc((run/'program.nc').read_text(),plan)
        if LOAD(run/'simulation.camotics')!=simulation_project(plan):raise ValueError('Original stock or resource configuration changed')
    return intent,plan


def initialize(run):
    # Qualification precedes creating a new run or asking FreeCAD to generate NC.
    checked_qualification(QUALIFICATION,ENGINE);candidate_engine(ENGINE)
    base=LOAD(BASE)
    if base['status']!='passed' or base['engine_manifest']!=file_binding(ENGINE):
        raise ValueError('Complete-engine qualification differs')
    run.mkdir(parents=True,exist_ok=False)
    preparation=prepare(INPUT);write(run/'preparation-result.json',preparation)
    if preparation['status']!='ready_for_declared_scope':raise ValueError('Frozen connection input is not ready')
    plan=checked_plan(INPUT,run/'preparation-result.json')
    if plan['schema']!='genlaw.execution-plan.v11':raise ValueError('Expected the frozen profiled connection reference')
    write(run/'execution-plan.json',plan)
    write(run/'intent.json',{'schema':'genlaw.staged-connection-intent.v1','input':file_binding(INPUT),
        'engine_manifest':file_binding(ENGINE),'base_engine_regression':file_binding(BASE),
        'profile_window_regression':file_binding(QUALIFICATION),'sources':source_files(),
        'source_code':file_binding(Path(__file__)),'production_approved':False,'whole_process_card_complete':False})
    config=simulation_project(plan);grid=expected_grid(config);windows=partition(grid['steps'])
    write(run/'simulation-budget.json',{'legacy_full_stock':camotics_budget(config),'global_grid':grid,
        'window_count':len(windows),'windows':[{'bounds':w,**window_budget(w,grid['steps'])} for w in windows],
        'original_stock_dimensions_preserved':True,'native_limits_unchanged':True})
    write(run/'request.json',{'input':str(INPUT),'preparation':str(run/'preparation-result.json'),'output':str(run)})
    env=workspace_environment();env.update(QT_QPA_PLATFORM='offscreen',GENLAW_CAM_REQUEST=str(run/'request.json'))
    bounded(run,'freecad',[FREECAD,'freecadcmd',ROOT/'genlaw_pipeline/freecad_worker.py'],env)
    cam=LOAD(run/'cam-result.json')
    if cam['status']!='generated_and_checked':raise ValueError('Native CAM failed: '+str(cam.get('error')))
    verify_binding(run/'program.nc',cam['nc_sha256']);check_nc((run/'program.nc').read_text(),plan)
    write(run/'simulation.camotics',config)
    cell=inspect_and_plan(run/'simulation.camotics',ENGINE,run/'windows')
    if LOAD(cell)['windows']!=windows or LOAD(cell)['grid']!=grid:
        raise ValueError('Native grid differs from the independent preflight')
    checked(run,programmed=False)
    write(run/'programmed.json',{'status':'native_nc_generated_and_checked',
        'cam':file_binding(run/'cam-result.json'),'nc':file_binding(run/'program.nc'),
        'project':file_binding(run/'simulation.camotics'),'window_plan':file_binding(cell),
        'reference':file_binding(run/'native-geometry.json'),'source_code':file_binding(Path(__file__)),
        'material_simulated':False,'production_approved':False})
    print(json.dumps({'status':'nc_ready','windows':len(windows),'native_checkpoints':len(cam['checkpoint_prefixes'])}),flush=True)


def case_path(run,key):
    if key=='final':return run
    keys=[r['id'] for r in LOAD(run/'cam-result.json')['checkpoint_prefixes']]
    if key not in keys:raise ValueError('Unknown native checkpoint')
    # FreeCAD already owns checkpoints/<id> with immutable native NC prefixes.
    return run/'fine-checkpoints'/key


def checked_case(run,key):
    checked(run);case=case_path(run,key)
    if key!='final':
        context=checked_context(case/'context.json')
        if context['files']['parent_cam']!=file_binding(run/'cam-result.json'):
            raise ValueError('Checkpoint belongs to another complete NC job')
    return case


def initialize_checkpoint(run,key):
    checked(run)
    if key=='final':raise ValueError('The final case is initialized with the CAM job')
    c=native_checkpoint(run,key,INPUT.parent/'software-policy.json');case=case_path(run,key)
    case.mkdir(parents=True,exist_ok=False)
    for name in ('program.nc','prefix-binding.json','native-geometry.json','target-display.stl','target-display.json'):
        shutil.copy2(c['source']/name,case/name)
    write(case/'software-policy.json',subset_policy(c));write(case/'simulation.camotics',simulation_project(c['plan']))
    make_context(run,key,INPUT.parent/'software-policy.json',case,case/'context.json')
    inspect_and_plan(case/'simulation.camotics',ENGINE,case/'windows')
    write(case/'staged-intent.json',{'parent_intent':file_binding(run/'intent.json'),
        'context':file_binding(case/'context.json'),'source_code':file_binding(Path(__file__))})
    print(json.dumps({'checkpoint_initialized':key,'windows':len(LOAD(case/'windows/plan.json')['windows'])}),flush=True)


def windows(run,key,start,end):
    case=checked_case(run,key);cell=case/'windows/plan.json';count=len(LOAD(cell)['windows'])
    if not 0<=start<end<=count or end-start>8:raise ValueError('Execute at most eight explicit windows per stage')
    for i in range(start):
        if not (case/'windows'/f'window-{i:03d}'/'sections.json').exists():
            raise ValueError('Earlier windows must finish before the next sequential stage')
    receipt=case/f'window-stage-{start:03d}-{end:03d}.json'
    if receipt.exists():raise ValueError('This window stage is already complete; inspect its receipt')
    results=[]
    for i in range(start,end):
        folder=case/'windows'/f'window-{i:03d}'
        # Existing partial directories require separate inspection and recovery.
        run_window(cell,i,folder)
        bounded(folder,'postprocess',[sys.executable,ROOT/'tools/postprocess_window.py','--plan',cell,'--directory',folder])
        results.append({'index':i,'native':file_binding(folder/'result.json'),
            'audit':file_binding(folder/'audit.json'),'cache':file_binding(folder/'sections.json')})
        print(json.dumps({'case':key,'completed_window':i+1,'total_windows':count}),flush=True)
    checked_case(run,key)
    write(receipt,{'status':'window_stage_completed','source_code':file_binding(Path(__file__)),
        'plan':file_binding(cell),'windows':results,'native_limits_increased':False})


def measure(run,key):
    case=checked_case(run,key);cell=case/'windows/plan.json'
    bounded(case,'surface-audit',[sys.executable,ROOT/'genlaw_pipeline/sharded_surface.py','aggregate',
        '--plan',cell,'--directory',case/'windows','--output',case/'surface.json'])
    measure_shards(case,case/'surface.json',case/'native-geometry.json',case/'windows',case/'metrics.json')
    checked_case(run,key)
    if key=='final':
        metrics=LOAD(case/'metrics.json');intent=LOAD(run/'intent.json')
        write(run/'run-result.json',{'schema':'genlaw.window-chain-run.v1','status':'original_size_global_geometry_measured',
            'input':intent['input'],'engine_manifest':intent['engine_manifest'],'regression':intent['base_engine_regression'],
            'profile_window_regression':intent['profile_window_regression'],'source_code':file_binding(Path(__file__)),
            'surface':file_binding(case/'surface.json'),'metrics':file_binding(case/'metrics.json'),
            'iou_percent':metrics['iou_percent'],'nc':file_binding(case/'program.nc'),
            'project':file_binding(case/'simulation.camotics'),'native_cad_binding':file_binding(case/'native-geometry.json'),
            'window_count':len(LOAD(cell)['windows']),'process_checkpoint_verification':'pending_actual_prefix_windows',
            'feature_acceptance':'pending_actual_mesh_observations','display_and_replay':'pending_actual_mesh_export',
            'production_approved':False,'whole_process_card_complete':False})
    print(json.dumps({'case':key,'iou_percent':LOAD(case/'metrics.json')['iou_percent']}),flush=True)


def features(run,key):
    case=checked_case(run,key)
    if key=='final':
        output=observe_final(case/'sharded-measure-plan.json',INPUT.parent/'software-policy.json',case/'features')
        report=LOAD(output)
    else:report=checkpoint_runner.observe(case)
    if not report['all_software_geometry_checks_passed']:raise ValueError('Actual geometry checks failed: '+key)
    checked_case(run,key)
    if key!='final':
        metrics=LOAD(case/'metrics.json');surface=LOAD(case/'surface.json')
        write(case/'checkpoint-result.json',{'schema':'genlaw.fine-window-checkpoint.v1','status':'software_geometry_passed',
            'id':key,'context':file_binding(case/'context.json'),'surface':file_binding(case/'surface.json'),
            'metrics':file_binding(case/'metrics.json'),'verification':file_binding(case/'features/verification.json'),
            'iou_percent':metrics['iou_percent'],'checks_passed':len(report['software_regression']),
            'actual_volume_mm3':surface['volume_mm3'],'window_count':len(surface['windows']),'resolution_mm':.025,
            'legacy_comparison':None,'fine_checkpoint_acceptance':True,'source_code':file_binding(Path(__file__)),
            'production_approved':False,'whole_process_card_complete':False})
    print(json.dumps({'case':key,'geometry_checks_passed':len(report['software_regression'])}),flush=True)


def requirements(run):
    checked(run)
    bounded(run,'manufacturing-requirements',[sys.executable,ROOT/'genlaw_pipeline/sharded_requirements.py',
        '--run',run,'--observations',run/'features/verification.json','--output',run/'requirement-verification.json'])


def finish(run):
    intent,plan=checked(run);cam=LOAD(run/'cam-result.json');records=[]
    bounds=plan['stock']['bounds_mm'];volumes=[math.prod(b-a for a,b in zip(bounds['min'],bounds['max']))]
    for row in cam['checkpoint_prefixes']:
        case=checked_case(run,row['id']);result=LOAD(case/'checkpoint-result.json')
        for key in ('context','surface','metrics','verification'):checked_file(result[key])
        report=LOAD(checked_file(result['verification']));surface=LOAD(checked_file(result['surface']))
        metrics=LOAD(checked_file(result['metrics']));observed=LOAD(checked_file(report['plan']))
        context=checked_context(case/'context.json')
        if (not result['fine_checkpoint_acceptance'] or not report['all_software_geometry_checks_passed']
                or result['surface']!=metrics['surface'] or metrics['surface']!=observed['surface']
                or observed['reference']!=context['files']['reference'] or metrics['reference']!=observed['reference']
                or result['actual_volume_mm3']!=surface['volume_mm3'] or result['iou_percent']!=metrics['iou_percent']):
            raise ValueError('A cumulative checkpoint did not pass')
        records.append({'id':row['id'],'result':file_binding(case/'checkpoint-result.json')})
        volumes.append(result['actual_volume_mm3'])
    volumes.append(LOAD(run/'surface.json')['volume_mm3'])
    if not all(a>b for a,b in zip(volumes,volumes[1:])):raise ValueError('Actual cumulative material must decrease')
    final=LOAD(run/'features/verification.json');requirement=LOAD(run/'requirement-verification.json')
    final_observed=LOAD(checked_file(final['plan']));metrics=LOAD(run/'metrics.json')
    if (not final['all_software_geometry_checks_passed'] or requirement['actual_observations']!=file_binding(run/'features/verification.json')
            or metrics['surface']!=file_binding(run/'surface.json') or final_observed['surface']!=metrics['surface']
            or final_observed['reference']!=metrics['reference'] or requirement['surface']!=metrics['surface']):
        raise ValueError('Final geometry or manufacturing observations are not bound')
    write(run/'fine-checkpoints/summary.json',{'schema':'genlaw.fine-window-checkpoints.v1','status':'fine_checkpoints_passed',
        'intent':file_binding(run/'intent.json'),'engine_manifest':intent['engine_manifest'],'checkpoints':records,
        'final_metrics':file_binding(run/'metrics.json'),'full_nc':file_binding(run/'program.nc'),
        'actual_volumes_mm3':volumes,'material_volume_monotonically_decreases':True,
        'original_stock_used':True,'expected_cad_used_as_stock':False,'resolution_mm':.025,
        'observer_source':file_binding(ROOT/'genlaw_pipeline/sharded_verification.py'),
        'context_source':file_binding(ROOT/'genlaw_pipeline/checkpoint_context.py'),'legacy_comparison_passed':False,
        'source_code':file_binding(Path(__file__)),'production_approved':False,'whole_process_card_complete':False})
    write(run/'completion.json',{'status':'reference_geometry_measured_with_requirement_results',
        'final':file_binding(run/'run-result.json'),'checkpoints':file_binding(run/'fine-checkpoints/summary.json'),
        'software_geometry':file_binding(run/'features/verification.json'),
        'manufacturing_requirements':file_binding(run/'requirement-verification.json'),
        'defined_numerical_checks_passed':requirement['all_defined_numerical_requirements_passed'],
        'source_code':file_binding(Path(__file__)),'whole_process_card_complete':False,'production_approved':False})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage',choices=['initialize','checkpoint-initialize','windows','measure','features','requirements','finish'])
    p.add_argument('--run',type=Path,required=True);p.add_argument('--case',default='final')
    p.add_argument('--start',type=int);p.add_argument('--end',type=int)
    a=p.parse_args();run=a.run.resolve();run.relative_to(ROOT)
    pipeline_run.bounded=bounded;checkpoint_runner.bounded=bounded
    if a.stage=='initialize':initialize(run)
    elif a.stage=='checkpoint-initialize':initialize_checkpoint(run,a.case)
    elif a.stage=='windows':windows(run,a.case,a.start,a.end)
    elif a.stage=='measure':measure(run,a.case)
    elif a.stage=='features':features(run,a.case)
    elif a.stage=='requirements':requirements(run)
    else:finish(run)
