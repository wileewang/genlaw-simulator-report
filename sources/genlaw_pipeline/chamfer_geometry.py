"""Independent sections of a box with equal 45-degree top perimeter chamfers."""
import math

REFERENCE='box_with_equal_45_degree_top_perimeter_chamfers'
CAPABILITY='rectangular_top_chamfer_xy'


def nominals(feature):
    return {'chamfer_sections':{'width_mm':feature['width_mm'],'angle_degrees':45},
            'chamfer_width':feature['width_mm'],'retained_thickness':feature['thickness_mm'],
            'retained_planes':{'top_z_mm':feature['top_z_mm'],'bottom_z_mm':feature['top_z_mm']-feature['thickness_mm']}}


class ChamferReference:
    def __init__(self,reference,segments=2048):
        from shapely.geometry import box
        self.low,self.high=(reference['bounds'][k] for k in ['min','max'])
        features=reference['features']
        if len(features)!=1 or features[0]['kind']!='top_perimeter_chamfer_45':
            raise ValueError('Only one complete rectangular top perimeter is supported')
        self.width=features[0]['width_mm']
        values=self.low+self.high+[self.width]
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in values):
            raise ValueError('Finite native chamfer dimensions required')
        length,width,height=[b-a for a,b in zip(self.low,self.high)]
        if not 0<self.width<min(length/2,width/2,height):
            raise ValueError('Chamfer must retain a top rectangle and vertical walls')
        self.breaks=[self.low[2],self.high[2]-self.width,self.high[2]]
        self.analytic_volume=length*width*height-(length+width)*self.width**2+4*self.width**3/3
        self.rectangle=box(*self.low[:2],*self.high[:2])

    def at(self,z):
        from shapely.geometry import Polygon,box
        if not self.low[2]<z<self.high[2]:return Polygon()
        inset=max(0,self.width+z-self.high[2])
        return box(self.low[0]+inset,self.low[1]+inset,self.high[0]-inset,self.high[1]-inset)
