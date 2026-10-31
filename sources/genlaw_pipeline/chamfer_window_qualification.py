"""Require complete native and streamed chamfer evidence before larger execution."""
import json
from pathlib import Path

from .cell_windows import file_binding, checked_file
from .chamfer_shards import DEPENDENCIES

ROOT = Path(__file__).resolve().parents[1]
NORMALS = ['baseline', 'wider', 'translated', 'wider-tip']
CONTROLS = ['overcut-depth', 'too-shallow', 'wrong-position', 'extra-inner-edge']
SOURCES = list(dict.fromkeys(DEPENDENCIES + [
    'genlaw_pipeline/chamfer_shards.py', 'genlaw_pipeline/chamfer_window_qualification.py',
    'tools/probe_chamfer_windows.py', 'tools/run_chamfer_shards.py',
    'tools/qualify_chamfer_windows.py', 'tools/probe_cell_windows.py',
    'genlaw_pipeline/admission_wait.py', 'tests/test_chamfer_windows.py',
]))
LOAD = lambda p: json.loads(p.read_text())


def source_bindings():
    return [file_binding(ROOT / p) for p in SOURCES]


def numeric_count(value):
    if type(value) in (int, float):
        return 1
    if isinstance(value, dict):
        return sum(numeric_count(v) for v in value.values())
    if isinstance(value, list):
        return sum(numeric_count(v) for v in value)
    return 0


def checked_qualification(path, engine):
    from .chamfer_shards import checked as checked_measurement
    from .window_controls import checked_control
    data = LOAD(path)
    if (data.get('schema') != 'genlaw.chamfer-window-regression.v1' or data.get('status') != 'passed'
            or data['engine_manifest'] != file_binding(engine) or data['sources'] != source_bindings()
            or [row['id'] for row in data['normal_cases']] != NORMALS
            or [row['id'] for row in data['controls']] != CONTROLS):
        raise ValueError('Current complete chamfer-window qualification is required')
    base = LOAD(checked_file(data['base_engine_regression']))
    if (base.get('schema') != 'genlaw.cell-window-regression.v1' or base['status'] != 'passed'
            or base['engine_manifest'] != file_binding(engine)
            or not all(r['regression_passed'] for r in base['normal_cases']+base['controls']+base['default_cases'])):
        raise ValueError('The existing complete-engine regression is required')
    for row in data['normal_cases']+data['controls']:
        default = LOAD(checked_file(row['default'])); native = LOAD(checked_file(row['native']))
        comparison = LOAD(checked_file(row['streamed_comparison']))
        if (not default['regression_passed'] or not default['count_and_all_triangle_payload_bytes_identical']
                or default['engine_manifest'] != file_binding(engine) or not native['regression_passed']
                or native['case'] != row['id'] or comparison['case'] != row['id'] or comparison['status'] != 'passed'
                or not comparison['same_actual_surface_compared'] or not comparison['criteria_and_verdicts_unchanged']
                or comparison['absolute_iou_difference'] > 1e-10
                or comparison['legacy_absolute_iou_difference'] > 1e-5
                or comparison['maximum_absolute_observation_difference'] > 1e-10):
            raise ValueError('Native or streamed chamfer regression is incomplete')
        checked_file(default['candidate'])
        for binding in default['original'].values():
            checked_file(binding)
        for binding in native['sources']:
            checked_file(binding)
        inv = LOAD(checked_file(native['invariance']))
        if not inv['equal_oriented_float32_facets']:
            raise ValueError('Partitions changed the actual material surface')
        checked_file(inv['canonical']); checked_file(inv['surface'])
        geometry = LOAD(checked_file(native['geometry']))
        if not geometry['same_frozen_geometric_verdicts']:
            raise ValueError('An original chamfer verdict changed')
        if comparison['all_numeric_observations_compared'] != numeric_count(geometry['observations']['features']):
            raise ValueError('Not all original numeric observations were compared')
        for key in ['old_metrics', 'new_metrics', 'old_verification', 'new_verification', 'legacy_metrics']:
            checked_file(comparison[key])
        report = LOAD(checked_file(comparison['new_verification']))
        plan, _, reference = checked_measurement(checked_file(report['plan']))
        for binding in report['ray_parts']+report['section_parts']:
            checked_file(binding)
        metrics = LOAD(checked_file(comparison['new_metrics']))
        if metrics['plan'] != report['plan'] or metrics['per_window_iou_averaged'] is not False:
            raise ValueError('IoU and feature observations refer to different material')
        for binding in metrics['parts']:
            checked_file(binding)
        control = row['id'] in CONTROLS
        if (report.get('test_only_guard_bypass', False) != control
                or native['test_only_guard_bypass'] != control
                or report['all_software_geometry_checks_passed'] == control):
            raise ValueError('Normal success or negative-control rejection is missing')
        if control:
            checked_control(checked_file(plan['test_control']), checked_file(plan['reference']).parent)
    resources = LOAD(checked_file(data['tests']['resources']))
    log = checked_file(data['tests']['log']).read_text()
    if resources.get('success') is not True or '\nOK\n' not in log:
        raise ValueError('Bounded fault and legacy tests must pass')
    for binding in data['tests']['sources']:
        checked_file(binding)
    return data
