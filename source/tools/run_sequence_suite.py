"""Four ordered milling chains, actual intermediate states and five defect controls."""
import argparse,copy,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.programming import check_nc,simulation_project,verify_binding
from genlaw_pipeline.run import run,bounded,write,CAMOTICS,simulation_env
from genlaw_pipeline.measurement import measure_bounded
from genlaw_pipeline.sequence_verification import evaluate

load=lambda p:json.loads(p.read_text())
CONTROLS={'missing-face':'baseline','wrong-tool':'tool-change','shifted-hole':'baseline','reversed-order':'baseline','startup-collision':'shifted-z'}


def spans(text):
    lines=text.splitlines(keepends=True);found={};start=None;name=None
    for i,line in enumerate(lines):
        begin=re.fullmatch(r'\(begin operation: ([^()]+)\)',line.strip())
        end=re.fullmatch(r'\(finish operation: ([^()]+)\)',line.strip())
        if begin:
            if start is not None:raise ValueError('Nested native section')
            start=i;name=begin[1]
        if end:
            if start is None or end[1]!=name:raise ValueError('Native section not paired')
            found[name]=(start,i+1);start=None
    return lines,found


def defective_nc(text,kind):
    lines,found=spans(text);a,b=found['Face_01'];c,d=found['Helix_02']
    if kind=='startup-collision':
        changed,count=re.subn(r'G21\nM5\nG0 Z3\nG21\n','G21\n',text,count=1)
        if count!=1:raise ValueError('No explicit unloaded startup to remove')
        return changed,3
    if kind=='missing-face':return ''.join(lines[:a]+lines[b:]),b-a
    if kind=='reversed-order':
        if b!=c:raise ValueError('This controlled reordering requires adjacent same-tool operations')
        return ''.join(lines[:a]+lines[c:d]+lines[a:b]+lines[d:]),(b-a)+(d-c)
    if kind=='wrong-tool':
        left,right=found['TC_02'];original=''.join(lines[left:right]);changed,count=re.subn(r'\bM6 T2\b','M6 T1',original)
        if count!=1:raise ValueError('Need one explicit T2 transition')
        return ''.join(lines[:left])+changed+''.join(lines[right:]),count
    if kind=='shifted-hole':
        count=0;body=[]
        for line in lines[c:d]:
            if line.lstrip().startswith(('G0 ','G1 ','G3 ')):
                def shift(m):
                    nonlocal count
                    count+=1;return 'X'+f'{float(m[1])+.5:.5f}'
                line=re.sub(r'\bX([-+]?\d+(?:\.\d+)?)',shift,line)
            body.append(line)
        if not count:raise ValueError('No hole positions changed')
        return ''.join(lines[:c]+body+lines[d:]),count
    raise ValueError('Unknown control')


def statuses(report):
    result={}
    for row in report['software_regression']:result.setdefault(row['feature_id'],{})[row['kind']]=row['status']
    return result


def verify_case(parent,case,policy,control=False):
    command=[sys.executable,ROOT/'genlaw_pipeline/verification.py','--run',parent,'--output',case/'software-verification.json','--software-policy',policy]
    if control:command+=['--control',case/'control-binding.json']
    bounded(case,'verify-software',command);report=load(case/'software-verification.json')
    for path,key in [(policy,'software_policy_sha256'),(case/'simulated.stl','mesh_sha256'),(case/'program.nc','nc_sha256')]:verify_binding(path,report[key])
    verify_binding(ROOT/'genlaw_pipeline/verification.py',report['source_code_sha256'])
    for name,sha in report['additional_source_code_sha256'].items():verify_binding(ROOT/'genlaw_pipeline'/name,sha)
    got=statuses(report);rules=load(policy)['features']
    if set(got)!=set(rules) or any(set(got[fid])!=set(rules[fid]) for fid in got):raise ValueError('Incomplete combined acceptance report')
    return report,got


