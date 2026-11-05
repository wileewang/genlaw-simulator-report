"""Original planar top of a box; incoming stock is a separate resource."""

FACING = 'axis_aligned_box_after_top_facing'
CAPABILITY = 'top_facing_xy'


class FacingReference:
    def __init__(self, reference, segments=2048):
        from shapely.geometry import box
        self.low, self.high = (reference['bounds'][key] for key in ['min', 'max'])
        self.rectangle = box(*self.low[:2], *self.high[:2])
        self.breaks = [self.low[2], self.high[2]]
        self.analytic_volume = self.rectangle.area * (self.high[2] - self.low[2])

    def at(self, z):
        from shapely.geometry import Polygon
        return self.rectangle if self.low[2] < z < self.high[2] else Polygon()
