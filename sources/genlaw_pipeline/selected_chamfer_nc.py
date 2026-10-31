"""Check every native NC move against the requested open, parallel top edges."""
import math
from .milling_nc import EPS
from .sequence_nc import native_header, segments


def edge_path(plan, side):
    spec = plan['specification']; lo, hi = spec['stock']['min'], spec['stock']['max']
    axis = 'xy'.index(side[0]); along = 1-axis
    offset = plan['tool']['tip_diameter_mm']/2+plan['parameters']['extra_depth_mm']
    coordinate = lo[axis]-offset if side.endswith('min') else hi[axis]+offset
    a, b = [lo[0], lo[1], plan['final_tip_z_mm']], [hi[0], hi[1], plan['final_tip_z_mm']]
    a[axis] = b[axis] = coordinate
    return a, b, along


def check_nc(text, plan):
    if plan.get('schema') != 'genlaw.selected-chamfer-execution-plan.v1':
        raise ValueError('Unsupported selected-edge execution plan')
    items, blocks = segments(text, native_header(plan)); p = plan['parameters']
    if len(items) != 2 or [s['label'] for s in items] != ['TC SelectedChamfer T1', 'SelectedChamferEdges']:
        raise ValueError('Require one declared cutter and one selected-edge operation')
    if items[0]['blocks'] != [[('M', 5)], [('M', 6), ('T', 1)], [('M', 3), ('S', p['spindle_rpm'])]]:
        raise ValueError('Native tool change or spindle differs from the card')
    expected = {s: edge_path(plan, s) for s in plan['specification']['selected_sides']}
    tolerance = 2*p['path_tolerance_mm']+2e-5; final = plan['final_tip_z_mm']
    position = [None, None, p['clearance_height_mm']]
    phase = 'travel'; current = None; cutting_start = None; completed = []; trace = []; elapsed = 0.
    length = 0.; cuts = 0; plunges = 0
    for index, block in enumerate(items[1]['blocks'], items[1]['first_executable_block']):
        if len({k for k, v in block}) != len(block): raise ValueError('Duplicate NC word')
        words = dict(block); code = words.get('G')
        if code not in (0, 1) or set(words)-set('GXYZF') or not any(a in words for a in 'XYZ'):
            raise ValueError('Only explicit straight selected-edge motions are supported')
        end = [words.get(a, v) for a, v in zip('XYZ', position)]
        if end[2] is None or not final-EPS <= end[2] <= p['clearance_height_mm']+EPS:
            raise ValueError('Native Z leaves the declared cutting/clearance envelope')
        lateral = any(a in words and (position[i] is None or abs(end[i]-position[i]) > EPS) for i, a in enumerate('XY'))
        vertical = abs(end[2]-position[2]) > EPS
        if code == 0:
            if 'F' in words: raise ValueError('Unexpected rapid-feed override')
            if end[2] < p['safe_height_mm']-EPS or lateral and min(position[2], end[2]) < p['safe_height_mm']-EPS:
                raise ValueError('Rapid move intersects the declared safe plane')
            if phase == 'cut':
                if lateral or cuts != 1 or current is None:
                    raise ValueError('Retract before exactly one complete selected edge')
                a, b, along = expected[current]
                if abs(length-abs(b[along]-a[along])) > tolerance:
                    raise ValueError('Incomplete selected edge length')
                completed.append(current); current = None; phase = 'travel'
        else:
            if any(v is None for v in position+end) or 'F' not in words or lateral == vertical:
                raise ValueError('Require initialized XYZ, explicit feed and planar/vertical motion')
            if abs(words['F']-(p['feed_mm_min'] if lateral else p['plunge_mm_min'])) > EPS:
                raise ValueError('Native feed differs from the resource attachment')
            if abs(end[2]-final) > EPS: raise ValueError('Wrong selected-edge cutting depth')
            if not lateral:
                if phase != 'travel' or position[2] < p['safe_height_mm']-EPS:
                    raise ValueError('Require a safe vertical entry for each selected edge')
                candidates = [s for s, (a, b, _) in expected.items()
                    if min(math.dist(end, a), math.dist(end, b)) <= tolerance]
                if len(candidates) != 1 or candidates[0] in completed:
                    raise ValueError('Entry is not an endpoint of an unprocessed selected edge')
                current = candidates[0]; cutting_start = list(end); phase = 'cut'
                length = 0.; cuts = 0; plunges += 1; distance = abs(end[2]-position[2])
            else:
                if phase != 'cut' or current is None or abs(position[2]-final) > EPS or cuts:
                    raise ValueError('Additional or unplanned edge cutting move')
                a, b, along = expected[current]
                correct = ((math.dist(cutting_start, a) <= tolerance and math.dist(end, b) <= tolerance)
                           or (math.dist(cutting_start, b) <= tolerance and math.dist(end, a) <= tolerance))
                if not correct or abs(end[1-along]-position[1-along]) > tolerance:
                    raise ValueError('Cutter leaves the selected original edge offset')
                distance = math.dist(position, end); length += distance; cuts += 1
            elapsed += 60*distance/words['F']
        trace.append({'block': index, 'motion': 'G'+str(int(code)), 'start': list(position),
            'end': list(end), 'feed_mm_min': words.get('F'), 'feed_time_end_seconds': elapsed,
            'selected_side': current})
        position = end
    if (phase != 'travel' or set(completed) != set(expected) or len(completed) != len(expected)
            or plunges != len(expected) or abs(position[2]-p['clearance_height_mm']) > EPS):
        raise ValueError('Missing/extra selected edge or no final clearance retract')
    return {'status': 'checked_native_selected_edges', 'NC_text_modified': False,
        'selected_sides': plan['specification']['selected_sides'], 'executed_sides': completed,
        'one_safe_entry_and_retract_per_edge': True, 'executable_blocks': len(blocks),
        'motion_trace': trace, 'feed_time_seconds': elapsed, 'rapid_time_not_included': True,
        'final_tip_z_mm': final, 'step_map': [{'step_id': 'OP20',
            'first_executable_block': items[1]['first_executable_block'],
            'last_executable_block': items[1]['last_executable_block']}],
        'scope': 'One or two parallel complete top edges, fixed Z, explicit finite 90 degree frustum; no general NC interpreter.'}
