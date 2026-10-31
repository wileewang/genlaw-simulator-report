"""Compose registered CAM operations without replacing their simulated material.

The first qualified cohort uses facing, static X flipping, facing and chamfering.
Additional operations need their own CAM, incoming-CAD and measurement adapters.
"""
import json
import re
from pathlib import Path

from .cell_windows import checked_file, file_binding, expected_grid, window_budget
from .indexed_facing import IDENTITY, FLIP_X, checked_bounds, close_bounds, transform_bounds
from .preparation import prepare
from .programming import simulation_project

ADAPTERS = {
    'top_facing_xy': ('genlaw.execution-plan.v7', 'axis_aligned_box_after_top_facing'),
    'rectangular_top_chamfer_xy': ('genlaw.execution-plan.v14', 'box_with_equal_45_degree_top_perimeter_chamfers'),
}
ACCEPTANCE = {'normal_iou_min': .99, 'control_iou_max': .98,
              'bounds_error_mm_max': .025, 'volume_error_mm3_max': .03,
              'quadrature_iou_difference_max': 1e-5,
              'mesh_vs_integrated_volume_mm3_max': 1e-4,
              'feature_deviation_mm_max': .1, 'numerical_guard_cells': 2}


def read(path):
    return json.loads(Path(path).read_text())


