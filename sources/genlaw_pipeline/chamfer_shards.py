"""Bounded full-material chamfer sections and rays using the existing shard cache.

The qualified profile observer and its source bindings remain unchanged. This
adapter uses the same section kernel and the original chamfer acceptance rules;
its observation equations are regressed against the complete actual mesh.
"""
import argparse
import bisect
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'web/tools')]
from mesh_metrics import np, require_bounded_worker, controls
from shapely.geometry import LineString
from genlaw_pipeline.cell_windows import file_binding, checked_file, checked_plan as checked_cell
from genlaw_pipeline.sharded_measure import checked_surface, ShardedSections
from genlaw_pipeline.sharded_measure import MAX_ACTIVE_TRIANGLES, MAX_SECTION_SEGMENTS, INTERVALS_PER_WORKER
from genlaw_pipeline.sharded_surface import triangles
from genlaw_pipeline.chamfer_geometry import REFERENCE, ChamferReference
from genlaw_pipeline.chamfer_verification import evaluate
from genlaw_pipeline.measure_worker import combine_volumes, section_height
from genlaw_pipeline.reference_sections import integrate_variable
from genlaw_pipeline.region_verification import topology
from genlaw_pipeline.verification import ray_hits
from genlaw_pipeline.run import write

RAY_WINDOWS_PER_WORKER = 8
LOAD = lambda p: json.loads(p.read_text())
DEPENDENCIES = [
    'genlaw_pipeline/cell_windows.py', 'genlaw_pipeline/sharded_surface.py',
    'genlaw_pipeline/sharded_measure.py', 'genlaw_pipeline/chamfer_geometry.py',
    'genlaw_pipeline/chamfer_verification.py', 'genlaw_pipeline/profile_verification.py',
    'genlaw_pipeline/region_verification.py', 'genlaw_pipeline/verification.py',
    'genlaw_pipeline/reference_sections.py', 'genlaw_pipeline/measure_worker.py',
    'genlaw_pipeline/window_controls.py', 'web/tools/mesh_metrics.py',
]


def target(reference):
    if reference.get('status') != 'verified' or reference.get('reference_class') != REFERENCE:
        raise ValueError('Native-verified rectangular chamfer CAD is required')
    obj = ChamferReference(reference)
    if abs(obj.analytic_volume-reference['volume_mm3']) > 1e-8:
        raise ValueError('Independent chamfer volume differs from original native CAD')
    return obj


def parameters(surface, reference, resolution):
    obj = target(reference); lo, hi = obj.low, obj.high
    levels = np.unique(np.concatenate((surface['z_levels_mm'], obj.breaks))).astype('<f8')
    if not 2 <= len(levels) <= 4096:
        raise ValueError('Invalid or over-budget global height intervals')
    requests = [('bevel', hi[2]-obj.width*float(v)) for v in np.linspace(.05, .95, 11)]
    requests += [('retained_wall', lo[2]+float(v)*(hi[2]-obj.width-lo[2])) for v in [.2, .5, .8]]
    heights = []
    for kind, requested in requests:
        z = section_height(levels, requested) if float(np.min(abs(levels-requested))) < 1e-10 else requested
        heights.append({'region': kind, 'requested_z_mm': requested, 'z_mm': z})
    margin = obj.width+4*resolution
    if any(hi[i]-lo[i] <= 2*margin for i in range(2)):
        raise ValueError('Insufficient retained-top probe space')
    positions = [[float(x), float(y)]
                 for x in np.linspace(lo[0]+margin, hi[0]-margin, 5)
                 for y in np.linspace(lo[1]+margin, hi[1]-margin, 5)]
    return {'levels_mm': levels.tolist(), 'levels_sha256': hashlib.sha256(levels.tobytes()).hexdigest(),
        'parts': [[i, min(i+INTERVALS_PER_WORKER, len(levels)-1)] for i in range(0, len(levels)-1, INTERVALS_PER_WORKER)],
        'heights': heights, 'probes': [{'id': i, 'xy_mm': xy} for i, xy in enumerate(positions)],
        'ray_groups': [[i, min(i+RAY_WINDOWS_PER_WORKER, len(surface['windows']))]
                       for i in range(0, len(surface['windows']), RAY_WINDOWS_PER_WORKER)],
        'ray_z_bounds_mm': [lo[2]-2*resolution, hi[2]+2*resolution], 'resolution_mm': resolution,
        'maximum_active_triangles': MAX_ACTIVE_TRIANGLES, 'maximum_section_segments': MAX_SECTION_SEGMENTS,
        'intervals_per_worker': INTERVALS_PER_WORKER, 'ray_windows_per_worker': RAY_WINDOWS_PER_WORKER}


