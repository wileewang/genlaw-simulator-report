"""Versioned geometric scope shared by drilling preparation and native checks."""
import math

SINGLE = 'axis_aligned_box_minus_one_z_through_cylinder'
MULTI = 'axis_aligned_box_minus_disjoint_z_through_cylinders'
DRILLED = 'axis_aligned_box_minus_disjoint_z_drilled_holes'
MAX_HOLES = 16


def operations(plan):
    if plan['schema'] == 'genlaw.execution-plan.v1':
        return [plan['operation']]
    if plan['schema'] in ('genlaw.execution-plan.v2', 'genlaw.execution-plan.v3','genlaw.execution-plan.v4','genlaw.execution-plan.v5','genlaw.execution-plan.v6','genlaw.execution-plan.v7','genlaw.execution-plan.v8','genlaw.execution-plan.v9'):
        return plan['operations']
    raise ValueError('Unsupported execution-plan version')


def reference_holes(reference):
    if reference['reference_class'] == SINGLE:
        return [{'id': reference.get('feature_id', 'F_HOLE_1'),
                 'center_xy_mm': reference['hole_center_xy'],
                 'diameter_mm': 2 * reference['hole_radius_mm'],
                 'kind': 'through_hole', 'axis': [0, 0, 1]}]
    if reference['reference_class'] in (MULTI, DRILLED):
        return reference['features']
    raise ValueError('Unsupported native geometric reference')


def operation_parameters(plan, operation):
    if plan['schema']=='genlaw.execution-plan.v9':return operation['parameters']
    if plan['schema']=='genlaw.execution-plan.v3':
        return {**plan['parameters'], **operation['parameters']}
    return plan['parameters']


def blind_levels(feature, top):
    """CAD geometry; independent of the NC/tool chosen for its manufacture."""
    height=feature['diameter_mm']/2/math.tan(math.radians(feature['bottom_angle_degrees']/2))
    shoulder=top-feature['cylindrical_depth_mm']
    return shoulder, shoulder-height
