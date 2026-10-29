"""Bounded original-stock execution, per-pocket checks and actual faulty NC controls."""
import argparse
import json
import re
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.programming import check_nc,simulation_project,verify_binding
from genlaw_pipeline.run import write,CAMOTICS,simulation_env
from genlaw_pipeline.measurement import measure_bounded
from genlaw_pipeline.admission_wait import admitted_bounded
import genlaw_pipeline.run as pipeline
pipeline.bounded=admitted_bounded
from tools.run_pocket_sequence_suite import checked_inputs,checkpoint_acceptance
from tools.run_sequence_suite import spans,verify_case
load=lambda p:json.loads(p.read_text())
CONTROLS={k:'different-depth' for k in ['missing-second','shallow-second','breached-second-floor','swapped-order']}


def defective_nc(text,kind):
    lines,found=spans(text);a,b=found['Pocket_02'];c,d=found['Pocket_03']
    if kind=='missing-second':return ''.join(lines[:c]+lines[d:]),d-c
    if kind=='swapped-order':
        if b!=c:raise ValueError('Need adjacent same-tool pocket operations')
        return ''.join(lines[:a]+lines[c:d]+lines[a:b]+lines[d:]),d-a
    if kind not in ('shallow-second','breached-second-floor'):raise ValueError('Unknown controlled defect')
    count=0;body=[]
    def change(match):
        nonlocal count
        z=float(match[1]);new=z+(.25 if kind=='shallow-second' else -.4) if z<-.5 else z
        if new!=z:count+=1
        return 'Z'+f'{new:.5f}'
    for line in lines[c:d]:
        if line.lstrip().startswith(('G0 ','G1 ','G2 ','G3 ')):
            line=re.sub(r'\bZ([-+]?\d+(?:\.\d+)?)',change,line)
        body.append(line)
    if not count:raise ValueError('Controlled defect changed no depth')
    return ''.join(lines[:c]+body+lines[d:]),count


def check_sources(case):
    for name,sha in load(case/'run-result.json')['source_code_sha256'].items():
        verify_binding(ROOT/'genlaw_pipeline'/name,sha)


def normal(inputs,out,key):
    checked_inputs(inputs);spec=next(s for s in load(inputs/'specifications.json')['cases'] if s['id']==key)
    case=out/key;policy=inputs/key/'software-policy.json';result=pipeline.run(inputs/key/'input.json',case)
    if result['status']!='software_chain_measured':raise RuntimeError('Multiple-pocket chain failed: '+str(result.get('error',result['status'])))
    report,status=verify_case(case,case,policy);checkpoints=checkpoint_acceptance(case,policy)
    plan=load(case/'execution-plan.json');checked=load(case/'cam-result.json')['nc_check'];wanted=[]
    for op in plan['operations']:
        if not wanted or wanted[-1]!=op['tool_number']:wanted.append(op['tool_number'])
    passed=(all(v=='numeric_pass' for row in status.values() for v in row.values())
            and not report['observations']['unassigned_contours_detected']
            and [r['feature_id'] for r in checked['step_map']]==spec['order']
            and [r['tool_number'] for r in checked['tool_transitions']]==wanted
            and report['process_conformance']['status']=='matched_card_and_measured_checkpoints')
    record={'id':key,'specification':spec,'iou_percent':result['iou_percent'],'software_statuses':status,
            'regression_expectation_met':passed,'executed_feature_order':spec['order'],'tool_transitions':checked['tool_transitions'],
            'checkpoints':[{'id':r['id'],'iou_percent':r['iou_percent'],'check_count':len(r['software_regression']),
                'all_regression_expectations_met':r['all_regression_expectations_met']} for r in checkpoints],
            'process_conformance':report['process_conformance'],'verifier_sha256':report['source_code_sha256'],'runner_sha256':digest(Path(__file__))}
    write(case/'suite-case.json',record);check_sources(case)
    print(json.dumps({'normal_completed':key,'iou_percent':result['iou_percent'],'all_checks_pass':passed}),flush=True)
    if not passed:raise RuntimeError('Normal multiple-pocket acceptance failed')


