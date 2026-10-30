"""Measure declared dimensions from actual planar contours in original CAD axes.

Nominal CAD selects features and end-arc regions; it never supplies measured
coordinates or radii. No registration, contour repair or component removal.
"""
import math
import numpy as np
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import linemerge
from .verification import fit_ring, match_rings

POLICY = {
    'samples_per_fitted_arc': 256,
    'minimum_arc_coverage_degrees': 150,
    'maximum_fit_condition_number': 1_000_000,
    'span_guard_cells': 4,
    'two_anchor_distance_guard_cells': 4,
    'radius_guard_cells': 2,
    'guard_is_certified_uncertainty': False,
}
KINDS = {'support_span', 'end_radius', 'center_pitch', 'end_offset'}


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Dimension values must be finite numbers')
    return float(value)


def features(reference):
    candidates = [reference['profile_feature']] if 'profile_feature' in reference else [
        f for f in reference['features'] if f['kind'] == 'capsule_outline']
    if len(candidates) != 1:
        raise ValueError('Exactly one original capsule outline is required')
    profile = candidates[0]
    if (profile['kind'] != 'capsule_outline' or profile['major_axis'] not in ('X', 'Y')
            or not finite(profile['length_mm']) > finite(profile['width_mm']) > 0):
        raise ValueError('Only a noncircular X/Y capsule is supported')
    holes = [f for f in reference['features'] if f['kind'] == 'through_hole']
    if any(f['kind'] not in ('through_hole', 'capsule_outline') for f in reference['features']):
        raise ValueError('Unsupported original feature in dimension inspection')
    if len({f['id'] for f in holes + [profile]}) != len(holes) + 1:
        raise ValueError('Duplicate original feature ID')
    return profile, holes


def compile_requirements(request, reference):
    if (request.get('schema') != 'genlaw.planar-dimension-requirements.v1'
            or request.get('units') != 'mm' or request.get('cad_sha256') != reference['cad_sha256']
            or request.get('measurement_policy') != POLICY):
        raise ValueError('Dimension requirements must bind the original CAD and fixed policy')
    profile, holes = features(reference)
    byid = {f['id']: f for f in holes}
    major = 'XY'.index(profile['major_axis'])
    items = request.get('items')
    if not isinstance(items, list) or not 1 <= len(items) <= 64:
        raise ValueError('Need a bounded nonempty dimension set')
    seen = set()
    for item in items:
        if not isinstance(item.get('id'), str) or not item['id'] or item['id'] in seen:
            raise ValueError('Missing or duplicate requirement ID')
        seen.add(item['id'])
        kind = item['kind']
        if kind not in KINDS or item['profile_id'] != profile['id']:
            raise ValueError('Unknown dimension kind or outer-profile binding')
        if item.get('axis') not in ('X', 'Y'):
            raise ValueError('A fixed original X/Y axis is required')
        axis = 'XY'.index(item['axis'])
        span = profile['length_mm'] if axis == major else profile['width_mm']
        if kind == 'support_span':
            nominal = span
        elif kind == 'end_radius':
            if axis != major or item.get('side') not in ('low', 'high'):
                raise ValueError('End radius requires an explicit capsule end')
            nominal = profile['width_mm'] / 2
        elif kind == 'center_pitch':
            ids = item.get('hole_ids', [])
            if len(ids) != 2 or ids[0] == ids[1] or any(i not in byid for i in ids):
                raise ValueError('Pitch requires two distinct original holes')
            nominal = byid[ids[1]]['center_xy_mm'][axis] - byid[ids[0]]['center_xy_mm'][axis]
        else:
            if axis != major or item.get('side') not in ('low', 'high') or item.get('hole_id') not in byid:
                raise ValueError('Offset requires a named original hole and capsule end')
            center = byid[item['hole_id']]['center_xy_mm'][axis]
            nominal = (center - (profile['center_xy_mm'][axis] - span / 2) if item['side'] == 'low'
                       else profile['center_xy_mm'][axis] + span / 2 - center)
        if not math.isclose(finite(item['nominal_mm']), nominal, rel_tol=0, abs_tol=1e-8):
            raise ValueError('Dimension nominal disagrees with original CAD and declared datum')
        rule = item.get('acceptance')
        if rule is not None:
            if (rule.get('schema') != 'genlaw.numeric-acceptance.v1' or rule.get('units') != 'mm'
                    or rule.get('scope') not in ('engineering_requirement', 'software_regression')
                    or rule.get('method') != kind or not isinstance(rule.get('basis'), str) or not rule['basis']
                    or finite(rule['min_mm']) > finite(rule['max_mm'])):
                raise ValueError('Unsupported, missing or reversed dimension criterion')
    return profile, holes


