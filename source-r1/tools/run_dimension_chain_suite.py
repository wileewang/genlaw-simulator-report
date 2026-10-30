"""Run declared inspection through the normal, bounded four-fixture pipeline."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.programming import verify_binding
from genlaw_pipeline.run import run,write
from genlaw_pipeline.cell_windows import file_binding
from tools.run_sequence_suite import verify_case,checkpoint_acceptance

INPUTS=ROOT/'validation/feature-inputs/S4-DIMENSION-CHAIN-20261030-r2'
OUT=ROOT/'validation/runs/S4-DIMENSION-CHAIN-20261030-r1'
CP=ROOT/'docs/checkpoints/2026-10-30/dimension-chain-01'
load=lambda p:json.loads(p.read_text())


def frozen():
    for name,row in load(INPUTS/'manifest.json')['files'].items():verify_binding(INPUTS/name,row['sha256'])
    for row in load(CP/'numerical-source-freeze.json')['sources']:verify_binding(ROOT/row['path'],row['sha256'])


def one(key):
    frozen()
    spec=next(c for c in load(INPUTS/'cases.json')['cases'] if c['id']==key)
    case=OUT/key;result=run(INPUTS/key/'input.json',case)
    if result['status']!='software_chain_measured':raise RuntimeError('Normal execution failed: '+str(result.get('error')))
    report,states=verify_case(case,case,INPUTS/key/'software-policy.json')
    checks=checkpoint_acceptance(case,INPUTS/key/'software-policy.json') if (case/'material-checkpoints.json').exists() else []
    dims=load(case/'dimension-verification.json')
    passed=(result['all_declared_dimensions_passed'] and dims['dimension_inspection_passed']
        and len(dims['requirements'])==spec['dimension_count']
        and all(v=='numeric_pass' for row in states.values() for v in row.values())
        and not report['observations']['unassigned_contours_detected']
        and all(row['all_regression_expectations_met'] for row in checks))
    if checks:passed=passed and report['process_conformance']['status']=='matched_card_and_measured_checkpoints'
    old=ROOT/spec['source_run'];oldmetrics=load(old/'metrics.json')
    row={'id':key,'status':'passed' if passed else 'failed','input':file_binding(INPUTS/key/'input.json'),
        'result':file_binding(case/'run-result.json'),'dimensions':file_binding(case/'dimension-verification.json'),
        'iou_percent':result['iou_percent'],'dimension_count':len(dims['requirements']),
        'dimension_statuses':{r['id']:r['status'] for r in dims['requirements']},'original_feature_statuses':states,
        'checkpoints':[{'id':c['id'],'iou_percent':c['iou_percent'],'check_count':len(c['software_regression']),
            'all_passed':c['all_regression_expectations_met']} for c in checks],
        'actual_mesh':file_binding(case/'simulated.stl'),'target_cad':load(case/'native-geometry.json')['cad_sha256'],
        'prior_fixture':{'run':spec['source_run'],'mesh_sha256':digest(old/'simulated.stl'),
            'identical_actual_mesh_bytes':digest(case/'simulated.stl')==digest(old/'simulated.stl'),
            'identical_iou':result['iou_percent']==oldmetrics['iou_percent']},
        'fresh_freecad_and_actual_material':True,'dimensions_declared_before_native':True,
        'original_checks_replaced':False,'production_approved':False}
    frozen();write(case/'integration-result.json',row)
    if not passed:raise RuntimeError('Declared chain acceptance failed: '+key)
    print(json.dumps({'completed':key,'iou_percent':row['iou_percent'],'dimensions':row['dimension_count'],
        'identical_mesh':row['prior_fixture']['identical_actual_mesh_bytes']},ensure_ascii=False),flush=True)


def finish():
    frozen();rows=[load(OUT/c['id']/'integration-result.json') for c in load(INPUTS/'cases.json')['cases']]
    if any(r['status']!='passed' for r in rows):raise RuntimeError('Incomplete four-case evidence')
    resources=[]
    for p in sorted(OUT.rglob('*-resources.json')):
        data=load(p)
        if not data['success'] or data['status']!='completed':raise RuntimeError('Failed resource record: '+str(p))
        resources.append({'source':file_binding(p),'seconds':data['seconds'],'peak_rss_bytes':data['peak_observed_group_rss_bytes']})
    summary={'schema':'genlaw.dimension-chain-suite.v1','id':OUT.name,'status':'completed','cases':rows,
        'dimension_checks':sum(r['dimension_count'] for r in rows),
        'original_feature_checks':sum(len(v) for r in rows for v in r['original_feature_statuses'].values()),
        'intermediate_states':sum(len(r['checkpoints']) for r in rows),
        'intermediate_checks':sum(c['check_count'] for r in rows for c in r['checkpoints']),
        'inputs':file_binding(INPUTS/'manifest.json'),'sources':file_binding(CP/'numerical-source-freeze.json'),
        'resources':resources,'max_worker_seconds':max(r['seconds'] for r in resources),
        'peak_worker_rss_bytes':max(r['peak_rss_bytes'] for r in resources),
        'new_part_families':0,'new_negative_material_executions':0,
        'meaning':'Four existing CAD fixtures run afresh with declared dimensions carried automatically through the normal pipeline.',
        'production_approved':False,'whole_process_card_complete':False,'s4_complete':False}
    write(OUT/'summary.json',summary)
    print(json.dumps({k:v for k,v in summary.items() if k not in ('cases','resources')},ensure_ascii=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('task',choices=['profile-x','profile-y','two-holes','three-holes','finish'])
    a=p.parse_args();finish() if a.task=='finish' else one(a.task)
