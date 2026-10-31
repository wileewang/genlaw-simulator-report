"""Explicit original-CAD, program and machine frames for one XYZ setup.

C is the unchanged original CAD/material frame. With its physical placement P
and work origin D, W=C-D, O=P+D, M=W+O and material cutting uses C=M-P.
This module translates a verification/CAM view, never an exported NC file.
"""
import copy
import math

SCHEMA = 'genlaw.setup.xyz-translation.v1'
FIELDS = {'schema', 'units', 'height_parameter_frame', 'material_origin_machine_mm',
          'work_origin_cad_mm', 'work_offset_origin_machine_mm', 'work_offset',
          'rotation_degrees', 'tool_length_offset_mm', 'fixture_mode', 'startup', 'basis'}


def enabled(plan):
    from .tool_length_setup import enabled as tool_lengths
    return plan.get('setup', {}).get('schema') == SCHEMA or tool_lengths(plan)


def finite_vector(value, field):
    if (not isinstance(value, list) or len(value) != 3
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)):
        raise ValueError('Need three finite coordinates: ' + field)
    return value


def validate_setup(setup, stock_bounds):
    from .tool_length_setup import enabled as tool_lengths, validate_setup as validate_tool_lengths
    if isinstance(setup,dict) and tool_lengths({'setup': setup}):
        return validate_tool_lengths(setup,stock_bounds)
    if not isinstance(setup, dict) or set(setup) != FIELDS or setup.get('schema') != SCHEMA:
        raise ValueError('Explicit XYZ setup fields/version missing or unknown')
    if setup['units'] != 'mm' or setup['height_parameter_frame'] != 'original_cad':
        raise ValueError('Setup and card heights must explicitly use millimetres and original CAD coordinates')
    if setup['work_offset'] not in ('G54', 'G55'):
        raise ValueError('Only an explicit G54 or G55 is supported by this adapter')
    if (finite_vector(setup['rotation_degrees'], 'rotation') != [0, 0, 0]
            or type(setup['tool_length_offset_mm']) not in (int, float)
            or setup['tool_length_offset_mm'] != 0
            or setup['fixture_mode'] != 'unmodelled_explicitly_excluded'):
        raise ValueError('Rotation, tool length compensation and fixture collision are not supported')
    if not isinstance(setup['basis'], str) or not setup['basis'].strip():
        raise ValueError('Setup basis is required')
    p, d, o = [finite_vector(setup[k], k) for k in
               ['material_origin_machine_mm', 'work_origin_cad_mm', 'work_offset_origin_machine_mm']]
    if any(abs(a+b-c) > 1e-9 for a, b, c in zip(p, d, o)):
        raise ValueError('Controller work origin must equal physical material placement plus CAD work origin (O=P+D)')
    if any(abs(v-round(v, 5)) > 1e-10 for v in p+d+o):
        raise ValueError('Declared frame coordinates exceed the five-decimal NC precision')
    start = setup['startup']
    keys = {'position_mm', 'position_frame', 'tool', 'spindle', 'retract_before_tool_selection_z_mm', 'basis'}
    if not isinstance(start, dict) or set(start) != keys:
        raise ValueError('Explicit machine startup fields are required')
    if (finite_vector(start['position_mm'], 'startup position') != [0, 0, 0]
            or start['position_frame'] != 'machine' or start['tool'] != 'unloaded' or start['spindle'] != 'stopped'):
        raise ValueError('Reference startup is machine (0,0,0), unloaded and stopped')
    z = start['retract_before_tool_selection_z_mm']
    if type(z) not in (int, float) or not math.isfinite(z) or abs(z-round(z, 5)) > 1e-10:
        raise ValueError('Initial machine retract must be finite and expressible at NC precision')
    if z <= stock_bounds['max'][2]+p[2]:
        raise ValueError('Initial machine retract must clear physically placed original stock')
    if not isinstance(start['basis'], str) or not start['basis'].strip():
        raise ValueError('Startup basis is required')
    return z-p[2]  # Same physical clearance expressed in original CAD coordinates.


def translated_bounds(bounds, delta):
    return {key: [v-delta[i] for i, v in enumerate(bounds[key])] for key in ('min', 'max')}


def setup_requirements(setup):
    """Card-level semantic requirements, independent of explanatory basis text."""
    from .tool_length_setup import enabled as tool_lengths, setup_requirements as tool_requirements
    if tool_lengths({'setup': setup}):return tool_requirements(setup)
    result={key:copy.deepcopy(setup[key]) for key in FIELDS-{'schema','basis'}}
    result['startup'].pop('basis')
    return result


