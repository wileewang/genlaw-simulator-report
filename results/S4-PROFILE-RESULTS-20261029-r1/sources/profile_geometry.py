"""Straight-sided capsule outlines; the CAD reference is independent of NC."""
import math

CAPABILITY = 'capsule_profile_xy'
REFERENCE = 'z_extruded_capsule_with_semicircular_ends'


def endpoints(feature):
    center = feature['center_xy_mm']; half = (feature['length_mm']-feature['width_mm'])/2
    axis = 'XY'.index(feature['major_axis'])
    a, b = list(center), list(center); a[axis] -= half; b[axis] += half
    return a, b


def profile(feature, segments=2048):
    from shapely.geometry import LineString
    return LineString(endpoints(feature)).buffer(feature['width_mm']/2, quad_segs=segments)


def nominal_area(feature):
    width = feature['width_mm']
    return (feature['length_mm']-width)*width+math.pi*(width/2)**2


def nominals(feature):
    return {
        'outer_profile_xy': {k:feature[k] for k in ['center_xy_mm','major_axis','length_mm','width_mm']},
        'retained_thickness': feature['thickness_mm'],
        'retained_planes': {'top_z_mm':feature['top_z_mm'], 'bottom_z_mm':feature['top_z_mm']-feature['thickness_mm']},
        'exterior_clearance': 'all_material_outside_original_capsule_removed_through_stock',
    }


class ProfileReference:
    def __init__(self, reference, segments=2048):
        self.low, self.high = (reference['bounds'][k] for k in ['min','max'])
        feature = reference['features'][0]
        self.region = profile(feature, segments)
        self.breaks = [self.low[2],self.high[2]]
        self.analytic_volume = nominal_area(feature)*(self.high[2]-self.low[2])

    def at(self,z):
        from shapely.geometry import Polygon
        return self.region if self.low[2]<z<self.high[2] else Polygon()