def arc_observation(outer, profile, side, resolution):
    """Select an original-CAD end region, then fit only actual boundary points."""
    axis = 'XY'.index(profile['major_axis'])
    split = profile['center_xy_mm'][axis] + (-1 if side == 'low' else 1) * (
        profile['length_mm'] - profile['width_mm']) / 2
    low, high = list(outer.bounds[:2]), list(outer.bounds[2:])
    margin = max(profile['length_mm'], profile['width_mm'])
    low = [v - margin for v in low]; high = [v + margin for v in high]
    if side == 'low': high[axis] = split
    else: low[axis] = split
    curve = outer.exterior.intersection(box(*low, *high))
    if curve.geom_type == 'MultiLineString': curve = linemerge(curve)
    result = {'selection_axis': profile['major_axis'], 'selection_side': side,
              'original_split_coordinate_mm': split, 'status': 'not_measurable'}
    if curve.geom_type != 'LineString' or curve.is_empty or curve.length <= 0:
        return result | {'reason': 'Missing, disconnected or degenerate actual end contour'}
    count = POLICY['samples_per_fitted_arc']
    points = np.array([curve.interpolate(i / (count - 1), normalized=True).coords[0][:2] for i in range(count)])
    origin = points.mean(axis=0); local = points - origin
    scale = float(np.linalg.norm(local, axis=1).max())
    if scale <= 1e-12:
        return result | {'reason': 'Degenerate end-arc extent'}
    local /= scale
    matrix = np.column_stack((2 * local, np.ones(count)))
    solution, _, rank, singular = np.linalg.lstsq(matrix, np.sum(local * local, axis=1), rcond=None)
    condition = float(singular[0] / singular[-1]) if singular[-1] > 0 else None
    if rank != 3 or condition is None or condition > POLICY['maximum_fit_condition_number']:
        return result | {'reason': 'Actual arc does not determine a stable circle', 'fit_rank': int(rank)}
    center = solution[:2] * scale + origin
    radii = np.linalg.norm(points - center, axis=1)
    angles = np.sort(np.arctan2(points[:, 1] - center[1], points[:, 0] - center[0]))
    coverage = 2 * math.pi - max(np.diff(np.r_[angles, angles[0] + 2 * math.pi]))
    vertices = np.asarray(curve.coords)[:, :2]
    band = [float(curve.distance(Point(*center))), float(np.linalg.norm(vertices - center, axis=1).max())]
    result |= {'fit_center_xy_mm': center.tolist(), 'fit_radius_mm': float(radii.mean()),
               'radial_band_mm': band, 'radial_rms_mm': float(np.sqrt(np.mean((radii - radii.mean()) ** 2))),
               'coverage_degrees': math.degrees(coverage), 'fit_condition_number': condition,
               'samples': count, 'actual_curve_length_mm': curve.length}
    if math.degrees(coverage) < POLICY['minimum_arc_coverage_degrees']:
        return result | {'reason': 'Insufficient measured arc coverage'}
    if band[1] - band[0] > 4 * resolution:
        return result | {'reason': 'Actual end is not circular within the declared grid-scale form check'}
    return result | {'status': 'measured'}


