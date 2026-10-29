"""Bounded line/circular CAD loops for a single planar milling floor.

Exact Green integrals provide the analytic area. Polygonal sections are refined
independently for IoU; their volume must still agree with the original BREP.
"""
import math

REGION='axis_aligned_box_minus_one_planar_floor_region'
CAPABILITY='planar_floor_xy'


def point(value):
    if not isinstance(value,list) or len(value)!=2 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in value):
        raise ValueError('Profile coordinates must be finite XY pairs')
    return value


def arc_data(segment):
    a,b,c=[point(segment[k]) for k in ['start','end','center']]
    r=math.dist(a,c)
    if r<1e-8 or abs(math.dist(b,c)-r)>1e-8 or math.dist(a,b)<1e-8:raise ValueError('Invalid arc endpoints or radii; full circles use circle records')
    start=math.atan2(a[1]-c[1],a[0]-c[0]);end=math.atan2(b[1]-c[1],b[0]-c[0])
    direction=segment['direction']
    if direction not in ('ccw','cw'):raise ValueError('Arc direction must be explicit')
    sweep=(end-start)%(2*math.pi) if direction=='ccw' else -((start-end)%(2*math.pi))
    return c,r,start,sweep


def validate_loop(loop):
    if not isinstance(loop,list) or not 1<=len(loop)<=128:raise ValueError('Profile loop segment budget exceeded')
    if len(loop)==1 and loop[0].get('kind')=='circle':
        s=loop[0];point(s['center']);r=s['radius_mm']
        if set(s)!={'kind','center','radius_mm'} or isinstance(r,bool) or not isinstance(r,(int,float)) or not math.isfinite(r) or r<=0:raise ValueError('Invalid circle')
        return
    for i,s in enumerate(loop):
        kind=s.get('kind');fields={'kind','start','end'}|({'center','direction'} if kind=='arc' else set())
        if kind not in ('line','arc') or set(s)!=fields:raise ValueError('Only explicit line and circular arc segments are supported')
        a,b=point(s['start']),point(s['end'])
        if math.dist(a,b)<1e-8 or math.dist(b,point(loop[(i+1)%len(loop)]['start']))>1e-8:raise ValueError('Degenerate or disconnected CAD loop')
        if kind=='arc':arc_data(s)


def loop_area(loop):
    validate_loop(loop)
    if len(loop)==1 and loop[0]['kind']=='circle':return math.pi*loop[0]['radius_mm']**2
    terms=[]
    for s in loop:
        a,b=s['start'],s['end']
        if s['kind']=='line':terms.append((a[0]*b[1]-a[1]*b[0])/2)
        else:
            c,r,angle,sweep=arc_data(s)
            terms.append((c[0]*(b[1]-a[1])-c[1]*(b[0]-a[0])+r*r*sweep)/2)
    return abs(math.fsum(terms))


def sample_loop(loop,segments=2048):
    validate_loop(loop)
    if not isinstance(segments,int) or not 8<=segments<=2048:raise ValueError('Profile refinement budget exceeded')
    points=[]
    for s in loop:
        if s['kind']=='line':points.append(s['start']);continue
        if s['kind']=='circle':c,r,start,sweep=s['center'],s['radius_mm'],0,2*math.pi
        else:c,r,start,sweep=arc_data(s)
        count=max(2,math.ceil(abs(sweep)/(math.pi/2)*segments))
        if len(points)+count>65536:raise ValueError('Profile vertex budget exceeded')
        points.extend([[c[0]+r*math.cos(start+sweep*i/count),c[1]+r*math.sin(start+sweep*i/count)] for i in range(count)])
    points.append(points[0]);return points


def profile(feature,segments=2048):
    from shapely.geometry import Polygon
    p=feature['profile']
    if not isinstance(p,dict) or set(p)!={'outer','islands'} or not isinstance(p['islands'],list) or len(p['islands'])>8:raise ValueError('Invalid bounded CAD floor profile')
    result=Polygon(sample_loop(p['outer'],segments),[sample_loop(w,segments) for w in p['islands']])
    if not result.is_valid or result.is_empty or result.area<=1e-8:raise ValueError('Self-intersecting, nested or empty CAD floor profile')
    return result


def nominal_area(feature):
    p=feature['profile'];return loop_area(p['outer'])-math.fsum(loop_area(w) for w in p['islands'])


class RegionReference:
    def __init__(self,reference,segments=2048):
        from shapely.geometry import box
        bounds=reference['bounds'];self.low=bounds['min'];self.high=bounds['max'];f=reference['features'][0]
        self.rectangle=box(*self.low[:2],*self.high[:2]);self.void=profile(f,segments)
        self.floor=self.high[2]-f['depth_mm'];self.breaks=[self.low[2],self.floor,self.high[2]]
        self.open_section=self.rectangle.difference(self.void)
        self.analytic_volume=self.rectangle.area*(self.high[2]-self.low[2])-nominal_area(f)*f['depth_mm']

    def at(self,z):
        from shapely.geometry import Polygon
        if not self.low[2]<z<self.high[2]:return Polygon()
        return self.open_section if z>self.floor else self.rectangle
