"""Height-dependent sections of native-checked through and conical blind holes."""
import math
import numpy as np
from shapely.geometry import Point,Polygon,box
from .drilling_geometry import blind_levels


class DrilledReference:
    def __init__(self, reference, segments):
        self.low=reference['bounds']['min'];self.high=reference['bounds']['max']
        self.rectangle=box(*self.low[:2],*self.high[:2]);self.holes=[];self.breaks=list([self.low[2],self.high[2]])
        volume=self.rectangle.area*(self.high[2]-self.low[2])
        for feature in reference['features']:
            radius=feature['diameter_mm']/2;center=Point(*feature['center_xy_mm'])
            if feature['kind']=='blind_hole':
                shoulder,tip=blind_levels(feature,self.high[2]);self.breaks.extend([shoulder,tip])
                volume-=math.pi*radius**2*(feature['cylindrical_depth_mm']+(shoulder-tip)/3)
            else:
                shoulder=tip=self.low[2];volume-=math.pi*radius**2*(self.high[2]-self.low[2])
            self.holes.append((center,radius,shoulder,tip,center.buffer(radius,quad_segs=segments),segments))
        self.analytic_volume=volume

    def at(self,z):
        if not self.low[2]<z<self.high[2]:return Polygon()
        section=self.rectangle
        for center,radius,shoulder,tip,circle,segments in self.holes:
            if z<=tip:continue
            cutter=circle if z>=shoulder else center.buffer(radius*(z-tip)/(shoulder-tip),quad_segs=segments)
            section=section.difference(cutter)
        return section


def integrate_variable(sections,levels,target,order):
    nodes,weights=np.polynomial.legendre.leggauss(order);volumes=np.zeros(3,dtype=np.float64)
    for lo,hi in zip(levels[:-1],levels[1:]):
        if hi-lo<=1e-10:continue
        half=(hi-lo)/2
        for node,weight in zip(nodes,weights):
            z=(hi+lo)/2+half*node;actual=sections.at(z);reference=target.at(z)
            volumes+=weight*half*np.array([actual.area,reference.area,actual.intersection(reference).area])
    a,b,intersection=volumes;union=a+b-intersection
    if union<=0 or not -1e-8<=intersection<=min(a,b)+1e-8:raise ValueError('Invalid variable-section intersection')
    return {'actual_volume_mm3':float(a),'target_volume_mm3':float(b),'intersection_mm3':float(intersection),
            'union_mm3':float(union),'iou':float(intersection/union),'alternative_intersection_mm3':0.,'quadrature_order':order}
