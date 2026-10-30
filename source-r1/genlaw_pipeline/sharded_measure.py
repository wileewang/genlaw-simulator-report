"""Global sectional IoU of audited surface shards, with bounded interval workers.

Horizontal faces cannot intersect an open vertex-height interval. Their original
heights and signed volume remain in the full surface audit; only the section
acceleration cache omits them. No surface is capped, repaired or downsampled.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'web/tools'))
from mesh_metrics import np, require_bounded_worker, controls
from shapely.geometry import Polygon
from shapely.ops import polygonize_full, unary_union
from genlaw_pipeline.cell_windows import checked_file, file_binding
from genlaw_pipeline.sharded_surface import checked_result, triangles, validate_results
from genlaw_pipeline.drilling_geometry import SINGLE, MULTI, reference_holes
from genlaw_pipeline.facing_geometry import FACING, FacingReference
from genlaw_pipeline.reference_sections import DrilledReference, integrate_variable
from genlaw_pipeline.measure_worker import combine_volumes
from genlaw_pipeline.profile_sequence_geometry import REFERENCE as PROFILED, ProfileSequenceReference

MAX_ACTIVE_TRIANGLES = 500_000
MAX_SECTION_SEGMENTS = 32_768
INTERVALS_PER_WORKER = 16


def load(path):
    return json.loads(path.read_text())


def write(path, data):
    with path.open('x') as stream:
        json.dump(data, stream, indent=2)
        stream.write('\n')


def cache_window(plan_path, result_path, output):
    require_bounded_worker()
    record, path = checked_result(plan_path, result_path)
    points = triangles(path)
    side = points[np.ptp(points[:, :, 2], axis=1) > 0] if len(points) else points
    array_path = output.with_suffix('.npy')
    with array_path.open('xb') as stream:
        np.save(stream, side, allow_pickle=False)
    write(output, {'schema': 'genlaw.window-section-cache.v1', 'result': file_binding(result_path),
                   'original_surface': record['surface'], 'triangles': len(side),
                   'omitted_exactly_horizontal_triangles': len(points)-len(side),
                   'array': file_binding(array_path), 'source_code': file_binding(Path(__file__)),
                   'geometry_modified': False,
                   'scope': 'Section acceleration only; original complete triangles retained in audited surface.'})
    print(json.dumps({'section_cache_triangles': len(side)}), flush=True)


def checked_surface(surface_path):
    surface = load(surface_path)
    if (surface.get('schema') != 'genlaw.sharded-surface.v1'
            or surface.get('status') != 'closed_oriented_surface'
            or not surface.get('all_global_cells_covered_once')
            or surface.get('end_time_seconds') is not None):
        raise ValueError('A complete audited final surface is required')
    checked_file(surface['source_code'])
    plan = checked_file(surface['plan'])
    validate_results(plan, [checked_file(w['result']) for w in surface['windows']])
    for row in surface['windows']:
        checked_file(row['audit'])
    return surface


def target(reference, segments):
    if reference['status'] != 'verified':
        raise ValueError('Missing original native CAD verification')
    if reference['reference_class'] == FACING:
        obj = FacingReference(reference, segments)
    elif reference['reference_class'] in (SINGLE, MULTI):
        obj = DrilledReference({**reference, 'features': reference_holes(reference)}, segments)
    elif reference['reference_class'] == PROFILED:
        obj = ProfileSequenceReference(reference, segments)
    else:
        raise ValueError('Large-shard reference adapter requires a supported native-checked planar part')
    if abs(obj.analytic_volume-reference['volume_mm3']) > 1e-8:
        raise ValueError('Reference section volume differs from original CAD')
    return obj


def circle_resolution(reference):
    """Refine circle polygons to retain the existing absolute CAD-volume check."""
    segments = 2048
    while segments <= 32768:
        reference_volume = reference['volume_mm3']
        obj = target(reference, segments)
        lo, hi = reference['bounds']['min'][2], reference['bounds']['max'][2]
        error = abs(obj.at((lo+hi)/2).area*(hi-lo)-reference_volume)
        if error < 2.5e-6:
            return segments
        segments *= 2
    raise ValueError('Original CAD requires a larger reference-circle budget')


def check_reference_sources(plan, reference):
    expected = [file_binding(ROOT/'genlaw_pipeline'/name)
        for name in ('profile_geometry.py','profile_sequence_geometry.py')] if reference['reference_class'] == PROFILED else []
    if plan.get('additional_reference_sources', []) != expected:
        raise ValueError('Independent profiled reference source changed')


def make_plan(surface_path, reference_path, caches, output):
    require_bounded_worker()
    surface = checked_surface(surface_path)
    reference = load(reference_path)
    fine = circle_resolution(reference)
    ref = target(reference, fine)
    levels = np.unique(np.concatenate((surface['z_levels_mm'], ref.breaks))).astype('<f8')
    if not 2 <= len(levels) <= 4096:
        raise ValueError('Invalid or over-budget global height intervals')
    if len(caches) != len(surface['windows']):
        raise ValueError('Missing section caches')
    bound = {}
    for path in caches:
        cache = load(path)
        if cache['source_code'] != file_binding(Path(__file__)):
            raise ValueError('Section cache source changed')
        result = checked_file(cache['result'])
        mesh = load(result)['surface']
        if cache['original_surface'] != mesh:
            raise ValueError('Section cache surface binding changed')
        checked_file(cache['array'])
        key = cache['result']['sha256']
        if key in bound:
            raise ValueError('Duplicate section cache')
        bound[key] = file_binding(path)
    keys = [row['result']['sha256'] for row in surface['windows']]
    if set(keys) != set(bound):
        raise ValueError('Section caches differ from the audited complete surface')
    plan = {'schema': 'genlaw.sharded-iou-plan.v1', 'surface': file_binding(surface_path),
            'reference': file_binding(reference_path), 'caches': [bound[k] for k in keys],
            'levels_mm': levels.tolist(), 'levels_sha256': hashlib.sha256(levels.tobytes()).hexdigest(),
            'parts': [[i, min(i+INTERVALS_PER_WORKER, len(levels)-1)] for i in range(0, len(levels)-1, INTERVALS_PER_WORKER)],
            'fine_circle_quadrant_segments': fine, 'coarse_circle_quadrant_segments': fine//2,
            'maximum_active_triangles': MAX_ACTIVE_TRIANGLES, 'maximum_section_segments': MAX_SECTION_SEGMENTS,
            'intervals_per_worker': INTERVALS_PER_WORKER, 'source_code': file_binding(Path(__file__)),
            'original_reference_source': file_binding(ROOT/'genlaw_pipeline/reference_sections.py'),
            'volume_assembly_source': file_binding(ROOT/'genlaw_pipeline/measure_worker.py'),
            'artificial_surface_caps': False, 'registration': 'none'}
    if reference['reference_class'] == PROFILED:
        plan['additional_reference_sources'] = [file_binding(ROOT/'genlaw_pipeline'/name)
            for name in ('profile_geometry.py','profile_sequence_geometry.py')]
    write(output, plan)
    print(json.dumps({'height_levels': len(levels), 'bounded_parts': len(plan['parts']), 'circle_quadrant_segments': fine}), flush=True)


class ShardedSections:
    def __init__(self, plan, low, high):
        pieces = []
        count = 0
        for binding in plan['caches']:
            cache = load(checked_file(binding))
            if cache['source_code'] != plan['source_code']:
                raise ValueError('Cached section program changed')
            array = np.load(checked_file(cache['array']), mmap_mode='r', allow_pickle=False)
            if array.shape != (cache['triangles'], 3, 3) or array.dtype != np.dtype('<f4'):
                raise ValueError('Section cache shape changed')
            zmin, zmax = array[:, :, 2].min(axis=1), array[:, :, 2].max(axis=1)
            selected = np.asarray(array[(zmin < high) & (zmax > low)], dtype=np.float64)
            count += len(selected)
            if count > MAX_ACTIVE_TRIANGLES:
                raise ValueError('Section interval exceeds active triangle budget')
            pieces.append(selected)
        self.triangles = np.concatenate(pieces)
        self.zmin, self.zmax = self.triangles[:, :, 2].min(axis=1), self.triangles[:, :, 2].max(axis=1)
        self.calls = 0
        self.maximum_observed_segments = 0

    def at(self, z):
        self.calls += 1
        tri = self.triangles[(self.zmin < z) & (self.zmax > z)]
        self.maximum_observed_segments = max(self.maximum_observed_segments, len(tri))
        if not len(tri):
            return Polygon()
        if len(tri) > MAX_SECTION_SEGMENTS:
            raise ValueError('Global section exceeds the bounded segment budget')
        points = np.empty((len(tri), 2, 2), dtype=np.float64)
        added = np.zeros(len(tri), dtype=np.int8)
        for i, j in ((0, 1), (1, 2), (2, 0)):
            a, b = tri[:, i], tri[:, j]
            hit = ((a[:, 2] < z) & (b[:, 2] > z)) | ((b[:, 2] < z) & (a[:, 2] > z))
            ids = np.flatnonzero(hit)
            ratio = (z-a[hit, 2])/(b[hit, 2]-a[hit, 2])
            points[ids, added[ids]] = a[hit, :2]+ratio[:, None]*(b[hit, :2]-a[hit, :2])
            added[ids] += 1
        if not np.all(added == 2):
            raise ValueError('Ambiguous shard section edge')
        # Same 1e-8 mm interpolation-roundoff rule as the existing small-mesh path.
        points = np.round(points, 8)
        points = points[np.any(points[:, 0] != points[:, 1], axis=1)]
        polygons, cuts, dangles, invalid = polygonize_full(points)
        if not cuts.is_empty or not dangles.is_empty or not invalid.is_empty:
            raise ValueError('Unclosed or invalid global material section')
        pieces = list(polygons.geoms)
        rings = [Polygon(piece.exterior) for piece in pieces]
        selected = [piece for piece in pieces if sum(r.contains(piece.representative_point()) for r in rings) % 2 == 1]
        section = unary_union(selected)
        if not section.is_valid:
            raise ValueError('Invalid global material section')
        return section


def measure_part(plan_path, index, output):
    require_bounded_worker()
    plan = load(plan_path)
    checked_file(plan['source_code']); checked_file(plan['original_reference_source']); checked_file(plan['volume_assembly_source'])
    surface = checked_surface(checked_file(plan['surface']))
    reference = load(checked_file(plan['reference']))
    check_reference_sources(plan, reference)
    if type(index) is not int or not 0 <= index < len(plan['parts']):
        raise ValueError('Invalid height partition')
    start, end = plan['parts'][index]
    levels = np.array(plan['levels_mm'], dtype='<f8')
    if hashlib.sha256(levels.tobytes()).hexdigest() != plan['levels_sha256'] or not 0 < end-start <= INTERVALS_PER_WORKER:
        raise ValueError('Height plan changed or exceeded the interval budget')
    sections = ShardedSections(plan, levels[start], levels[end])
    coarse = integrate_variable(sections, levels[start:end+1], target(reference, plan['coarse_circle_quadrant_segments']), 2)
    refined = integrate_variable(sections, levels[start:end+1], target(reference, plan['fine_circle_quadrant_segments']), 3)
    write(output, {'schema': 'genlaw.sharded-iou-part.v1', 'plan': file_binding(plan_path), 'index': index,
                   'interval_start': start, 'interval_end': end, 'coarse': coarse, 'refined': refined,
                   'active_triangles': len(sections.triangles), 'section_evaluations': sections.calls,
                   'maximum_observed_segments': sections.maximum_observed_segments,
                   'source_code': file_binding(Path(__file__))})
    print(json.dumps({'measured_intervals': [start, end], 'maximum_segments': sections.maximum_observed_segments}), flush=True)


def finish(plan_path, parts, output):
    require_bounded_worker()
    plan = load(plan_path)
    checked_file(plan['source_code']); checked_file(plan['original_reference_source']); checked_file(plan['volume_assembly_source'])
    surface = checked_surface(checked_file(plan['surface']))
    reference = load(checked_file(plan['reference']))
    check_reference_sources(plan, reference)
    if len(parts) != len(plan['parts']):
        raise ValueError('Missing global IoU intervals')
    values = []
    for index, path in enumerate(parts):
        row = load(path)
        if (row.get('schema') != 'genlaw.sharded-iou-part.v1' or row['plan'] != file_binding(plan_path)
                or row['index'] != index or [row['interval_start'], row['interval_end']] != plan['parts'][index]
                or row['source_code'] != plan['source_code']):
            raise ValueError('Missing, duplicate, foreign or out-of-order IoU intervals')
        values.append(row)
    coarse = combine_volumes([v['coarse'] for v in values], 2)
    refined = combine_volumes([v['refined'] for v in values], 3)
    delta = abs(coarse['iou']-refined['iou'])
    if delta > 1e-5 or abs(refined['actual_volume_mm3']-surface['volume_mm3']) > 1e-4:
        raise ValueError('Global section integration failed unchanged volume/stability checks')
    if abs(refined['target_volume_mm3']-reference['volume_mm3']) > 1e-5:
        raise ValueError('Integrated target differs from native original CAD volume')
    write(output, {'schema': 'genlaw.sharded-sectional-iou.v1', 'status': 'measured_numerical_estimate',
                   'iou': refined['iou'], 'iou_percent': refined['iou']*100, 'volumes': refined,
                   'excess_material_mm3': refined['actual_volume_mm3']-refined['intersection_mm3'],
                   'missing_material_mm3': refined['target_volume_mm3']-refined['intersection_mm3'],
                   'absolute_iou_refinement_difference': delta, 'plan': file_binding(plan_path),
                   'surface': plan['surface'], 'reference': plan['reference'], 'parts': [file_binding(p) for p in parts],
                   'all_vertex_height_intervals_preserved': True, 'per_window_iou_averaged': False,
                   'actual_surface_volume_mm3': surface['volume_mm3'], 'section_levels': len(plan['levels_mm']),
                   'method': 'Global closed-surface sections from exact uncapped shards; all height intervals; 3-point Gauss integration against native-checked independent CAD.',
                   'registration': 'none; original coordinates', 'source_code': file_binding(Path(__file__)),
                   'controls': controls(), 'full_tolerance_acceptance': False, 'production_approved': False})
    print(json.dumps({'iou_percent': refined['iou']*100, 'volume_mm3': surface['volume_mm3'], 'refinement_difference': delta}), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    c = sub.add_parser('cache')
    for name in ('plan', 'result', 'output'):
        c.add_argument('--'+name, required=True, type=Path)
    m = sub.add_parser('plan')
    for name in ('surface', 'reference', 'caches', 'output'):
        m.add_argument('--'+name, required=True, type=Path)
    part = sub.add_parser('part')
    part.add_argument('--plan', required=True, type=Path); part.add_argument('--index', required=True, type=int)
    part.add_argument('--output', required=True, type=Path)
    final = sub.add_parser('finish')
    for name in ('plan', 'parts', 'output'):
        final.add_argument('--'+name, required=True, type=Path)
    a = p.parse_args()
    if a.action == 'cache':
        cache_window(a.plan, a.result, a.output)
    elif a.action == 'plan':
        make_plan(a.surface, a.reference, sorted(a.caches.glob('window-*/sections.json')), a.output)
    elif a.action == 'part':
        measure_part(a.plan, a.index, a.output)
    else:
        finish(a.plan, sorted(a.parts.glob('part-*.json')), a.output)


if __name__ == '__main__':
    main()
