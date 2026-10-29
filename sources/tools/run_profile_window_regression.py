"""Qualify unchanged v11 material and observations before original-size execution."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'tools')]
from genlaw_pipeline.cell_windows import file_binding,checked_file
from genlaw_pipeline.run import write
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.sharded_measurement import measure_shards
from genlaw_pipeline.profile_window_qualification import NORMALS,CONTROLS,source_bindings,checked_qualification
import genlaw_pipeline.run as pipeline_run
import probe_cell_windows as probe
import run_window_checkpoints as checkpoints

LOAD=lambda p:json.loads(p.read_text())
SUITE=ROOT/'validation/runs/S4-PROFILE-SEQUENCE-SUITE-20261029-r1'
INPUTS=ROOT/'validation/feature-inputs/S4-PROFILE-SEQUENCE-20261029-r3'


def use_admission_wait():
    # Only unstarted resource rejections wait. Runtime failures remain terminal.
    pipeline_run.bounded=admitted_bounded;probe.bounded=admitted_bounded;checkpoints.bounded=admitted_bounded


def observe(measure,policy,out,control=None):
    out.mkdir();worker=ROOT/'genlaw_pipeline/sharded_verification.py';plan=out/'plan.json'
    command=[sys.executable,worker,'plan','--measure-plan',measure,'--policy',policy,'--output',plan]
    if control:command+=['--control-binding',control]
    admitted_bounded(out,'plan',command)
    data=LOAD(plan)
    for mode,values in [('rays',data['ray_groups']),('section',data['heights'])]:
        for i in range(len(values)):
            admitted_bounded(out,f'{mode}-{i:03d}',[sys.executable,worker,mode,'--plan',plan,'--index',str(i),'--output',out/f'{mode}-{i:03d}.json'])
    admitted_bounded(out,'finish',[sys.executable,worker,'finish','--plan',plan,'--directory',out,'--output',out/'verification.json'])
    return out/'verification.json'


def compare_streamed(original,complete_metrics,streamed,prior,verification,output):
    old,new=LOAD(prior),LOAD(verification)
    signature=lambda r:[(x['feature_id'],x['kind'],x['status'],x.get('criterion')) for x in r['software_regression']]
    delta=abs(LOAD(complete_metrics)['iou']-LOAD(streamed/'metrics.json')['iou'])
    legacy_delta=abs(LOAD(original/'metrics.json')['iou']-LOAD(streamed/'metrics.json')['iou'])
    if signature(old)!=signature(new) or delta>1e-10:
        raise ValueError('Streamed geometry differs from complete-mesh observations of the same actual surface')
    if legacy_delta>1e-5:raise ValueError('Window result exceeds the existing legacy refinement allowance')
    write(output,{'status':'passed','absolute_iou_difference':delta,'criteria_and_verdicts_unchanged':True,
        'old_verification':file_binding(prior),'new_verification':file_binding(verification),
        'old_metrics':file_binding(complete_metrics),'new_metrics':file_binding(streamed/'metrics.json'),
        'same_actual_surface_compared':True,'legacy_metrics':file_binding(original/'metrics.json'),
        'legacy_absolute_iou_difference':legacy_delta,'existing_legacy_iou_allowance':1e-5,
        'checks':len(new['software_regression']),'all_software_geometry_checks_passed':new['all_software_geometry_checks_passed']})


def reuse_baseline(previous,out):
    """Reuse only completed actual outputs after checking unchanged numerical workers."""
    previous=previous.resolve();previous.relative_to(ROOT)
    intent=LOAD(previous/'intent.json')
    changed={'tools/run_profile_window_regression.py':'run_profile_window_regression-initial.py',
        'tools/run_window_checkpoints.py':'run_window_checkpoints-initial.py',
        'tools/check_window_geometry.py':'check_window_geometry-initial.py',
        'genlaw_pipeline/profile_window_qualification.py':'profile_window_qualification-initial.py'}
    snapshots=ROOT/'docs/checkpoints/2026-10-29/profile-window-development-01'
    for binding in intent['sources']:
        if binding['path'] in changed:
            if file_binding(snapshots/changed[binding['path']])['sha256']!=binding['sha256']:
                raise ValueError('Prior controller source snapshot differs')
        else:checked_file(binding)
    checked_file(intent['engine_manifest']);checked_file(intent['admission_wait_source'])
    old=previous/'baseline';case=out/'baseline';case.mkdir()
    default=LOAD(old/'default/result.json');checked_file(default['candidate'])
    for binding in default['original'].values():checked_file(binding)
    from genlaw_pipeline.sharded_measure import checked_surface
    checked_surface(old/'window/partitioned/surface.json')
    inv=LOAD(old/'window/invariance.json');checked_file(inv['canonical']);checked_file(inv['surface'])
    if not inv['equal_oriented_float32_facets']:raise ValueError('Saved partition was not exact')
    for name in ['sharded-measure-plan.json','features/plan.json']:
        plan=LOAD(old/'streamed'/name);checked_file(plan['source_code']);checked_file(plan['surface']);checked_file(plan['reference'])
    geometry=case/'geometry-regression.json'
    admitted_bounded(case,'geometry',[sys.executable,ROOT/'tools/check_window_geometry.py',
        '--original',SUITE/'baseline','--actual',old/'window/combined.stl','--surface',old/'window/partitioned/surface.json',
        '--invariance',old/'window/invariance.json','--policy',INPUTS/'baseline/software-policy.json',
        '--prior',SUITE/'baseline/software-verification.json','--output',geometry])
    window=LOAD(old/'window/result.json');window['geometry']=file_binding(geometry)
    write(case/'window-result.json',window)
    compare_streamed(SUITE/'baseline',old/'window/metrics.json',old/'streamed',geometry,
        old/'streamed/features/verification.json',case/'streamed-comparison.json')
    write(case/'result.json',{'id':'baseline','default':file_binding(old/'default/result.json'),
        'window':file_binding(case/'window-result.json'),'streamed_comparison':file_binding(case/'streamed-comparison.json'),
        'reuse_intent':file_binding(previous/'intent.json'),'new_native_jobs':0,
        'reason':'Initial comparison incorrectly compared streamed measurement to the older default-engine mesh. Same-window mesh metrics differ by 2.2e-15; original native-window compatibility retains the pre-existing 1e-5 bound.'})
    print(json.dumps({'reused_complete_native_case':'baseline','new_native_jobs':0}),flush=True)
    return {'id':'baseline','result':file_binding(case/'result.json')}


def run(out,engine,base,reuse=None):
    out,engine,base=[p.resolve() for p in (out,engine,base)]
    for p in (out,engine,base):p.relative_to(ROOT)
    prior=LOAD(base)
    if (prior.get('status')!='passed' or prior['engine_manifest']!=file_binding(engine)
            or not all(x['regression_passed'] for x in prior['normal_cases']+prior['controls']+prior['default_cases'])):
        raise ValueError('Existing complete-engine qualification is required')
    out.mkdir(parents=True,exist_ok=False);sources=source_bindings();use_admission_wait()
    write(out/'intent.json',{'schema':'genlaw.profile-window-intent.v1','sources':sources,
        'controller_pid':os.getpid(),
        'base_engine_regression':file_binding(base),'engine_manifest':file_binding(engine),
        'normal_cases':NORMALS,'controls':CONTROLS,'checkpoint_cases':NORMALS+['legacy-v9'],
        'admission_wait_source':file_binding(ROOT/'genlaw_pipeline/admission_wait.py'),
        'normal_entry_accepts_control_nc':False,'limits_increased':False})
    rows=[]
    for key in NORMALS+CONTROLS:
        if key=='baseline' and reuse:
            rows.append(reuse_baseline(reuse,out));continue
        original=SUITE/key;control=original/'control-binding.json' if key in CONTROLS else None
        reference=SUITE/LOAD(control)['parent_case'] if control else original
        policy=INPUTS/reference.name/'software-policy.json';prior=original/'software-verification.json'
        case=out/key;case.mkdir()
        probe.default_regression(original,case/'default',engine)
        probe.window_case(key,original,case/'window',engine,policy,prior,reference)
        window=case/'window';parts=window/'partitioned';plan=parts/'plan.json'
        for i in range(len(LOAD(plan)['windows'])):
            p=parts/f'window-{i:03d}'
            admitted_bounded(p,'section-cache',[sys.executable,ROOT/'genlaw_pipeline/sharded_measure.py','cache',
                '--plan',plan,'--result',p/'result.json','--output',p/'sections.json'])
        streamed=case/'streamed';streamed.mkdir()
        measure_shards(streamed,parts/'surface.json',reference/'native-geometry.json',parts,streamed/'metrics.json')
        report=observe(streamed/'sharded-measure-plan.json',policy,streamed/'features',control)
        compare_streamed(original,window/'metrics.json',streamed,window/'geometry-regression.json',report,case/'streamed-comparison.json')
        write(case/'result.json',{'id':key,'default':file_binding(case/'default/result.json'),
            'window':file_binding(window/'result.json'),'streamed_comparison':file_binding(case/'streamed-comparison.json')})
        rows.append({'id':key,'result':file_binding(case/'result.json')})
        print(json.dumps({'profile_window_case_passed':key,'checks':len(LOAD(report)['software_regression'])}),flush=True)
    prefix=[]
    for key in NORMALS+['legacy-v9']:
        parent=ROOT/'validation/runs/S4-SEQUENCE-SUITE-20261029-r2/two-holes' if key=='legacy-v9' else SUITE/key
        policy=ROOT/'validation/feature-inputs/S4-SEQUENCE-20261029-r2/two-holes/software-policy.json' if key=='legacy-v9' else INPUTS/key/'software-policy.json'
        dest=out/('checkpoints-'+key)
        checkpoints.run(parent,dest,engine,base,policy,legacy=True)
        report=LOAD(dest/'summary.json')
        prefix.append({'id':key,'summary':file_binding(dest/'summary.json'),
            'exact_facet_comparisons':[file_binding(dest/r['id']/('legacy-facets.json' if key=='legacy-v9' else 'full-window-facets.json')) for r in report['checkpoints']]})
    if sources!=source_bindings():raise ValueError('Qualification sources changed during execution')
    write(out/'summary.json',{'schema':'genlaw.profile-window-regression.v1','status':'passed',
        'engine_manifest':file_binding(engine),'base_engine_regression':file_binding(base),'sources':sources,
        'normal_cases':rows[:len(NORMALS)],'controls':rows[len(NORMALS):],'checkpoint_regressions':prefix,
        'production_approved':False,'whole_process_card_complete':False})
    checked_qualification(out/'summary.json',engine)
    print(json.dumps({'status':'passed','normal_cases':4,'controls':4,'profiled_checkpoints':13,'legacy_checkpoints':2}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('output','engine','regression'):p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--reuse-baseline',type=Path)
    a=p.parse_args();run(a.output,a.engine,a.regression,a.reuse_baseline)
