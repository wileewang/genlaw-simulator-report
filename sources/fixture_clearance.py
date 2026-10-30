"""Continuous native linear sweeps of fixed-Z tool assemblies against boxes.

This optional observer does not remove material, infer fixtures or rewrite NC.
Clearance is certified only for the declared envelopes, with the native arc
linearizer's error conservatively included. Near-contact remains indeterminate.
"""
import math

SCHEMA = 'genlaw.fixture-clearance-model.v1'
FLOAT_GUARD_MM = 1e-6
MAX_PAIRS = 2_000_000


def finite(value, label, positive=False):
    if type(value) not in (float, int) or not math.isfinite(value) or abs(value) > 1e6 or (positive and value <= 0):
        raise ValueError('Finite bounded millimetres required: ' + label)
    return value


def vector(values, label):
    if not isinstance(values, list) or len(values) != 3:
        raise ValueError('Three coordinates required: ' + label)
    return [finite(v, label) for v in values]


def validate_model(model):
    fields = {'schema', 'units', 'frame', 'scope', 'minimum_clearance_mm', 'basis', 'tools', 'fixtures'}
    if (not isinstance(model, dict) or set(model) != fields or model['schema'] != SCHEMA
            or model['units'] != 'mm' or model['frame'] != 'original_cad_material'
            or model['scope'] != 'modeled_tool_assembly_vs_static_fixtures'):
        raise ValueError('Explicit fixture-clearance model/version/frame/scope required')
    finite(model['minimum_clearance_mm'], 'minimum clearance', True)
    if not isinstance(model['basis'], str) or not model['basis'].strip():
        raise ValueError('Declared fixture and mounted-tool modeling basis required')
    if not isinstance(model['tools'], list) or not 1 <= len(model['tools']) <= 16:
        raise ValueError('Need 1 to 16 explicit mounted tools')
    tools = {}
    for tool in model['tools']:
        if not isinstance(tool, dict) or set(tool) != {'tool_number', 'gauge_to_tip_mm', 'segments'}:
            raise ValueError('Unknown or missing mounted-tool fields')
        number = tool['tool_number']; length = finite(tool['gauge_to_tip_mm'], 'mounted length', True)
        if type(number) is not int or not 1 <= number <= 99 or number in tools:
            raise ValueError('Duplicate or invalid mounted tool number')
        bands = tool['segments']
        if not isinstance(bands, list) or not 1 <= len(bands) <= 16:
            raise ValueError('Need 1 to 16 ordered axial tool segments')
        end = 0; ids = set()
        for i, band in enumerate(bands):
            if not isinstance(band, dict) or set(band) != {'id', 'role', 'z_min_mm', 'z_max_mm', 'radius_mm'}:
                raise ValueError('Unknown or missing tool-segment fields')
            ident = band['id']
            if not isinstance(ident, str) or not ident.strip() or ident in ids:
                raise ValueError('Unique tool-segment names required')
            ids.add(ident)
            if band['role'] not in ('cutting', 'shank', 'holder') or (i == 0) != (band['role'] == 'cutting'):
                raise ValueError('The first and only cutting segment must begin at the tip')
            lo, hi = [finite(band[k], k) for k in ('z_min_mm', 'z_max_mm')]
            finite(band['radius_mm'], 'segment radius', True)
            if lo != end or hi <= lo:
                raise ValueError('Axial segments must cover tip-to-gauge without gaps or overlap')
            end = hi
        if end != length:
            raise ValueError('Modeled assembly must end at the independently declared gauge length')
        tools[number] = tool
    fixtures = model['fixtures']
    if not isinstance(fixtures, list) or not 1 <= len(fixtures) <= 16:
        raise ValueError('Need 1 to 16 explicit static fixture boxes')
    ids = set()
    for fixture in fixtures:
        if not isinstance(fixture, dict) or set(fixture) != {'id', 'shape', 'bounds_mm'} or fixture['shape'] != 'box':
            raise ValueError('Only explicitly declared exact box solids are supported')
        ident = fixture['id']
        if not isinstance(ident, str) or not ident.strip() or ident in ids:
            raise ValueError('Unique fixture IDs required')
        ids.add(ident); bounds = fixture['bounds_mm']
        if not isinstance(bounds, dict) or set(bounds) != {'min', 'max'}:
            raise ValueError('Fixture bounds must have explicit minimum and maximum')
        lo, hi = [vector(bounds[k], k) for k in ('min', 'max')]
        if any(a >= b for a, b in zip(lo, hi)):
            raise ValueError('Fixture must have positive extent in all three dimensions')
    return tools


