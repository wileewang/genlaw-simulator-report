"""Explicit face / capsule / hole sequences, independent of operation order.

The v11 contract keeps the outside contour on its own feature. Facing checks
height, thickness and flatness; it does not require later steps to retain the
rectangular blank outline. All checks refer to the original coordinate system.
"""
import math
from .profile_geometry import profile, nominal_area

CAPABILITY = 'profiled_planar_milling_xy'
REFERENCE = 'z_extruded_capsule_minus_disjoint_through_cylinders'


def feature_nominals(feature):
    from .milling_features import CONTRACTS, required_nominals
    from .sequence_geometry import KINDS
    values = required_nominals(CONTRACTS[KINDS[feature['kind']]], feature)
    if feature['kind'] == 'planar_top':
        values.pop('outline_xy')
    return values


def validate_hole_retention(outline, holes):
    """Reject holes touching the final outer boundary, without mesh evidence."""
    from shapely.geometry import Point
    region = profile(outline, 2048)
    for hole in holes:
        center = Point(hole['center_xy_mm'])
        if not region.contains(center) or region.boundary.distance(center) <= hole['diameter_mm']/2 + 1e-8:
            raise ValueError('Hole intersects or touches the original capsule boundary')


class ProfileSequenceReference:
    def __init__(self, reference, segments=2048):
        from shapely.geometry import Point
        self.low, self.high = (reference['bounds'][k] for k in ['min', 'max'])
        outline, holes = reference['profile_feature'], reference['features']
        validate_hole_retention(outline, holes)
        self.region = profile(outline, segments)
        for hole in holes:
            self.region = self.region.difference(Point(hole['center_xy_mm']).buffer(hole['diameter_mm']/2, quad_segs=segments))
        self.breaks = [self.low[2], self.high[2]]
        area = nominal_area(outline)-sum(math.pi*(f['diameter_mm']/2)**2 for f in holes)
        self.analytic_volume = area*(self.high[2]-self.low[2])

    def at(self, z):
        from shapely.geometry import Polygon
        return self.region if self.low[2] < z < self.high[2] else Polygon()
