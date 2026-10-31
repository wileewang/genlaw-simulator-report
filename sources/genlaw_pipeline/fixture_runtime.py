"""Check explicit fixture resources before the independent material simulation."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'web/tools')]
from .cell_windows import checked_file, file_binding, candidate_engine
from .fixture_setup import enabled
from .fixture_clearance import inspect_path
from .preparation import digest, load_json
from .tool_length_setup import gauge_argument

SELECTION = ROOT/'engines/camotics/fixture-observer-engine.json'


def selected_observer():
    selection = load_json(SELECTION)
    if selection['schema'] != 'genlaw.fixture-observer-engine-selection.v1':
        raise ValueError('Unknown fixture observer selection')
    checked_file(selection['clearance_observer_source'])
    engine_path = checked_file(selection['engine']); binary, env, engine = candidate_engine(engine_path)
    qualified = load_json(checked_file(selection['qualification']))
    modes = load_json(checked_file(selection['mode_qualification']))
    observations = load_json(checked_file(selection['clearance_observer_qualification']))
    if (qualified['status'] != 'qualified_for_fixture_observer' or qualified['engine'] != selection['engine']
            or len(qualified['cases']) != 16 or qualified['independent_motion_checks'] != 153
            or len(qualified['native_rejections']) != 13 or len(qualified['default_surface_regressions']) != 3
            or modes['status'] != 'passed' or modes['engine'] != selection['engine'] or len(modes['cases']) != 6
            or observations['status'] != 'passed' or len(observations['cases']) != 4):
        raise ValueError('Bound native path and continuous clearance qualifications missing')
    capability = engine['native_toolpath_capability']
    checked_file({'path': capability['patch_ref'], 'sha256': capability['patch_sha256']})
    return binary, env, selection, engine_path


def arguments(binary, plan, out):
    if not enabled(plan) or plan.get('fixture_inspection', {}).get('schema') != 'genlaw.fixture-inspection-plan.v1':
        raise ValueError('No prepared, explicit fixture inspection')
    return [str(binary), '--threads', '1', '--reduce=false', '--inspect-toolpath',
            '--material-origin='+','.join(str(v) for v in plan['setup']['material_origin_machine_mm']),
            gauge_argument(plan['setup']['tool_length_compensation']['actual_lengths_mm']),
            str(out/'simulation.camotics'), str(out/'fixture-native-path.json')]


def execute(out, plan):
    """Coordinator only: one native path job, then one bounded observer job."""
    from .run import bounded, write
    binary, env, selection, engine_path = selected_observer()
    write(out/'fixture-path-binding.json', {
        'schema': 'genlaw.fixture-native-path-binding.v1', 'engine': file_binding(engine_path),
        'selection': file_binding(SELECTION), 'binary': file_binding(binary),
        'plan': file_binding(out/'execution-plan.json'), 'nc': file_binding(out/'program.nc'),
        'project': file_binding(out/'simulation.camotics'), 'controller': file_binding(out/'setup-registers.nc'),
        'source': file_binding(Path(__file__)), 'material_removal_performed_by_this_stage': False})
    bounded(out, 'fixture-native-path', arguments(binary, plan, out), env, out/'simulation.camotics')
    bounded(out, 'verify-fixture-clearance', [sys.executable, '-m', 'genlaw_pipeline.fixture_runtime', '--run', str(out)])
    return load_json(out/'fixture-verification.json')


def verify(out):
    from mesh_metrics import require_bounded_worker
    from .programming import checked_plan, check_nc, verify_binding, simulation_project
    from .coordinate_setup import check_setup_registers
    from .coordinate_backend import verify_job_frame
    from .run import write
    require_bounded_worker(); out = out.resolve(); out.relative_to(ROOT)
    request = load_json(out/'request.json'); input_path = Path(request['input'])
    plan = checked_plan(input_path, out/'preparation-result.json')
    if plan != load_json(out/'execution-plan.json'):
        raise ValueError('Fixture path execution plan changed')
    binding = load_json(out/'fixture-path-binding.json')
    for key in ['engine', 'selection', 'binary', 'plan', 'nc', 'project', 'controller', 'source']:
        checked_file(binding[key])
    binary, _, selection, engine_path = selected_observer()
    if binding['engine'] != file_binding(engine_path) or binding['binary'] != file_binding(binary):
        raise ValueError('Wrong fixture path engine')
    cam = load_json(out/'cam-result.json')
    if cam['status'] != 'generated_and_checked':
        raise ValueError('Fixture inspection requires checked native FreeCAD NC')
    verify_binding(out/'program.nc', cam['nc_sha256']); verify_job_frame(out, plan, cam)
    guard = check_nc((out/'program.nc').read_text(), plan)
    check_setup_registers((out/'setup-registers.nc').read_text(), plan)
    if load_json(out/'simulation.camotics') != simulation_project(plan):
        raise ValueError('Actual native project differs from declared machining resources')
    resources = load_json(out/'fixture-native-path-resources.json')
    if (not resources['success'] or not resources['started'] or resources['command'] != arguments(binary, plan, out)
            or resources['wall_limit_seconds'] > 60 or resources['observed_group_rss_limit_bytes'] > 2*1024**3
            or len(resources['cpu_affinity']) != 1):
        raise ValueError('Actual native path invocation is incomplete or uses unexpected resources')
    declared = plan['fixture_inspection']; model_file = input_path.parent/declared['model_ref']['path']
    verify_binding(model_file, declared['model_ref']['sha256']); model = load_json(model_file)
    path = load_json(out/'fixture-native-path.json'); tools = []
    for move in path['moves']:
        n = move['tool_number']
        if n > 0 and (not tools or tools[-1] != n): tools.append(n)
    if tools != [t['tool_number'] for t in guard['tool_transitions']]:
        raise ValueError('Native path omits a declared tool-change start pose')
    report = inspect_path(path, model, plan['setup']['material_origin_machine_mm'])
    report.update(model_ref=file_binding(model_file), native_path=file_binding(out/'fixture-native-path.json'),
                  engine=file_binding(engine_path), observer_selection=file_binding(SELECTION),
                  original_cad_sha256=plan['cad_ref']['sha256'], nc_sha256=cam['nc_sha256'],
                  nc_text_modified=False, project_sha256=digest(out/'simulation.camotics'),
                  controller_initialization_sha256=digest(out/'setup-registers.nc'),
                  native_resources=file_binding(out/'fixture-native-path-resources.json'),
                  checked_before_material_removal=not (out/'simulated.stl').exists(),
                  declared_resource_scope_integrated=True, actual_tool_change_poses_present=True,
                  source_code=[file_binding(Path(__file__)), file_binding(ROOT/'genlaw_pipeline/fixture_clearance.py')],
                  whole_process_card_complete=False)
    checked_plan(input_path, out/'preparation-result.json')
    write(out/'fixture-verification.json', report)
    print(json.dumps({'status': report['status'], 'pairs': report['tested_pairs'], 'findings': len(report['findings'])}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--run', type=Path, required=True)
    verify(parser.parse_args().run)
