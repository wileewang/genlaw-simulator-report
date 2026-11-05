"""Bounded rough/finish NC and actual material-stage verification."""
import argparse
import json
from pathlib import Path
import sys

from .preparation import digest
from .rough_finish_preparation import prepare, checked_plan, guard_plan
from .rough_finish_nc import check_nc, check_prefix
from .programming import simulation_project, verify_binding
from .measurement import measure_bounded
from .run import ROOT, FREECAD, bounded, write
from .simulation_engine import selected_engine, workspace_environment, simulation_environment
from local_resource_limits import camotics_budget


def simulate(folder, plan, engine):
    configuration = simulation_project(guard_plan(plan, plan['operations'][1]))
    budget = camotics_budget(configuration)
    write(folder / 'simulation-budget.json', budget)
    if not budget['allowed']:
        raise ValueError('Original stock and declared resolution exceed the local resource budget')
    nc = folder / 'program.nc'
    nc_hash = digest(nc)
    project = folder / 'simulation.camotics'
    write(project, configuration)
    project_hash = digest(project)
    bounded(folder, 'camotics', [engine['binary'], '--threads', '1', '--reduce=false', '--binary',
                               project, folder / 'simulated.stl'], simulation_environment(), project)
    verify_binding(nc, nc_hash)
    verify_binding(project, project_hash)
    verify_binding(engine['binary'], engine['manifest']['binary_sha256'])
    result = {'schema': 'genlaw.rough-finish-material-run.v1', 'status': 'executed',
              'nc_sha256_before': nc_hash, 'nc_sha256_after': digest(nc), 'nc_text_modified': False,
              'mesh_sha256': digest(folder / 'simulated.stl'), 'project_sha256': project_hash,
              'engine_binary_sha256': digest(engine['binary']), 'engine_manifest_sha256': engine['manifest_sha256'],
              'engine_build_id': engine['manifest']['id'], 'resolution_mm': configuration['resolution'],
              'stock_source': 'Original explicit rectangular blank, no expected CAD as stock.',
              'tool_model': 'Flat cylindrical cutting segment; holder/shank excluded.',
              'fixture_collision_verified': False, 'material_physics_verified': False}
    write(folder / 'simulation-result.json', result)
    return result


def measure_stage(folder, plan, policy, stage):
    measure_bounded(folder, 'measure', folder, folder / 'simulated.stl', folder / 'metrics.json', display=True)
    bounded(folder, 'verify-stage', [sys.executable, ROOT / 'genlaw_pipeline/rough_finish_verify.py',
        '--reference', folder, '--actual', folder / 'simulated.stl', '--policy', policy, '--stage', stage,
        '--resolution', str(plan['simulation']['resolution_mm']), '--output', folder / 'stage-verification.json'])
    metrics = json.loads((folder / 'metrics.json').read_text())
    verification = json.loads((folder / 'stage-verification.json').read_text())
    return {'stage': stage, 'iou': metrics['iou'], 'iou_percent': metrics['iou_percent'],
            'all_software_checks_passed': verification['all_software_checks_passed'],
            'metrics_sha256': digest(folder / 'metrics.json'),
            'stage_verification_sha256': digest(folder / 'stage-verification.json')}


def run(input_path, out, cam_only=False):
    input_path, out = input_path.resolve(), out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    record = {'schema': 'genlaw.rough-finish-run.v1', 'status': 'started',
              'input_sha256': digest(input_path), 'input': str(input_path),
              'production_approved': False, 'whole_process_card_complete': False,
              'source_pdf_automatically_recognized': False, 'native_stages': []}
    sources = out / 'sources'
    sources.mkdir()
    for source in sorted((ROOT / 'genlaw_pipeline').glob('rough_finish_*.py')):
        (sources / source.name).write_bytes(source.read_bytes())
    record['source_code_sha256'] = {p.name: digest(p) for p in sorted((ROOT / 'genlaw_pipeline').glob('*.py'))}
    try:
        preparation = prepare(input_path)
        write(out / 'preparation-result.json', preparation)
        plan = checked_plan(input_path, out / 'preparation-result.json')
        write(out / 'execution-plan.json', plan)
        record['input_id'] = plan['input_id']
        request = {'input': str(input_path), 'preparation': str(out / 'preparation-result.json'), 'output': str(out)}
        write(out / 'request.json', request)
        env = workspace_environment()
        env.pop('GENLAW_CAM_REQUEST', None)
        env['GENLAW_ROUGH_FINISH_CAM_REQUEST'] = str(out / 'request.json')
        env['QT_QPA_PLATFORM'] = 'offscreen'
        bounded(out, 'freecad', [FREECAD, 'freecadcmd', ROOT / 'genlaw_pipeline/rough_finish_cam.py'], env)
        cam = json.loads((out / 'cam-result.json').read_text())
        if cam['status'] != 'generated_and_checked':
            raise ValueError('Native CAM failed: ' + str(cam.get('error')))
        record['native_stages'].append('freecad_rough_finish')
        verify_binding(out / 'program.nc', cam['nc_sha256'])
        verify_binding(out / 'rough/program.nc', cam['rough_nc_sha256'])
        check_nc((out / 'program.nc').read_text(), plan)
        check_prefix((out / 'rough/program.nc').read_text(), (out / 'program.nc').read_text(), plan)
        if cam_only:
            record['status'] = 'native_cam_checked_only'
            return record
        engine = selected_engine()
        policy = input_path.parent / plan['source_bindings']['stage_policy']['path']
        stages = []
        # Intermediate: identical native prefix from the same original blank.
        # Final: the ENTIRE native program in ONE engine run, so finish sees rough's material.
        for stage, folder in [('rough', out / 'rough'), ('finish', out)]:
            simulate(folder, plan, engine)
            stages.append(measure_stage(folder, plan, policy, stage))
            record['native_stages'].append('camotics_' + stage)
        checked_plan(input_path, out / 'preparation-result.json')
        record.update(status='software_stage_checks_passed' if all(s['all_software_checks_passed'] for s in stages)
                      else 'software_stage_checks_not_passed', stages=stages,
                      full_program_executed_continuously=True, original_stock_used_for_both_runs=True,
                      expected_rough_cad_used_as_stock=False, final_cad_registration_applied=False,
                      execution_plan_sha256=preparation['execution_plan_sha256'],
                      nc_sha256=cam['nc_sha256'], cam_result_sha256=digest(out / 'cam-result.json'))
    except Exception as error:
        record.update(status='failed', error=str(error))
    finally:
        write(out / 'run-result.json', record)
        print(json.dumps({k: record.get(k) for k in ('status', 'input_id', 'error', 'stages')}, ensure_ascii=False), flush=True)
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cam-only', action='store_true')
    args = parser.parse_args()
    result = run(args.input, args.output, args.cam_only)
    sys.exit(0 if result['status'] in ('software_stage_checks_passed', 'native_cam_checked_only') else 1)