def control(inputs,out,key):
    checked_inputs(inputs);parent_key=CONTROLS[key];parent=out/parent_key;case=out/key;check_sources(parent)
    plan=load(parent/'execution-plan.json');changed,count=defective_nc((parent/'program.nc').read_text(),key)
    try:check_nc(changed,plan)
    except ValueError as error:rejection=str(error)
    else:raise AssertionError('Normal NC guard accepted the defect')
    case.mkdir();(case/'program.nc').write_text(changed);write(case/'simulation.camotics',simulation_project(plan))
    admitted_bounded(case,'camotics',[CAMOTICS,'--threads','1','--reduce=false','--binary',case/'simulation.camotics',case/'simulated.stl'],simulation_env(),case/'simulation.camotics')
    write(case/'control-binding.json',{'schema':'genlaw.verification-control.v1','test_only_guard_bypass':True,'parent_case':parent_key,
        'origin':key+': explicitly defective native NC executed on unchanged original stock / tools / resolution for regression only.',
        'parent_nc_sha256':digest(parent/'program.nc'),'nc_file':'program.nc','mesh_file':'simulated.stl','project_file':'simulation.camotics',
        'nc_sha256':digest(case/'program.nc'),'mesh_sha256':digest(case/'simulated.stl'),'project_sha256':digest(case/'simulation.camotics'),
        'normal_guard_rejection':rejection,'changed_words_or_blocks':count,'production_approved':False})
    measure_bounded(case,'measure',parent,case/'simulated.stl',case/'metrics.json')
    report,status=verify_case(parent,case,inputs/parent_key/'software-policy.json',control=True)
    geometry_pass=all(v=='numeric_pass' for row in status.values() for v in row.values()) and not report['observations']['unassigned_contours_detected']
    expected=(status['K2']['pocket_depth']=='numeric_fail' if key in ('missing-second','shallow-second') else
              status['K2']['floor_geometry']=='numeric_fail' if key=='breached-second-floor' else geometry_pass)
    expected=expected and report['process_conformance']['status']=='rejected'
    record={'id':key,'parent_case':parent_key,'guard_rejected':True,'normal_guard_rejection':rejection,
        'iou_percent':load(case/'metrics.json')['iou_percent'],'software_statuses':status,'geometry_checks_pass':geometry_pass,
        'process_conformance':report['process_conformance'],'regression_expectation_met':expected,
        'verifier_sha256':report['source_code_sha256'],'runner_sha256':digest(Path(__file__))}
    write(case/'suite-case.json',record)
    print(json.dumps({'control_completed':key,'iou_percent':record['iou_percent'],'expected_defect_detected':expected}),flush=True)
    if not expected:raise RuntimeError('Controlled defect not identified: '+key)


def finish(inputs,out):
    checked_inputs(inputs);normals=[load(out/s['id']/'suite-case.json') for s in load(inputs/'specifications.json')['cases']]
    controls=[load(out[key]/'suite-case.json') for key in CONTROLS]
    if not all(r['regression_expectation_met'] for r in normals+controls):raise ValueError('Incomplete qualification')
    for row in normals:check_sources(out/row['id'])
    for row in normals+controls:verify_binding(Path(__file__),row['runner_sha256'])
    write(out/'summary.json',{'schema':'genlaw.multi-pocket-suite.v1','id':out.name,'status':'completed','normal_cases':normals,
        'controls':controls,'all_regression_expectations_met':True,'input_archive':inputs.name,'input_manifest_sha256':digest(inputs/'manifest.json'),
        'production_approved':False,'whole_process_card_complete':False,'s4_complete':False,
        'implemented':'Individually bound disjoint pocket floors and layered material, optional floor holes, explicit repeated tool changes and card order; independent pre-NC cumulative CAD and unchanged original stock.'})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('stage',choices=['normal','control','finish']);p.add_argument('--case')
    p.add_argument('--inputs',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    inputs,out=a.inputs.resolve(),a.output.resolve();inputs.relative_to(ROOT);out.relative_to(ROOT);out.mkdir(parents=True,exist_ok=True)
    if a.stage=='finish':finish(inputs,out)
    elif a.stage=='normal':normal(inputs,out,a.case)
    else:control(inputs,out,a.case)
