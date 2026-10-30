"""Standard-library input contract for the frozen v1 planar observer.

Kept separate so native FreeCAD preparation does not import mesh libraries.
The frozen observer validates its own contract again against native CAD.
"""
import math


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
