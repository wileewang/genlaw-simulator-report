"""Run frozen two-setup inputs through FreeCAD, cumulative native removal and CAD checks."""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.cell_windows import checked_file, file_binding
from genlaw_pipeline.indexed_facing import prepare_chain, IDENTITY
from genlaw_pipeline.native_stream import checked_build
from genlaw_pipeline.programming import check_nc
from genlaw_pipeline.run import write, FREECAD, workspace_environment

LOAD = lambda p: json.loads(p.read_text())


def run(inputs, build_path, out):
    inputs = inputs.resolve(); build_path = build_path.resolve(); out = out.resolve()
    out.relative_to(ROOT); out.mkdir(parents=True, exist_ok=False)
    archive = LOAD(inputs/'manifest.json')
    if archive['schema'] != 'genlaw.indexed-facing-input-archive.v1' or not archive['all_targets_frozen_before_NC']:
        raise ValueError('Require the frozen paper/CAD input archive')
    for binding in archive['files']: checked_file(binding)
    build = LOAD(build_path)
    if build['schema'] != 'genlaw.indexed-stream-build.v1':
        raise ValueError('Require the separately qualified indexed native engine')
    binary = checked_file(build['binary'])
    for binding in build['source_code'] + build['reused_objects']: checked_file(binding)
    _, engine_env = checked_build(checked_file(build['reference_build']))
    originals = [*sorted((ROOT/'genlaw_pipeline').glob('*.py')),
        ROOT/'tools/indexed_facing_cam_worker.py', Path(__file__), ROOT/'web/tools/mesh_metrics.py']
    original_bindings = [file_binding(p) for p in originals]
    frozen = []
    for path in originals:
        dest = out/'sources'/path.relative_to(ROOT); dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest); frozen.append(file_binding(dest))
    write(out/'request.json', {'schema': 'genlaw.indexed-facing-suite-request.v1',
        'input_manifest': file_binding(inputs/'manifest.json'), 'build': file_binding(build_path),
        'execution_sources': original_bindings, 'preserved_sources': frozen,
        'limits_changed': False, 'public_site_updated': False})
    rows = []; baseline = None
    for spec in LOAD(inputs/'specifications.json')['cases']:
        key = spec['id']; case = out/key; case.mkdir()
        chain = prepare_chain(inputs/key/'input.json'); write(case/'preparation-result.json', chain)
        request = {'input': str(inputs/key/'input.json'), 'output': str(case)}
        write(case/'request.json', request)
        env = workspace_environment(); env['QT_QPA_PLATFORM'] = 'offscreen'
        env['GENLAW_INDEXED_CAM_REQUEST'] = str(case/'request.json')
        cam_command = [str(FREECAD), 'freecadcmd', str(ROOT/'tools/indexed_facing_cam_worker.py')]
        admitted_bounded(case, 'freecad', cam_command, env)
        cam = LOAD(case/'cam-result.json')
        if cam['status'] != 'two_native_CAM_jobs_generated_and_checked':
            raise ValueError('Native CAM generation incomplete')
        bound = [file_binding(build_path), build['binary'], file_binding(case/'cam-result.json'),
                 file_binding(case/'request.json')]
        for i, stage in enumerate(chain['setups']):
            dest = case/stage['id']; result = LOAD(dest/'cam-result.json')
            nc = dest/'program.nc'; binding = file_binding(nc)
            if result['nc_sha256'] != binding['sha256']:
                raise ValueError('FreeCAD NC changed before simulation')
            check_nc(nc.read_text(), chain['stage_plans'][i])
            write(dest/'simulation.camotics', chain['simulation_projects'][i])
            write(dest/'material-role.json', {
                'CAM_planned_incoming_stock': chain['stage_plans'][i]['stock']['bounds_mm'],
                'native_original_stock_envelope_in_this_setup': chain['simulation_projects'][i]['workpiece']['bounds'],
                'simulation_uses_original_stock_minus_all_previous_native_sweeps': True,
                'CAM_nominal_target_is_not_simulated_material': True})
            bound += [binding, file_binding(dest/'simulation.camotics'), file_binding(dest/'cam-result.json')]

        def native_command(kind):
            cmd = [str(binary), '--cell-window', ','.join(map(str, chain['window'])), '--facets-per-file', '500000']
            if kind in ('final', 'wrong-pose'):
                pose = chain['setups'][1]['to_material'] if kind == 'final' else IDENTITY
                cmd += ['--second-project', str(case/'S2/simulation.camotics'),
                        '--second-pose', ','.join(map(str, pose))]
            return cmd + [str(case/'S1/simulation.camotics'), str(case/kind/'native')]

        def execute_measure(kind, reuse=None):
            dest = case/kind; dest.mkdir()
            source = case/reuse if reuse else dest
            if reuse is None:
                cmd = native_command(kind)
                admitted_bounded(dest, 'native', cmd, engine_env)
            else:
                cmd = LOAD(source/'measurement-request.json')['native_command']
            req = {'schema': 'genlaw.indexed-facing-measurement-request.v1',
                'case_kind': kind, 'preparation': file_binding(case/'preparation-result.json'),
                'CAD_chain': file_binding(case/'native-cad-chain.json'),
                'cam_resources': file_binding(case/'freecad-resources.json'), 'cam_command': cam_command,
                'native_manifest': file_binding(source/'native/native.json'),
                'native_resources': file_binding(source/'native-resources.json'), 'native_command': cmd,
                'bound_artifacts': bound, 'source_code': frozen,
                'test_only_wrong_pose': kind == 'wrong-pose',
                'reuse_completed_first_setup_as_omission_control': reuse is not None}
            write(dest/'measurement-request.json', req)
            admitted_bounded(dest, 'measure', [sys.executable,
                ROOT/'genlaw_pipeline/indexed_facing_measurement.py', '--request', dest/'measurement-request.json',
                '--output', dest/'metrics.json'])
            metrics = LOAD(dest/'metrics.json')
            if not metrics['regression_expectation_met']: raise ValueError('Geometry did not meet frozen criteria')
            return {'metrics': file_binding(dest/'metrics.json'), 'iou_percent': metrics['iou_percent'],
                    'geometry_pass': metrics['geometry_pass'], 'native_reused': reuse is not None}

        first = execute_measure('first')
        if not first['geometry_pass']: raise ValueError('First setup did not pass; second material execution is blocked')
        final = execute_measure('final')
        row = {'id': key, 'first': first, 'final': final,
               'paper_card': chain['paper_card'], 'original_final_CAD': chain['final_cad'],
               'FreeCAD_jobs': [file_binding(case/s['id']/'cam-job.FCStd') for s in chain['setups']]}
        if key == 'baseline':
            row['negative_controls'] = {'wrong-pose': execute_measure('wrong-pose'),
                                        'omitted-second': execute_measure('omitted-second', reuse='first')}
            baseline = row
        for binding in original_bindings + archive['files']: checked_file(binding)
        write(case/'result.json', row); rows.append(row)
        print(json.dumps({'case_completed': key, 'first_iou_percent': first['iou_percent'],
                          'final_iou_percent': final['iou_percent']}), flush=True)
    if len(rows) != 4 or baseline is None: raise ValueError('Incomplete frozen indexed cohort')
    write(out/'summary.json', {'schema': 'genlaw.indexed-facing-suite.v1',
        'status': 'four_synthetic_two_setup_chains_measured', 'request': file_binding(out/'request.json'),
        'normal_cases': rows, 'negative_controls': baseline['negative_controls'],
        'NC_generated_by_FreeCAD': True, 'actual_CAD_reference_verified': True,
        'two_static_setups_share_original_material': True, 'continuous_five_axis_supported': False,
        'machine_collision_verified': False, 'whole_process_card_complete': False,
        'production_approved': False, 'public_site_updated': False})
    print(json.dumps({'suite_completed': True, 'normal_cases': 4, 'geometry_negative_controls': 2}), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('inputs', 'build', 'output'): p.add_argument('--'+key, type=Path, required=True)
    args = p.parse_args(); run(args.inputs, args.build, args.output)
