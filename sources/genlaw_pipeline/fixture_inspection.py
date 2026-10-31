"""Bind the optional fixture observer to complete native motion and prior material.

This entry is a qualification observer of an existing complete machining run.
It does not yet add fixture requirements to the normal process-card interface.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'web/tools')]
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.cell_windows import file_binding, candidate_engine, checked_file
from genlaw_pipeline.fixture_clearance import inspect_path, validate_model
from genlaw_pipeline.tool_length_setup import gauge_argument, actual_lengths
from genlaw_pipeline.run import write


def read(path):
    return json.loads(path.read_text())


def checked_context(inputs, path_run):
    from genlaw_pipeline.programming import checked_plan, check_nc, verify_binding
    from genlaw_pipeline.coordinate_backend import verify_execution_evidence, check_execution_setup, verify_job_frame
    manifest = read(inputs/'manifest.json')
    if manifest['schema'] != 'genlaw.fixture-observer-inputs.v1':
        raise ValueError('Unknown fixture input archive')
    for name, sha in manifest['files'].items():
        verify_binding(inputs/name, sha)
    checked_file(manifest['observer_source']); checked_file(manifest['builder'])
    source = ROOT/manifest['source_run']
    for binding in manifest['source_files'].values():
        checked_file(binding)
    request = read(source/'request.json')
    plan = checked_plan(Path(request['input']), source/'preparation-result.json')
    if plan != read(source/'execution-plan.json'):
        raise ValueError('Original execution plan changed')
    cam = read(source/'cam-result.json'); simulation = read(source/'simulation-result.json')
    if cam['status'] != 'generated_and_checked' or simulation['status'] != 'executed' or simulation['nc_text_modified']:
        raise ValueError('Need the original complete native CAM/material execution')
    check_execution_setup(source, plan, simulation); verify_execution_evidence(source, plan, simulation)
    verify_job_frame(source, plan, cam)
    verify_binding(source/'program.nc', cam['nc_sha256']); verify_binding(source/'simulated.stl', simulation['mesh_sha256'])
    guards = check_nc((source/'program.nc').read_text(), plan)
    for name in ('program.nc', 'setup-registers.nc', 'simulation.camotics'):
        if (path_run/name).read_bytes() != (source/name).read_bytes():
            raise ValueError('Observer changes original NC or native project: '+name)
    binding = read(path_run/'source-binding.json')
    if binding['original_run'] != manifest['source_run']:
        raise ValueError('Native path was exported from a different machining run')
    engine_path = checked_file(binding['candidate_engine'])
    binary, _, engine = candidate_engine(engine_path)
    if checked_file(binding['candidate_binary']) != binary:
        raise ValueError('Wrong native path binary')
    capability = engine['native_toolpath_capability']
    checked_file({'path': capability['patch_ref'], 'sha256': capability['patch_sha256']})
    if (capability['schema'] != 'genlaw.native-toolpath-export.v1' or not capability['opt_in']
            or capability['output_schema'] != 'genlaw.native-toolpath.v1' or capability['nc_text_modified']
            or capability['native_max_arc_error_mm'] != .01 or capability['double_output_digits'] != 17):
        raise ValueError('Unexpected native path export contract')
    resource = read(path_run/'native-path-resources.json')
    expected = [str(binary), '--threads', '1', '--reduce=false', '--inspect-toolpath',
                '--material-origin='+','.join(str(v) for v in plan['setup']['material_origin_machine_mm']),
                gauge_argument(plan['setup']['tool_length_compensation']['actual_lengths_mm']),
                str(path_run/'simulation.camotics'), str(path_run/'native-path.json')]
    if (not resource['success'] or not resource['started'] or resource['command'] != expected
            or resource['wall_limit_seconds'] > 60 or resource['observed_group_rss_limit_bytes'] > 2*1024**3
            or len(resource['cpu_affinity']) != 1):
        raise ValueError('Incomplete or unbounded native motion export')
    path = read(path_run/'native-path.json')
    # Source guard accounts for all programmed tool changes. Each installed tool
    # must have a subsequent native motion: its actual starting pose is checked.
    transitions = []
    for move in path['moves']:
        n = move['tool_number']
        if n > 0 and (not transitions or transitions[-1] != n):
            transitions.append(n)
    if transitions != [t['tool_number'] for t in guards['tool_transitions']]:
        raise ValueError('Native path omits a declared tool-change pose')
    return manifest, source, plan, path, simulation, engine_path


def verify(inputs, path_run, output):
    from mesh_metrics import require_bounded_worker
    require_bounded_worker()
    inputs, path_run, output = [p.resolve() for p in (inputs, path_run, output)]
    for p in (inputs, path_run, output):
        p.relative_to(ROOT)
    output.mkdir(parents=True, exist_ok=False)
    manifest, source, plan, path, actual, engine = checked_context(inputs, path_run)
    declared_lengths = actual_lengths(plan['setup']['tool_length_compensation']['actual_lengths_mm'])
    original_tools = {t['number']: t for t in plan['tools']}
    rows = []
    for key in manifest['cases']:
        model_file = inputs/key/'fixture-model.json'; model = read(model_file)
        tools = validate_model(model)
        if {n: t['gauge_to_tip_mm'] for n, t in tools.items()} != declared_lengths:
            raise ValueError('Fixture model replaces actual mounted tool lengths')
        for n, tool in tools.items():
            cutting = tool['segments'][0]; original = original_tools[n]
            if (cutting['z_min_mm'] != 0 or cutting['z_max_mm'] != original['cutting_length_mm']
                    or cutting['radius_mm']*2 != original['diameter_mm']):
                raise ValueError('Fixture observer changes native cutting geometry')
        report = inspect_path(path, model, plan['setup']['material_origin_machine_mm'])
        expected = read(inputs/key/'expectation.json')
        success = report['status'] == expected['status']
        if expected['fixture'] is not None:
            success = success and any(f['fixture_id'] == expected['fixture'] and f['role'] == expected['role']
                                      and f['status'] == 'collision' and (not expected.get('motion') or f['first_motion_type'] == expected['motion'])
                                      for f in report['findings'])
        report.update(id=key, expectation_matched=success, fixture_model=file_binding(model_file),
                      native_path=file_binding(path_run/'native-path.json'), engine=file_binding(engine),
                      original_nc=file_binding(source/'program.nc'), original_material_sha256=actual['mesh_sha256'],
                      original_iou_percent=read(source/'metrics.json')['iou_percent'],
                      new_material_simulation=False, whole_process_card_complete=False,
                      normal_preparation_interface_integrated=False,
                      source=[file_binding(Path(__file__)), file_binding(ROOT/'genlaw_pipeline/fixture_clearance.py')])
        write(output/(key+'.json'), report)
        rows.append({'id': key, 'status': report['status'], 'expectation_matched': success,
                     'tested_pairs': report['tested_pairs'], 'findings': len(report['findings']),
                     'report': file_binding(output/(key+'.json'))})
    result = {'schema': 'genlaw.fixture-observer-suite.v1',
              'status': 'passed' if all(r['expectation_matched'] for r in rows) else 'failed',
              'inputs': file_binding(inputs/'manifest.json'), 'path': file_binding(path_run/'native-path.json'),
              'cases': rows, 'native_moves': len(path['moves']),
              'original_nc_cad_and_material_unchanged': True,
              'new_material_simulations': 0, 'new_part_geometries': 0,
              'fixture_configurations': len(rows), 'whole_process_card_complete': False,
              'normal_preparation_interface_integrated': False, 'production_approved': False}
    write(output/'summary.json', result)
    print(json.dumps({'status': result['status'], 'cases': [{k:v for k,v in row.items() if k != 'report'} for row in rows]}))
    if result['status'] != 'passed':
        raise ValueError('Fixture observer predictions disagree with actual native path; preserve reports for diagnosis')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--path-run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    verify(args.inputs, args.path_run, args.output)
