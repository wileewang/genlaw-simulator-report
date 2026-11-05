"""Read exact line/circle floor boundaries and original stock-edge identities."""
import math
from .region_geometry import arc_data,nominal_area


def xy(v):return [float(v.x),float(v.y)]


def read_loop(wire):
    import Part
    edges=list(wire.Edges)
    if len(edges)==1 and isinstance(edges[0].Curve,Part.Circle) and edges[0].isClosed():
        c=edges[0].Curve;return [{'kind':'circle','center':xy(c.Center),'radius_mm':float(c.Radius)}]
    if not 2<=len(edges)<=128:raise ValueError('Native boundary segment budget exceeded')
    ordered=[];remaining=list(edges);first=remaining.pop(0)
    if len(first.Vertexes)!=2:raise ValueError('Unsupported boundary vertex topology')
    start=first.Vertexes[0].Point;end=first.Vertexes[1].Point;origin=start
    while True:
        c=first.Curve;record={'start':xy(start),'end':xy(end)}
        if isinstance(c,Part.Line):record['kind']='line'
        elif isinstance(c,Part.Circle):
            center=c.Center;mid=first.valueAt((first.FirstParameter+first.LastParameter)/2)
            a,b,m=[math.atan2(p.y-center.y,p.x-center.x) for p in [start,end,mid]]
            record.update(kind='arc',center=xy(center),direction='ccw' if (m-a)%(2*math.pi)<(b-a)%(2*math.pi) else 'cw')
        else:raise ValueError('Native floor contains an unsupported ellipse, spline or other curve')
        ordered.append(record)
        if not remaining:break
        candidates=[]
        for i,e in enumerate(remaining):
            if len(e.Vertexes)!=2:raise ValueError('Unsupported mixed closed boundary')
            for k,v in enumerate(e.Vertexes):
                if v.Point.distanceToPoint(end)<1e-8:candidates.append((i,k))
        if len(candidates)!=1:raise ValueError('Native wire connectivity is ambiguous')
        i,k=candidates[0];first=remaining.pop(i);start=first.Vertexes[k].Point;end=first.Vertexes[1-k].Point
    if end.distanceToPoint(origin)>1e-8:raise ValueError('Native wire does not close')
    return ordered


def read_floor(shape,depth,bounds):
    import Part
    z=bounds['max'][2]-depth
    floors=[(i,f) for i,f in enumerate(shape.Faces,1) if isinstance(f.Surface,Part.Plane) and f.BoundBox.ZLength<1e-8 and abs(f.BoundBox.ZMin-z)<1e-8]
    if len(floors)!=1:raise ValueError('Need one uniquely identified planar floor at declared depth')
    index,floor=floors[0];outer=floor.OuterWire
    islands=[read_loop(w) for w in floor.Wires if not w.isSame(outer)]
    feature={'id':'P1','kind':'planar_floor','axis':[0,0,1],'depth_mm':depth,'floor_face':'Face'+str(index),
             'profile':{'outer':read_loop(outer),'islands':islands}}
    if abs(nominal_area(feature)-floor.Area)>1e-8:raise ValueError('Exact curve area disagrees with original CAD floor')
    opened=[]
    for edge in outer.Edges:
        if not isinstance(edge.Curve,Part.Line):continue
        ends=[xy(v.Point) for v in edge.Vertexes]
        if any(all(abs(p[k]-value)<1e-8 for p in ends) for k in range(2) for value in (bounds['min'][k],bounds['max'][k])):opened.append(edge)
    groups=[]
    for group in Part.sortEdges(opened) if opened else []:
        ids=[]
        for edge in group:
            matches=[i for i,e in enumerate(shape.Edges,1) if e.isSame(edge)]
            if len(matches)!=1:raise ValueError('Original open-edge identity is ambiguous')
            ids.append(matches[0])
        groups.append(ids)
    feature['open_edge_groups']=groups
    return feature,floor


def make_face(profile,z):
    import FreeCAD as App
    import Part
    def wire(loop):
        edges=[]
        for s in loop:
            if s['kind']=='circle':edges.append(Part.makeCircle(s['radius_mm'],App.Vector(*s['center'],z)));continue
            a,b=App.Vector(*s['start'],z),App.Vector(*s['end'],z)
            if s['kind']=='line':edges.append(Part.makeLine(a,b))
            else:
                c,r,start,sweep=arc_data(s);mid=App.Vector(c[0]+r*math.cos(start+sweep/2),c[1]+r*math.sin(start+sweep/2),z)
                edges.append(Part.Arc(a,mid,b).toShape())
        return Part.Wire(edges)
    face=Part.Face(wire(profile['outer']))
    for inner in profile['islands']:face=face.cut(Part.Face(wire(inner)))
    if len(face.Faces)!=1 or not face.isValid():raise ValueError('Declared native planar profile is invalid')
    return face.Faces[0]