def validate_caches(surface, bindings):
    expected = [row['result'] for row in surface['windows']]
    if len(bindings) != len(expected):
        raise ValueError('Missing section caches')
    for binding, result_binding in zip(bindings, expected):
        cache = LOAD(checked_file(binding)); result = LOAD(checked_file(result_binding))
        if (cache.get('schema') != 'genlaw.window-section-cache.v1'
                or cache['source_code'] != file_binding(ROOT / 'genlaw_pipeline/sharded_measure.py')
                or cache['result'] != result_binding or cache['original_surface'] != result['surface']
                or cache['geometry_modified'] is not False):
            raise ValueError('Duplicate, foreign, reordered or modified section cache')
        checked_file(cache['array'])


def make_plan(surface_path, reference_path, policy_path, caches, output, control_binding=None):
    from genlaw_pipeline.programming import checked_plan, verify_binding, check_nc, simulation_project
    from genlaw_pipeline.window_controls import checked_control
    require_bounded_worker()
    surface = checked_surface(surface_path)
    reference = LOAD(reference_path); target(reference)
    policy = LOAD(policy_path)
    source = reference_path.parent
    request = LOAD(source / 'request.json')
    original = checked_plan(Path(request['input']), source / 'preparation-result.json')
    cam = LOAD(source / 'cam-result.json')
    cell, project, _, _ = checked_cell(checked_file(surface['plan']))
    if (original['schema'] != 'genlaw.execution-plan.v14' or cam['native_cad_binding'] != reference
            or original['cad_ref']['sha256'] != reference['cad_sha256']
            or policy.get('schema') != 'genlaw.software-geometry-policy.v14'
            or policy['cad_sha256'] != reference['cad_sha256']
            or set(policy['features']) != {reference['features'][0]['id']}):
        raise ValueError('Surface reference or policy differs from the original chamfer contract')
    frozen_policy = Path(request['input']).parent / 'software-policy.json'
    manifest = LOAD(frozen_policy.parent / 'manifest.json')
    verify_binding(policy_path, manifest['files']['software-policy.json']['sha256'])
    verify_binding(source / 'program.nc', cam['nc_sha256'])
    check_nc((source / 'program.nc').read_text(), original)
    control = checked_control(control_binding, source) if control_binding else None
    actual_source = control['source'] if control else source
    if (file_binding(project) != file_binding(actual_source / 'simulation.camotics')
            or cell['binding']['nc_files'] != [file_binding(actual_source / 'program.nc')]
            or LOAD(project) != simulation_project(original)):
        raise ValueError('Surface must use the exact original NC, material and tool configuration')
    bindings = [file_binding(p) for p in caches]
    validate_caches(surface, bindings)
    plan = {'schema': 'genlaw.chamfer-sharded-plan.v1', 'surface': file_binding(surface_path),
        'reference': file_binding(reference_path), 'policy': file_binding(policy_path), 'caches': bindings,
        'source_code': file_binding(Path(__file__)),
        'dependencies': [file_binding(ROOT / p) for p in DEPENDENCIES],
        'prepared_source': file_binding(source / 'preparation-result.json'),
        'native_cam': file_binding(source / 'cam-result.json'),
        **parameters(surface, reference, cell['grid']['resolution_mm']),
        'artificial_surface_caps': False, 'registration': 'none', 'production_approved': False}
    if control:
        plan.update(test_control=control['binding'], normal_guard_rejection=control['normal_guard_rejection'])
    write(output, plan)
    print(json.dumps({'height_intervals': len(plan['levels_mm'])-1, 'parts': len(plan['parts']),
                      'feature_sections': len(plan['heights']), 'retained_rays': len(plan['probes'])}), flush=True)


