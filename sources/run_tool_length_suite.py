"""Card-bound native TLO: one admitted normal or negative-control case at a time."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.run import write
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.programming import check_nc, simulation_project, verify_binding
from genlaw_pipeline.coordinate_backend import (engine_for_plan, applied_arguments,
    native_environment, initialize_controller, check_execution_setup, REGISTER_FILE)
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.measurement import measure_bounded
from tools.run_pocket_sequence_suite import normal, checked_inputs
from tools.run_sequence_suite import verify_case

LOAD=lambda p:json.loads(p.read_text())
CONTROLS={
    'wrong-h-register':{'parent':'cad-origin',
        'register_words':['G10 L1 P1 Z30.40000','G10 L1 P1 Z30.70000'],
        'failure':['P1','top_height'],
        'reason':'H1 is 0.3 mm too large; mounted length and cutting segment stay unchanged.'},
    'wrong-mounted-length':{'parent':'cad-origin','actual_tool_number':1,'actual_length_mm':30.7,
        'failure':['P1','top_height'],
        'reason':'Actual mounted L1 is 0.3 mm too long; H1 and cutting segment stay unchanged.'},
    'missing-g43':{'parent':'single-tool','nc_words':['G43 H1 \n',''],
        'failure':['P1','top_height'],
        'reason':'First native G43 is removed; H remains cancelled by the original G49 preamble.'},
    'stale-h-after-m6':{'parent':'shorter-tool','nc_words':['G43 H2','G43 H1'],
        'failure':['H1','through'],
        'reason':'After mounting shorter T2, the longer T1 offset is reloaded; hole ends 0.3 mm above the stock bottom.'},
}


def control(inputs,out,key):
    checked_inputs(inputs);spec=CONTROLS[key];parent=out/spec['parent'];case=out/key
    if not LOAD(parent/'suite-case.json')['regression_expectation_met']:raise ValueError('Positive parent incomplete')
    source=LOAD(parent/'run-result.json')['source_code_sha256']
    for name,sha in source.items():verify_binding(ROOT/'genlaw_pipeline'/name,sha)
    plan=LOAD(parent/'execution-plan.json');engine=engine_for_plan(plan);case.mkdir(exist_ok=False)
    nc=(parent/'program.nc').read_text()
    if 'nc_words' in spec:
        old,new=spec['nc_words']
        if nc.count(old)!=1:raise ValueError('Control requires one exact native compensation block')
        nc=nc.replace(old,new)
    (case/'program.nc').write_text(nc)
    initial=initialize_controller(case,plan)
    if 'register_words' in spec:
        path=case/REGISTER_FILE;text=path.read_text();old,new=spec['register_words']
        if text.count(old)!=1:raise ValueError('Expected one independent offset register')
        path.write_text(text.replace(old,new));initial['controller_initialization_sha256']=digest(path)
    if 'actual_tool_number' in spec:
        rows=[r for r in initial['tool_gauge_lengths_mm'] if r['tool_number']==spec['actual_tool_number']]
        if len(rows)!=1:raise ValueError('Expected one independently mounted physical cutter')
        rows[0]['gauge_to_tip_mm']=spec['actual_length_mm']
    try:
        check_nc(nc,plan);check_execution_setup(case,plan,initial)
    except ValueError as error:rejection=str(error)
    else:raise ValueError('Normal entry accepted deliberate tool-length defect')
    write(case/'normal-guard-rejection.json',{'status':'rejected','reason':rejection,'control':spec,
        'native_simulation_not_yet_started':True,'source_sha256':digest(Path(__file__))})
    project=simulation_project(plan);write(case/'simulation.camotics',project)
    args=applied_arguments(plan,engine,initial)
    before={name:digest(case/name) for name in ['program.nc',REGISTER_FILE,'simulation.camotics']}
    admitted_bounded(case,'camotics',args+[case/'simulation.camotics',case/'simulated.stl'],
        native_environment(engine),case/'simulation.camotics')
    for name,sha in before.items():verify_binding(case/name,sha)
    binding={'schema':'genlaw.verification-control.v1','test_only_guard_bypass':True,
        'parent_case':spec['parent'],'parent_nc_sha256':digest(parent/'program.nc'),
        'origin':spec['reason']+' Original CAD, stock, cutting geometry and acceptance policy remain unchanged.',
        'nc_file':'program.nc','mesh_file':'simulated.stl','project_file':'simulation.camotics',
        'nc_sha256':digest(case/'program.nc'),'mesh_sha256':digest(case/'simulated.stl'),
        'project_sha256':digest(case/'simulation.camotics'),'engine_manifest_sha256':engine['manifest_sha256'],
        'engine_binary_sha256':engine['manifest']['binary_sha256'],
        'native_resources_sha256':digest(case/'camotics-resources.json'),**initial,
        'normal_guard_rejection':rejection,'normal_guard_rejection_sha256':digest(case/'normal-guard-rejection.json'),
        'nc_modified':nc!=(parent/'program.nc').read_text(),'source_sha256':digest(Path(__file__)),
        'production_approved':False}
    write(case/'control-binding.json',binding)
    measure_bounded(case,'measure',parent,case/'simulated.stl',case/'metrics.json')
    report,status=verify_case(parent,case,inputs/spec['parent']/'software-policy.json',control=True)
    fid,kind=spec['failure']
    expected=status[fid][kind]=='numeric_fail' and report['process_conformance']['status']=='rejected'
    record={'id':key,'parent_case':spec['parent'],'guard_rejected':True,'normal_guard_rejection':rejection,
        'iou_percent':LOAD(case/'metrics.json')['iou_percent'],'software_statuses':status,
        'expected_failure':spec['failure'],'regression_expectation_met':expected,
        'process_conformance':report['process_conformance'],'nc_modified':binding['nc_modified'],
        'control_binding_sha256':digest(case/'control-binding.json'),'verifier_sha256':report['source_code_sha256']}
    write(case/'suite-case.json',record)
    for name,sha in source.items():verify_binding(ROOT/'genlaw_pipeline'/name,sha)
    print(json.dumps({'control_completed':key,'iou_percent':record['iou_percent'],
                     'expected_defect_detected':expected}),flush=True)
    if not expected:raise ValueError('Tool-length defect differs from its frozen regression prediction')


def finish(inputs,out):
    checked_inputs(inputs)
    normals=[LOAD(out/s['id']/'suite-case.json') for s in LOAD(inputs/'specifications.json')['cases']]
    controls=[LOAD(out/key/'suite-case.json') for key in CONTROLS]
    if not all(r['regression_expectation_met'] for r in normals+controls):raise ValueError('Incomplete tool-length qualification')
    for row in normals:
        for name,sha in LOAD(out/row['id']/'run-result.json')['source_code_sha256'].items():
            verify_binding(ROOT/'genlaw_pipeline'/name,sha)
    write(out/'summary.json',{'schema':'genlaw.tool-length-suite.v1','id':out.name,'status':'completed',
        'normal_cases':normals,'controls':controls,'all_regression_expectations_met':True,
        'input_manifest_sha256':digest(inputs/'manifest.json'),'source_sha256':digest(Path(__file__)),
        'selection_sha256':digest(ROOT/'engines/camotics/tool-length-engine.json'),
        'implemented':'Card-bound independent actual lengths and H registers, native FreeCAD G43 export, physical clearance before/after stationary M6, untouched normal NC, final and cumulative-prefix material compared with frozen original CAD.',
        'scope':'Metric XYZ, G54/G55, static absolute-gauge Z compensation with H=T and normal H=L, single setup and nonrandom M6; cutting segment only.',
        'not_qualified':['dynamic offsets','relative gauge calibration','rotary axes','five-axis machining',
            'holders or fixture collisions','machine limits','material physics','whole real process card'],
        'production_approved':False,'whole_process_card_complete':False,'s4_complete':False,
        'web_publication_pending':True})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['normal','control','finish'])
    parser.add_argument('--inputs',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case');args=parser.parse_args()
    inputs,out=args.inputs.resolve(),args.output.resolve();inputs.relative_to(ROOT);out.relative_to(ROOT)
    out.mkdir(parents=True,exist_ok=True)
    if args.stage=='normal':normal(inputs,out,args.case)
    elif args.stage=='control':control(inputs,out,args.case)
    else:finish(inputs,out)
