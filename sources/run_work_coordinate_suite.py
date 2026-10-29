"""Unchanged FreeCAD NC plus explicit controller/material configurations.

Normals reuse the complete ordered-pocket workflow. Four deliberate defects
first fail normal guards and then execute only as bound negative controls.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.run import write
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.programming import check_nc,simulation_project,verify_binding
from genlaw_pipeline.coordinate_backend import (engine_for_plan,native_arguments,native_environment,
    initialize_controller,check_execution_setup,REGISTER_FILE)
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.measurement import measure_bounded
from tools.run_pocket_sequence_suite import normal,checked_inputs
from tools.run_sequence_suite import verify_case

LOAD=lambda p:json.loads(p.read_text())
CONTROLS={
    'wrong-register-x':{'parent':'cad-origin','register_word':['X14.00000','X14.40000'],'failure':['K1','profile_xy']},
    'wrong-register-z':{'parent':'cad-origin','register_word':['Z29.50000','Z29.80000'],'failure':['P1','top_height']},
    'wrong-material-placement':{'parent':'cad-origin','applied_origin':[10.4,20,30],'failure':['K1','profile_xy']},
    'wrong-work-slot':{'parent':'g55-tool-change','nc_words':['G55','G54'],'failure':['P1','top_height']},
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
        if nc.count(old)!=2:raise ValueError('Expect one selected slot in preamble and shutdown')
        nc=nc.replace(old,new)
    (case/'program.nc').write_text(nc)
    initial=initialize_controller(case,plan)
    if 'register_word' in spec:
        path=case/REGISTER_FILE;text=path.read_text();old,new=spec['register_word']
        if text.count(old)!=1:raise ValueError('Expected one explicitly initialized register coordinate')
        path.write_text(text.replace(old,new));initial['controller_initialization_sha256']=digest(path)
    if 'applied_origin' in spec:initial['material_origin_machine_mm']=spec['applied_origin']
    try:
        check_nc(nc,plan);check_execution_setup(case,plan,initial)
    except ValueError as error:rejection=str(error)
    else:raise ValueError('Normal entry accepted the deliberate coordinate defect')
    write(case/'normal-guard-rejection.json',{'status':'rejected','reason':rejection,'control':spec,
        'native_simulation_not_yet_started':True,'source_sha256':digest(Path(__file__))})
    project=simulation_project(plan);write(case/'simulation.camotics',project)
    args=native_arguments(plan,engine)
    args[-1]='--material-origin='+','.join(f'{v:.5f}' for v in initial['material_origin_machine_mm'])
    before={name:digest(case/name) for name in ['program.nc',REGISTER_FILE,'simulation.camotics']}
    admitted_bounded(case,'camotics',args+[case/'simulation.camotics',case/'simulated.stl'],native_environment(engine),case/'simulation.camotics')
    for name,sha in before.items():verify_binding(case/name,sha)
    binding={'schema':'genlaw.verification-control.v1','test_only_guard_bypass':True,
        'parent_case':spec['parent'],'parent_nc_sha256':digest(parent/'program.nc'),
        'origin':'Explicit coordinate/configuration defect; original CAD, stock, tools and geometry criteria remain unchanged.',
        'nc_file':'program.nc','mesh_file':'simulated.stl','project_file':'simulation.camotics',
        'nc_sha256':digest(case/'program.nc'),'mesh_sha256':digest(case/'simulated.stl'),'project_sha256':digest(case/'simulation.camotics'),
        'engine_manifest_sha256':engine['manifest_sha256'],'engine_binary_sha256':engine['manifest']['binary_sha256'],
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
    print(json.dumps({'control_completed':key,'iou_percent':record['iou_percent'],'expected_defect_detected':expected}),flush=True)
    if not expected:raise ValueError('Deliberate coordinate defect not identified')


def finish(inputs,out):
    checked_inputs(inputs)
    normals=[LOAD(out/s['id']/'suite-case.json') for s in LOAD(inputs/'specifications.json')['cases']]
    controls=[LOAD(out/key/'suite-case.json') for key in CONTROLS]
    if not all(r['regression_expectation_met'] for r in normals+controls):raise ValueError('Incomplete coordinate qualification')
    for row in normals:
        for name,sha in LOAD(out/row['id']/'run-result.json')['source_code_sha256'].items():verify_binding(ROOT/'genlaw_pipeline'/name,sha)
    write(out/'summary.json',{'schema':'genlaw.work-coordinate-suite.v1','id':out.name,'status':'completed',
        'normal_cases':normals,'controls':controls,'all_regression_expectations_met':True,
        'input_manifest_sha256':digest(inputs/'manifest.json'),'source_sha256':digest(Path(__file__)),
        'selection_sha256':digest(ROOT/'engines/camotics/coordinate-engine.json'),
        'implemented':'Explicit card/setup binding, native FreeCAD translated job and untouched NC, separate controller register initialization, fixed original-CAD material frame, final and cumulative-prefix geometry verification.',
        'scope':'One metric XYZ translation setup with G54/G55, no rotation or tool length compensation.',
        'production_approved':False,'whole_process_card_complete':False,'s4_complete':False,
        'web_publication_pending':True})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('stage',choices=['normal','control','finish'])
    p.add_argument('--inputs',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--case')
    a=p.parse_args();inputs,out=a.inputs.resolve(),a.output.resolve();inputs.relative_to(ROOT);out.relative_to(ROOT)
    out.mkdir(parents=True,exist_ok=True)
    if a.stage=='normal':normal(inputs,out,a.case)
    elif a.stage=='control':control(inputs,out,a.case)
    else:finish(inputs,out)
