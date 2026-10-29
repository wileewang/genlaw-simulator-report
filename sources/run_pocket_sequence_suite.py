"""Run one admitted stage at a time, preserving every completed case and defect."""
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
from genlaw_pipeline.pocket_sequence_verification import evaluate
import genlaw_pipeline.run as pipeline
# Scheduling change only: retry capacity rejections which have started no worker.
# Actual native / measurement failures keep their evidence and are not retried.
pipeline.bounded=admitted_bounded
from tools.run_sequence_suite import spans,verify_case
load=lambda p:json.loads(p.read_text())
CONTROLS={key:'floor-hole' for key in ('missing-pocket','shallow-pocket','shifted-floor-hole','blind-floor-hole','wrong-order')}


def defective_nc(text,kind):
    lines,found=spans(text);a,b=found['Pocket_02'];c,d=found['Helix_03']
    if kind=='missing-pocket':return ''.join(lines[:a]+lines[b:]),b-a
    if kind=='wrong-order':
        if b!=c:raise ValueError('Control needs adjacent same-tool operations')
        return ''.join(lines[:a]+lines[c:d]+lines[a:b]+lines[d:]),b-a+d-c
    left,right=(a,b) if kind=='shallow-pocket' else (c,d)
    count=0;body=[]
    def change(match):
        nonlocal count
        value=float(match[1]);new=(value+.25 if value<-.5 else value) if kind=='shallow-pocket' else max(value,-1.15) if kind=='blind-floor-hole' else value+.35
        if new!=value:count+=1
        return ('X' if kind=='shifted-floor-hole' else 'Z')+f'{new:.5f}'
    if kind not in ('shallow-pocket','shifted-floor-hole','blind-floor-hole'):raise ValueError('Unknown explicit NC defect')
    for line in lines[left:right]:
        if line.lstrip().startswith(('G0 ','G1 ','G2 ','G3 ')):
            line=re.sub(r'\b'+('X' if kind=='shifted-floor-hole' else 'Z')+r'([-+]?\d+(?:\.\d+)?)',change,line)
        body.append(line)
    if not count:raise ValueError('Controlled defect changed no coordinate')
    return ''.join(lines[:left])+''.join(body)+''.join(lines[right:]),count


def checkpoint_acceptance(case,policy):
    rules=load(policy)['features'];records=[]
    for row in load(case/'material-checkpoints.json')['checkpoints']:
        folder=case/'checkpoints'/row['id'];record=load(folder/'observations.json');obs=record['observations'];checks=[]
        if set(obs['features'])!=set(record['completed_features']):raise ValueError('Incomplete material-state feature coverage')
        for fid,values in obs['features'].items():
            checks.extend({'feature_id':fid,'kind':kind,**evaluate(kind,values,rule)} for kind,rule in rules[fid].items())
        passed=all(c['status']=='numeric_pass' for c in checks) and not obs['unassigned_contours_detected']
        result={'id':row['id'],'iou_percent':row['iou_percent'],'observations_sha256':digest(folder/'observations.json'),
                'software_policy_sha256':digest(policy),'software_regression':checks,'all_regression_expectations_met':passed,'production_approved':False}
        write(folder/'software-verification.json',result);records.append(result)
        if not passed:raise RuntimeError('Intermediate material failed: '+case.name+'/'+row['id'])
    return records


def checked_inputs(inputs):
    for name,binding in load(inputs/'manifest.json')['files'].items():verify_binding(inputs/name,binding['sha256'])


def normal(inputs,out,key):
    spec=next(s for s in load(inputs/'specifications.json')['cases'] if s['id']==key)
    checked_inputs(inputs);case=out/key;policy=inputs/key/'software-policy.json'
    result=pipeline.run(inputs/key/'input.json',case)
    if result['status']!='software_chain_measured':raise RuntimeError('Layered sequence failed: '+str(result.get('error',result['status'])))
    report,status=verify_case(case,case,policy);checkpoints=checkpoint_acceptance(case,policy)
    checked=load(case/'cam-result.json')['nc_check']
    passed=(all(v=='numeric_pass' for row in status.values() for v in row.values()) and not report['observations']['unassigned_contours_detected']
        and [r['feature_id'] for r in checked['step_map']]==spec['order']
        and [r['tool_number'] for r in checked['tool_transitions']]==([1,2] if spec['two_tools'] else [1])
        and report['process_conformance']['status']=='matched_card_and_measured_checkpoints')
    record={'id':key,'specification':spec,'iou_percent':result['iou_percent'],'software_statuses':status,'regression_expectation_met':passed,
        'executed_feature_order':spec['order'],'tool_transitions':checked['tool_transitions'],
        'checkpoints':[{'id':r['id'],'iou_percent':r['iou_percent'],'check_count':len(r['software_regression']),
                       'all_regression_expectations_met':r['all_regression_expectations_met']} for r in checkpoints],
        'process_conformance':report['process_conformance'],'verifier_sha256':report['source_code_sha256']}
    write(case/'suite-case.json',record)
    for name,sha in result['source_code_sha256'].items():verify_binding(ROOT/'genlaw_pipeline'/name,sha)
    print(json.dumps({'normal_completed':key,'iou_percent':result['iou_percent'],'all_checks_pass':passed}),flush=True)
    if not passed:raise RuntimeError('Layered sequence acceptance failed: '+key)


