"""Bounded single-hole workflow: checked card -> FreeCAD -> unchanged NC -> CAMotics."""
import argparse
import json
import os
from pathlib import Path
import sys

from .preparation import digest, prepare
from .programming import check_nc, checked_plan, simulation_project, verify_binding
from .drilling_geometry import operations
from .measurement import measure_bounded
from .simulation_engine import configured_binary, selected_engine, simulation_environment, workspace_environment

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'docs/checkpoints/2026-10-27/freecad-cam-predevelopment-01'))
from run_check import capacity
from local_resource_limits import run_bounded,camotics_budget

FREECAD=ROOT/'step2gcode_demo/out_external_tools/freecad-1.1.4-linux/squashfs-root/AppRun'
CAMOTICS=configured_binary()


def write(path,value):
    with path.open('x') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')


def bounded(out,label,command,env=None,project=None):
    # Keep temporary files and caches local even for Python tests/measurements.
    if env is None:env=workspace_environment()
    cap=capacity();write(out/(label+'-capacity.json'),cap)
    if not (cap['eligible_cpus'] and cap['mem_available_bytes']>3*1024**3 and cap['disk_free_bytes']>4.5*1024**3):
        raise RuntimeError('Capacity unavailable; '+label+' not started')
    affinity=os.sched_getaffinity(0)
    try:
        os.sched_setaffinity(0,{cap['eligible_cpus'][0]})
        result=run_bounded(command,log_path=out/(label+'.log'),report_path=out/(label+'-resources.json'),
                           env=env,project_path=project,timeout_seconds=60)
    finally:
        os.sched_setaffinity(0,affinity)
    if not result['success']:
        raise RuntimeError(label+' failed: '+result['status'])
    return result


def simulation_env():
    if selected_engine()['binary'] != CAMOTICS:
        raise ValueError('Engine selection changed after import; restart the run')
    return simulation_environment()


