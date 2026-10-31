"""Original-CAD sections for one or two parallel, explicitly selected top edges."""
import math

REFERENCE = 'box_with_selected_parallel_top_chamfers_45'
SIDES = ('xmin', 'xmax', 'ymin', 'ymax')
ACCEPTANCE = {'iou_percent_min': 99., 'bounds_deviation_mm_max': .025,
    'volume_deviation_mm3_max': .03, 'section_deviation_mm_max': .1,
    'selected_edge_width_deviation_mm_max': .1, 'retained_surface_deviation_mm_max': .1,
    'numerical_guard_mm': .05, 'quadrature_orders': [2, 3],
    'quadrature_iou_difference_max': 1e-6, 'target_integral_error_mm3_max': 1e-8,
    'mesh_integral_error_mm3_max': 1e-4, 'scope': 'software_regression',
    'manufacturing_tolerance_approved': False}


def dimensions(bounds, width, sides):
    if set(bounds) != {'min', 'max'} or any(len(bounds[k]) != 3 for k in bounds):
        raise ValueError('Require explicit original XYZ bounds')
    values = bounds['min'] + bounds['max'] + [width]
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise ValueError('Require finite dimensions, excluding boolean values')
    sizes = [b-a for a, b in zip(bounds['min'], bounds['max'])]
    if (not isinstance(sides, list) or not 1 <= len(sides) <= 2
            or len(set(sides)) != len(sides) or any(s not in SIDES for s in sides)
            or sides != [s for s in SIDES if s in sides]
            or len({s[0] for s in sides}) != 1):
        raise ValueError('Select one or two parallel top edges in canonical order')
    if not 0 < width < min(sizes[0]/2, sizes[1]/2, sizes[2]):
        raise ValueError('Chamfer must retain the top and original vertical walls')
    return sizes


class SelectedChamferReference:
    def __init__(self, reference):
        if reference['reference_class'] != REFERENCE:
            raise ValueError('Unsupported selected-edge reference')
        self.low, self.high = [reference['bounds'][k] for k in ('min', 'max')]
        self.width, self.sides = reference['width_mm'], reference['selected_sides']
        length, width, height = dimensions(reference['bounds'], self.width, self.sides)
        nx, ny = (sum(s[0] == axis for s in self.sides) for axis in 'xy')
        self.analytic_volume = (length*width*height
            - (width*nx + length*ny)*self.width**2/2 + nx*ny*self.width**3/3)
        self.breaks = [self.low[2], self.high[2]-self.width, self.high[2]]

    def at(self, z):
        from shapely.geometry import Polygon, box
        if not self.low[2] < z < self.high[2]:
            return Polygon()
        inset = max(0, self.width+z-self.high[2])
        lo, hi = list(self.low[:2]), list(self.high[:2])
        for side in self.sides:
            axis = 'xy'.index(side[0])
            if side.endswith('min'): lo[axis] += inset
            else: hi[axis] -= inset
        return box(*lo, *hi)


def wedge_points(bounds, width, side):
    """Independent triangular cuts; the input builder uses OCC makeChamfer."""
    if side not in SIDES: raise ValueError('Unknown selected edge')
    lo, hi = bounds['min'], bounds['max']
    axis = 'xy'.index(side[0]); along = 1-axis
    anchor = [lo[0], lo[1], hi[2]]
    anchor[axis] = lo[axis] if side.endswith('min') else hi[axis]
    inside = list(anchor); inside[axis] += width if side.endswith('min') else -width
    bottom = list(anchor); bottom[2] -= width
    direction = [0, 0, 0]; direction[along] = hi[along]-lo[along]
    return [anchor, inside, bottom], direction


def side_of_top_edge(edge, bounds):
    """Name a full original box edge by geometry, never by unstable OCC number."""
    lo, hi = bounds['min'], bounds['max']; points = [v.Point for v in edge.Vertexes]
    if len(points) != 2 or any(abs(p.z-hi[2]) > 1e-8 for p in points): return None
    matches = []
    for side in SIDES:
        axis = 'xy'.index(side[0]); along = 1-axis
        bound = lo[axis] if side.endswith('min') else hi[axis]
        if (all(abs(p[axis]-bound) <= 1e-8 for p in points)
                and abs(abs(points[0][along]-points[1][along])-(hi[along]-lo[along])) <= 1e-8):
            matches.append(side)
    if len(matches) > 1: raise ValueError('Ambiguous original edge')
    return matches[0] if matches else None


def selected_faces(shape, sides):
    import Part
    result = {}
    for i, face in enumerate(shape.Faces, 1):
        if not isinstance(face.Surface, Part.Plane): raise ValueError('Non-planar target face')
        n = face.normalAt(0, 0)
        if abs(n.z-math.sqrt(.5)) > 1e-8: continue
        found = []
        for side in SIDES:
            axis = 'xy'.index(side[0]); sign = -1 if side.endswith('min') else 1
            if abs(n[axis]-sign*math.sqrt(.5)) < 1e-8 and abs(n[1-axis]) < 1e-8:
                found.append(side)
        if len(found) != 1 or found[0] in result: raise ValueError('Ambiguous chamfer face')
        result[found[0]] = 'Face'+str(i)
    if set(result) != set(sides): raise ValueError('CAD bevel faces differ from selected edges')
    return {side: result[side] for side in sides}
