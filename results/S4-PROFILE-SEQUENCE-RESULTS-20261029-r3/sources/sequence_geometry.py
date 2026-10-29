"""Bounded ordered milling: one facing pass followed by independent through holes.

The final solid's reference class remains independent of the manufacturing route.
Child plans are verification projections, never substitutes for native CAD binding.
"""
import copy
from .drilling_geometry import MULTI

CAPABILITY = 'ordered_planar_milling_xy'
REFERENCE = MULTI
KINDS = {'planar_top': 'top_facing_xy', 'through_hole': 'helical_hole_milling_xy',
         'capsule_outline': 'capsule_profile_xy'}
MAX_HOLES = 8


def label(index, operation):
    return {'face_milling':'Face', 'helical_hole_milling':'Helix',
            'external_profile_milling':'Profile'}[operation['type']] + f'_{index + 1:02d}'


def child_plan(plan, operation):
    """Project explicit per-step resources for existing geometric NC guards only."""
    facing = operation['type'] == 'face_milling'
    external = operation['geometry']['kind'] == 'capsule_outline'
    child = copy.deepcopy(plan)
    child.update(schema='genlaw.execution-plan.v7' if facing else 'genlaw.execution-plan.v10' if external else 'genlaw.execution-plan.v8',
                 capability=KINDS[operation['geometry']['kind']], operations=[copy.deepcopy(operation)],
                 tool=next(t for t in plan['tools'] if t['id'] == operation['tool_id']),
                 parameters=operation['parameters'], strategy=operation['strategy'])
    if not facing:
        child['stock']['bounds_mm'] = copy.deepcopy(plan['target_bounds_mm'])
    return child
