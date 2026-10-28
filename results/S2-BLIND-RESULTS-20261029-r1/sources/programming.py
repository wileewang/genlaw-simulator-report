"""Contracts shared by the single-hole CAM adapter and simulation runner."""
import math
from pathlib import Path
import re

from .preparation import canonical, digest, load_json, prepare
from .drilling_geometry import operations, operation_parameters


def checked_plan(input_path, result_path):
    """Bind the persisted plan to the current source files before native work."""
    supplied = load_json(Path(result_path))
    current = prepare(input_path)
    if current['status'] != 'ready_for_declared_scope':
        raise ValueError('Preparation is blocked: ' + current['status'])
    for field in ('schema', 'status', 'input_manifest_sha256',
                  'execution_plan_sha256', 'execution_plan', 'input_bindings'):
        if canonical(supplied.get(field)) != canonical(current.get(field)):
            raise ValueError('Preparation result changed or stale: ' + field)
    return current['execution_plan']


def nc_words(text):
    """Parse only the small, declared postprocessor dialect; no general interpreter."""
    lines = []
    for raw in text.splitlines():
        if raw.count('(') != raw.count(')') or raw.count('(') > 1:
            raise ValueError('Invalid NC comment')
        line = re.sub(r'\([^()]*\)', '', raw).strip()
        if not line:
            continue
        words = re.findall(r'([A-Z])\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))', line)
        if re.sub(r'[A-Z]\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)', '', line).strip():
            raise ValueError('Unsupported NC syntax: ' + line)
        if not words:
            raise ValueError('Empty NC block')
        values = [(letter, float(value)) for letter, value in words]
        if not all(math.isfinite(value) for _, value in values):
            raise ValueError('Non-finite NC word')
        lines.append(values)
    return lines


def check_nc(text, plan):
    """Check every executable word against this adapter's explicit XYZ/G81 trace.

    Changes to units, mode, offsets, cycles, feeds, positions or extra blocks are
    rejected. The original text is never rewritten before simulation.
    """
    p = plan['parameters']
    expected = [
        [('G',17),('G',54),('G',40),('G',49),('G',80),('G',90)], [('G',21)],
        [('M',5)], [('M',6),('T',1)], [('M',3),('S',p['spindle_rpm'])],
    ]
    step_map = []
    for op in operations(plan):
        p=operation_parameters(plan,op)
        x, y = op['hole_center_xy_mm']
        step_map.append({'step_id':op['step_id'],'feature_id':op['feature_id'],
                         'first_executable_block':len(expected)+1,'drilling_executable_block':len(expected)+6})
        expected += [
            [('G',90)], [('G',0),('Z',p['clearance_height_mm'])], [('G',98)],
            [('G',0),('X',x),('Y',y)], [('G',0),('Z',p['safe_height_mm'])],
            [('G',81),('X',x),('Y',y),('Z',p['final_tip_z_mm']),('F',p['feed_mm_min']),('R',p['retract_plane_mm'])],
            [('G',80)], [('G',0),('Z',p['clearance_height_mm'])],
        ]
    expected += [[('M',5)], [('G',17),('G',54),('G',90),('G',80),('G',40)], [('M',2)]]
    actual = nc_words(text)
    if len(actual) != len(expected):
        raise ValueError('Unexpected NC block count')
    for i, (got, wanted) in enumerate(zip(actual, expected), 1):
        if len(got) != len(wanted) or any(a != b or abs(x-y) > 5.1e-6
                for (a,x),(b,y) in zip(got,wanted)):
            raise ValueError(f'NC block {i} disagrees with the execution plan')
    return {'status':'matched_declared_trace','executable_blocks':len(actual),
            'post_decimal_places':5,'text_modified':False,
            'step_map':step_map,'hole_count':len(step_map),
            'scope':'ordered XYZ/G54/G81 reference; not a general NC interpreter'}


def simulation_project(plan, nc_name='program.nc', resolution=None):
    if Path(nc_name).name != nc_name:
        raise ValueError('NC must be in the simulation directory')
    if resolution is None:
        resolution=plan.get('simulation',{}).get('resolution_mm',.025)
    if isinstance(resolution,bool) or not 0 < resolution <= 0.1:
        raise ValueError('Unsupported simulation resolution')
    return {'units':'metric','resolution-mode':'manual','resolution':resolution,
            'tools':{'1':{'units':'metric','shape':'conical',
                          'length':plan['parameters']['drill_tip_length_mm'],
                          'diameter':plan['tool']['diameter_mm']}},
            'workpiece':{'automatic':False,'margin':0,'bounds':plan['stock']['bounds_mm']},
            'files':[nc_name]}


def verify_binding(path, expected):
    if digest(Path(path)) != expected:
        raise ValueError('Artifact changed: ' + Path(path).name)