def program_view(plan):
    """Explicit derived plan for native CAM parameters and geometric NC guards."""
    if not enabled(plan):
        return plan
    setup = plan['setup']
    clearance = validate_setup(setup, plan['stock']['bounds_mm'])
    delta = setup['work_origin_cad_mm']
    view = copy.deepcopy(plan)
    view['target_bounds_mm'] = translated_bounds(plan['target_bounds_mm'], delta)
    view['stock']['bounds_mm'] = translated_bounds(plan['stock']['bounds_mm'], delta)
    for state in view['material_states']:
        state['bounds_mm'] = translated_bounds(state['bounds_mm'], delta)
    for op in view['operations']:
        op['start_z_mm'] -= delta[2]
        op['final_z_mm'] -= delta[2]
        for key in ('safe_height_mm', 'clearance_height_mm'):
            op['parameters'][key] -= delta[2]
        if 'hole_center_xy_mm' in op:
            op['hole_center_xy_mm'] = [v-delta[i] for i, v in enumerate(op['hole_center_xy_mm'])]
        feature = op['geometry']
        if 'center_xy_mm' in feature:
            feature['center_xy_mm'] = [v-delta[i] for i, v in enumerate(feature['center_xy_mm'])]
        if 'bounds_xy_mm' in feature:
            feature['bounds_xy_mm'] = translated_bounds(feature['bounds_xy_mm'], delta)
        if 'top_z_mm' in feature:
            feature['top_z_mm'] -= delta[2]
    # Existing per-operation guards use an isolated work-coordinate wrapper.
    # This setup is never executed and never becomes a machine configuration.
    view['setup'] = {'origin_mm': [0, 0, 0], 'rotation_degrees': [0, 0, 0],
                     'tool_length_offset_mm': 0, 'work_offset': 'G54',
                     'fixture_mode': setup['fixture_mode'], 'basis': 'Program-coordinate verification projection only',
                     'startup': {'position_mm': [0, 0, 0], 'tool': 'unloaded', 'spindle': 'stopped',
                                 'retract_before_tool_selection_z_mm': clearance-delta[2],
                                 'basis': 'Guard wrapper only; actual NC startup uses machine G53'}}
    return view


def trace_to_cad(point, plan):
    if not enabled(plan):
        return point
    point = copy.deepcopy(point)
    delta = plan['setup']['work_origin_cad_mm']
    for key in ('start', 'end', 'arc_center_xy_mm'):
        if key in point:
            point[key] = [None if v is None else v+delta[i] for i, v in enumerate(point[key])]
    return point


def place_native_model(job, original_shape, plan):
    """Translate the native job's model clone, retaining the original CAD object."""
    import FreeCAD as App
    from .native_stock import bounds
    if not enabled(plan) or len(job.Model.Group) != 1:
        raise ValueError('One explicit original-CAD model is required')
    delta = plan['setup']['work_origin_cad_mm']
    clone = job.Model.Group[0]
    clone.Placement = App.Placement(clone.Placement.Base-App.Vector(*delta), clone.Placement.Rotation)
    job.Document.recompute()
    back = clone.Shape.copy()
    back.translate(App.Vector(*delta))
    difference = back.cut(original_shape).Volume+original_shape.cut(back).Volume
    expected = translated_bounds(bounds(original_shape), delta)
    observed = bounds(clone.Shape)
    if (not clone.Shape.isValid() or len(clone.Shape.Solids) != 1
            or len(clone.Shape.Faces) != len(original_shape.Faces) or difference > 1e-8
            or any(abs(observed[k][i]-expected[k][i]) > 1e-8 for k in expected for i in range(3))):
        raise ValueError('Native model placement disagrees with the declared CAD work origin')
    return {'schema': 'genlaw.native-work-frame.v1', 'original_cad_sha256': plan['cad_ref']['sha256'],
            'cad_to_program_translation_mm': [-v for v in delta], 'native_model_bounds_program_mm': observed,
            'native_model_inverse_translation_difference_mm3': difference,
            'original_cad_modified': False, 'nc_text_rewritten': False,
            'job_model': clone.Name, 'setup': copy.deepcopy(plan['setup'])}


def setup_registers(plan):
    """Controller configuration input, separate from unchanged FreeCAD NC."""
    if not enabled(plan):
        raise ValueError('No explicit coordinate setup')
    s = plan['setup']
    validate_setup(s, plan['stock']['bounds_mm'])
    number = {'G54': 1, 'G55': 2}[s['work_offset']]
    xyz = ' '.join(f'{axis}{v:.5f}' for axis, v in zip('XYZ', s['work_offset_origin_machine_mm']))
    text='(GenLaw declared controller register initialization; no motion)\nG21\n'+f'G10 L2 P{number} {xyz}\n'
    from .tool_length_setup import enabled as tool_lengths, register_lines
    if tool_lengths(plan):text+=register_lines(s)
    return text


def check_setup_registers(text, plan):
    from .programming import nc_words
    if nc_words(text) != nc_words(setup_registers(plan)):
        raise ValueError('Controller initialization differs from the explicit setup')
    return {'status': 'matched_explicit_controller_register', 'motion_present': False,
            'program_nc_modified': False, 'work_offset': plan['setup']['work_offset']}