def observe_section(actual, reference, resolution):
    profile, holes = features(reference)
    if not finite(resolution) > 0:
        raise ValueError('A positive material-grid resolution is required')
    pieces = list(actual.geoms) if actual.geom_type == 'MultiPolygon' else [actual]
    result = {'body_count': len(pieces), 'expected_holes': len(holes), 'complete_topology': False,
              'registration_applied': False, 'components_discarded': False, 'holes': {}, 'ends': {}}
    if len(pieces) != 1 or pieces[0].geom_type != 'Polygon' or actual.is_empty or not actual.is_valid:
        return result | {'reason': 'Actual section is not one valid complete material body'}
    part = pieces[0]; outer = Polygon(part.exterior); rings = list(part.interiors)
    matched = match_rings(rings, holes, resolution)
    result |= {'support_bounds_xy_mm': list(outer.bounds), 'observed_holes': len(rings),
               'complete_topology': len(rings) == len(holes) == len(matched),
               'unassigned_contours': len(rings) - len(matched)}
    for hole in holes:
        key = hole['id']; row = {'status': 'not_measurable', 'reason': 'Missing or ambiguous hole association'}
        if key in matched:
            row = fit_ring(rings[matched[key]])
            lo, hi = row['radial_diameter_band_mm']
            row['status'] = 'measured' if hi - lo <= 4 * resolution else 'not_measurable'
            if row['status'] != 'measured': row['reason'] = 'Hole contour is not circular at the grid scale'
        result['holes'][key] = row
    result['ends'] = {side: arc_observation(outer, profile, side, resolution) for side in ('low', 'high')}
    return result


def dimension_band(item, observation):
    kind = item['kind']; axis = 'XY'.index(item['axis'])
    bounds = observation.get('support_bounds_xy_mm')
    if bounds is None:
        return None
    if kind == 'support_span':
        value = bounds[axis + 2] - bounds[axis]
    elif kind == 'end_radius':
        row = observation['ends'][item['side']]
        return row['radial_band_mm'] if row['status'] == 'measured' else None
    elif kind == 'center_pitch':
        rows = [observation['holes'][key] for key in item['hole_ids']]
        if any(r['status'] != 'measured' for r in rows): return None
        value = rows[1]['fit_center_xy_mm'][axis] - rows[0]['fit_center_xy_mm'][axis]
    else:
        row = observation['holes'][item['hole_id']]
        if row['status'] != 'measured': return None
        value = (row['fit_center_xy_mm'][axis] - bounds[axis] if item['side'] == 'low'
                 else bounds[axis + 2] - row['fit_center_xy_mm'][axis])
    return [float(value), float(value)]


def evaluate_band(band, rule, guard):
    if rule is None: return {'status': 'pending_criteria'}
    if band is None: return {'status': 'not_measurable'}
    low, high = band; minimum, maximum = rule['min_mm'], rule['max_mm']
    if low - guard >= minimum - 1e-10 and high + guard <= maximum + 1e-10:
        status = 'numeric_pass'
    elif low + guard < minimum - 1e-10 or high - guard > maximum + 1e-10:
        status = 'numeric_fail'
    else:
        status = 'indeterminate'
    return {'status': status, 'allowed_band_mm': [minimum, maximum],
            'guarded_observed_band_mm': [low - guard, high + guard]}


def evaluate_dimensions(request, reference, sections, resolution):
    compile_requirements(request, reference)
    if len(sections) != 11:
        raise ValueError('All eleven declared interior sections are required')
    rows = []
    for item in request['items']:
        values = [dimension_band(item, section) for section in sections]
        complete = all(value is not None for value in values)
        band = [min(v[0] for v in values), max(v[1] for v in values)] if complete else None
        cells = POLICY['radius_guard_cells'] if item['kind'] == 'end_radius' else (
            POLICY['span_guard_cells'] if item['kind'] == 'support_span' else POLICY['two_anchor_distance_guard_cells'])
        guard = cells * resolution
        rows.append({**item, 'observed_band_mm': band, 'section_bands_mm': values,
                     'complete': complete, 'numerical_guard_mm': guard,
                     **evaluate_band(band, item.get('acceptance'), guard)})
    topology = all(section['complete_topology'] for section in sections)
    return {'requirements': rows, 'all_declared_dimensions_passed': all(r['status'] == 'numeric_pass' for r in rows),
            'topology_matches_original': topology,
            'dimension_inspection_passed': topology and all(r['status'] == 'numeric_pass' for r in rows),
            'registration_applied': False, 'measurement_policy': POLICY, 'resolution_mm': resolution,
            'manufacturing_acceptance': False, 'production_approved': False, 'whole_process_card_complete': False}
