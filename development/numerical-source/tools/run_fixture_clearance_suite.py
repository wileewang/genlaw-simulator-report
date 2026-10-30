"""Execute the archived card-to-clearance chain, serially and without retries of started work."""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import genlaw_pipeline.run as pipeline
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.cell_windows import file_binding
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.programming import verify_binding
import tools.run_sequence_suite as sequence

INPUTS=ROOT/'validation/feature-inputs/S4-FIXTURE-CLEARANCE-20261030-r3'
OUT=ROOT/'validation/runs/S4-FIXTURE-CLEARANCE-SUITE-20261030-r1'
CP=ROOT/'docs/checkpoints/2026-10-30/fixture-clearance-01'
PRIOR=ROOT/'validation/runs/S4-TOOL-LENGTH-SUITE-20261030-r1/shorter-tool'
KEYS=['clear-jaws','cutting-clamp','holder-overhang','rapid-crossing','near-clearance']
load=lambda p:json.loads(p.read_text())
write=pipeline.write


def frozen():
    for name,row in load(INPUTS/'manifest.json')['files'].items():verify_binding(INPUTS/name,row['sha256'])
    for row in load(CP/'numerical-source-freeze.json')['sources']:verify_binding(ROOT/row['path'],row['sha256'])


def words(path):
    return [s.strip() for s in re.sub(r'\([^()]*\)','',path.read_text()).splitlines() if s.strip()]


def one(key):
    frozen();case=OUT/key
    # The wrapper only waits when capacity denied a worker before execution.
    # Native execution failures are preserved and never automatically retried.
    pipeline.bounded=admitted_bounded;sequence.bounded=admitted_bounded
    result=pipeline.run(INPUTS/key/'input.json',case)
    expected=next(r['expected_fixture_status'] for r in load(INPUTS/'specifications.json')['cases'] if r['id']==key)
    if result['status'] not in ('software_chain_measured','blocked_at_fixture_clearance'):
        raise RuntimeError('Unexpected chain failure: '+str(result.get('error')))
    report=load(case/'fixture-verification.json')
    if report['status']!=expected or not report['checked_before_material_removal']:
        raise ValueError('Fixture gate differs from the frozen expected classification')
    if (not report['declared_resource_scope_integrated'] or report['expected_pairs']!=report['tested_pairs']
            or not report['actual_tool_change_poses_present'] or report['nc_text_modified']):
        raise ValueError('Fixture result lacks complete native coverage')
    if words(case/'program.nc')!=words(PRIOR/'program.nc'):
        raise ValueError('Fixture-only resource change unexpectedly changed executable NC')
    row={'id':key,'status':'passed','input':file_binding(INPUTS/key/'input.json'),
        'run_result':file_binding(case/'run-result.json'),'fixture_report':file_binding(case/'fixture-verification.json'),
        'fixture_status':report['status'],'native_motion_count':report['native_motion_count'],
        'tested_pairs':report['tested_pairs'],'findings':report['findings'],
        'minimum_clearance_mm':report['minimum_clearance_mm'],
        'same_executable_nc_as_original':True,'original_cad_unchanged':True,
        'source_cad_sha256':load(case/'execution-plan.json')['cad_ref']['sha256'],
        'material_simulation_executed':expected=='clear','iou_percent':None,
        'production_approved':False,'whole_process_card_complete':False}
    verify_binding(INPUTS/key/'target.brep',load(PRIOR/'execution-plan.json')['cad_ref']['sha256'])
    if expected=='clear':
        if result['status']!='software_chain_measured':raise ValueError('Clear fixture did not continue through material simulation')
        geometry,states=sequence.verify_case(case,case,INPUTS/key/'software-policy.json')
        steps=sequence.checkpoint_acceptance(case,INPUTS/key/'software-policy.json')
        if (any(v!='numeric_pass' for m in states.values() for v in m.values())
                or geometry['observations']['unassigned_contours_detected']
                or geometry['process_conformance']['status']!='matched_card_and_measured_checkpoints'):
            raise ValueError('Fresh material result failed its unchanged geometry/process criteria')
        row.update(iou_percent=result['iou_percent'],feature_checks=states,
            checkpoints=[{'id':r['id'],'iou_percent':r['iou_percent'],'checks':len(r['software_regression']),
                         'all_passed':r['all_regression_expectations_met']} for r in steps],
            mesh=file_binding(case/'simulated.stl'),
            same_material_mesh_as_original=digest(case/'simulated.stl')==digest(PRIOR/'simulated.stl'),
            same_iou_as_original=result['iou_percent']==load(PRIOR/'metrics.json')['iou_percent'])
        if not row['same_material_mesh_as_original'] or not row['same_iou_as_original']:
            raise ValueError('Adding an independent fixture guard changed unchanged material simulation')
    else:
        if (result['status']!='blocked_at_fixture_clearance' or result['material_simulation_started']
                or any(p.exists() for p in [case/'simulated.stl',case/'simulation-result.json',case/'metrics.json',case/'camotics-resources.json'])
                or list((case/'checkpoints').glob('*/simulated.stl'))):
            raise ValueError('Blocked fixture must not generate material evidence or IoU')
        if key=='holder-overhang' and not all(f['role']=='holder' for f in report['findings']):
            raise ValueError('Holder-only control was not isolated')
        if key in ('rapid-crossing','near-clearance') and not all(f['first_motion_type']=='RAPID' for f in report['findings']):
            raise ValueError('Approach control did not concern actual rapid motion')
    frozen();write(case/'integration-result.json',row)
    print(json.dumps({'completed':key,'fixture':expected,'new_iou_percent':row['iou_percent']},ensure_ascii=False),flush=True)


def finish():
    frozen();rows=[load(OUT/key/'integration-result.json') for key in KEYS]
    resources=[]
    for key in KEYS:
        for p in sorted((OUT/key).rglob('*-resources.json')):
            r=load(p)
            if not r['success']:raise ValueError('Failed execution in completed result: '+str(p))
            resources.append({'source':file_binding(p),'seconds':r['seconds'],'rss_bytes':r['peak_observed_group_rss_bytes']})
    s={'schema':'genlaw.fixture-clearance-suite.v1','status':'completed','cases':rows,
       'existing_part_geometries':1,'new_part_families':0,'resource_configurations':5,
       'complete_material_runs':1,'collision_blocks':3,'indeterminate_blocks':1,
       'all_fixture_checks_before_material':True,'production_approved':False,'whole_process_card_complete':False,
       's4_complete':False,'inputs':file_binding(INPUTS/'manifest.json'),'sources':file_binding(CP/'numerical-source-freeze.json'),
       'resources':resources,'max_worker_seconds':max(r['seconds'] for r in resources),
       'peak_worker_rss_bytes':max(r['rss_bytes'] for r in resources)}
    write(OUT/'summary.json',s)
    print(json.dumps({k:v for k,v in s.items() if k not in ('cases','resources')},ensure_ascii=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('task',choices=KEYS+['finish']);a=p.parse_args()
    finish() if a.task=='finish' else one(a.task)