def checkpoint_acceptance(case,policy):
    rules=load(policy)['features'];material=load(case/'material-checkpoints.json');records=[]
    for row in material['checkpoints']:
        folder=case/'checkpoints'/row['id'];observed=load(folder/'observations.json');obs=observed['observations'];checks=[]
        if set(obs['features'])!=set(observed['completed_features']):raise ValueError('Checkpoint feature coverage changed')
        for fid,values in obs['features'].items():
            checks.extend({'feature_id':fid,'kind':kind,**evaluate(kind,values,rule)} for kind,rule in rules[fid].items())
        passed=all(c['status']=='numeric_pass' for c in checks) and not obs['unassigned_contours_detected']
        record={'id':row['id'],'iou_percent':row['iou_percent'],'observations_sha256':digest(folder/'observations.json'),
            'software_policy_sha256':digest(policy),'software_regression':checks,'all_regression_expectations_met':passed,
            'production_approved':False};write(folder/'software-verification.json',record);records.append(record)
        if not passed:raise RuntimeError('Actual intermediate material failed its frozen criterion: '+case.name+'/'+row['id'])
    return records


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inputs',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    inputs=a.inputs.resolve();out=a.output.resolve()
    if not out.is_relative_to(ROOT):raise ValueError('Keep results in project')
    out.mkdir(parents=True,exist_ok=False)
    for name,value in load(inputs/'manifest.json')['files'].items():verify_binding(inputs/name,value['sha256'])
    normals=[];controls=[]
    for spec in load(inputs/'specifications.json')['cases']:
        key=spec['id'];case=out/key;policy=inputs/key/'software-policy.json';result=run(inputs/key/'input.json',case)
        if result['status']!='software_chain_measured':raise RuntimeError('Ordered chain failed: '+key+' '+str(result.get('error')))
        report,status=verify_case(case,case,policy);checkpoints=checkpoint_acceptance(case,policy)
        cam=load(case/'cam-result.json');plan=load(case/'execution-plan.json');checked=cam['nc_check']
        passed=(all(v=='numeric_pass' for row in status.values() for v in row.values()) and not report['observations']['unassigned_contours_detected']
            and [row['feature_id'] for row in checked['step_map']]==spec['order']
            and [row['tool_number'] for row in checked['tool_transitions']]==([1,2] if spec['two_tools'] else [1])
            and report['process_conformance']['status']=='matched_card_and_measured_checkpoints')
        row={'id':key,'specification':spec,'iou_percent':result['iou_percent'],'software_statuses':status,
            'regression_expectation_met':passed,'executed_feature_order':[r['feature_id'] for r in checked['step_map']],
            'tool_transitions':checked['tool_transitions'],'checkpoints':[{'id':r['id'],'iou_percent':r['iou_percent'],'all_regression_expectations_met':r['all_regression_expectations_met']} for r in checkpoints],
            'process_conformance':report['process_conformance'],'verifier_sha256':report['source_code_sha256']}
        normals.append(row);print(json.dumps({'normal_completed':row},ensure_ascii=False),flush=True)
        if not passed:raise RuntimeError('Ordered normal failed: '+key)
        # The producer snapshot is checked before retaining this completed run.
        for name,sha in result['source_code_sha256'].items():verify_binding(ROOT/'genlaw_pipeline'/name,sha)
    origins={'missing-face':'Drop the face operation, retaining the original uncut stock for actual helical execution.',
        'wrong-tool':'Change actual M6 T2 to M6 T1; keep the declared tool table, so a 2 mm cutter executes the 1 mm cutter path.',
        'shifted-hole':'Shift only the helical operation absolute X by +0.5 mm; incremental I/J unchanged.',
        'reversed-order':'Execute helix before facing in the same stock. Final Boolean removal may match; card order and material preconditions must still be rejected.',
        'startup-collision':'Remove unloaded startup retract; select the cutter at the default origin inside the positive-Z stock. Reproduce the detected corner gouge.'}
    for key,parent_key in CONTROLS.items():
        parent=out/parent_key;case=out/key;plan=load(parent/'execution-plan.json');changed,count=defective_nc((parent/'program.nc').read_text(),key)
        try:check_nc(changed,plan)
        except ValueError as error:rejection=str(error)
        else:raise AssertionError('Normal guard accepted intentional defect: '+key)
        case.mkdir();(case/'program.nc').write_text(changed);write(case/'simulation.camotics',simulation_project(plan))
        bounded(case,'camotics',[CAMOTICS,'--threads','1','--reduce=false','--binary',case/'simulation.camotics',case/'simulated.stl'],simulation_env(),case/'simulation.camotics')
        binding={'schema':'genlaw.verification-control.v1','test_only_guard_bypass':True,'parent_case':parent_key,'origin':origins[key],
            'parent_nc_sha256':digest(parent/'program.nc'),'nc_file':'program.nc','mesh_file':'simulated.stl','project_file':'simulation.camotics',
            'nc_sha256':digest(case/'program.nc'),'mesh_sha256':digest(case/'simulated.stl'),'project_sha256':digest(case/'simulation.camotics'),
            'normal_guard_rejection':rejection,'changed_words_or_blocks':count,'production_approved':False}
        write(case/'control-binding.json',binding)
        measure_bounded(case,'measure',parent,case/'simulated.stl',case/'metrics.json')
        report,status=verify_case(parent,case,inputs/parent_key/'software-policy.json',control=True)
        geometry_pass=all(v=='numeric_pass' for row in status.values() for v in row.values()) and not report['observations']['unassigned_contours_detected']
        expected=(status['P1']['top_height']=='numeric_fail' and status['P1']['target_thickness']=='numeric_fail') if key=='missing-face' else (
            status['H1']['diameter']=='numeric_fail' if key=='wrong-tool' else status['H1']['position_xy']=='numeric_fail' if key=='shifted-hole' else
            status['P1']['outline_xy']=='numeric_fail' if key=='startup-collision' else geometry_pass)
        expected=expected and report['process_conformance']['status']=='rejected'
        row={'id':key,'parent_case':parent_key,'guard_rejected':True,'normal_guard_rejection':rejection,'iou_percent':load(case/'metrics.json')['iou_percent'],
            'software_statuses':status,'geometry_checks_pass':geometry_pass,'process_conformance':report['process_conformance'],
            'regression_expectation_met':expected,'verifier_sha256':report['source_code_sha256']}
        controls.append(row);print(json.dumps({'control_completed':row},ensure_ascii=False),flush=True)
        if not expected:raise RuntimeError('Controlled error was not identified as expected: '+key)
    write(out/'summary.json',{'schema':'genlaw.sequence-suite.v1','id':out.name,'status':'completed','normal_cases':normals,'controls':controls,
        'all_regression_expectations_met':True,'input_archive':inputs.name,'input_manifest_sha256':digest(inputs/'manifest.json'),
        'production_approved':False,'whole_process_card_complete':False,'s4_complete':False,
        'implemented':'One native face/helix job, ordered explicit tools, one continuous original stock and exact cumulative-prefix checkpoints.'})


if __name__=='__main__':main()