def checked(plan_path):
    plan = LOAD(plan_path)
    if (plan.get('schema') != 'genlaw.chamfer-sharded-plan.v1'
            or plan['source_code'] != file_binding(Path(__file__))
            or plan['dependencies'] != [file_binding(ROOT / p) for p in DEPENDENCIES]
            or plan['artificial_surface_caps'] is not False or plan['registration'] != 'none'):
        raise ValueError('Chamfer shard sources or representation changed')
    surface = checked_surface(checked_file(plan['surface']))
    reference = LOAD(checked_file(plan['reference']))
    cell, _, _, _ = checked_cell(checked_file(surface['plan']))
    for key, value in parameters(surface, reference, cell['grid']['resolution_mm']).items():
        if plan.get(key) != value:
            raise ValueError('Original-coordinate measurement plan or budget changed: '+key)
    for key in ['policy', 'prepared_source', 'native_cam']:
        checked_file(plan[key])
    if 'test_control' in plan:
        checked_file(plan['test_control'])
    validate_caches(surface, plan['caches'])
    return plan, surface, reference


class ChamferSections(ShardedSections):
    """Original section kernel with double-precision interval membership.

Cached vertices are exact float32 native output. Comparing a float32 height
array to a Python float can round a CAD break to float32 before the comparison,
dropping triangles across a thin but real interval. Promote before selection;
do not move a vertex, merge a height or widen the measurement tolerance.
"""
    def __init__(self, plan, low, high):
        pieces = []; count = 0
        for binding in plan['caches']:
            cache = LOAD(checked_file(binding))
            if cache['source_code'] != file_binding(ROOT / 'genlaw_pipeline/sharded_measure.py'):
                raise ValueError('Cached section program changed')
            array = np.load(checked_file(cache['array']), mmap_mode='r', allow_pickle=False)
            if array.shape != (cache['triangles'], 3, 3) or array.dtype != np.dtype('<f4'):
                raise ValueError('Section cache shape changed')
            zmin = array[:, :, 2].min(axis=1).astype(np.float64)
            zmax = array[:, :, 2].max(axis=1).astype(np.float64)
            selected = np.asarray(array[(zmin < high) & (zmax > low)], dtype=np.float64)
            count += len(selected)
            if count > MAX_ACTIVE_TRIANGLES:
                raise ValueError('Section interval exceeds active triangle budget')
            pieces.append(selected)
        self.triangles = np.concatenate(pieces)
        self.zmin = self.triangles[:, :, 2].min(axis=1)
        self.zmax = self.triangles[:, :, 2].max(axis=1)
        self.calls = 0
        self.maximum_observed_segments = 0


def sections(plan, low, high):
    return ChamferSections(plan, low, high)


def measure_part(plan_path, index, output):
    require_bounded_worker(); plan, _, reference = checked(plan_path)
    if type(index) is not int or not 0 <= index < len(plan['parts']):
        raise ValueError('Invalid height partition')
    start, end = plan['parts'][index]; levels = plan['levels_mm'][start:end+1]
    provider = sections(plan, levels[0], levels[-1])
    coarse = integrate_variable(provider, levels, target(reference), 2)
    refined = integrate_variable(provider, levels, target(reference), 3)
    write(output, {'schema': 'genlaw.chamfer-sharded-part.v1', 'plan': file_binding(plan_path),
        'index': index, 'interval': [start, end], 'coarse': coarse, 'refined': refined,
        'active_triangles': len(provider.triangles), 'maximum_observed_segments': provider.maximum_observed_segments,
        'source_code': file_binding(Path(__file__))})


