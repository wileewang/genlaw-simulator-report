"""Explicit composition of two existing facing inputs in one material frame.

This experimental contract supports a static 180-degree X flip about the
original box centre. Each CAM job has its own planned incoming stock; the
native simulator always evaluates both unchanged programs on one original
stock. It does not add rotary NC or physical workholding qualification.
"""
import itertools
import json
import math
from pathlib import Path

from .cell_windows import checked_file, file_binding, expected_grid, window_budget
from .preparation import prepare
from .programming import simulation_project

IDENTITY = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]
FLIP_X = [1, 0, 0, 0, -1, 0, 0, 0, -1]


def close_bounds(a, b):
    return all(abs(a[k][i] - b[k][i]) <= 1e-8
               for k in ('min', 'max') for i in range(3))


def checked_bounds(value):
    if (set(value) != {'min', 'max'}
            or any(not isinstance(value[k], list) or len(value[k]) != 3
                   for k in ('min', 'max'))
            or any(type(v) not in (int, float) or not math.isfinite(v)
                   for k in ('min', 'max') for v in value[k])
            or any(value['min'][i] >= value['max'][i] for i in range(3))):
        raise ValueError('Require finite positive box bounds')
    return value


def transform_bounds(bounds, pose):
    checked_bounds(bounds)
    if (not isinstance(pose, list) or len(pose) != 12
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in pose)
            or pose[:9] not in (IDENTITY[:9], FLIP_X)):
        raise ValueError('This adapter accepts only identity or static X flip')
    corners = itertools.product(*zip(bounds['min'], bounds['max']))
    points = [[sum(pose[3*i+j] * p[j] for j in range(3)) + pose[9+i]
               for i in range(3)] for p in corners]
    return {'min': [min(p[i] for p in points) for i in range(3)],
            'max': [max(p[i] for p in points) for i in range(3)]}


def prepare_chain(path):
    path = Path(path).resolve()
    entry = json.loads(path.read_text())
    if (entry.get('schema') != 'genlaw.indexed-facing-input.v1'
            or entry.get('mode') != 'software_geometry_reference'
            or entry.get('material_frame') != 'original_CAD_mm'
            or entry.get('stock_reset_between_setups') is not False
            or entry.get('continuous_rotary_motion') is not False):
        raise ValueError('Unsupported indexed facing contract')
    allowed = {'schema', 'id', 'mode', 'material_frame', 'stock_reset_between_setups',
               'continuous_rotary_motion', 'paper_card', 'original_stock', 'setups',
               'final_cad', 'final_reference', 'acceptance'}
    if set(entry) != allowed:
        raise ValueError('Unknown or missing indexed input fields')
    if not isinstance(entry['id'], str) or not entry['id']:
        raise ValueError('A stable chain identifier is required')
    for key in ('paper_card', 'final_cad', 'final_reference'):
        checked_file(entry[key])
    reference = json.loads(checked_file(entry['final_reference']).read_text())
    if (reference.get('sha256') != entry['final_cad']['sha256']
            or reference.get('valid_single_solid') is not True
            or reference.get('target_created_before_nc') is not True):
        raise ValueError('Final CAD observation is missing or stale')
    final_bounds = checked_bounds(reference['bounds'])
    original = entry['original_stock']
    if set(original) != {'bounds_mm', 'material_label'} or not original['material_label']:
        raise ValueError('Original stock and material label must be explicit')
    bounds = checked_bounds(original['bounds_mm'])
    expected_flip = FLIP_X + [0, bounds['min'][1] + bounds['max'][1],
                              bounds['min'][2] + bounds['max'][2]]
    stages = entry['setups']
    if (not isinstance(stages, list) or len(stages) != 2
            or [s.get('id') for s in stages] != ['S1', 'S2']):
        raise ValueError('Exactly two ordered static setups are required')
    plans, prepared, inputs, projected = [], [], [], []
    for i, stage in enumerate(stages):
        if set(stage) != {'id', 'input', 'to_material', 'predecessor'}:
            raise ValueError('Unknown or missing setup fields')
        if stage['predecessor'] != ('original_stock' if i == 0 else 'S1'):
            raise ValueError('The second setup must consume the first setup result')
        pose = stage['to_material']
        if pose != (IDENTITY if i == 0 else expected_flip):
            raise ValueError('Setup pose differs from the declared original-box X flip')
        inp = checked_file(stage['input']); preparation = prepare(inp)
        if preparation['status'] != 'ready_for_declared_scope':
            raise ValueError('Facing preparation blocked: ' + stage['id'] + ' ' +
                             json.dumps(preparation['issues'], ensure_ascii=False))
        plan = preparation['execution_plan']
        if plan['schema'] != 'genlaw.execution-plan.v7' or plan['capability'] != 'top_facing_xy':
            raise ValueError('Each setup must use the existing native facing contract')
        inp_data = json.loads(inp.read_text())
        if inp_data['files']['paper_card']['sha256'] != entry['paper_card']['sha256']:
            raise ValueError('Both facing steps must refer to the same archived paper card')
        if plan['stock']['material_label'] != original['material_label']:
            raise ValueError('Material identity cannot change between setups')
        incoming = transform_bounds(plan['stock']['bounds_mm'], pose)
        expected = bounds if i == 0 else projected[0]
        if not close_bounds(incoming, expected):
            raise ValueError('CAM incoming stock does not match the preceding nominal material')
        target = transform_bounds(plan['target_bounds_mm'], pose)
        if any(target['min'][j] < incoming['min'][j] - 1e-8
               or target['max'][j] > incoming['max'][j] + 1e-8 for j in range(3)):
            raise ValueError('A setup cannot add material')
        inputs.append(inp); prepared.append(preparation); plans.append(plan); projected.append(target)
    if not close_bounds(projected[1], final_bounds):
        raise ValueError('The second setup target does not match the original final CAD')
    if plans[0].get('simulation') != plans[1].get('simulation'):
        raise ValueError('Resolution must stay fixed across setups')
    projects = []
    for plan in plans:
        project = simulation_project(plan)
        # This engine composes native sweeps over the original material. The
        # second project envelope is expressly NOT its CAM planning stock.
        project['workpiece']['bounds'] = bounds
        projects.append(project)
    grid = expected_grid(projects[0]); window = [0, 0, 0, *grid['steps']]
    budget = window_budget(window, grid['steps'])
    if not budget['allowed']:
        raise ValueError('This first indexed adapter requires one bounded complete-stock window')
    acceptance = entry['acceptance']
    expected_acceptance = {'normal_iou_min': 0.99, 'control_iou_max': 0.9,
                          'bounds_error_mm_max': 0.025, 'volume_error_mm3_max': 0.03,
                          'quadrature_iou_difference_max': 1e-5,
                          'mesh_vs_integrated_volume_mm3_max': 1e-4}
    if acceptance != expected_acceptance:
        raise ValueError('Only the predeclared experimental measurement policy is supported')
    return {'schema': 'genlaw.indexed-facing-plan.v1', 'input': file_binding(path),
            'id': entry['id'], 'paper_card': entry['paper_card'], 'setups': stages,
            'stage_inputs': [str(p) for p in inputs], 'preparations': prepared,
            'stage_plans': plans, 'simulation_projects': projects, 'grid': grid,
            'window': window, 'budget': budget, 'original_stock': original,
            'stage_target_bounds_in_material': projected,
            'final_cad': entry['final_cad'], 'final_reference': entry['final_reference'],
            'acceptance': acceptance, 'stock_reset_between_setups': False,
            'whole_process_card_complete': False, 'production_approved': False}
