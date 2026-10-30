"""Finish acceptance of preserved material evidence with the qualified pocket evaluator.

The first suite coordinator accidentally selected the generic sequence evaluator
for intermediate pockets. No NC, mesh, measurement or acceptance rule changes.
"""
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.run_fixture_clearance_suite import INPUTS,OUT,CP,PRIOR,load,write,frozen,words
from genlaw_pipeline.cell_windows import file_binding
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.programming import checked_plan,verify_binding
from genlaw_pipeline.pocket_sequence_verification import evaluate
from genlaw_pipeline.sequence_checkpoints import verify_checkpoint_bindings
from tools.run_sequence_suite import statuses


def main():
    from mesh_metrics import require_bounded_worker
    require_bounded_worker();frozen();case=OUT/'clear-jaws';policy=INPUTS/'clear-jaws/software-policy.json'
    result=load(case/'run-result.json');r=load(case/'fixture-verification.json');geom=load(case/'software-verification.json')
    assert result['status']=='software_chain_measured' and r['status']=='clear'
    assert r['checked_before_material_removal'] and r['declared_resource_scope_integrated']
    assert r['tested_pairs']==r['expected_pairs'] and r['actual_tool_change_poses_present'] and not r['nc_text_modified']
    plan=checked_plan(INPUTS/'clear-jaws/input.json',case/'preparation-result.json')
    assert words(case/'program.nc')==words(PRIOR/'program.nc')
    assert plan['cad_ref']['sha256']==load(PRIOR/'execution-plan.json')['cad_ref']['sha256']
    for name,sha in result['source_code_sha256'].items():verify_binding(ROOT/'genlaw_pipeline'/name,sha)
    for name,key in [('program.nc','nc_sha256'),('simulated.stl','mesh_sha256'),('metrics.json','metrics_sha256')]:verify_binding(case/name,result[key])
    verify_binding(case/'simulated.stl',geom['mesh_sha256']);verify_binding(policy,geom['software_policy_sha256'])
    states=statuses(geom)
    assert all(s=='numeric_pass' for feature in states.values() for s in feature.values())
    assert not geom['observations']['unassigned_contours_detected']
    assert geom['process_conformance']['status']=='matched_card_and_measured_checkpoints'
    material=verify_checkpoint_bindings(case,load(case/'cam-result.json'));rules=load(policy)['features'];steps=[]
    for item in material['checkpoints']:
        folder=case/'checkpoints'/item['id'];record=load(folder/'observations.json');obs=record['observations']
        assert set(obs['features'])==set(record['completed_features'])
        checks=[{'feature_id':fid,'kind':kind,**evaluate(kind,values,rule)}
                for fid,values in obs['features'].items() for kind,rule in rules[fid].items()]
        assert all(c['status']=='numeric_pass' for c in checks) and not obs['unassigned_contours_detected']
        old=load(PRIOR/'checkpoints'/item['id']/'software-verification.json')
        assert checks==old['software_regression']
        report={'id':item['id'],'iou_percent':item['iou_percent'],'observations':file_binding(folder/'observations.json'),
            'policy':file_binding(policy),'evaluator':file_binding(ROOT/'genlaw_pipeline/pocket_sequence_verification.py'),
            'software_regression':checks,'all_regression_expectations_met':True,
            'same_checks_and_values_as_previous_original_case':True,'no_native_or_measurement_reexecution':True}
        write(folder/'fixture-integration-verification.json',report)
        steps.append({'id':item['id'],'iou_percent':item['iou_percent'],'checks':len(checks),'all_passed':True,
                      'acceptance':file_binding(folder/'fixture-integration-verification.json')})
    assert digest(case/'simulated.stl')==digest(PRIOR/'simulated.stl')
    assert result['iou_percent']==load(PRIOR/'metrics.json')['iou_percent']
    row={'id':'clear-jaws','status':'passed','input':file_binding(INPUTS/'clear-jaws/input.json'),
        'run_result':file_binding(case/'run-result.json'),'fixture_report':file_binding(case/'fixture-verification.json'),
        'fixture_status':r['status'],'native_motion_count':r['native_motion_count'],'tested_pairs':r['tested_pairs'],
        'findings':r['findings'],'minimum_clearance_mm':r['minimum_clearance_mm'],
        'same_executable_nc_as_original':True,'original_cad_unchanged':True,'source_cad_sha256':plan['cad_ref']['sha256'],
        'material_simulation_executed':True,'iou_percent':result['iou_percent'],'feature_checks':states,'checkpoints':steps,
        'mesh':file_binding(case/'simulated.stl'),'same_material_mesh_as_original':True,'same_iou_as_original':True,
        'acceptance_coordinator':file_binding(Path(__file__)),
        'preserved_incorrect_evaluator_attempt':file_binding(case/'checkpoints/after-02/software-verification.json'),
        'production_approved':False,'whole_process_card_complete':False}
    frozen();write(case/'integration-result.json',row)
    print(json.dumps({'status':'passed','iou_percent':row['iou_percent'],'intermediate_checks':sum(s['checks'] for s in steps),'material_runs_repeated':False}))


if __name__=='__main__':main()
