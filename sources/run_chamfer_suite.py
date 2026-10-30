"""Qualify frozen chamfer cards with native runs and intentionally defective NC."""
import argparse,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.programming import check_nc,simulation_project,verify_binding
from genlaw_pipeline.run import run,write,CAMOTICS,simulation_env
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.measurement import measure_bounded
from tools.run_pattern_suite import checked_existing,statuses

CONTROLS=['overcut-depth','too-shallow','wrong-position','extra-inner-edge']
load=lambda path:json.loads(path.read_text())


def defective_nc(original,kind):
    if kind not in CONTROLS:raise ValueError('Unknown intentional chamfer defect')
    if kind=='extra-inner-edge':
        marker='G2 X8.00000 Y-0.40000 Z-0.60000 I-0.40000 J0.00000 F120.00000 '
        if original.count(marker)!=1:raise ValueError('Expected baseline final corner not found')
        return original.replace(marker,marker+'\nG1 X8.00000 Y5.60000 Z-0.60000 F120.00000\nG1 X0.40000 Y5.60000 Z-0.60000 F120.00000',1),2
    count=0;lines=[]
    for line in original.splitlines(keepends=True):
        if line.split() and line.split()[0] in ('G0','G1','G2'):
            def change(match):
                nonlocal count
                axis=match[1];value=float(match[2]);new=value
                if kind=='wrong-position' and axis=='X':new=value+.5
                elif kind in ('too-shallow','overcut-depth') and axis=='Z' and value<0:new=value+(.25 if kind=='too-shallow' else -.2)
                if abs(new-value)<1e-10:return match[0]
                count+=1;return axis+f'{new:.5f}'
            line=re.sub(r'\b([XYZ])([-+]?\d+(?:\.\d+)?)',change,line)
        lines.append(line)
    if not count:raise ValueError('No intentional defect inserted')
    return ''.join(lines),count


