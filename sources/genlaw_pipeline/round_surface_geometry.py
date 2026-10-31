"""Independent reference for two longitudinal external rounds on a rectangular solid."""
import math

REFERENCE='rectangular_solid_with_two_top_x_rounds'


class RoundReference:
    def __init__(self,reference):
        if reference['reference_class']!=REFERENCE:raise ValueError('Unsupported round reference')
        self.low,self.high=[reference['bounds'][k] for k in ['min','max']]
        self.radius=reference['radius_mm']
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in self.low+self.high+[self.radius]):
            raise ValueError('Finite original coordinates and radius required')
        length,width,height=[b-a for a,b in zip(self.low,self.high)]
        if min(length,height)<=0 or not 0<self.radius<min(width/2,height):
            raise ValueError('Rounds must retain a central top plane and lower straight walls')
        self.breaks=[self.low[2],self.high[2]-self.radius,self.high[2]]
        self.analytic_volume=length*width*height-2*length*self.radius**2*(1-math.pi/4)

    def inset(self,z):
        u=max(0,z-(self.high[2]-self.radius))
        return self.radius-math.sqrt(max(0,self.radius**2-u*u))

    def at(self,z):
        from shapely.geometry import Polygon,box
        if not self.low[2]<z<self.high[2]:return Polygon()
        d=self.inset(z)
        return box(self.low[0],self.low[1]+d,self.high[0],self.high[1]-d)


def independent_native(shape,spec):
    """Check a saved makeFillet solid with separate corner-box/cylinder booleans."""
    import FreeCAD as App
    import Part
    from .native_stock import bounds
    lo,hi=[spec['stock'][k] for k in ['min','max']];length,width,height=[b-a for a,b in zip(lo,hi)];r=spec['radius_mm']
    expected=Part.makeBox(length,width,height,App.Vector(*lo))
    for side in ['ymin','ymax']:
        center=lo[1]+r if side=='ymin' else hi[1]-r
        corner_y=lo[1] if side=='ymin' else hi[1]-r
        corner=Part.makeBox(length,r,r,App.Vector(lo[0],corner_y,hi[2]-r))
        cylinder=Part.makeCylinder(r,length,App.Vector(lo[0],center,hi[2]-r),App.Vector(1,0,0))
        expected=expected.cut(corner.cut(cylinder))
    delta=shape.cut(expected).Volume+expected.cut(shape).Volume
    cylinders=[{'face':'Face'+str(i),'radius_mm':face.Surface.Radius,
                'axis':list(face.Surface.Axis),'center':list(face.Surface.Center)}
               for i,face in enumerate(shape.Faces,1) if isinstance(face.Surface,Part.Cylinder)]
    reference={'reference_class':REFERENCE,'bounds':bounds(shape),'radius_mm':r}
    analytic=RoundReference(reference)
    if (not shape.isValid() or len(shape.Solids)!=1 or len(cylinders)!=2 or len(shape.Faces)!=8
            or any(abs(c['radius_mm']-r)>1e-8 or abs(abs(c['axis'][0])-1)>1e-8 for c in cylinders)
            or delta>1e-8 or abs(shape.Volume-analytic.analytic_volume)>1e-8
            or any(abs(reference['bounds'][k][i]-spec['stock'][k][i])>1e-8 for k in ['min','max'] for i in range(3))):
        raise ValueError('Original CAD differs from independently constructed two-round solid')
    return {**reference,'status':'verified','valid_single_solid':True,'volume_mm3':shape.Volume,
            'symmetric_difference_mm3':delta,'analytic_volume_mm3':analytic.analytic_volume,
            'cylindrical_faces':cylinders,'construction_check':'Subtract two corner boxes excluding their axial cylinders',
            'original_CAD_reparsed':True,'registration_applied':False}
