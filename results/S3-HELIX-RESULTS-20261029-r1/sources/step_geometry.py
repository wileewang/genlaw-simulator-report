"""Original-coordinate geometry contract for a flat step open at a stock edge."""
STEP='axis_aligned_box_minus_one_z_open_edge_step'
CAPABILITY='edge_step_xy'


def floor_bounds(feature,bounds):
    low=list(bounds['min'][:2]);high=list(bounds['max'][:2])
    axis='XY'.index(feature['side'][0]);coordinate=feature['shoulder_coordinate_mm']
    (low if feature['side'][1]=='+' else high)[axis]=coordinate
    return low,high


def profile(feature,bounds):
    from shapely.geometry import box
    low,high=floor_bounds(feature,bounds)
    return box(*low,*high)


def retained_profile(feature,bounds):
    from shapely.geometry import box
    return box(*bounds['min'][:2],*bounds['max'][:2]).difference(profile(feature,bounds))


class StepReference:
    def __init__(self,reference,segments=None):
        from shapely.geometry import box
        bounds=reference['bounds'];self.low=bounds['min'];self.high=bounds['max'];self.feature=reference['features'][0]
        self.rectangle=box(*self.low[:2],*self.high[:2]);self.void=profile(self.feature,bounds)
        self.floor=self.high[2]-self.feature['depth_mm'];self.breaks=[self.low[2],self.floor,self.high[2]]
        self.analytic_volume=self.rectangle.area*(self.high[2]-self.low[2])-self.void.area*self.feature['depth_mm']
        self.open_section=self.rectangle.difference(self.void)

    def at(self,z):
        from shapely.geometry import Polygon
        if not self.low[2]<z<self.high[2]:return Polygon()
        return self.open_section if z>self.floor else self.rectangle