def prepare_chain(path):
    path = Path(path).resolve(); entry = read(path)
    fields = {'schema', 'id', 'mode', 'material_frame', 'stock_reset_between_stages',
              'continuous_rotary_motion', 'paper_card', 'process_plan', 'original_stock',
              'stages', 'final_cad', 'final_reference', 'acceptance'}
    if (set(entry) != fields or entry['schema'] != 'genlaw.static-stage-input.v1'
            or entry['mode'] != 'software_geometry_reference' or entry['material_frame'] != 'original_CAD_mm'
            or entry['stock_reset_between_stages'] is not False or entry['continuous_rotary_motion'] is not False
            or not isinstance(entry['id'], str) or not entry['id'].strip()):
        raise ValueError('Unsupported static stage input contract')
    for key in ['paper_card', 'process_plan', 'final_cad', 'final_reference']: checked_file(entry[key])
    master = read(checked_file(entry['process_plan']))
    if master.get('cad_sha256') != entry['final_cad']['sha256']:
        raise ValueError('Master process card is not bound to the final CAD')
    order = [s['id'] for op in master['operations'] for s in op['steps']]
    if len(order) != len(set(order)): raise ValueError('Duplicate master card step')
    original = entry['original_stock']
    if (set(original) != {'bounds_mm', 'material_label'} or not isinstance(original['material_label'], str)
            or not original['material_label'].strip()): raise ValueError('Explicit original material is required')
    bounds = checked_bounds(original['bounds_mm'])
    flip = FLIP_X+[0, bounds['min'][1]+bounds['max'][1], bounds['min'][2]+bounds['max'][2]]
    stages = entry['stages']
    if not isinstance(stages, list) or not 1 <= len(stages) <= 8:
        raise ValueError('Require one to eight ordered programs')
    stage_ids = set(); setup_poses = {}; plans = []; preparations = []; inputs = []
    references = []; projected = []; projects = []; selected = []; steps = []
    for i, stage in enumerate(stages):
        if set(stage) != {'id', 'setup_id', 'input', 'to_material', 'predecessor'}:
            raise ValueError('Unknown or missing stage fields')
        ident = stage['id']; setup = stage['setup_id']; pose = stage['to_material']
        if (not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', ident)
                or ident in stage_ids or not isinstance(setup, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', setup)):
            raise ValueError('Require unique stage IDs and explicit setup IDs')
        stage_ids.add(ident)
        if pose not in [IDENTITY, flip] or (i == 0 and pose != IDENTITY):
            raise ValueError('Only identity and original-box static X flip are qualified')
        if setup in setup_poses and setup_poses[setup] != pose:
            raise ValueError('A setup ID cannot silently change its placement')
        setup_poses[setup] = pose
        if stage['predecessor'] != ('original_stock' if i == 0 else stages[i-1]['id']):
            raise ValueError('Every program must consume the immediately preceding material state')
        inp = checked_file(stage['input']); prepared = prepare(inp)
        if prepared['status'] != 'ready_for_declared_scope':
            raise ValueError('Stage preparation blocked: '+ident+' '+json.dumps(prepared['issues'], ensure_ascii=False))
        plan = prepared['execution_plan']; capability = plan['capability']
        if capability not in ADAPTERS or plan['schema'] != ADAPTERS[capability][0]:
            raise ValueError('Stage needs a registered CAM and measurement adapter')
        manifest = read(inp); local_card = read(inp.parent/manifest['files']['process_plan']['path'])
        if (manifest['files']['paper_card']['sha256'] != entry['paper_card']['sha256']
                or local_card['operations'] != master['operations']):
            raise ValueError('Stage card differs from the common archived process card')
        sid = plan['operations'][0]['step_id']
        if sid not in order or sid in steps: raise ValueError('Stage is not a unique master card step')
        steps.append(sid); selected.append(order.index(sid))
        if selected != sorted(selected): raise ValueError('Program order disagrees with the process card')
        if plan['stock']['material_label'] != original['material_label']:
            raise ValueError('Material identity cannot change between programs')
        if i and plan['simulation'] != plans[0]['simulation']:
            raise ValueError('Resolution and its declared basis must remain fixed')
        incoming = transform_bounds(plan['stock']['bounds_mm'], pose)
        if not close_bounds(incoming, bounds if i == 0 else projected[-1]):
            raise ValueError('Nominal CAM incoming bounds disagree with the predecessor')
        # Bounds are only a preliminary gate. The native CAD worker must also
        # prove full incoming-solid continuity, containment and final CAD equality.
        target = transform_bounds(plan['target_bounds_mm'], pose)
        if any(target['min'][j] < incoming['min'][j]-1e-8 or target['max'][j] > incoming['max'][j]+1e-8 for j in range(3)):
            raise ValueError('A stage cannot add nominal material')
        reference = file_binding(inp.parent/manifest['files']['native_reference']['path'])
        observed = read(checked_file(reference))
        if (observed.get('reference_class') != ADAPTERS[capability][1]
                or observed.get('sha256') != plan['cad_ref']['sha256']
                or observed.get('target_created_before_nc') is not True):
            raise ValueError('Stage target must be independently archived before NC')
        project = simulation_project(plan)
        # Both admitted placements are their own inverse. Each native program
        # declares the original envelope, even when CAM plans from later stock.
        project['workpiece']['bounds'] = transform_bounds(bounds, pose)
        inputs.append(str(inp)); preparations.append(prepared); plans.append(plan)
        references.append(reference); projected.append(target); projects.append(project)
    final = read(checked_file(entry['final_reference']))
    if (final.get('schema') != 'genlaw.static-stage-final-reference.v1'
            or final.get('sha256') != entry['final_cad']['sha256']
            or final.get('valid_single_solid') is not True or final.get('target_created_before_nc') is not True
            or final.get('local_reference') != references[-1] or final.get('to_material') != stages[-1]['to_material']
            or not close_bounds(final['bounds'], projected[-1])):
        raise ValueError('Final independent CAD/reference does not match the final program frame')
    grid = expected_grid(projects[0]); window = [0, 0, 0, *grid['steps']]
    budget = window_budget(window, grid['steps'])
    if not budget['allowed']: raise ValueError('This adapter requires one bounded complete-stock window')
    if entry['acceptance'] != ACCEPTANCE: raise ValueError('The frozen software criteria changed')
    return {'schema': 'genlaw.static-stage-plan.v1', 'input': file_binding(path), 'id': entry['id'],
            'paper_card': entry['paper_card'], 'process_plan': entry['process_plan'], 'stages': stages,
            'stage_inputs': inputs, 'preparations': preparations, 'stage_plans': plans,
            'stage_references': references, 'stage_target_bounds_in_material': projected,
            'simulation_projects': projects, 'grid': grid, 'window': window, 'budget': budget,
            'original_stock': original, 'final_cad': entry['final_cad'], 'final_reference': entry['final_reference'],
            'acceptance': entry['acceptance'], 'native_CAD_continuity_required_before_CAM': True,
            'stock_reset_between_stages': False, 'whole_process_card_complete': False, 'production_approved': False}
