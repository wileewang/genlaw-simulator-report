"""Independent planar profile contract for the first prismatic milling feature."""
import math

POCKET='axis_aligned_box_minus_one_z_rounded_rectangular_pocket'
CAPABILITY='rounded_pocket_xy'


def profile(feature,segments=1024,inset=0):
    from shapely.geometry import box
    x,y=feature['center_xy_mm'];w=feature['width_mm'];h=feature['height_mm'];r=feature['corner_radius_mm']
    core=box(x-w/2+r,y-h/2+r,x+w/2-r,y+h/2-r)
    return core.buffer(r-inset,quad_segs=segments) if r>inset else core.buffer(-(inset-r),join_style='mitre') if inset>r else core


def nominal_area(feature):
    return feature['width_mm']*feature['height_mm']-(4-math.pi)*feature['corner_radius_mm']**2


class PocketReference:
    def __init__(self,reference,segments):
        from shapely.geometry import box
        self.low=reference['bounds']['min'];self.high=reference['bounds']['max'];self.feature=reference['features'][0]
        self.rectangle=box(*self.low[:2],*self.high[:2]);self.void=profile(self.feature,segments)
        self.floor=self.high[2]-self.feature['depth_mm'];self.breaks=[self.low[2],self.floor,self.high[2]]
        self.analytic_volume=self.rectangle.area*(self.high[2]-self.low[2])-nominal_area(self.feature)*self.feature['depth_mm']
        self.open_section=self.rectangle.difference(self.void)

    def at(self,z):
        from shapely.geometry import Polygon
        if not self.low[2]<z<self.high[2]:return Polygon()
        return self.open_section if z>self.floor else self.rectangle
