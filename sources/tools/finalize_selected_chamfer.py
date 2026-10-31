"""Bind the completed cohort, native prefix replay and retained development failures."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from genlaw_pipeline.cell_windows import checked_file, file_binding
from genlaw_pipeline.planar_resume import process_identity
from genlaw_pipeline.programming import nc_words
from genlaw_pipeline.sequence_nc import native_header, segments, SHUTDOWN
from genlaw_pipeline.selected_chamfer_measurement_v2 import checked_run
from genlaw_pipeline.run import write
from step2gcode_demo.validation.local_resource_limits import require_bounded_worker

CP = ROOT/'docs/checkpoints/2026-10-31/selected-chamfer-01'
RUNS = ROOT/'validation/runs/S4-SELECTED-CHAMFER-20261031-r2'
OLD = ROOT/'validation/runs/S4-SELECTED-CHAMFER-20261031-r1'
INPUTS = ROOT/'validation/feature-inputs/S4-SELECTED-CHAMFER-20261031-r2'
READ = lambda p: json.loads(Path(p).read_text())


def retain_or_write(path, value):
    if path.exists():
        if READ(path) != value: raise ValueError('Existing finalized evidence changed: '+str(path))
    else: write(path, value)


def main():
    require_bounded_worker(); phase = CP/'cohort-phase-03'; owner = READ(phase/'owner.json')
    if process_identity(owner['process']['pid']) == owner['process']:
        raise ValueError('Native phase owner must actually have ended')
    terminal = READ(phase/'result.json'); suite = READ(checked_file(terminal['summary']))
    if (terminal['status'] != 'completed' or suite['status'] != 'four_normal_three_controls_qualified'
            or not suite['feature_cohort_qualified'] or len(suite['normal_cases']) != 4
            or len(suite['geometry_controls']) != 3): raise ValueError('Incomplete cohort')
    reports = {}; plans = {}
    for row in suite['normal_cases']:
        key = row['id']; case = RUNS/key; plan, _ = checked_run(case); plans[key] = plan
        report = READ(checked_file(row['metrics'])); reports[key] = report
        for binding in report['source_code']+[report['mesh'], report['native_material'], report['original_target_CAD']]:
            checked_file(binding)
        if (not report['geometry_pass'] or not report['numerical_stability_pass']
                or row['iou_percent'] != report['iou_percent'] or not report['regression_expectation_met']
                or report['source_NC_modified'] or report['registration_applied'] or report['material_discarded']):
            raise ValueError('Changed normal measurement or scope')
    for row in suite['geometry_controls']:
        report = READ(checked_file(row['metrics']))
        for binding in [report['mesh'], report['native_material'], report['original_target_CAD'], *report['source_code']]:
            checked_file(binding)
        if (report['geometry_pass'] or not report['regression_expectation_met'] or not report['numerical_stability_pass']
                or not report['target_NC_guard_rejection'] or row['iou_percent'] != report['iou_percent']):
            raise ValueError('Changed negative control')
    checks = READ(checked_file(suite['guard_checks']))
    if checks['status'] != 'passed' or len(checks['input_rejections']) != 21 or len(checks['native_NC_rejections']) != 8:
        raise ValueError('Incomplete input/NC rejection matrix')

    # A previously executed one-edge program is exactly the first-edge prefix
    # of the two-edge program plus the standard non-cutting shutdown.
    short, full = RUNS/'one-edge', RUNS/'opposite-long'
    a = (short/'program.nc').read_text(); b = (full/'program.nc').read_text()
    ia, wa = segments(a, native_header(plans['one-edge'])); ib, wb = segments(b, native_header(plans['opposite-long']))
    tail = nc_words(SHUTDOWN); prefix_count = len(wa)-len(tail)
    if wa != wb[:prefix_count]+tail or ia[1]['blocks'] != ib[1]['blocks'][:len(ia[1]['blocks'])]:
        raise ValueError('Executed intermediate material is not the original NC prefix')
    for key in ['stock', 'tool', 'parameters', 'simulation_project', 'grid', 'window']:
        if plans['one-edge'][key] != plans['opposite-long'][key]:
            raise ValueError('Replay prefix changed stock, cutter, coordinate frame or sampling')
    first, last = READ(short/'cam-result.json'), READ(full/'cam-result.json')
    trace = first['nc_check']['motion_trace']; full_trace = last['nc_check']['motion_trace']
    if trace != full_trace[:len(trace)]: raise ValueError('Motion replay differs at the first-edge boundary')
    stock_volume = 1.
    for lo, hi in zip(plans['one-edge']['stock']['bounds_mm']['min'], plans['one-edge']['stock']['bounds_mm']['max']):
        stock_volume *= hi-lo
    volumes = [stock_volume, reports['one-edge']['mesh_inspection']['volume_mm3'], reports['opposite-long']['mesh_inspection']['volume_mm3']]
    if not volumes[0] > volumes[1] > volumes[2] > 0: raise ValueError('Actual material did not decrease across edge prefixes')
    replay = {'schema': 'genlaw.selected-edge-prefix-replay.v1', 'status': 'actual_prefix_material_ready',
        'representative_case': 'opposite-long', 'original_full_NC': file_binding(full/'program.nc'),
        'executed_prefix_NC': file_binding(short/'program.nc'), 'matching_executable_prefix_blocks': prefix_count,
        'prefix_followed_only_by_standard_shutdown': True, 'original_NC_rewritten': False,
        'same_stock_tool_coordinates_and_lattice': True, 'motion_trace': file_binding(full/'cam-result.json'),
        'frames': [{'kind': 'original_stock', 'label': '完整毛坯', 'bounds': plans['one-edge']['stock']['bounds_mm'],
                    'feed_time_seconds': 0., 'volume_mm3': volumes[0]},
                   {'kind': 'executed_exact_NC_prefix', 'label': '第一条指定边加工后',
                    'surface': reports['one-edge']['mesh'], 'native_result': reports['one-edge']['native_material'],
                    'feed_time_seconds': first['nc_check']['feed_time_seconds'], 'volume_mm3': volumes[1]},
                   {'kind': 'full_NC_material', 'label': '两条指定边加工后',
                    'surface': reports['opposite-long']['mesh'], 'native_result': reports['opposite-long']['native_material'],
                    'feed_time_seconds': last['nc_check']['feed_time_seconds'], 'volume_mm3': volumes[2]}],
        'intermediate_material_reconstructed_from_target': False, 'extra_simulations_for_replay': 0,
        'browser_animation_verified': False, 'production_approved': False}
    retain_or_write(CP/'replay-prefix.json', replay)
    unchanged = []
    for key in ['one-edge', 'opposite-long']:
        old = READ(OLD/key/'metrics.json'); checked_file(old['mesh'])
        if old['mesh']['sha256'] != reports[key]['mesh']['sha256']:
            raise ValueError('Native correction changed a previously correct complete surface')
        unchanged.append({'case': key, 'old_mesh': old['mesh'], 'new_mesh': reports[key]['mesh'],
            'complete_native_STL_byte_identical': True, 'old_iou_percent': old['iou_percent'],
            'new_iou_percent': reports[key]['iou_percent']})
    retain_or_write(CP/'native-correction-regression.json', {'status': 'passed', 'cases': unchanged,
        'newly_corrected_case': 'opposite-short', 'rejected_prior_NC': file_binding(OLD/'opposite-short/program.nc'),
        'corrected_NC': file_binding(RUNS/'opposite-short/program.nc'), 'default_backend_changed': False})
    resources = []
    roots = [CP, RUNS, OLD, INPUTS, ROOT/'validation/feature-inputs/S4-SELECTED-CHAMFER-20261031-r1']
    for root in roots:
        for path in sorted(root.rglob('*-resources.json')):
            if path.name.startswith('finalize-'): continue  # Audit workers are recorded separately.
            resource = READ(path)
            if (resource['wall_limit_seconds'] > 60 or resource['per_process_address_space_limit_bytes'] != 2*1024**3
                    or resource['file_size_limit_bytes'] != 64*1024**2 or len(resource['cpu_affinity']) != 1
                    or resource['thread_environment_limit'] != 1): raise ValueError('Resource limits changed')
            resources.append({'record': file_binding(path), 'subprocess_success': resource['success'],
                'seconds': resource['seconds'], 'peak_group_RSS_bytes': resource['peak_observed_group_rss_bytes']})
    sources = set()
    for case in [OLD/'one-edge', OLD/'opposite-long', OLD/'opposite-short', *[RUNS/r['id'] for r in suite['normal_cases']]]:
        for binding in READ(case/'request.json')['execution_sources']: sources.add(checked_file(binding))
    sources.update([Path(__file__), ROOT/'tools/build_selected_chamfer_inputs.py',
        ROOT/'tools/build_selected_chamfer_cad.py', ROOT/'tools/build_selected_chamfer_inputs_v2.py',
        ROOT/'tools/run_selected_chamfer_cohort.py', ROOT/'tools/run_selected_chamfer_cohort_v2.py',
        ROOT/'tools/check_selected_chamfer.py', ROOT/'tools/check_selected_chamfer_v2.py'])
    sources.update(p for p in (ROOT/'engines/freecad/deburr-selected-edge-side-r1').iterdir() if p.is_file())
    frozen = []
    for source in sorted(sources):
        dest = CP/'qualified-source'/source.relative_to(ROOT); dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            if file_binding(source)['sha256'] != file_binding(dest)['sha256']:
                raise ValueError('Existing preserved source changed')
        else: shutil.copyfile(source, dest)
        frozen.append({'execution_source': file_binding(source), 'preserved_source': file_binding(dest)})
    write(CP/'completion.json', {'schema': 'genlaw.selected-chamfer-completion.v1',
        'status': 'passed_for_declared_synthetic_cohort', 'at_utc': datetime.now(timezone.utc).isoformat(),
        'suite': file_binding(RUNS/'summary.json'), 'input_archive': file_binding(INPUTS/'manifest.json'),
        'input_review': file_binding(CP/'input-review-02.json'), 'guard_checks': suite['guard_checks'],
        'replay': file_binding(CP/'replay-prefix.json'), 'native_regression': file_binding(CP/'native-correction-regression.json'),
        'retained_wrong_side_diagnosis': file_binding(CP/'native-side-diagnosis-01.json'),
        'preserved_source_code': frozen, 'resources': resources,
        'bounded_worker_records': len(resources), 'subprocess_failures': sum(not r['subprocess_success'] for r in resources),
        'CAM_semantic_failure_despite_zero_subprocess_exit': [file_binding(OLD/'opposite-short/cam-failure.json')],
        'maximum_worker_seconds': max(r['seconds'] for r in resources),
        'maximum_worker_RSS_bytes': max(r['peak_group_RSS_bytes'] for r in resources),
        'development_dimension': 'part_features', 'normal_cases': 4, 'geometry_controls': 3,
        'input_rejections': 21, 'NC_rejections': 8, 'public_site_updated': False,
        'web_review_and_publication_pending': True, 'actual_machine_or_material_physics_added': False,
        'real_C3_part_size_tested': False, 'whole_real_card_complete': False, 'production_approved': False, 'goal_complete': False})
    print(json.dumps({'backend_qualified': True, 'actual_replay_frames': 3, 'unchanged_prior_surfaces': 2,
        'worker_records': len(resources), 'maximum_seconds': max(r['seconds'] for r in resources)}), flush=True)


if __name__ == '__main__': main()