def checked_rows(paths, plan_path, plan, schema, count):
    if len(paths) != count:
        raise ValueError('Missing measurement partitions')
    rows = []
    for index, path in enumerate(paths):
        row = LOAD(path)
        if (row.get('schema') != schema or row['plan'] != file_binding(plan_path)
                or row['source_code'] != plan['source_code'] or type(row['index']) is not int or row['index'] != index):
            raise ValueError('Missing, duplicate, reordered or foreign measurement partition')
        rows.append(row)
    return rows


def finish(plan_path, paths, output):
    require_bounded_worker(); plan, surface, reference = checked(plan_path)
    rows = checked_rows(paths, plan_path, plan, 'genlaw.chamfer-sharded-part.v1', len(plan['parts']))
    if [row['interval'] for row in rows] != plan['parts']:
        raise ValueError('Height intervals changed')
    coarse = combine_volumes([row['coarse'] for row in rows], 2)
    refined = combine_volumes([row['refined'] for row in rows], 3)
    delta = abs(coarse['iou']-refined['iou'])
    if delta > 1e-5 or abs(refined['actual_volume_mm3']-surface['volume_mm3']) > 1e-4:
        raise ValueError('Section integration failed unchanged volume/stability checks')
    if abs(refined['target_volume_mm3']-reference['volume_mm3']) > 1e-5:
        raise ValueError('Integrated target differs from native original CAD')
    write(output, {'schema': 'genlaw.chamfer-sharded-iou.v1', 'status': 'measured_numerical_estimate',
        'iou': refined['iou'], 'iou_percent': refined['iou']*100, 'volumes': refined,
        'missing_material_mm3': refined['target_volume_mm3']-refined['intersection_mm3'],
        'excess_material_mm3': refined['actual_volume_mm3']-refined['intersection_mm3'],
        'absolute_iou_refinement_difference': delta, 'plan': file_binding(plan_path),
        'surface': plan['surface'], 'reference': plan['reference'], 'parts': [file_binding(p) for p in paths],
        'source_code': plan['source_code'], 'actual_surface_volume_mm3': surface['volume_mm3'],
        'all_vertex_height_intervals_preserved': True, 'per_window_iou_averaged': False,
        'registration': 'none; original coordinates', 'controls': controls(),
        'full_tolerance_acceptance': False, 'production_approved': False})
    print(json.dumps({'streamed_iou_percent': refined['iou']*100}), flush=True)


def section(plan_path, index, output):
    require_bounded_worker(); plan, _, reference = checked(plan_path)
    if type(index) is not int or not 0 <= index < len(plan['heights']):
        raise ValueError('Invalid chamfer section index')
    height = plan['heights'][index]; z = height['z_mm']; levels = plan['levels_mm']
    slot = bisect.bisect_right(levels, z)-1
    if not 0 <= slot < len(levels)-1:
        raise ValueError('Section outside original global height intervals')
    provider = sections(plan, levels[slot], levels[slot+1])
    actual = provider.at(z); obj = target(reference); expected = obj.at(z); lo, hi = obj.low, obj.high
    contour = {**height, 'expected_topology': topology(expected), 'observed_topology': topology(actual),
        'topology_matches': topology(actual) == topology(expected),
        'boundary_deviation_mm': actual.boundary.hausdorff_distance(expected.boundary) if not actual.is_empty else None,
        'missing_material_area_mm2': expected.difference(actual).area, 'excess_material_area_mm2': actual.difference(expected).area}
    midline = None
    if height['region'] == 'bevel':
        xline = actual.intersection(LineString([(lo[0]-1, (lo[1]+hi[1])/2), (hi[0]+1, (lo[1]+hi[1])/2)]))
        yline = actual.intersection(LineString([((lo[0]+hi[0])/2, lo[1]-1), ((lo[0]+hi[0])/2, hi[1]+1)]))
        valid = all(g.geom_type == 'LineString' and not g.is_empty for g in [xline, yline])
        midline = {'z_mm': z, 'status': 'measured' if valid else 'ambiguous_midline',
            'insets_mm': {'xmin': xline.bounds[0]-lo[0], 'xmax': hi[0]-xline.bounds[2],
                         'ymin': yline.bounds[1]-lo[1], 'ymax': hi[1]-yline.bounds[3]} if valid else None}
    write(output, {'schema': 'genlaw.chamfer-sharded-section.v1', 'plan': file_binding(plan_path),
        'index': index, 'contour': contour, 'midline': midline, 'source_code': plan['source_code']})


