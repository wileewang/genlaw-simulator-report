"""Use the qualified translation engine only for an explicit XYZ setup.

Legacy engine selection stays unchanged. The initialization is a separate
configuration file; FreeCAD's exported part program remains byte-identical.
"""
import json
from pathlib import Path
from .coordinate_setup import enabled, validate_setup, setup_registers, check_setup_registers
from .preparation import digest
from .simulation_engine import selected_engine, workspace_environment, workspace_path
from .cell_windows import checked_file, candidate_engine

ROOT=Path(__file__).resolve().parents[1]
SELECTION=ROOT/'engines/camotics/coordinate-engine.json'
REGISTER_FILE='setup-registers.nc'


def engine_for_plan(plan):
    if not enabled(plan):return selected_engine()
    validate_setup(plan['setup'],plan['stock']['bounds_mm'])
    selection=json.loads(SELECTION.read_text())
    if selection.get('schema')!='genlaw.coordinate-engine-selection.v1':raise ValueError('Unknown coordinate engine selection')
    manifest_path=checked_file(selection['engine']);evidence=json.loads(checked_file(selection['qualification']).read_text())
    binary,_,manifest=candidate_engine(manifest_path)
    if (evidence['status']!='declared_probe_scope_verified' or evidence['engine']!=selection['engine']
            or checked_file(evidence['engine_binary'])!=binary or evidence['motion_check_count']!=43
            or evidence['normal_geometry_case_count']!=4 or evidence['geometry_defect_case_count']!=3
            or len(evidence['legacy_exact_payload_regressions'])!=6):
        raise ValueError('Missing bound work-coordinate engine probe evidence')
    for key in ['work_coordinate_capability','material_frame_capability']:
        capability=manifest[key]
        checked_file({'path':capability['patch_ref'],'sha256':capability['patch_sha256']})
    return {'binary':binary,'manifest':manifest,'manifest_path':manifest_path,
            'manifest_sha256':digest(manifest_path),'selection_sha256':digest(SELECTION),
            'coordinate_probe_qualification_sha256':selection['qualification']['sha256']}


def native_environment(engine):
    env=workspace_environment();env.pop('LD_LIBRARY_PATH',None)
    directories=engine['manifest']['library_directories']
    if directories:env['LD_LIBRARY_PATH']=':'.join(str(workspace_path(p)) for p in directories)
    return env


def native_arguments(plan,engine):
    args=[engine['binary'],'--threads','1','--reduce=false','--binary']
    if enabled(plan):
        validate_setup(plan['setup'],plan['stock']['bounds_mm'])
        if 'coordinate_probe_qualification_sha256' not in engine:raise ValueError('Explicit setup needs the qualified coordinate backend')
        args.append('--material-origin='+','.join(f'{v:.5f}' for v in plan['setup']['material_origin_machine_mm']))
    return args


def initialize_controller(out,plan):
    if not enabled(plan):return {}
    path=out/REGISTER_FILE
    with path.open('x') as stream:stream.write(setup_registers(plan))
    check_setup_registers(path.read_text(),plan)
    return {'controller_initialization_file':REGISTER_FILE,'controller_initialization_sha256':digest(path),
            'stock_and_output_frame':'original_cad_material','nc_interpreter_frame':'machine',
            'material_origin_machine_mm':list(plan['setup']['material_origin_machine_mm']),
            'work_origin_cad_mm':list(plan['setup']['work_origin_cad_mm']),
            'target_based_registration':False}


def verify_initialization(out,plan,record):
    if not enabled(plan):
        if record:raise ValueError('Unexpected controller initialization on a legacy plan')
        return
    if record['controller_initialization_file']!=REGISTER_FILE:raise ValueError('Unexpected controller file')
    path=out/REGISTER_FILE
    if digest(path)!=record['controller_initialization_sha256']:raise ValueError('Controller initialization changed')
    check_setup_registers(path.read_text(),plan)


def verify_execution_evidence(out,plan,record):
    """Bind the actual native invocation, including a disclosed test-control origin."""
    from .coordinate_setup import finite_vector
    if not enabled(plan):return
    engine=engine_for_plan(plan)
    if (record['engine_manifest_sha256']!=engine['manifest_sha256']
            or record['engine_binary_sha256']!=engine['manifest']['binary_sha256']
            or record['controller_initialization_file']!=REGISTER_FILE
            or record['stock_and_output_frame']!='original_cad_material'
            or record['nc_interpreter_frame']!='machine'):
        raise ValueError('Wrong coordinate engine or execution frame')
    if digest(out/REGISTER_FILE)!=record['controller_initialization_sha256']:
        raise ValueError('Executed controller initialization changed')
    resources=out/'camotics-resources.json'
    if digest(resources)!=record['native_resources_sha256']:raise ValueError('Native invocation evidence changed')
    report=json.loads(resources.read_text())
    origin=finite_vector(record['material_origin_machine_mm'],'executed material origin')
    expected=native_arguments(plan,engine)
    expected[-1]='--material-origin='+','.join(f'{v:.5f}' for v in origin)
    expected=[str(v) for v in expected+[out/'simulation.camotics',out/'simulated.stl']]
    if not report['success'] or not report['started'] or report['command']!=expected:
        raise ValueError('Recorded native command disagrees with the applied material placement')


def check_execution_setup(out,plan,record):
    """The normal setup guard; explicit negative controls must fail this guard."""
    if not enabled(plan):return
    if (record['material_origin_machine_mm']!=plan['setup']['material_origin_machine_mm']
            or record['work_origin_cad_mm']!=plan['setup']['work_origin_cad_mm']):
        raise ValueError('Applied physical material placement disagrees with the original card/setup')
    verify_initialization(out,plan,record)


def verify_job_frame(out,plan,cam):
    if not enabled(plan):return
    path=out/'work-frame-binding.json'
    if digest(path)!=cam['work_frame_binding_sha256']:raise ValueError('Native job placement binding changed')
    frame=json.loads(path.read_text())
    if (frame['original_cad_sha256']!=plan['cad_ref']['sha256'] or frame['setup']!=plan['setup']
            or frame['cad_to_program_translation_mm']!=[-v for v in plan['setup']['work_origin_cad_mm']]
            or frame['original_cad_modified'] or frame['nc_text_rewritten']
            or not 0<=frame['native_model_inverse_translation_difference_mm3']<=1e-8):
        raise ValueError('Native job frame is not the declared translation of the original CAD')