def control(inputs,out,key):
    checked_inputs(inputs);parent_key=CONTROLS[key];parent=out/parent_key;case=out/key
    normal_result=load(parent/'run-result.json')
    if not load(parent/'suite-case.json')['regression_expectation_met']:raise ValueError('Need a qualified positive parent')
    for name,sha in normal_result['source_code_sha256'].items():verify_binding(ROOT/'genlaw_pipeline'/name,sha)
    plan=load(parent/'execution-plan.json');changed,count=defective_nc((parent/'program.nc').read_text(),key)
    try:check_nc(changed,plan)
    except ValueError as error:rejection=str(error)
    else:raise AssertionError('Normal NC guard accepted defect')
    case.mkdir();(case/'program.nc').write_text(changed);write(case/'simulation.camotics',simulation_project(plan))
    admitted_bounded(case,'camotics',[CAMOTICS,'--threads','1','--reduce=false','--binary',case/'simulation.camotics',case/'simulated.stl'],simulation_env(),case/'simulation.camotics')
    write(case/'control-binding.json',{'schema':'genlaw.verification-control.v1','test_only_guard_bypass':True,'parent_case':parent_key,
        'origin':key+': explicit faulty NC executed on unchanged original stock / tools / resolution for regression only.',
        'parent_nc_sha256':digest(parent/'program.nc'),'nc_file':'program.nc','mesh_file':'simulated.stl','project_file':'simulation.camotics',
        'nc_sha256':digest(case/'program.nc'),'mesh_sha256':digest(case/'simulated.stl'),'project_sha256':digest(case/'simulation.camotics'),
        'normal_guard_rejection':rejection,'changed_words_or_blocks':count,'production_approved':False})
    measure_bounded(case,'measure',parent,case/'simulated.stl',case/'metrics.json')
    report,status=verify_case(parent,case,inputs/parent_key/'software-policy.json',control=True)
    geometry_pass=all(v=='numeric_pass' for row in status.values() for v in row.values()) and not report['observations']['unassigned_contours_detected']
    expected=(status['K1']['pocket_depth']=='numeric_fail' if key in ('missing-pocket','shallow-pocket') else
              status['H1']['position_xy']=='numeric_fail' if key=='shifted-floor-hole' else
              status['H1']['through']=='numeric_fail' if key=='blind-floor-hole' else geometry_pass)
    expected=expected and report['process_conformance']['status']=='rejected'
    record={'id':key,'parent_case':parent_key,'guard_rejected':True,'normal_guard_rejection':rejection,
        'iou_percent':load(case/'metrics.json')['iou_percent'],'software_statuses':status,'geometry_checks_pass':geometry_pass,
        'process_conformance':report['process_conformance'],'regression_expectation_met':expected,'verifier_sha256':report['source_code_sha256']}
    write(case/'suite-case.json',record)
    print(json.dumps({'control_completed':key,'iou_percent':record['iou_percent'],'expected_defect_detected':expected}),flush=True)
    if not expected:raise RuntimeError('Controlled defect not identified: '+key)


def finish(inputs,out):
    checked_inputs(inputs)
    normals=[load(out/s['id']/'suite-case.json') for s in load(inputs/'specifications.json')['cases']]
    controls=[load(out/key/'suite-case.json') for key in CONTROLS]
    if not all(r['regression_expectation_met'] for r in normals+controls):raise ValueError('Incomplete positive / negative qualification')
    for row in normals:
        case=out/row['id']
        for name,sha in load(case/'run-result.json')['source_code_sha256'].items():verify_binding(ROOT/'genlaw_pipeline'/name,sha)
    write(out/'summary.json',{'schema':'genlaw.pocket-sequence-suite.v1','id':out.name,'status':'completed','normal_cases':normals,'controls':controls,
        'all_regression_expectations_met':True,'input_archive':inputs.name,'input_manifest_sha256':digest(inputs/'manifest.json'),
        'production_approved':False,'whole_process_card_complete':False,'s4_complete':False,
        'implemented':'One original-CAD native MillFace/PocketShape/Helix job in card order, complete pocket outer-wire clearing, layer-aware floor-hole verification, unchanged original stock, independently frozen cumulative oracles.'})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage',choices=('normal','control','finish'));p.add_argument('--case')
    p.add_argument('--inputs',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    inputs,out=a.inputs.resolve(),a.output.resolve()
    if not inputs.is_relative_to(ROOT) or not out.is_relative_to(ROOT):raise ValueError('Keep all work in project')
    out.mkdir(parents=True,exist_ok=True)
    if a.stage=='finish':finish(inputs,out)
    elif a.stage=='normal':normal(inputs,out,a.case)
    else:control(inputs,out,a.case)