def verify_case(parent,case,policy,control=False):
    target=case/'software-verification.json'
    if not target.exists():
        cmd=[sys.executable,ROOT/'genlaw_pipeline/verification.py','--run',parent,'--output',target,'--software-policy',policy]
        if control:cmd+=['--control',case/'control-binding.json']
        admitted_bounded(case,'verify-software',cmd)
    report=load(target)
    for path,key in [(policy,'software_policy_sha256'),(case/'simulated.stl','mesh_sha256'),(case/'program.nc','nc_sha256')]:verify_binding(path,report[key])
    verify_binding(ROOT/'genlaw_pipeline/verification.py',report['source_code_sha256'])
    for name,sha in report['additional_source_code_sha256'].items():verify_binding(ROOT/'genlaw_pipeline'/name,sha)
    states=statuses(report)
    if set(states)!={'C1'} or set(states['C1'])!=set(load(policy)['features']['C1']):raise ValueError('Incomplete chamfer software acceptance')
    return report,states


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['inputs','output']:parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--resume',action='store_true');parser.add_argument('--only-case')
    args=parser.parse_args();inputs=args.inputs.resolve();out=args.output.resolve()
    if not out.is_relative_to(ROOT):raise ValueError('Keep all results in project')
    out.mkdir(parents=True,exist_ok=args.resume)
    for name,value in load(inputs/'manifest.json')['files'].items():verify_binding(inputs/name,value['sha256'])
    specs=load(inputs/'specifications.json')['cases']
    if args.only_case and args.only_case not in {s['id'] for s in specs}:raise ValueError('Unknown case')
    normals=[];controls=[]
    for spec in specs:
        key=spec['id']
        if args.only_case and key!=args.only_case:continue
        case=out/key;entry=inputs/key/'input.json'
        if args.resume and case.exists():
            result=checked_existing(case,entry)
            for name in ['run.py','milling_features.py','milling_preparation.py','chamfer_cam.py','chamfer_nc.py','chamfer_geometry.py','freecad_deburr.py','native_stock.py']:
                verify_binding(ROOT/'genlaw_pipeline'/name,result['source_code_sha256'][name])
        else:result=run(entry,case)
        if result['status']!='software_chain_measured':raise RuntimeError('Chamfer chain failed: '+key)
        report,states=verify_case(case,case,inputs/key/'software-policy.json');obs=report['observations']['features']['C1'];cam=load(case/'cam-result.json')
        expected=(all(s=='numeric_pass' for s in states['C1'].values()) and obs['complete'] and obs['width_complete']
            and obs['topology_matches'] and obs['section_count']==14 and obs['probe_count']==25 and cam['native_operation']=='Deburr')
        row={'id':key,'specification':spec,'iou_percent':result['iou_percent'],'software_statuses':states,
            'regression_expectation_met':expected,'max_boundary_deviation_mm':obs['max_boundary_deviation_mm'],
            'max_width_deviation_mm':obs['max_width_deviation_mm'],'widths':obs['widths'],
            'nc_sha256':digest(case/'program.nc'),'mesh_sha256':digest(case/'simulated.stl'),'software_verification_sha256':digest(case/'software-verification.json')}
        if not (case/'case-summary.json').exists():write(case/'case-summary.json',row)
        elif load(case/'case-summary.json')!=row:raise ValueError('Normal summary changed')
        normals.append(row);print(json.dumps({'normal_completed':key,'iou_percent':row['iou_percent'],'software_statuses':states}),flush=True)
        if not expected:raise RuntimeError('Normal chamfer did not meet frozen criteria: '+key)
    if args.only_case:return
    parent=out/'baseline';plan=load(parent/'execution-plan.json');policy=inputs/'baseline/software-policy.json'
    for key in CONTROLS:
        case=out/key;changed,count=defective_nc((parent/'program.nc').read_text(),key)
        try:check_nc(changed,plan)
        except ValueError as error:rejection=str(error)
        else:raise AssertionError('Normal guard accepted intentional defect')
        if args.resume and case.exists():
            binding=load(case/'control-binding.json')
            if binding['parent_nc_sha256']!=digest(parent/'program.nc') or (case/'program.nc').read_text()!=changed:raise ValueError('Control source changed')
            for name,field in [('program.nc','nc_sha256'),('simulation.camotics','project_sha256'),('simulated.stl','mesh_sha256')]:verify_binding(case/name,binding[field])
            if not load(case/'camotics-resources.json')['success']:raise ValueError('Control simulation not completed')
        else:
            case.mkdir();(case/'program.nc').write_text(changed);write(case/'simulation.camotics',simulation_project(plan))
            admitted_bounded(case,'camotics',[CAMOTICS,'--threads','1','--reduce=false','--binary',case/'simulation.camotics',case/'simulated.stl'],simulation_env(),case/'simulation.camotics')
            write(case/'control-binding.json',{'schema':'genlaw.verification-control.v1','test_only_guard_bypass':True,'parent_case':'baseline',
                'origin':{'overcut-depth':'Every cutting Z lowered 0.2 mm.','too-shallow':'Every cutting Z raised 0.25 mm.',
                    'wrong-position':'Absolute X shifted 0.5 mm, incremental I/J unchanged.',
                    'extra-inner-edge':'Two extra inside-cutting lines after the closed loop reproduce the native extra-wire failure class.'}[key],
                'parent_nc_sha256':digest(parent/'program.nc'),'nc_file':'program.nc','mesh_file':'simulated.stl','project_file':'simulation.camotics',
                'nc_sha256':digest(case/'program.nc'),'mesh_sha256':digest(case/'simulated.stl'),'project_sha256':digest(case/'simulation.camotics'),
                'normal_guard_rejection':rejection,'changed_words_or_blocks':count,'production_approved':False})
        if not (case/'metrics.json').exists():measure_bounded(case,'measure',parent,case/'simulated.stl',case/'metrics.json')
        else:verify_binding(case/'simulated.stl',load(case/'metrics.json')['actual_mesh']['source_sha256'])
        report,states=verify_case(parent,case,policy,control=True)
        expected=states['C1']['chamfer_sections']=='numeric_fail' and report['process_conformance']['status']=='rejected'
        row={'id':key,'parent_case':'baseline','guard_rejected':True,'iou_percent':load(case/'metrics.json')['iou_percent'],
            'software_statuses':states,'regression_expectation_met':expected,'software_verification_sha256':digest(case/'software-verification.json')}
        controls.append(row);print(json.dumps({'control_completed':row}),flush=True)
        if not expected:raise RuntimeError('Original geometry checks failed to detect defect: '+key)
    write(out/'summary.json',{'schema':'genlaw.chamfer-suite.v1','id':out.name,'status':'completed','normal_cases':normals,'controls':controls,
        'all_regression_expectations_met':True,'input_archive':inputs.name,'input_manifest_sha256':digest(inputs/'manifest.json'),
        'production_approved':False,'whole_process_card_complete':False,'real_process_card_tested':False,
        'scope':'One synthetic rectangular four-edge chamfer family; finite-frustum cutter, fixed Z, no certified manufacturing angle acceptance.'})


if __name__=='__main__':main()