def ray_group(plan_path, index, output):
    require_bounded_worker(); plan, surface, _ = checked(plan_path)
    if type(index) is not int or not 0 <= index < len(plan['ray_groups']):
        raise ValueError('Invalid ray group')
    start, end = plan['ray_groups'][index]; hits = {p['id']: [] for p in plan['probes']}
    for row in surface['windows'][start:end]:
        bounds = LOAD(checked_file(row['audit']))['bounds']
        if bounds is None:
            continue
        probes = [p for p in plan['probes'] if all(bounds['min'][k]-1e-8 <= p['xy_mm'][k] <= bounds['max'][k]+1e-8 for k in range(2))]
        if not probes:
            continue
        result = LOAD(checked_file(row['result']))
        tri = triangles(checked_file(result['surface'])).astype(np.float64)
        for p in probes:
            hits[p['id']].extend(ray_hits(tri, p['xy_mm'], plan['ray_z_bounds_mm']))
    write(output, {'schema': 'genlaw.chamfer-sharded-rays.v1', 'plan': file_binding(plan_path),
        'index': index, 'interval': [start, end],
        'hits': [{'id': p['id'], 'surface_z_mm': sorted(set(hits[p['id']]))} for p in plan['probes']],
        'source_code': plan['source_code']})


def verify(plan_path, rays, slices, output):
    require_bounded_worker(); plan, surface, reference = checked(plan_path)
    rr = checked_rows(rays, plan_path, plan, 'genlaw.chamfer-sharded-rays.v1', len(plan['ray_groups']))
    sr = checked_rows(slices, plan_path, plan, 'genlaw.chamfer-sharded-section.v1', len(plan['heights']))
    hits = {p['id']: [] for p in plan['probes']}
    for i, row in enumerate(rr):
        if row['interval'] != plan['ray_groups'][i] or [p['id'] for p in row['hits']] != list(hits):
            raise ValueError('Incomplete or reordered retained-solid rays')
        for p in row['hits']:
            hits[p['id']].extend(p['surface_z_mm'])
    for row, height in zip(sr, plan['heights']):
        if {key: row['contour'][key] for key in height} != height:
            raise ValueError('Observed section height changed')
    obj = target(reference); lo, hi = obj.low, obj.high
    contours = [row['contour'] for row in sr]; midlines = [row['midline'] for row in sr if row['midline'] is not None]
    if len(midlines) != 11:
        raise ValueError('Missing chamfer midlines')
    widths = []; width_complete = all(row['status'] == 'measured' for row in midlines)
    if width_complete:
        zs = np.array([row['z_mm']-hi[2] for row in midlines])
        for edge in ['xmin', 'xmax', 'ymin', 'ymax']:
            values = np.array([row['insets_mm'][edge] for row in midlines]); slope, intercept = np.polyfit(zs, values, 1)
            widths.append({'edge': edge, 'extrapolated_top_width_mm': float(intercept), 'nominal_width_mm': obj.width,
                'width_error_mm': abs(float(intercept)-obj.width),
                'max_fit_residual_mm': float(max(abs(values-(slope*zs+intercept)))),
                'estimated_angle_degrees': math.degrees(math.atan2(1, float(slope))), 'angle_manufacturing_acceptance': False})
    probes = []
    for p in plan['probes']:
        zs = sorted(set(hits[p['id']])); valid = len(zs) == 2
        probes.append({'xy_mm': p['xy_mm'], 'surface_z_mm': zs, 'status': 'measured' if valid else 'ambiguous_intersections',
            'thickness_mm': zs[-1]-zs[0] if valid else None,
            'plane_deviation_mm': max(abs(zs[0]-lo[2]), abs(zs[-1]-hi[2])) if valid else None})
    complete = all(p['status'] == 'measured' for p in probes)
    contours_complete = all(r['boundary_deviation_mm'] is not None for r in contours)
    thickness = [p['thickness_mm'] for p in probes] if complete else []
    item = {'sections': contours, 'section_count': len(contours), 'bevel_section_count': 11,
        'contours_complete': contours_complete, 'topology_matches': all(r['topology_matches'] for r in contours),
        'max_boundary_deviation_mm': max(r['boundary_deviation_mm'] for r in contours) if contours_complete else None,
        'midlines': midlines, 'widths': widths, 'width_complete': width_complete,
        'max_width_deviation_mm': max(max(r['width_error_mm'], r['max_fit_residual_mm']) for r in widths) if width_complete else None,
        'probes': probes, 'probe_count': 25, 'complete': complete,
        'thickness_band_mm': [min(thickness), max(thickness)] if complete else None,
        'max_plane_deviation_mm': max([p['plane_deviation_mm'] for p in probes]+
            [abs(surface['bounds']['min'][2]-lo[2]), abs(surface['bounds']['max'][2]-hi[2])]) if complete else None,
        'resolution_mm': plan['resolution_mm'], 'numerical_guard_mm': 2*plan['resolution_mm'],
        'guard_is_certified_uncertainty': False, 'registration_applied': False, 'disconnected_material_discarded': False}
    fid = reference['features'][0]['id']; policy = LOAD(checked_file(plan['policy']))
    rows = [{'feature_id': fid, 'kind': kind, **evaluate(kind, item, rule)} for kind, rule in policy['features'][fid].items()]
    write(output, {'schema': 'genlaw.chamfer-sharded-verification.v1', 'status': 'measured',
        'observations': {'features': {fid: item}, 'unassigned_contours_detected': not item['topology_matches']},
        'software_regression': rows, 'all_software_geometry_checks_passed': all(r['status'] == 'numeric_pass' for r in rows),
        'plan': file_binding(plan_path), 'ray_parts': [file_binding(p) for p in rays],
        'section_parts': [file_binding(p) for p in slices], 'source_code': plan['source_code'],
        **({'test_only_guard_bypass': True, 'control_binding': plan['test_control'],
            'normal_guard_rejection': plan['normal_guard_rejection']} if 'test_control' in plan else {}),
        'full_tolerance_acceptance': False, 'production_approved': False, 'whole_process_card_complete': False})


