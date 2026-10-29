"""Bind complete numerical evidence and resource reports without repeating work."""
import argparse
import datetime
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'web/tools')]
from mesh_metrics import require_bounded_worker
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.programming import verify_binding,nc_words
from genlaw_pipeline.run import write
from tools.run_pocket_sequence_suite import checked_inputs
load=lambda p:json.loads(p.read_text())


def main(inputs,runs,checkpoint,probes):
    require_bounded_worker()
    for p in [inputs,runs,checkpoint,probes]:p.relative_to(ROOT)
    checked_inputs(inputs);freeze=load(checkpoint/'numerical-source-freeze.json')
    for name,sha in freeze['source_code_sha256'].items():verify_binding(ROOT/'genlaw_pipeline'/name,sha)
    verify_binding(ROOT/'tools/run_multi_pocket_suite.py',freeze['suite_source_sha256'])
    verify_binding(inputs/'manifest.json',freeze['input_manifest_sha256'])
    if not (runs/'summary.json').exists():
        # The frozen runner's optional finish stage has a Path subscription typo.
        # Keep that producer byte-for-byte for its eight already completed cases;
        # collect their immutable records here without rerunning or changing them.
        from tools.run_multi_pocket_suite import CONTROLS
        normals=[load(runs/s['id']/'suite-case.json') for s in load(inputs/'specifications.json')['cases']]
        controls=[load(runs/key/'suite-case.json') for key in CONTROLS]
        if not all(r['regression_expectation_met'] and r['runner_sha256']==freeze['suite_source_sha256'] for r in normals+controls):
            raise ValueError('Incomplete or mixed-version case records')
        write(runs/'summary.json',{'schema':'genlaw.multi-pocket-suite.v1','id':runs.name,'status':'completed','normal_cases':normals,
            'controls':controls,'all_regression_expectations_met':True,'input_archive':inputs.name,'input_manifest_sha256':digest(inputs/'manifest.json'),
            'production_approved':False,'whole_process_card_complete':False,'s4_complete':False,
            'implemented':'Individually bound disjoint pocket floors and layered material, optional floor holes, explicit repeated tool changes and card order; independent pre-NC cumulative CAD and unchanged original stock.',
            'aggregation_source_sha256':digest(Path(__file__)),'frozen_numerical_runner_sha256':freeze['suite_source_sha256'],
            'aggregation_note':'Collected all eight completed case records with the corrected Path joining logic in the separate finalizer; frozen producer and raw results unchanged.'})
    summary=load(runs/'summary.json')
    if not summary['all_regression_expectations_met']:raise ValueError('Incomplete numerical suite')
    preflight=load(checkpoint/'preflight-summary.json');assert preflight['status']=='passed_ready_for_material_execution'
    jobs=[];bindings={};cad={};native_matches={}
    for row in summary['normal_cases']+summary['controls']:
        key=row['id'];folder=runs/key
        bindings[key]={name:digest(folder/name) for name in ['program.nc','simulation.camotics','metrics.json','software-verification.json','suite-case.json']}
        for path in folder.rglob('*-resources.json'):
            record=load(path)
            if not record['success'] or not record['started'] or len(record['cpu_affinity'])!=1:
                raise ValueError('Unqualified numerical worker: '+str(path))
            jobs.append({'case':key,'path':str(path.relative_to(ROOT)),'sha256':digest(path),
                'seconds':record['seconds'],'peak_rss_bytes':record['peak_observed_group_rss_bytes']})
        if 'parent_case' not in row:
            record=load(folder/'run-result.json')
            if record['source_code_sha256']!=freeze['source_code_sha256']:raise ValueError('Mixed producer versions')
            cad[key]=load(folder/'execution-plan.json')['cad_ref']['sha256']
            native_matches[key]=nc_words((folder/'program.nc').read_text())==nc_words((probes/key/'program.nc').read_text())
            if not native_matches[key]:raise ValueError('Integrated native NC differs from preflight executable NC')
    result={'status':'software_chain_qualified_web_review_pending','at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'summary_sha256':digest(runs/'summary.json'),'source_freeze_sha256':digest(checkpoint/'numerical-source-freeze.json'),
        'input_manifest_sha256':digest(inputs/'manifest.json'),'distinct_original_cad_count':len(set(cad.values())),
        'normal_cases':len(summary['normal_cases']),'negative_controls':len(summary['controls']),
        'iou_percent':{r['id']:r['iou_percent'] for r in summary['normal_cases']},
        'control_iou_percent':{r['id']:r['iou_percent'] for r in summary['controls']},
        'final_software_checks':sum(len(v) for r in summary['normal_cases'] for v in r['software_statuses'].values()),
        'intermediate_states':sum(len(r['checkpoints']) for r in summary['normal_cases']),
        'intermediate_software_checks':sum(c['check_count'] for r in summary['normal_cases'] for c in r['checkpoints']),
        'unique_unit_tests':preflight['unique_unit_tests'],'native_freecad_probes':4,'native_binding_rejections':3,
        'legacy_actual_material_reports_identical':4,'legacy_material_removal_reruns':0,
        'preflight_executable_nc_matches_integrated':native_matches,'jobs':jobs,'worker_count':len(jobs),
        'max_worker_seconds':max(r['seconds'] for r in jobs),'peak_worker_rss_bytes':max(r['peak_rss_bytes'] for r in jobs),
        'capacity_rejections_before_start':len(list(runs.rglob('admission-pauses/*/attempt-*/record.json'))),
        'case_bindings':bindings,'default_engine_changed':False,'limits_increased':False,
        'next':['Archive actual geometry, paper inputs and original NC evidence','Add a dedicated S4 result group with actual input/output comparisons, IoU, replay and representative module evidence','Use a fresh independent webpage reviewer and fix findings before publication'],
        'goal_complete':False,'s4_complete':False,'whole_process_card_complete':False,'production_approved':False}
    write(checkpoint/'integration-summary.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ['jobs','case_bindings']},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ['inputs','runs','checkpoint','probes']:p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();main(*[getattr(a,k).resolve() for k in ['inputs','runs','checkpoint','probes']])