def parameter_interval(start, end, low, high):
    """Closed t interval where low <= start + t*(end-start) <= high."""
    if low > high:
        return None
    delta = end - start
    if delta == 0:
        return (0., 1.) if low <= start <= high else None
    a, b = sorted(((low-start)/delta, (high-start)/delta))
    lo, hi = max(0., a), min(1., b)
    return (lo, hi) if lo <= hi else None


def segment_rectangle_minimum(start, end, bounds, interval):
    """Exact piecewise-quadratic minimum; no temporal or spatial sampling."""
    low, high = interval; delta = [end[i]-start[i] for i in range(2)]
    knots = {low, high}
    for axis in range(2):
        if delta[axis] != 0:
            for edge in (bounds['min'][axis], bounds['max'][axis]):
                t = (edge-start[axis])/delta[axis]
                if low < t < high:
                    knots.add(t)
    ordered = sorted(knots); candidates = set(knots)
    for a, b in zip(ordered, ordered[1:]):
        middle = (a+b)/2; quadratic = linear = 0.
        for axis in range(2):
            value = start[axis] + middle*delta[axis]
            edge = (bounds['min'][axis] if value < bounds['min'][axis]
                    else bounds['max'][axis] if value > bounds['max'][axis] else None)
            if edge is not None:
                quadratic += delta[axis]**2
                linear += delta[axis]*(start[axis]-edge)
        if quadratic:
            candidates.add(max(a, min(b, -linear/quadratic)))
    def observed(t):
        point = [start[i]+t*(end[i]-start[i]) for i in range(3)]
        nearest = [max(bounds['min'][i], min(bounds['max'][i], point[i])) for i in range(2)]
        squared = sum((point[i]-nearest[i])**2 for i in range(2))
        return squared, t, point, nearest
    squared, t, point, nearest = min(observed(t) for t in candidates)
    return {'parameter': t, 'tip_mm': point, 'closest_fixture_xy_mm': nearest,
            'axis_to_rectangle_mm': math.sqrt(squared), 'minimum_squared_xy_mm2': squared,
            'axial_overlap_parameter_interval': list(interval), 'candidate_count': len(candidates)}


def cylinder_box_minimum(start, end, segment, bounds, radial_adjust=0., axial_adjust=0.):
    radius = segment['radius_mm'] + radial_adjust
    zlo = segment['z_min_mm'] - axial_adjust; zhi = segment['z_max_mm'] + axial_adjust
    if radius <= 0 or zlo >= zhi:
        return None
    interval = parameter_interval(start[2], end[2], bounds['min'][2]-zhi, bounds['max'][2]-zlo)
    if interval is None:
        return None
    result = segment_rectangle_minimum(start, end, bounds, interval)
    result.update(radial_gap_mm=result['axis_to_rectangle_mm']-radius,
                  effective_radius_mm=radius, effective_axial_interval_mm=[zlo, zhi])
    return result


def classify_sweep(start, end, segment, bounds, clearance_mm, arc_error_mm):
    """Outer clearance proof / inner interference witness / uncertain contact."""
    error = arc_error_mm + FLOAT_GUARD_MM
    outer = cylinder_box_minimum(start, end, segment, bounds, clearance_mm+error, clearance_mm+error)
    if outer is None or outer['radial_gap_mm'] > 0:
        return {'status': 'clear', 'outer_envelope': outer}
    inner = cylinder_box_minimum(start, end, segment, bounds, -error, -error)
    if inner is not None and inner['radial_gap_mm'] < 0:
        return {'status': 'collision', 'witness': inner, 'outer_envelope': outer}
    return {'status': 'indeterminate', 'reason': 'Requested clearance or contact cannot be decided inside the conservative error envelope.',
            'outer_envelope': outer}


