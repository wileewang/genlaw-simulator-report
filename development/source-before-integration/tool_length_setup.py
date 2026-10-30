"""Explicit mounted cutter lengths and independent static Z compensation.

The reference uses an absolute spindle gauge datum: Q=W+O+H, tip=Q-L.
Normal setup requires H=L; erroneous applied values are separate test controls.
Cutting geometry length remains a different, unchanged tool resource.
"""
import copy
from decimal import Decimal
import math

SCHEMA = 'genlaw.setup.xyz-tool-length.v1'
COMPENSATION_FIELDS = {'mode', 'actual_lengths_mm', 'registers_mm', 'tool_bindings',
                       'minimum_clearance_above_stock_mm', 'basis'}


def enabled(plan):
    return plan.get('setup', {}).get('schema') == SCHEMA


def fields():
    from .coordinate_setup import FIELDS
    return (FIELDS-{'tool_length_offset_mm'}) | {'tool_length_compensation'}


def finite_mm(value, label, positive=False):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or (positive and value <= 0) or abs(value-round(value, 5)) > 1e-10):
        raise ValueError('Finite five-decimal millimetres required: '+label)
    return value


def tool_number(value):
    if type(value) is not int or not 1 <= value <= 99:
        raise ValueError('Explicit tool/register number must be an integer from 1 to 99')
    return value


def actual_lengths(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError('Actual mounted gauge-to-tip lengths are required')
    result={}
    for row in rows:
        if not isinstance(row, dict) or set(row)!={'tool_number','gauge_to_tip_mm'}:
            raise ValueError('Unknown or missing mounted tool fields')
        number=tool_number(row['tool_number'])
        if number in result:raise ValueError('Duplicate physical tool length')
        result[number]=finite_mm(row['gauge_to_tip_mm'],'actual mounted length',True)
    return result


def gauge_argument(rows):
    return '--tool-gauge-lengths='+','.join(f'{n}:{v:.5f}' for n,v in sorted(actual_lengths(rows).items()))


def validate_setup(setup, stock_bounds):
    from .coordinate_setup import SCHEMA as XYZ, validate_setup as validate_xyz
    if not isinstance(setup,dict) or setup.get('schema')!=SCHEMA or set(setup)!=fields():
        raise ValueError('Explicit static tool-length setup version/fields required')
    comp=setup['tool_length_compensation']
    if not isinstance(comp,dict) or set(comp)!=COMPENSATION_FIELDS:
        raise ValueError('Physical lengths, compensation table and tool bindings must be separate')
    if comp['mode']!='static_Z_absolute_gauge' or not isinstance(comp['basis'],str) or not comp['basis'].strip():
        raise ValueError('Only explicit static Z compensation in the absolute gauge reference is supported')
    actual=actual_lengths(comp['actual_lengths_mm']); registers={}; bindings={}
    if not isinstance(comp['registers_mm'],list) or not isinstance(comp['tool_bindings'],list):
        raise ValueError('Explicit compensation register and native tool-binding lists required')
    for row in comp['registers_mm']:
        if not isinstance(row,dict) or set(row)!={'h_register','offset_mm'}:
            raise ValueError('Unknown compensation register fields')
        n=tool_number(row['h_register'])
        if n in registers:raise ValueError('Duplicate compensation register')
        registers[n]=finite_mm(row['offset_mm'],'controller Z offset')
    for row in comp['tool_bindings']:
        if not isinstance(row,dict) or set(row)!={'tool_number','h_register'}:
            raise ValueError('Unknown native T/H binding fields')
        n=tool_number(row['tool_number']); h=tool_number(row['h_register'])
        if n in bindings or n!=h:
            raise ValueError('This native FreeCAD post requires exactly one H equal to each T number')
        bindings[n]=h
    if set(actual)!=set(bindings) or set(registers)!=set(bindings.values()):
        raise ValueError('Mounted tools, native T/H bindings and offset registers must cover the same tools')
    if any(abs(actual[n]-registers[h])>1e-9 for n,h in bindings.items()):
        raise ValueError('Normal absolute-gauge setup requires controller H to match independently declared actual length')
    minimum=finite_mm(comp['minimum_clearance_above_stock_mm'],'minimum tool-change clearance',True)
    projection=copy.deepcopy(setup);projection.pop('tool_length_compensation')
    projection.update(schema=XYZ,tool_length_offset_mm=0)
    validate_xyz(projection,stock_bounds)
    # Exact decimal resource arithmetic, not rounding any simulated geometry.
    clearance=float(Decimal(str(setup['startup']['retract_before_tool_selection_z_mm']))
                    -Decimal(str(setup['material_origin_machine_mm'][2]))
                    -Decimal(str(max(actual.values()))))
    if clearance < stock_bounds['max'][2]+minimum-1e-9:
        raise ValueError('Initial G53 gauge retract does not clear the stock with the longest mounted tool')
    return clearance


def setup_requirements(setup):
    result={key:copy.deepcopy(setup[key]) for key in fields()-{'schema','basis'}}
    result['startup'].pop('basis')
    result['tool_length_compensation'].pop('basis')
    return result


def transition(plan, next_tool, previous_tool=None, previous_tip_cad_z=None):
    setup=plan['setup']; validate_setup(setup,plan['stock']['bounds_mm'])
    comp=setup['tool_length_compensation']; actual=actual_lengths(comp['actual_lengths_mm'])
    if next_tool not in actual:raise ValueError('Missing actual length for next tool')
    pz=setup['material_origin_machine_mm'][2]
    if previous_tool is None:
        gauge=setup['startup']['retract_before_tool_selection_z_mm'];before=None
    else:
        if previous_tool not in actual or previous_tip_cad_z is None or not math.isfinite(previous_tip_cad_z):
            raise ValueError('Known previous physical tool position required before M6')
        before=previous_tip_cad_z;gauge=before+pz+actual[previous_tool]
    after=gauge-pz-actual[next_tool]
    minimum=plan['stock']['bounds_mm']['max'][2]+comp['minimum_clearance_above_stock_mm']
    if after<minimum-1e-9 or (before is not None and before<minimum-1e-9):
        raise ValueError('Tool change would violate the declared physical clearance above original stock')
    return {'previous_tool_number':previous_tool,'next_tool_number':next_tool,
            'gauge_machine_z_mm':gauge,'previous_tip_cad_z_mm':before,'next_tip_cad_z_mm':after,
            'minimum_tip_cad_z_mm':minimum,'gauge_moves_during_m6':False,
            'h_reloaded_by_native_g43_after_m6':True,'holder_collision_checked':False}


def validate_resources(setup, stock, tools, operations):
    if setup.get('schema')!=SCHEMA:return
    validate_setup(setup,stock['bounds_mm']); actual=actual_lengths(setup['tool_length_compensation']['actual_lengths_mm'])
    if set(actual)!={tool['number'] for tool in tools}:
        raise ValueError('Actual mounted lengths must cover exactly the declared cutting tools')
    if any(actual[t['number']]<t['cutting_length_mm'] for t in tools):
        raise ValueError('Mounted gauge-to-tip length cannot be shorter than the modeled cutting segment')
    plan={'setup':setup,'stock':stock}; previous=None; z=None
    for op in operations:
        n=op['tool_number']
        if n!=previous:transition(plan,n,previous,z)
        previous=n;z=op['parameters']['clearance_height_mm']


def register_lines(setup):
    return ''.join(f'G10 L1 P{row["h_register"]} Z{row["offset_mm"]:.5f}\n'
                   for row in sorted(setup['tool_length_compensation']['registers_mm'],key=lambda r:r['h_register']))
