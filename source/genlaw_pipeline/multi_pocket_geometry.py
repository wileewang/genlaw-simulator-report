"""Independent Z sections for disjoint closed pockets at individual floor heights."""
import bisect
import math

from .pocket_geometry import profile, nominal_area
from .pocket_sequence_geometry import hole_locations

CAPABILITY = 'multiple_pocketed_planar_milling_xy'
REFERENCE = 'z_layered_box_minus_disjoint_rounded_pockets_and_through_cylinders'
MAX_POCKETS = 8


def section_requests(bottom, top, layer_breaks):
    """Choose original-CAD samples away from other floors, before reading a mesh.

    A hole-wall sample can coincide with a neighbouring pocket's floor. Such a
    slice is a topology boundary, not an interior contour. Binary STL stores
    float32 vertices, so a few representable units around that plane are also
    excluded from *sample placement*, without rounding any geometry/measurements.
    """
    breaks=sorted(set([bottom,top]+[z for z in layer_breaks if bottom<z<top]))
    clearance=max(1e-7,max(abs(z) for z in breaks)*2**-21)
    rows=[]
    for i in range(11):
        nominal=bottom+(.05+.09*i)*(top-bottom);requested=nominal;avoided=None
        for j,z in enumerate(breaks[1:-1],1):
            if abs(nominal-z)<=clearance:
                shift=min((top-bottom)/40,(breaks[j+1]-z)/4)
                if shift<=clearance:raise ValueError('Original layer spacing is below stable STL contour sampling precision')
                requested=z+shift;avoided=z;break
        rows.append({'nominal_z_mm':nominal,'requested_z_mm':requested,'avoided_layer_z_mm':avoided,
                     'float32_selection_clearance_mm':clearance})
    return rows


def validate_combination(pockets, holes):
    """Reject intersections/tangencies; keep every hole associated with its cavity."""
    boundaries = [profile(f, 2048) for f in pockets]
    for i, first in enumerate(boundaries):
        for j in range(i + 1, len(boundaries)):
            # Include the arc polygon's maximum sagitta in the exclusion band.
            error = sum(pockets[k]['corner_radius_mm'] for k in (i, j)) * (1-math.cos(math.pi/8192))
            if first.distance(boundaries[j]) <= 1e-7 + error:
                raise ValueError('型腔相交、嵌套或相切尚未支持；须明确互不相交的完整型腔。')
    locations = {h['id']: None for h in holes}
    for pocket in pockets:
        for hid, location in hole_locations(pocket, holes).items():
            if location == 'pocket_floor':
                if locations[hid] is not None:
                    raise ValueError('同一孔不能属于多个不相交型腔。')
                locations[hid] = pocket['id']
    return locations


def hole_wall_top(reference, hole):
    top = reference['bounds']['max'][2]
    for pocket in reference.get('pocket_features', []):
        if hole_locations(pocket, [hole])[hole['id']] == 'pocket_floor':
            return top - pocket['depth_mm']
    return top


def floor_matches(shape, feature, top_z, Part):
    """Height alone is ambiguous when multiple cavities have the same depth."""
    x, y = feature['center_xy_mm']; w, h = feature['width_mm'], feature['height_mm']
    expected = [x-w/2, y-h/2, top_z-feature['depth_mm'], x+w/2, y+h/2]
    result = []
    for i, face in enumerate(shape.Faces, 1):
        b = face.BoundBox
        actual = [b.XMin, b.YMin, b.ZMax, b.XMax, b.YMax]
        if (isinstance(face.Surface, Part.Plane) and abs(face.normalAt(0, 0).z-1)<1e-8
                and b.ZLength<1e-8 and all(abs(a-e)<1e-8 for a, e in zip(actual, expected))):
            result.append((i, face))
    return result


class MultiPocketReference:
    def __init__(self, reference, segments):
        from shapely.geometry import box, Point
        from shapely.ops import unary_union
        self.low, self.high = [reference['bounds'][k] for k in ('min', 'max')]
        pockets = reference.get('pocket_features', []); holes = reference['features']
        validate_combination(pockets, holes)
        rectangle = box(*self.low[:2], *self.high[:2])
        circles = unary_union([Point(h['center_xy_mm']).buffer(h['diameter_mm']/2, quad_segs=segments) for h in holes])
        profiles = [(self.high[2]-p['depth_mm'], profile(p, segments)) for p in pockets]
        self.breaks = sorted(set([self.low[2], self.high[2]] + [z for z, _ in profiles]))
        self.layers = [rectangle.difference(unary_union([circles] + [area for z, area in profiles if z <= lo]))
                       for lo in self.breaks[:-1]]
        self.analytic_volume = rectangle.area*(self.high[2]-self.low[2])
        self.analytic_volume -= sum(nominal_area(p)*p['depth_mm'] for p in pockets)
        self.analytic_volume -= sum(math.pi*(h['diameter_mm']/2)**2*(hole_wall_top(reference, h)-self.low[2]) for h in holes)

    def at(self, z):
        from shapely.geometry import Polygon
        if not self.low[2] < z < self.high[2]:
            return Polygon()
        return self.layers[bisect.bisect_right(self.breaks, z)-1]