def validate_path(path, model, material_origin_mm):
    tools = validate_model(model); origin = vector(material_origin_mm, 'material placement')
    if (path.get('schema') != 'genlaw.native-toolpath.v1' or path.get('units') != 'mm'
            or path.get('frame') != 'original_cad_material_tip' or path.get('nc_executed') is not True
            or path.get('surface_computed') is not False or path.get('native_max_arc_error_mm') != .01):
        raise ValueError('Need a complete bound native metric material-tip path with its arc error')
    moves = path.get('moves')
    if not isinstance(moves, list) or not 1 <= len(moves) <= 200000:
        raise ValueError('Missing or excessive native path moves')
    last = None; loaded = set(); counts = {n: 0 for n in tools}
    for index, move in enumerate(moves):
        if move.get('index') != index or type(move.get('line')) is not int or move['line'] < 0:
            raise ValueError('Native path indices or original NC line numbers are incomplete')
        n = move.get('tool_number')
        if type(n) is not int or n < -1 or n == 0 or (n > 0 and n not in tools):
            raise ValueError('Unknown mounted tool or empty-spindle representation')
        if move.get('type') not in ('RAPID', 'CUTTING') or (n == -1 and move['type'] != 'RAPID'):
            raise ValueError('Unknown native motion or unloaded cutting')
        length = tools[n]['gauge_to_tip_mm'] if n > 0 else 0.
        for suffix in ('start_mm', 'end_mm'):
            tip = vector(move[suffix], 'native tip'); gauge = vector(move['gauge_'+suffix], 'native gauge')
            if any(abs(gauge[i]-origin[i]-(length if i == 2 else 0)-tip[i]) > 1e-8 for i in range(3)):
                raise ValueError('Native gauge and tip do not match original material placement and actual mounted length')
        if last is not None and any(abs(a-b) > 1e-8 for a, b in zip(last['gauge_end_mm'], move['gauge_start_mm'])):
            raise ValueError('Native gauge path has a discontinuity')
        if n > 0:
            loaded.add(n); counts[n] += 1
        last = move
    if loaded != set(tools):
        raise ValueError('Declared tool models must cover exactly all loaded native tools')
    pairs = sum(counts[n]*len(tools[n]['segments'])*len(model['fixtures']) for n in counts)
    if pairs > MAX_PAIRS:
        raise ValueError('Continuous collision pair budget exceeded before geometric evaluation')
    return tools, pairs


def inspect_path(path, model, material_origin_mm):
    tools, pairs = validate_path(path, model, material_origin_mm)
    counts = {'clear': 0, 'collision': 0, 'indeterminate': 0}; findings = {}; tested = 0
    for move in path['moves']:
        if move['tool_number'] < 0:
            continue
        for segment in tools[move['tool_number']]['segments']:
            for fixture in model['fixtures']:
                result = classify_sweep(move['start_mm'], move['end_mm'], segment, fixture['bounds_mm'],
                                        model['minimum_clearance_mm'], path['native_max_arc_error_mm'])
                status = result['status']; counts[status] += 1; tested += 1
                if status == 'clear':
                    continue
                key = (fixture['id'], move['tool_number'], segment['id'], status)
                if key not in findings:
                    findings[key] = {'fixture_id': fixture['id'], 'tool_number': move['tool_number'],
                                     'segment_id': segment['id'], 'role': segment['role'], 'status': status,
                                     'native_motion_count': 0, 'first_motion_index': move['index'],
                                     'first_nc_line': move['line'], 'first_motion_type': move['type'],
                                     'first_observation': result}
                findings[key]['native_motion_count'] += 1
    if tested != pairs:
        raise ValueError('Incomplete motion/component/fixture coverage')
    status = 'collision' if counts['collision'] else 'indeterminate' if counts['indeterminate'] else 'clear'
    return {'schema': 'genlaw.fixture-clearance-observations.v1', 'status': status,
            'modeled_clearance_passed': status == 'clear', 'pair_counts': counts, 'expected_pairs': pairs,
            'tested_pairs': tested, 'native_motion_count': len(path['moves']),
            'unloaded_spindle_moves_excluded': sum(m['tool_number'] < 0 for m in path['moves']),
            'findings': list(findings.values()), 'minimum_clearance_mm': model['minimum_clearance_mm'],
            'native_arc_error_mm': path['native_max_arc_error_mm'], 'floating_guard_mm': FLOAT_GUARD_MM,
            'continuous_linear_sweeps_checked': True, 'endpoint_only_sampling': False,
            'scope': model['scope'], 'manufacturing_approved': False,
            'limits': ['Only the explicitly modeled static boxes and fixed-Z cylindrical tool assembly are checked.',
                       'Native arc chord error is included conservatively; near contact or insufficient clearance stays indeterminate.',
                       'All loaded motion start/end poses are checked; physical tool-changer exchange motion is not modeled.',
                       'Spindle/machine bodies, clamping force, evolving workpiece versus noncutting tool parts and five-axis motion are not modeled.']}
