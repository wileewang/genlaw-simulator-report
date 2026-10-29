"""Require current v11 full/partition and cumulative-observer evidence."""
import json
from pathlib import Path
from .cell_windows import file_binding,checked_file

ROOT=Path(__file__).resolve().parents[1]
NORMALS=['baseline','three-holes','tool-change','holes-first']
CONTROLS=['missing-profile','shifted-hole','wrong-tool','wrong-order']
SOURCES=['genlaw_pipeline/sharded_measure.py','genlaw_pipeline/sharded_verification.py',
    'genlaw_pipeline/checkpoint_context.py','genlaw_pipeline/window_controls.py',
    'genlaw_pipeline/profile_sequence_geometry.py','genlaw_pipeline/profile_sequence_verification.py',
    'genlaw_pipeline/sequence_verification.py','genlaw_pipeline/cell_windows.py',
    'genlaw_pipeline/profile_window_qualification.py','tools/probe_cell_windows.py',
    'tools/check_window_geometry.py','tools/run_window_checkpoints.py','tools/run_profile_window_regression.py']
LOAD=lambda p:json.loads(p.read_text())


def source_bindings():return [file_binding(ROOT/p) for p in SOURCES]


def checked_qualification(path,engine):
    data=LOAD(path)
    if (data.get('schema')!='genlaw.profile-window-regression.v1' or data.get('status')!='passed'
            or data.get('engine_manifest')!=file_binding(engine) or data.get('sources')!=source_bindings()
            or [r['id'] for r in data['normal_cases']]!=NORMALS or [r['id'] for r in data['controls']]!=CONTROLS):
        raise ValueError('Current profiled-window regression is required')
    checked_file(data['base_engine_regression'])
    for row in data['normal_cases']+data['controls']:
        result=LOAD(checked_file(row['result']))
        default=LOAD(checked_file(result['default']))
        window=LOAD(checked_file(result['window']))
        comparison=LOAD(checked_file(result['streamed_comparison']))
        if (not default['regression_passed'] or not default['count_and_all_triangle_payload_bytes_identical']
                or not window['regression_passed'] or comparison['status']!='passed'
                or comparison['absolute_iou_difference']>1e-10 or not comparison['criteria_and_verdicts_unchanged']
                or comparison.get('same_actual_surface_compared') is not True
                or comparison['legacy_absolute_iou_difference']>1e-5):
            raise ValueError('Profiled final geometry regression is incomplete')
        checked_file(default['candidate'])
        if not LOAD(checked_file(window['invariance']))['equal_oriented_float32_facets']:
            raise ValueError('Profiled partitions changed actual material')
        if not LOAD(checked_file(window['geometry']))['same_frozen_geometric_verdicts']:
            raise ValueError('Window computation changed a legacy geometric verdict')
        for key in ('old_verification','new_verification','old_metrics','new_metrics'):checked_file(comparison[key])
    checkpoints=data['checkpoint_regressions']
    if [r['id'] for r in checkpoints]!=NORMALS+['legacy-v9']:
        raise ValueError('Profiled and old rectangle checkpoint regressions are required')
    for row,count in zip(checkpoints,[3,4,3,3,2]):
        report=LOAD(checked_file(row['summary']))
        if (report['status']!='fine_checkpoints_passed' or report['legacy_comparison_passed'] is not True
                or len(report['checkpoints'])!=count or len(row['exact_facet_comparisons'])!=count
                or report['observer_source']!=file_binding(ROOT/'genlaw_pipeline/sharded_verification.py')
                or report['context_source']!=file_binding(ROOT/'genlaw_pipeline/checkpoint_context.py')):
            raise ValueError('Cumulative material regression is incomplete or stale')
        for binding in row['exact_facet_comparisons']:
            if not LOAD(checked_file(binding))['equal_oriented_float32_facets']:
                raise ValueError('Checkpoint partitions changed actual material')
        for entry in report['checkpoints']:
            value=LOAD(checked_file(entry['result']))
            for key in ('context','surface','metrics','verification','legacy_comparison'):checked_file(value[key])
            comparison=LOAD(checked_file(value['legacy_comparison']))
            if (comparison['status']!='passed' or not comparison['criteria_and_verdicts_unchanged']
                    or comparison['absolute_iou_difference']>(1e-10 if row['id']=='legacy-v9' else 1e-5)):
                raise ValueError('Checkpoint legacy comparison failed unchanged criteria')
            if row['id']!='legacy-v9':
                if comparison['same_mesh_iou_difference']>1e-10:raise ValueError('Same checkpoint mesh measurement differs')
                for key in ('same_mesh_metrics','same_mesh_geometry','exact_partition_comparison'):checked_file(comparison[key])
    return data