def main():
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest='mode', required=True)
    start = sub.add_parser('plan')
    for name in ['surface', 'reference', 'policy', 'caches', 'output']:
        start.add_argument('--'+name, required=True, type=Path)
    start.add_argument('--control-binding', type=Path)
    for name in ['part', 'section', 'rays']:
        p = sub.add_parser(name); p.add_argument('--plan', required=True, type=Path)
        p.add_argument('--index', required=True, type=int); p.add_argument('--output', required=True, type=Path)
    for name in ['finish', 'verify']:
        p = sub.add_parser(name)
        for key in ['plan', 'directory', 'output']:
            p.add_argument('--'+key, required=True, type=Path)
    a = parser.parse_args()
    if a.mode == 'plan':
        make_plan(a.surface, a.reference, a.policy, sorted(a.caches.glob('window-*/sections.json')), a.output, a.control_binding)
    elif a.mode in ['part', 'section', 'rays']:
        {'part': measure_part, 'section': section, 'rays': ray_group}[a.mode](a.plan, a.index, a.output)
    elif a.mode == 'finish':
        finish(a.plan, sorted(a.directory.glob('part-[0-9][0-9][0-9].json')), a.output)
    else:
        verify(a.plan, sorted(a.directory.glob('rays-[0-9][0-9][0-9].json')),
               sorted(a.directory.glob('section-[0-9][0-9][0-9].json')), a.output)


if __name__ == '__main__':
    main()
