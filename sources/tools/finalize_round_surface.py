"""Bind the four measured coupons and negative evidence into one qualification.

This adds a cohort decision without rewriting any earlier result or failure.
It does not qualify arbitrary cards, machine execution, or manufacturing.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from genlaw_pipeline.cell_windows import checked_file, file_binding
from genlaw_pipeline.round_archive import checked as admit
from genlaw_pipeline.round_surface_nc import check_nc
from genlaw_pipeline.small_partition_material import checked as checked_material
from genlaw_pipeline.run import write
from step2gcode_demo.validation.local_resource_limits import require_bounded_worker

CP = ROOT / 'docs/checkpoints/2026-10-31/round-surface-01'
RUNS = ROOT / 'validation/runs/S4-ROUND-SURFACE-20261031-r2'
INPUTS = ROOT / 'validation/feature-inputs/S4-ROUND-SURFACE-20261031-r2'
NORMALS = ['baseline', 'larger-radius', 'longer', 'shifted-datum']
READ = lambda p: json.loads(Path(p).read_text())


def finalize(resources):
    require_bounded_worker()
    if (CP / 'completion.json').exists() or (RUNS / 'summary.json').exists():
        raise ValueError('Never overwrite a completed qualification')
    review = READ(CP / 'input-review-r2.json')
    if review['status'] != 'r2_paper_and_CAD_reviewed_before_CAM' or review['manufacturing_approval']:
        raise ValueError('Missing pre-NC paper review')
    inputs = READ(checked_file(review['input_archive']))
    sources = {str(Path(__file__).relative_to(ROOT)): file_binding(Path(__file__))}
    normal = []
    for key in NORMALS:
        case = RUNS / key
        plan, receipt = admit(INPUTS / key / 'input.json', review['input_archive'])
        if plan != READ(case / 'preparation-result.json'):
            raise ValueError('Reviewed plan differs from the executed plan')
        cam = READ(case / 'cam-result.json')
        if cam['status'] != 'native_generated_and_checked' or check_nc(checked_file(cam['NC']).read_text(), plan) != cam['nc_check']:
            raise ValueError('Original native NC or validation changed')
        request = READ(case / 'request.json')
        for b in request['execution_sources'] + request['preserved_sources']:
            checked_file(b)
            sources[b['path']] = b
        result = READ(case / 'partitioned-case-result.json')
        checked_file(result['request'])
        metric_path = checked_file(result['metrics'])
        metric = READ(metric_path)
        inspection = READ(checked_file(metric['inspection']))
        material, mesh = checked_material(checked_file(inspection['native_result']))
        if (inspection['mesh'] != file_binding(mesh)
                or inspection['preparation'] != file_binding(case / 'preparation-result.json')
                or inspection['reference']['CAD'] != plan['input_bindings']['CAD']
                or inspection['acceptance'] != plan['specification']['software_acceptance']):
            raise ValueError('Foreign CAD, material, plan or acceptance criteria')
        observations = inspection['feature_observations']
        if (not result['geometry_pass'] or not metric['geometry_pass']
                or not metric['numerical_stability_pass'] or not metric['complete_material_measured']
                or metric['registration_applied'] or metric['material_discarded']
                or result['iou_percent'] != metric['iou_percent']
                or not all(observations[k] for k in ['profile_pass', 'radius_pass', 'retained_planes_pass'])
                or inspection['mesh_inspection']['exact_duplicate_faces_removed'] != 0
                or not inspection['mesh_inspection']['watertight']
                or not inspection['mesh_inspection']['consistent_winding']):
            raise ValueError('Incomplete or failed normal result')
        for b in metric['parts'] + inspection['source_code']:
            checked_file(b)
        for b in inspection['source_code']:
            sources[b['path']] = b
        normal.append({'id': key, 'specification': plan['specification'],
            'case_result': file_binding(case / 'partitioned-case-result.json'),
            'preparation': file_binding(case / 'preparation-result.json'),
            'CAM': file_binding(case / 'cam-result.json'), 'NC': cam['NC'],
            'paper_card': plan['input_bindings']['paper_card'], 'CAD': plan['input_bindings']['CAD'],
            'input_admission': receipt, 'metrics': result['metrics'], 'inspection': metric['inspection'],
            'material': inspection['native_result'], 'mesh': inspection['mesh'],
            'iou_percent': metric['iou_percent'], 'geometry_pass': True,
            'maximum_profile_deviation_mm': observations['maximum_boundary_deviation_mm'],
            'maximum_radius_deviation_mm': observations['maximum_radius_deviation_mm'],
            'volume_deviation_mm3': inspection['volume_deviation_mm3'],
            'resolution_mm': plan['simulation_project']['resolution'], 'native_windows': len(material['native_windows'])})
    controls = READ(CP / 'geometry-controls-01/result.json')
    if controls['status'] != 'two_geometry_controls_detected' or [r['id'] for r in controls['controls']] != ['insufficient-boundary', 'omitted-round']:
        raise ValueError('Missing geometry controls')
    for row in controls['controls']:
        metric = READ(checked_file(row['metrics']))
        inspection = READ(checked_file(metric['inspection']))
        checked_file(inspection['mesh'])
        for b in metric['parts'] + inspection['source_code']:
            checked_file(b)
        if metric['geometry_pass'] or not metric['numerical_stability_pass'] or metric['iou_percent'] != row['iou_percent']:
            raise ValueError('Unstable or undetected geometry control')
        row['inspection'] = metric['inspection']
        row['mesh'] = inspection['mesh']
    negative = READ(CP / 'input-check-01/result.json')
    feeds = READ(CP / 'feed-adapter-check.json')
    partition = READ(CP / 'partition-qualification.json')
    regression = READ(CP / 'geometry-controls-01/section-regression.json')
    if (negative['status'] != 'passed' or len(negative['rejections']) != 23
            or len(negative['normal_inputs']) != 4 or feeds['status'] != 'passed'
            or len(feeds['rejected_controls']) != 8
            or partition['status'] != 'passed' or not partition['exact_oriented_facet_records_equal']
            or regression['status'] != 'passed' or len(regression['groups']) != 17
            or not all(r['numeric_values_equal'] for r in regression['groups'])):
        raise ValueError('Missing input, feed, partition or observer regression evidence')
    for record in [negative, feeds, partition]:
        for b in record['source_code']:
            checked_file(b)
            sources[b['path']] = b
    rows = []
    for binding in READ(resources)['resources']:
        r = READ(checked_file(binding))
        if (r['wall_limit_seconds'] != 60 or r['per_process_address_space_limit_bytes'] != 2*1024**3
                or r['observed_group_rss_limit_bytes'] != 2*1024**3
                or r['file_size_limit_bytes'] != 64*1024**2 or len(r['cpu_affinity']) != 1
                or r['thread_environment_limit'] != 1):
            raise ValueError('Changed compute limit')
        rows.append({'record': binding, 'status': r['status'], 'exit_code': r['exit_code'],
                     'success': r['success'], 'seconds': r['seconds'],
                     'peak_rss_bytes': r['peak_observed_group_rss_bytes']})
    frozen = CP / 'qualified-source'
    frozen.mkdir(exist_ok=False)
    copies = []
    for b in sources.values():
        p = checked_file(b); dest = frozen / b['path']; dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest); copies.append({'executed': b, 'preserved': file_binding(dest)})
    write(RUNS / 'summary.json', {'schema': 'genlaw.round-surface-suite.v1',
        'status': 'four_normal_two_geometry_controls_qualified', 'normal_cases': normal,
        'controls': controls['controls'], 'all_regression_expectations_met': True,
        'input_rejections': 23, 'feed_and_NC_rejections': 8, 'feature_cohort_qualified': True,
        'qualified_scope': 'Four synthetic rectangular coupons with two X-parallel upper exterior rounds, native fixed-Z ball-end Surface NC, original stock and coordinates.',
        'scope_dimension': 'part_features', 'new_machine_models': 0, 'new_material_physics': 0,
        'continuous_five_axis_supported': False, 'arbitrary_production_PDF_parsing': False,
        'whole_real_card_complete': False, 'manufacturing_approval': False,
        'original_development_failures_retained': True})
    write(CP / 'completion.json', {'schema': 'genlaw.round-surface-qualification.v1',
        'status': 'passed_for_declared_synthetic_cohort', 'suite': file_binding(RUNS / 'summary.json'),
        'input_archive': review['input_archive'], 'paper_review': file_binding(CP / 'input-review-r2.json'),
        'input_checks': file_binding(CP / 'input-check-01/result.json'),
        'feed_checks': file_binding(CP / 'feed-adapter-check.json'),
        'geometry_controls': file_binding(CP / 'geometry-controls-01/result.json'),
        'partition_qualification': file_binding(CP / 'partition-qualification.json'),
        'section_regression': file_binding(CP / 'geometry-controls-01/section-regression.json'),
        'source_freeze': copies, 'resources_frozen_before_finalizer': file_binding(resources),
        'resources': rows, 'success_worker_count': sum(r['success'] for r in rows),
        'failed_worker_count_preserved': sum(not r['success'] for r in rows),
        'maximum_success_seconds': max(r['seconds'] for r in rows if r['success']),
        'maximum_success_rss_bytes': max(r['peak_rss_bytes'] for r in rows if r['success']),
        'web_published': False, 'whole_real_card_complete': False, 'production_approved': False})
    print(json.dumps({'qualified_normal_cases': len(normal), 'geometry_controls': 2, 'input_rejections': 23,
                      'iou_percent': [r['iou_percent'] for r in normal]}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--resources', type=Path, required=True)
    finalize(parser.parse_args().resources.resolve())
