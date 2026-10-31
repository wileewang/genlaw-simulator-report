"""Explicit experimental input admission for selected parallel top-edge chamfers."""
import json
import math
from pathlib import Path
import re

from .cell_windows import file_binding, checked_file, expected_grid, window_budget
from .freecad_deburr import checked_formal_backend
from .selected_chamfer_geometry import ACCEPTANCE, SelectedChamferReference, dimensions

READ = lambda p: json.loads(Path(p).read_text())


def finite_positive(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError('Positive finite number required: '+name)


def prepare(path):
    path = Path(path).resolve(); entry = READ(path)
    if (set(entry) != {'schema', 'specification', 'CAD', 'CAD_reference', 'process_plan', 'paper_card',
                      'scope', 'material_physics_verified', 'production_approved'}
            or entry['schema'] != 'genlaw.selected-chamfer-input.v1'
            or entry['scope'] != 'experimental_software_geometry'
            or entry['material_physics_verified'] is not False or entry['production_approved'] is not False):
        raise ValueError('Unsupported selected-chamfer input contract')
    for key in ['CAD', 'CAD_reference', 'process_plan', 'paper_card']: checked_file(entry[key])
    spec = entry['specification']; ref = READ(checked_file(entry['CAD_reference']))
    card = READ(checked_file(entry['process_plan']))
    if (set(spec) != {'id', 'name', 'revision', 'stock', 'material_label', 'width_mm', 'angle_degrees',
                     'selected_sides', 'resolution_mm', 'tool', 'cam', 'acceptance'}
            or not isinstance(spec['id'], str) or not re.fullmatch('[a-z0-9-]{1,64}', spec['id'])
            or not isinstance(spec['name'], str) or not spec['name'].strip()
            or not isinstance(spec['material_label'], str) or not spec['material_label'].strip()
            or spec['revision'] != 'r1' or spec['angle_degrees'] != 45
            or spec['acceptance'] != ACCEPTANCE):
        raise ValueError('Invalid specification or changed predeclared acceptance')
    dimensions(spec['stock'], spec['width_mm'], spec['selected_sides'])
    target = SelectedChamferReference(ref)
    if (ref['schema'] != 'genlaw.selected-chamfer-reference.v1' or ref['cad'] != entry['CAD']
            or any(abs(ref['bounds'][k][i]-spec['stock'][k][i]) > 1e-8 for k in ['min', 'max'] for i in range(3))
            or ref['width_mm'] != spec['width_mm']
            or ref['selected_sides'] != spec['selected_sides']
            or set(ref['faces_by_side']) != set(spec['selected_sides'])
            or len(set(ref['faces_by_side'].values())) != len(spec['selected_sides'])
            or any(not isinstance(v, str) or not re.fullmatch(r'Face[1-9][0-9]*', v) for v in ref['faces_by_side'].values())
            or ref['valid_single_solid'] is not True or ref['target_created_before_NC'] is not True
            or type(ref['volume_mm3']) not in (int, float) or not math.isfinite(ref['volume_mm3'])
            or abs(target.analytic_volume-ref['volume_mm3']) > 1e-8):
        raise ValueError('Original CAD, edge selection and declared reference disagree')
    expected_card = {'schema': 'genlaw.selected-chamfer-card.v1', 'id': 'SELECTED-'+spec['id']+'-r1',
        'CAD': entry['CAD'], 'operations': [{'id': 'OP05', 'type': 'incoming_inspection'},
            {'id': 'OP10', 'type': 'setup', 'frame': 'original_CAD', 'rotary_motion': False},
            {'id': 'OP20', 'type': 'selected_edge_chamfer', 'width_mm': spec['width_mm'],
                'angle_degrees': 45, 'selected_sides': spec['selected_sides'], 'tool_id': 'T1'},
            {'id': 'OP30', 'type': 'geometry_inspection'}],
        'manufacturing_approval': False, 'whole_real_card_complete': False}
    if card != expected_card: raise ValueError('Structured process card does not bind the selected original edges')
    tool, cam = spec['tool'], spec['cam']
    if (set(tool) != {'id', 'kind', 'diameter_mm', 'tip_diameter_mm', 'included_angle_degrees',
                     'cutting_length_mm', 'length_mm'} or tool['id'] != 'T1' or tool['kind'] != 'finite_frustum'):
        raise ValueError('Require the explicit finite-frustum cutter')
    for key in ['diameter_mm', 'tip_diameter_mm', 'included_angle_degrees', 'cutting_length_mm', 'length_mm']:
        finite_positive(tool[key], key)
    if (tool['included_angle_degrees'] != 90 or tool['tip_diameter_mm'] >= tool['diameter_mm']
            or abs(tool['cutting_length_mm']-(tool['diameter_mm']-tool['tip_diameter_mm'])/2) > 1e-10
            or tool['length_mm'] <= tool['cutting_length_mm']):
        raise ValueError('Native snubnose must equal the declared 90 degree cutting frustum')
    if (set(cam) != {'native_operation', 'backend', 'extra_depth_mm', 'path_tolerance_mm',
                    'safe_height_mm', 'clearance_height_mm', 'spindle_rpm', 'feed_mm_min', 'plunge_mm_min'}
            or cam['native_operation'] != 'Deburr'):
        raise ValueError('Unsupported native CAM settings')
    checked_formal_backend(cam['backend'])
    for key in ['extra_depth_mm', 'path_tolerance_mm', 'spindle_rpm', 'feed_mm_min', 'plunge_mm_min']:
        finite_positive(cam[key], key)
    finite_positive(spec['resolution_mm'], 'resolution_mm')
    for key in ['safe_height_mm', 'clearance_height_mm']:
        if type(cam[key]) not in (int, float) or not math.isfinite(cam[key]): raise ValueError('Finite safe heights required')
    hi = spec['stock']['max']
    if (not hi[2] < cam['safe_height_mm'] < cam['clearance_height_mm']
            or spec['width_mm']+cam['extra_depth_mm'] >= tool['cutting_length_mm']
            or not 0 < cam['path_tolerance_mm'] <= .001 or spec['resolution_mm'] != .025):
        raise ValueError('Cutter reach, clearance or frozen resolution is unsupported')
    project = {'units': 'metric', 'resolution-mode': 'manual', 'resolution': spec['resolution_mm'],
        'tools': {'1': {'units': 'metric', 'shape': 'snubnose', 'diameter': tool['diameter_mm'],
                        'length': tool['cutting_length_mm'], 'snub_diameter': tool['tip_diameter_mm']}},
        'workpiece': {'automatic': False, 'margin': 0, 'bounds': spec['stock']}, 'files': ['program.nc']}
    grid = expected_grid(project); window = [0, 0, 0, *grid['steps']]; budget = window_budget(window, grid['steps'])
    if not budget['allowed']: raise ValueError('Selected-edge probe requires one bounded complete-stock window')
    return {'schema': 'genlaw.selected-chamfer-execution-plan.v1', 'status': 'prepared_for_native_probe',
        'input': file_binding(path), 'input_bindings': {k: entry[k] for k in ['CAD', 'CAD_reference', 'process_plan', 'paper_card']},
        'specification': spec, 'stock': {'bounds_mm': spec['stock']}, 'tool': tool, 'parameters': cam,
        'faces_by_side': ref['faces_by_side'], 'final_tip_z_mm': hi[2]-spec['width_mm']-cam['extra_depth_mm'],
        'setup': {'startup': {'position_mm': [0, 0, 0], 'tool': 'unloaded', 'spindle': 'stopped',
                             'retract_before_tool_selection_z_mm': cam['clearance_height_mm']}},
        'simulation_project': project, 'grid': grid, 'window': window, 'budget': budget,
        'feature_cohort_qualified': False, 'whole_real_card_complete': False, 'production_approved': False}


def checked_archive(input_path, binding):
    """Bind the developer-authored paper to its exact pre-NC archive membership."""
    archive_path = checked_file(binding); archive = READ(archive_path)
    if archive['schema'] != 'genlaw.selected-chamfer-input-archive.v1' or archive['all_inputs_saved_before_NC'] is not True:
        raise ValueError('Require the selected-edge pre-NC archive')
    files = archive['files']
    if not 1 <= len(files) <= 128 or len({b['path'] for b in files}) != len(files):
        raise ValueError('Invalid input archive membership')
    for item in files: checked_file(item)
    path = Path(input_path).resolve(); entry = READ(path); root = archive_path.parent
    if file_binding(path) not in files or file_binding(root/'specifications.json') not in files:
        raise ValueError('Input/specifications not in the unchanged archive')
    candidates = [s for s in READ(root/'specifications.json')['cases'] if s['id'] == entry['specification']['id']]
    if candidates != [entry['specification']] or path != root/entry['specification']['id']/'input.json':
        raise ValueError('Input is not the corresponding archived case')
    for key, name in [('CAD', 'target.brep'), ('CAD_reference', 'reference.json'),
                      ('process_plan', 'process-plan.json'), ('paper_card', 'process-card.pdf')]:
        if entry[key] not in files or checked_file(entry[key]) != path.parent/name:
            raise ValueError('Unmatched paper/CAD association: '+key)
    return prepare(path), {'status': 'admitted', 'archive': binding, 'input': file_binding(path),
        'arbitrary_production_PDF_parsed': False, 'manufacturing_approval': False}
