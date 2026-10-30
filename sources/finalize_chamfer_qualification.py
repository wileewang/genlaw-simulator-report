"""Bind completed qualification to frozen inputs, source, resource logs and replay."""
import argparse,json,shutil,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'step2gcode_demo/validation'))
from local_resource_limits import require_bounded_worker
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.programming import checked_plan,check_nc,verify_binding,simulation_project
from genlaw_pipeline.freecad_deburr import FORMAL,checked_formal_backend
from genlaw_pipeline.run import write
from tools.run_chamfer_suite import verify_case
load=lambda p:json.loads(p.read_text())


def finalize(out,inputs,attempt_label):
    require_bounded_worker();runs=out/'native-suite-01';summary=load(runs/'summary.json');replay=load(out/'replay-01/summary.json')
    if not summary['all_regression_expectations_met'] or len(summary['normal_cases'])!=4 or len(summary['controls'])!=4:
        raise ValueError('Incomplete native qualification')
    original=load(inputs/'manifest.json')
    for name,value in original['files'].items():verify_binding(inputs/name,value['sha256'])
    for name,sha in load(out/'source-before-native-01/manifest.json')['files'].items():verify_binding(ROOT/name,sha)
    checked_formal_backend(FORMAL)
    normals=[];negatives=[]
    for row in summary['normal_cases']:
        case=runs/row['id'];result=load(case/'run-result.json');plan=checked_plan(inputs/row['id']/'input.json',case/'preparation-result.json')
        if result['status']!='software_chain_measured' or not row['regression_expectation_met']:raise ValueError('Normal case incomplete')
        for file,key in [('program.nc','nc_sha256'),('simulated.stl','mesh_sha256'),('simulation.camotics','simulation_config_sha256'),('metrics.json','metrics_sha256')]:verify_binding(case/file,result[key])
        for name,sha in result['source_code_sha256'].items():verify_binding(ROOT/'genlaw_pipeline'/name,sha)
        if load(case/'simulation.camotics')!=simulation_project(plan):raise ValueError('Native tool or stock configuration changed')
        check_nc((case/'program.nc').read_text(),plan)
        report,states=verify_case(case,case,inputs/row['id']/'software-policy.json')
        if not all(s=='numeric_pass' for s in states['C1'].values()):raise ValueError('Normal acceptance failed')
        normal={'id':row['id'],'iou_percent':row['iou_percent'],'max_boundary_deviation_mm':row['max_boundary_deviation_mm'],
            'max_width_deviation_mm':row['max_width_deviation_mm'],'software_checks':states['C1'],
            'native_cad_sha256':plan['cad_ref']['sha256'],'native_nc_sha256':result['nc_sha256'],'actual_mesh_sha256':result['mesh_sha256']}
        normals.append(normal)
    parent=runs/'baseline';plan=load(parent/'execution-plan.json')
    for row in summary['controls']:
        case=runs/row['id'];binding=load(case/'control-binding.json')
        for name,field in [('program.nc','nc_sha256'),('simulation.camotics','project_sha256'),('simulated.stl','mesh_sha256')]:verify_binding(case/name,binding[field])
        if binding['parent_nc_sha256']!=digest(parent/'program.nc') or not binding['test_only_guard_bypass']:raise ValueError('Unbound negative control')
        try:check_nc((case/'program.nc').read_text(),plan)
        except ValueError:pass
        else:raise ValueError('Normal guard did not reject control')
        report,states=verify_case(parent,case,inputs/'baseline/software-policy.json',control=True)
        if states['C1']['chamfer_sections']!='numeric_fail' or not row['regression_expectation_met']:raise ValueError('Control not detected geometrically')
        negatives.append({'id':row['id'],'iou_percent':row['iou_percent'],'software_checks':states['C1'],'normal_guard_rejected':True})
    if replay['new_intermediate_simulations']!=3 or replay['resolution_reduced'] or replay['final_result_reexecuted']:
        raise ValueError('Replay scope or fidelity changed')
    capture=replay['cases'][0]
    if not capture['material_volume_monotonically_decreases']:raise ValueError('Replay does not show actual cumulative removal')
    for frame in capture['frames']:
        for field,sha in [('stl','stl_sha256'),('mesh','mesh_sha256')]:verify_binding(out/'replay-01/baseline'/frame[field],frame[sha])
        if frame['resolution_mm']!=.025:raise ValueError('Replay resolution changed')
    resources=[];finalization_attempts=[]
    for p in sorted(list(out.rglob('*-resources.json'))+list(inputs.glob('*-resources.json'))):
        # The wrapper reserves its report before executing this worker. Observe
        # only terminal prior attempts; the caller checks this attempt afterward.
        if p.name==attempt_label+'-resources.json':continue
        r=load(p)
        if p.name.startswith('qualification-finalize-'):
            finalization_attempts.append({'path':str(p.relative_to(ROOT)),'sha256':digest(p),'status':r['status'],'success':r['success']})
            continue
        if not r['success']:raise ValueError('Resource worker did not complete: '+str(p))
        if (r['wall_limit_seconds']!=60 or len(r['cpu_affinity'])!=1 or r['thread_environment_limit']!=1
            or r['observed_group_rss_limit_bytes']!=2*1024**3 or r['file_size_limit_bytes']!=64*1024**2):
            raise ValueError('Declared worker limits changed')
        resources.append({'path':str(p.relative_to(ROOT)),'sha256':digest(p),
            'seconds':r['seconds'],'peak_group_rss_bytes':r['peak_observed_group_rss_bytes']})
    for name,count in [('unit-chamfer-01',12),('milling-regressions-01',125)]:
        log=(out/(name+'.log')).read_text()
        if f'Ran {count} tests' not in log or not log.rstrip().endswith('OK'):raise ValueError('Required tests did not pass')
    extra=out/'presentation-source';extra.mkdir()
    for name in ['tools/build_chamfer_replay.py','tools/build_pattern_replays.py','tools/pack_feature_results.py','tools/finalize_chamfer_qualification.py']:
        target=extra/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,target)
    write(out/'qualification.json',{'schema':'genlaw.chamfer-qualification.v1','at_utc':datetime.now(timezone.utc).isoformat(),
        'status':'formal_synthetic_scope_qualified','normal_cases':normals,'negative_controls':negatives,
        'input_archive':str(inputs.relative_to(ROOT)),'input_manifest_sha256':digest(inputs/'manifest.json'),
        'input_zip_sha256':digest(inputs.with_suffix('.zip')),'suite_summary_sha256':digest(runs/'summary.json'),
        'backend_manifest':FORMAL,'original_freecad_runtime_modified':False,'native_nc_modified':False,
        'tests':{'chamfer':12,'existing_preparation_and_milling':125,'all_passed':True},
        'replay':{'representative_case':'baseline','intermediate_frames':3,'resolution_mm':.025,
            'actual_material_volumes_mm3':capture['volumes_mm3'],'summary_sha256':digest(out/'replay-01/summary.json'),
            'final_result_reexecuted':False,'resolution_reduced':False},
        'resources':resources,'prior_finalization_attempts':finalization_attempts,'limits_changed':False,'maximum_worker_seconds':max(r['seconds'] for r in resources),
        'peak_worker_rss_bytes':max(r['peak_group_rss_bytes'] for r in resources),
        'scope':'Four equal 45-degree top edges of one rectangular synthetic family; baseline, width, original CAD translation and finite flat-tip variation. Fixed Z, generic XYZ.',
        'manufacturing_angle_certified':False,'material_physics_verified':False,'real_process_card_tested':False,
        'whole_process_card_complete':False,'production_approved':False,'web_progress_updated':False,'goal_complete':False,
        'next_required':['Publish all four normal cases and four negative controls with CAD/actual visual results, IoU, representative paper-card/module I/O and this replay; new independent web reviewer required.',
            'Extend to original-size C3 and combined/multi-setup real-card operations, preserving original CAD coordinates and resource bounds.',
            'Complete actual CAD/card revision matching, manufacturing criteria and real equipment resources; continue planned 3+2 and five-axis scope.']})
    files={str(p.relative_to(out)):{'bytes':p.stat().st_size,'sha256':digest(p)} for p in sorted(out.rglob('*'))
        if p.is_file() and p.name not in [attempt_label+'.log',attempt_label+'-resources.json']}
    write(out/'artifact-manifest.json',{'schema':'genlaw.chamfer-artifact-manifest.v1','files':files,'original_files_preserved':True})
    print(json.dumps({'status':'formal_synthetic_scope_qualified','normal_cases':4,'negative_controls':4,'tests':137,'web_updated':False}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--inputs',type=Path,required=True)
    p.add_argument('--attempt-label',required=True)
    a=p.parse_args();finalize(a.checkpoint.resolve(),a.inputs.resolve(),a.attempt_label)
