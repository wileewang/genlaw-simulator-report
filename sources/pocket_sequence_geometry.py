"""Explicit face / closed pocket / through-hole composition, including floor holes.

This reference describes the solid, independently of native CAM and card order.
Partial intersections with pocket walls are rejected, never approximated away.
"""
import math
import re
from .milling_features import CONTRACTS, required_nominals, validate_feature
from .pocket_geometry import profile, nominal_area
from .preparation import InputError

CAPABILITY = 'pocketed_planar_milling_xy'
REFERENCE = 'z_layered_box_minus_rounded_pocket_and_disjoint_through_cylinders'
KINDS = {'planar_top': 'top_facing_xy', 'rounded_rectangular_pocket': 'rounded_pocket_xy',
         'through_hole': 'helical_hole_milling_xy'}


def contract_for_feature(feature):
    contract = CONTRACTS[KINDS[feature['kind']]]
    if feature['kind'] == 'rounded_rectangular_pocket':
        return {**contract, 'fields': contract['fields'] | {'floor_face'},
                'surface_constraint': 'bottom_shape',
                'extra_strategy': {'floor_outline': 'clear_entire_outer_wire'}}
    return contract


def validate_bound_feature(feature, bounds):
    contract = contract_for_feature(feature)
    result = validate_feature(contract, feature, bounds)
    if feature['kind'] == 'rounded_rectangular_pocket' and (
        not isinstance(feature['floor_face'], str)
        or not re.fullmatch(r'Face[1-9][0-9]{0,4}', feature['floor_face'])
    ):
        raise InputError('missing_information', 'floor_face', '须绑定原 CAD 型腔底面。')
    return result


def feature_nominals(feature):
    return required_nominals(contract_for_feature(feature), feature)


def hole_locations(pocket, holes):
    """Conservative original-geometry classification, not mesh-based selection."""
    from shapely.geometry import Point
    boundary = profile(pocket, 2048)
    result = {}
    for f in holes:
        center = Point(f['center_xy_mm']); radius = f['diameter_mm'] / 2
        # Exact circle radius against an inscribed rounded-rectangle polygon.
        # A small positive margin also rejects tangency / numerical ambiguity.
        if boundary.contains(center) and boundary.boundary.distance(center) > radius + 1e-7:
            result[f['id']] = 'pocket_floor'
        elif boundary.distance(center) > radius + 1e-7:
            result[f['id']] = 'top_land'
        else:
            raise ValueError('孔与型腔边界相交 / 相切尚未支持；不得忽略部分孔壁。')
    return result


def hole_wall_top(reference, hole):
    pocket = reference.get('pocket_feature')
    top = reference['bounds']['max'][2]
    return top - pocket['depth_mm'] if pocket and hole_locations(pocket, [hole])[hole['id']] == 'pocket_floor' else top


class PocketSequenceReference:
    def __init__(self, reference, segments):
        from shapely.geometry import box, Point
        from shapely.ops import unary_union
        self.low, self.high = [reference['bounds'][k] for k in ('min', 'max')]
        self.rectangle = box(*self.low[:2], *self.high[:2])
        holes = reference['features']; pocket = reference.get('pocket_feature')
        circles = unary_union([Point(f['center_xy_mm']).buffer(f['diameter_mm']/2, quad_segs=segments) for f in holes])
        self.lower = self.rectangle.difference(circles)
        self.floor = self.high[2] - pocket['depth_mm'] if pocket else self.high[2]
        self.upper = self.lower.difference(profile(pocket, segments)) if pocket else self.lower
        self.breaks = sorted(set([self.low[2], self.floor, self.high[2]]))
        self.analytic_volume = self.rectangle.area * (self.high[2]-self.low[2])
        if pocket:
            self.analytic_volume -= nominal_area(pocket) * pocket['depth_mm']
        self.analytic_volume -= sum(math.pi*(f['diameter_mm']/2)**2*(hole_wall_top(reference, f)-self.low[2]) for f in holes)

    def at(self, z):
        from shapely.geometry import Polygon
        if not self.low[2] < z < self.high[2]:
            return Polygon()
        return self.upper if z > self.floor else self.lower
