"""Freeze added dimension criteria, then observe existing NC material sequentially."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.cell_windows import file_binding, checked_file
from genlaw_pipeline.planar_dimensions import POLICY, features, compile_requirements
from genlaw_pipeline.run import write

INPUT = ROOT / 'validation/feature-inputs/S4-DRAWING-DIMENSIONS-20261030-r1'
OUT = ROOT / 'validation/runs/S4-DRAWING-DIMENSIONS-20261030-r1'
CHECKPOINT = ROOT / 'docs/checkpoints/2026-10-30/drawing-dimensions-01'
PROFILE = ROOT / 'validation/runs/S4-PROFILE-SUITE-20261029-r1'
SEQUENCE = ROOT / 'validation/runs/S4-PROFILE-SEQUENCE-SUITE-20261029-r1'
CONNECTION = ROOT / 'validation/runs/S4-CONNECTION-REFERENCE-20261029-r1'
CASES = [
    ('profile-x', PROFILE/'baseline', PROFILE/'baseline', 'single_mesh', True),
    ('profile-y', PROFILE/'vertical', PROFILE/'vertical', 'single_mesh', True),
    ('two-holes', SEQUENCE/'baseline', SEQUENCE/'baseline', 'single_mesh', True),
    ('connection-100mm', CONNECTION, CONNECTION, 'surface_shards', True),
    ('overcut', PROFILE/'baseline', PROFILE/'overcut', 'single_mesh', False),
    ('uncut-corners', PROFILE/'baseline', PROFILE/'uncut-corners', 'single_mesh', False),
    ('shifted-hole', SEQUENCE/'baseline', SEQUENCE/'shifted-hole', 'single_mesh', False),
    ('wrong-order', SEQUENCE/'baseline', SEQUENCE/'wrong-order', 'single_mesh', True),
]
LOAD = lambda path: json.loads(Path(path).read_text())


def requirements(reference, source_derived=False):
    profile, holes = features(reference)
    axis = profile['major_axis']; major = 'XY'.index(axis)
    holes = sorted(holes, key=lambda h: h['center_xy_mm'][major])
    items = [
        {'id': 'length', 'kind': 'support_span', 'axis': axis, 'nominal_mm': profile['length_mm'], 'tol': .3},
        {'id': 'width', 'kind': 'support_span', 'axis': 'YX'[major], 'nominal_mm': profile['width_mm'], 'tol': .2},
        *[{'id': side+'-radius', 'kind': 'end_radius', 'axis': axis, 'side': side,
           'nominal_mm': profile['width_mm']/2, 'tol': .2} for side in ('low', 'high')],
    ]
    if holes:
        items.append({'id': 'high-end-offset', 'kind': 'end_offset', 'axis': axis, 'side': 'high',
            'hole_id': holes[-1]['id'], 'nominal_mm': profile['center_xy_mm'][major] + profile['length_mm']/2
            - holes[-1]['center_xy_mm'][major], 'tol': .2})
        for first, second in zip(holes, holes[1:]):
            items.append({'id': first['id']+'-'+second['id']+'-pitch', 'kind': 'center_pitch', 'axis': axis,
                'hole_ids': [first['id'], second['id']],
                'nominal_mm': second['center_xy_mm'][major] - first['center_xy_mm'][major], 'tol': .3})
    for item in items:
        item['profile_id'] = profile['id']; tol = item.pop('tol')
        item['acceptance'] = {'schema': 'genlaw.numeric-acceptance.v1', 'units': 'mm',
            'scope': 'engineering_requirement' if source_derived else 'software_regression',
            'basis': ('Private source card PDF page 4, operation 15; explicit tolerances transcribed. '
                      'Drawing provides right-end to H3 datum. Identity/revision and manufacturing approval pending.'
                      if source_derived else 'Predeclared observer regression; does not assert a production tolerance.'),
            'method': item['kind'], 'min_mm': item['nominal_mm']-tol, 'max_mm': item['nominal_mm']+tol}
    result = {'schema': 'genlaw.planar-dimension-requirements.v1', 'units': 'mm',
        'cad_sha256': reference['cad_sha256'], 'measurement_policy': POLICY, 'items': items,
        'criteria_added_after_original_material_execution': True, 'criteria_frozen_before_added_measurement': True,
        'manufacturing_approval': False, 'source_identity_and_revision_approved': False,
        'source_dimension_stage_before_or_after_plating_approved': False}
    if source_derived:
        declaration = LOAD(CHECKPOINT/'plan.json')
        result['private_source_documents'] = [{'kind': row['kind'], 'sha256': row['sha256']}
                                              for row in declaration['source_documents_checked']]
        result['datum_note'] = declaration['datum_correction']
        result['previous_requirements_preserved'] = file_binding(CONNECTION/'requirement-verification.json')
    compile_requirements(result, reference)
    return result


def freeze():
    INPUT.mkdir(parents=True, exist_ok=False)
    rows = []
    for name, normal, material, backend, expected in CASES:
        folder = INPUT/name; folder.mkdir()
        source = {'schema': 'genlaw.dimension-material-source.v1', 'normal_run': str(normal.relative_to(ROOT)),
            'material_run': str(material.relative_to(ROOT)), 'backend': backend, 'test_only_control': normal != material,
            'scope': 'Added observer qualification using already executed actual NC material; no new CAM run.'}
        write(folder/'source.json', source)
        write(folder/'requirements.json', requirements(LOAD(normal/'native-geometry.json'), name == 'connection-100mm'))
        rows.append({'id': name, 'source': file_binding(folder/'source.json'),
            'requirements': file_binding(folder/'requirements.json'), 'dimension_pass_expected': expected,
            'normal_process_guard_rejection_expected': normal != material})
    write(INPUT/'manifest.json', {'schema': 'genlaw.dimension-qualification-inputs.v1', 'cases': rows,
        'source_code': file_binding(Path(__file__)), 'declaration': file_binding(CHECKPOINT/'plan.json'),
        'new_material_execution': False, 'new_manufacturing_approval': False})
    print(json.dumps({'frozen_cases': len(rows), 'path': str(INPUT.relative_to(ROOT))}), flush=True)


def run(name):
    manifest = LOAD(INPUT/'manifest.json'); checked_file(manifest['source_code'])
    row = next(c for c in manifest['cases'] if c['id'] == name)
    source = checked_file(row['source']); req = checked_file(row['requirements'])
    out = OUT/name; out.mkdir(parents=True, exist_ok=False)
    worker = [sys.executable, ROOT/'genlaw_pipeline/dimension_inspection.py']
    admitted_bounded(out, 'plan', worker + ['plan', '--source', source, '--requirements', req, '--output', out/'plan.json'])
    if LOAD(source)['backend'] == 'surface_shards':
        for index in range(11):
            admitted_bounded(out, f'section-{index:02d}', worker + ['section', '--plan', out/'plan.json',
                '--index', str(index), '--output', out/f'section-{index:02d}.json'])
    admitted_bounded(out, 'finish', worker + ['finish', '--plan', out/'plan.json', '--directory', out,
                                           '--output', out/'verification.json'])
    report = LOAD(out/'verification.json')
    matched = (report['dimension_inspection_passed'] == row['dimension_pass_expected']
               and bool(report['normal_guard_rejection']) == row['normal_process_guard_rejection_expected'])
    write(out/'qualification.json', {'schema': 'genlaw.dimension-case-qualification.v1', 'id': name,
        'report': file_binding(out/'verification.json'), 'input_manifest': file_binding(INPUT/'manifest.json'),
        'expectations_matched': matched, 'expected': row,
        'manufacturing_approved': False, 'new_material_execution': False})
    print(json.dumps({'case': name, 'expectations_matched': matched,
        'dimension_passed': report['dimension_inspection_passed'],
        'statuses': {r['id']:r['status'] for r in report['requirements']}}), flush=True)
    if not matched: raise ValueError('Dimension qualification differs from the frozen expected outcome')


def summarize():
    manifest = LOAD(INPUT/'manifest.json'); cases = []
    for entry in manifest['cases']:
        out = OUT/entry['id']; qualification = LOAD(out/'qualification.json')
        report = LOAD(checked_file(qualification['report']))
        if not qualification['expectations_matched']: raise ValueError('Qualification did not pass')
        cases.append({'id': entry['id'], 'qualification': file_binding(out/'qualification.json'),
            'verification': file_binding(out/'verification.json'), 'test_only_control': report['test_only_control'],
            'dimension_inspection_passed': report['dimension_inspection_passed'],
            'original_iou_percent': report['original_iou_percent'],
            'dimensions': report['requirements'], 'normal_guard_rejection': report['normal_guard_rejection']})
    records = [LOAD(p) for p in OUT.glob('*/*-resources.json')]
    if not records or not all(r['success'] for r in records): raise ValueError('Unresolved bounded worker failure')
    write(OUT/'summary.json', {'schema': 'genlaw.dimension-suite-summary.v1', 'status': 'qualified',
        'input_manifest': file_binding(INPUT/'manifest.json'), 'source_code': file_binding(Path(__file__)),
        'cases': cases, 'new_material_execution': False, 'prior_iou_recomputed': False,
        'manufacturing_approved': False, 'whole_process_card_complete': False,
        'workers': len(records), 'maximum_wall_seconds': max(r['seconds'] for r in records),
        'peak_group_rss_bytes': max(r['peak_observed_group_rss_bytes'] for r in records)})
    print(json.dumps({'status': 'qualified', 'cases': len(cases), 'workers': len(records)}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['freeze', 'run', 'summarize']); parser.add_argument('--case')
    args = parser.parse_args()
    if args.mode == 'freeze': freeze()
    elif args.mode == 'run': run(args.case)
    else: summarize()