def run(input_path,out):
    input_path=input_path.resolve();out=out.resolve()
    out.mkdir(parents=True,exist_ok=False)
    record={'schema':'genlaw.pipeline-run.v1','status':'started','input_sha256':digest(input_path),
            'input_id':None,'production_approved':False,'whole_process_card_complete':False,
            'native_stages':[],'source_code_sha256':{p.name:digest(p) for p in Path(__file__).parent.glob('*.py')}}
    try:
        preparation=prepare(input_path);write(out/'preparation-result.json',preparation)
        record['input_id']=preparation.get('input_id')
        if preparation['status']!='ready_for_declared_scope':
            record.update(status='blocked_at_preparation',issues=preparation['issues'])
            return record
        plan=checked_plan(input_path,out/'preparation-result.json')
        write(out/'execution-plan.json',plan)
        configuration=simulation_project(plan)
        budget=camotics_budget(configuration);write(out/'simulation-budget.json',budget)
        if not budget['allowed']:
            record.update(status='blocked_at_resource_budget',workload_budget=budget,
                          error='Declared stock and resolution exceed the local grid-equivalent budget; no native stage started.')
            return record
        request={'input':str(input_path),'preparation':str(out/'preparation-result.json'),'output':str(out)}
        write(out/'request.json',request)
        from .coordinate_backend import engine_for_plan,native_arguments,native_environment,initialize_controller,verify_initialization
        engine=engine_for_plan(plan);binary=engine['binary']
        env=workspace_environment();env['GENLAW_CAM_REQUEST']=str(out/'request.json');env['QT_QPA_PLATFORM']='offscreen'
        bounded(out,'freecad',[FREECAD,'freecadcmd',ROOT/'genlaw_pipeline/freecad_worker.py'],env)
        cam=json.loads((out/'cam-result.json').read_text())
        if cam['status']!='generated_and_checked':raise ValueError('Native CAM did not succeed: '+str(cam.get('error')))
        record['native_stages'].append('freecad_cam')
        nc=out/'program.nc';verify_binding(nc,cam['nc_sha256']);check_nc(nc.read_text(),plan)
        initial=initialize_controller(out,plan)
        project=out/'simulation.camotics';write(project,configuration)
        project_hash=digest(project)
        if 'fixture_inspection' in plan:
            from .fixture_runtime import execute as inspect_fixtures
            fixture_result=inspect_fixtures(out,plan)
            record['native_stages'].append('modeled_fixture_clearance')
            record['fixture_verification_sha256']=digest(out/'fixture-verification.json')
            record['modeled_fixture_clearance_status']=fixture_result['status']
            if not fixture_result['modeled_clearance_passed']:
                record.update(status='blocked_at_fixture_clearance',
                    material_simulation_started=False,
                    error='Declared fixture/tool envelopes collide or requested clearance is indeterminate; see fixture-verification.json.')
                return record
        bounded(out,'camotics',native_arguments(plan,engine)+[project,out/'simulated.stl'],native_environment(engine),project)
        verify_binding(nc,cam['nc_sha256']);verify_binding(project,project_hash)
        verify_binding(binary,engine['manifest']['binary_sha256']);verify_initialization(out,plan,initial)
        simulation={'schema':'genlaw.simulation-result.v1','status':'executed',
                    'nc_sha256_before':cam['nc_sha256'],'nc_sha256_after':digest(nc),'nc_text_modified':False,
                    'project_sha256':project_hash,'mesh_sha256':digest(out/'simulated.stl'),
                    'engine_binary_sha256':digest(binary),'resolution_mm':configuration['resolution'],
                    'engine_build_id':engine['manifest']['id'],'engine_manifest_sha256':engine['manifest_sha256'],
                    'drill_model':'CAMotics conical tip; length is tip cone height, not total tool/holder length.',
                    'fixture_collision_verified':False,'material_physics_verified':False,**initial,
                    **({'native_resources_sha256':digest(out/'camotics-resources.json')} if initial else {})}
        if plan['schema'] in ('genlaw.execution-plan.v4','genlaw.execution-plan.v5','genlaw.execution-plan.v6','genlaw.execution-plan.v7','genlaw.execution-plan.v8','genlaw.execution-plan.v9','genlaw.execution-plan.v10','genlaw.execution-plan.v11','genlaw.execution-plan.v12','genlaw.execution-plan.v13'):
            del simulation['drill_model']
            simulation['tool_model']='CAMotics flat cylindrical cutting segment; shank and holder excluded.'
        if plan['schema']=='genlaw.execution-plan.v14':
            del simulation['drill_model']
            simulation['tool_model']='Explicit 90-degree finite frustum including flat tip; cone height excludes shank and holder.'
            simulation['initial_machine_state']=plan['setup']['startup']
        if plan['schema'] in ('genlaw.execution-plan.v9','genlaw.execution-plan.v10','genlaw.execution-plan.v11','genlaw.execution-plan.v12','genlaw.execution-plan.v13'):simulation['initial_machine_state']=plan['setup']['startup']
        if 'fixture_inspection' in plan:
            simulation['modeled_fixture_clearance']={'status':fixture_result['status'],
                'scope':fixture_result['scope'],'report_sha256':digest(out/'fixture-verification.json')}
        write(out/'simulation-result.json',simulation);record['native_stages'].append('camotics')
        measure_bounded(out,'measure',out,out/'simulated.stl',out/'metrics.json',display=True)
        metrics=json.loads((out/'metrics.json').read_text())
        if plan['schema'] in ('genlaw.execution-plan.v9','genlaw.execution-plan.v11','genlaw.execution-plan.v12','genlaw.execution-plan.v13'):
            from .sequence_checkpoints import run_checkpoints
            run_checkpoints(out,plan,cam,engine)
            record['material_checkpoints_sha256']=digest(out/'material-checkpoints.json')
            record['native_stages'].append('cumulative_material_checkpoints')
        bounded(out,'verify-features',[sys.executable,ROOT/'genlaw_pipeline/verification.py','--run',out,'--output',out/'feature-verification.json'])
        features=json.loads((out/'feature-verification.json').read_text())
        if 'declared_inspection' in plan:
            bounded(out,'verify-declared-dimensions',[sys.executable,ROOT/'genlaw_pipeline/declared_dimensions.py',
                    '--run',out,'--output',out/'dimension-verification.json'])
            dimensions=json.loads((out/'dimension-verification.json').read_text())
            record['native_stages'].append('declared_dimension_inspection')
            record['dimension_verification_sha256']=digest(out/'dimension-verification.json')
            record['declared_dimension_checks']=dimensions['requirements']
            record['all_declared_dimensions_passed']=dimensions['dimension_inspection_passed']
        # Confirm all source bindings once more before declaring the software run complete.
        checked_plan(input_path,out/'preparation-result.json')
        record.update(status='software_chain_measured',execution_plan_sha256=preparation['execution_plan_sha256'],
                      nc_sha256=digest(nc),simulation_config_sha256=project_hash,mesh_sha256=simulation['mesh_sha256'],
                      iou=metrics['iou'],iou_percent=metrics['iou_percent'],
                      requirement_readiness=preparation['requirement_readiness'],coverage=preparation['coverage'],
                      full_tolerance_acceptance=False,source_pdf_automatically_recognized=False,
                      metrics_sha256=digest(out/'metrics.json'))
        record['feature_verification_sha256']=digest(out/'feature-verification.json')
        record['requirement_checks']=features['requirements']
        measured_steps={op['step_id'] for op in operations(plan)}
        for item in record['coverage']:
            if item['step_id'] in measured_steps:item['status']='software_geometry_measured'
    except Exception as error:
        record.update(status='failed',error=str(error))
    finally:
        write(out/'run-result.json',record)
        print(json.dumps({'status':record['status'],'error':record.get('error'),'iou_percent':record.get('iou_percent'),'output':str(out)},ensure_ascii=False),flush=True)
    return record


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    result=run(args.input,args.output)
    sys.exit(0 if result['status']=='software_chain_measured' else 2 if result['status'] in ('blocked_at_preparation','blocked_at_resource_budget','blocked_at_fixture_clearance') else 1)
