"""Original native CAD binding for layered pocket/through-hole compositions."""
import json
import math
from pathlib import Path
from .native_stock import bounds
from .native_planar_cam import write
from .preparation import digest
from .pocket_geometry import nominal_area
from .pocket_sequence_geometry import REFERENCE, hole_locations


def bind_geometry(request, plan, export_display=None):
    import FreeCAD as App
    import Part
    out = Path(request['output']); root = Path(request['input']).parent
    source = root/plan['cad_ref']['path']
    shape = Part.Shape(); shape.read(str(source))
    lo, hi = [plan['target_bounds_mm'][k] for k in ('min', 'max')]
    features = [op['geometry'] for op in plan['operations']]
    top = features[0]; holes = [f for f in features if f['kind']=='through_hole']
    f = next(f for f in features if f['kind']=='rounded_rectangular_pocket')
    locations = hole_locations(f, holes)
    x, y = f['center_xy_mm']; w, h, r, d = [f[k] for k in ('width_mm','height_mm','corner_radius_mm','depth_mm')]
    floor_z = hi[2]-d
    box = Part.makeBox(*[b-a for a,b in zip(lo,hi)], App.Vector(*lo))
    # Different construction from the archived filleted-prism target / oracles.
    cutter = Part.makeBox(w-2*r,h,d,App.Vector(x-w/2+r,y-h/2,floor_z)).fuse(
        Part.makeBox(w,h-2*r,d,App.Vector(x-w/2,y-h/2+r,floor_z)))
    for dx in (-1,1):
        for dy in (-1,1): cutter = cutter.fuse(Part.makeCylinder(r,d,App.Vector(x+dx*(w/2-r),y+dy*(h/2-r),floor_z)))
    cutter = cutter.removeSplitter()
    cylinders = {h['id']:Part.makeCylinder(h['diameter_mm']/2,hi[2]-lo[2],App.Vector(*h['center_xy_mm'],lo[2])) for h in holes}
    expected = box.cut(cutter)
    for cylinder in cylinders.values(): expected = expected.cut(cylinder)
    delta = shape.cut(expected).Volume + expected.cut(shape).Volume
    if not shape.isValid() or len(shape.Solids)!=1 or bounds(shape)!=plan['target_bounds_mm'] or delta>1e-8:
        raise ValueError('Original CAD disagrees with layered pocket / hole composition')
    def horizontal(z):
        return [(i,face) for i,face in enumerate(shape.Faces,1) if isinstance(face.Surface,Part.Plane)
                and abs(face.normalAt(0,0).z-1)<1e-8 and face.BoundBox.ZLength<1e-8 and abs(face.BoundBox.ZMax-z)<1e-8]
    tops, floors = horizontal(hi[2]), horizontal(floor_z)
    if len(tops)!=1 or len(floors)!=1 or top['top_face']!='Face'+str(tops[0][0]) or f['floor_face']!='Face'+str(floors[0][0]):
        raise ValueError('Original top / pocket floor face binding differs')
    outside = [h for h in holes if locations[h['id']]=='top_land']; inside = [h for h in holes if locations[h['id']]=='pocket_floor']
    area = lambda hs: sum(math.pi*(h['diameter_mm']/2)**2 for h in hs)
    top_face, floor_face = tops[0][1], floors[0][1]
    if (len(top_face.Wires)!=2+len(outside) or len(floor_face.Wires)!=1+len(inside)
        or abs(top_face.Area-((hi[0]-lo[0])*(hi[1]-lo[1])-nominal_area(f)-area(outside)))>1e-8
        or abs(floor_face.Area-(nominal_area(f)-area(inside)))>1e-8):
        raise ValueError('Original pocket / perforation topology differs')
    # FreeCAD PocketShape.UseOutline explicitly uses Wire1. Prove it is the
    # complete outer boundary, not a circular inner opening, before enabling it.
    whole_floor = Part.Face(floor_face.Wire1)
    cutter_floor = next(face for face in cutter.Faces if isinstance(face.Surface,Part.Plane)
                        and face.BoundBox.ZLength<1e-8 and abs(face.BoundBox.ZMax-floor_z)<1e-8)
    if (abs(whole_floor.Area-nominal_area(f))>1e-8 or
        whole_floor.cut(cutter_floor).Area+cutter_floor.cut(whole_floor).Area>1e-8):
        raise ValueError('PocketShape Wire1 is not the independent whole pocket outline')
    for hole in holes:
        x,y = hole['center_xy_mm']; radius=hole['diameter_mm']/2
        walls=[(i,face) for i,face in enumerate(shape.Faces,1) if isinstance(face.Surface,Part.Cylinder)
               and abs(face.Surface.Radius-radius)<1e-8 and abs(face.Surface.Center.x-x)<1e-8
               and abs(face.Surface.Center.y-y)<1e-8 and abs(abs(face.Surface.Axis.z)-1)<1e-8]
        wall_top=floor_z if locations[hole['id']]=='pocket_floor' else hi[2]
        if (len(walls)!=1 or hole['cylinder_face']!='Face'+str(walls[0][0])
            or abs(walls[0][1].BoundBox.ZMin-lo[2])>1e-8 or abs(walls[0][1].BoundBox.ZMax-wall_top)>1e-8):
            raise ValueError('Original cylindrical wall binding / retained height differs')
    ref={'schema':'genlaw.native-binding.v12','status':'verified','cad_sha256':digest(source),
        'reference_class':REFERENCE,'bounds':bounds(shape),'volume_mm3':shape.Volume,'valid_single_solid':True,
        'incoming_stock_bounds':plan['stock']['bounds_mm'],'features':holes,'facing_feature':top,'pocket_feature':f,
        'feature_bindings':features,'hole_locations':locations,'original_cad_reparsed':True,'symmetric_difference_mm3':delta,
        'pocket_outer_wire_verified':True,'reference_for_iou':'Independent rectangle / corner-cylinder union and full-height holes, native symmetric difference checked against original filleted-prism CAD.'}
    write(out/'native-geometry.json',ref)
    if export_display:export_display(shape,out/'target-display.stl')
    if not plan['expected_states_ref']:raise ValueError('Pocket sequence needs pre-NC independent state oracles')
    oracles=json.loads((root/plan['expected_states_ref']['path']).read_text())['states']
    current=box; completed=[]; completed_holes=[]; pocket_done=False
    for i,op in enumerate(plan['operations']):
        feature=op['geometry'];completed.append(feature['id'])
        if feature['kind']=='rounded_rectangular_pocket':current=current.cut(cutter);pocket_done=True
        elif feature['kind']=='through_hole':current=current.cut(cylinders[feature['id']]);completed_holes.append(feature)
        oracle=oracles[i];path=root/oracle['cad']['path'];observed=Part.Shape();observed.read(str(path))
        difference=current.cut(observed).Volume+observed.cut(current).Volume
        if (digest(path)!=oracle['cad']['sha256'] or not observed.isValid() or len(observed.Solids)!=1
            or difference>1e-8 or abs(observed.Volume-oracle['volume_mm3'])>1e-8 or bounds(current)!=plan['target_bounds_mm']):
            raise ValueError('Independent archived material oracle differs')
        record={'schema':'genlaw.native-binding.v12','status':'verified','reference_class':REFERENCE,
            'cad_sha256':digest(path),'oracle_ref':oracle['cad'],'symmetric_difference_mm3':difference,
            'bounds':bounds(current),'volume_mm3':current.Volume,'valid_single_solid':True,
            'incoming_stock_bounds':plan['stock']['bounds_mm'],'features':list(completed_holes),'facing_feature':top,
            'feature_bindings':features[:i+1],'original_final_cad_sha256':digest(source),'completed_features':list(completed),
            'reference_for_iou':'Frozen pre-NC independent cumulative CAD, never simulation stock.'}
        if pocket_done:record['pocket_feature']=f
        if i<len(plan['operations'])-1:
            folder=out/'checkpoints'/f'after-{i+1:02d}';folder.mkdir(parents=True)
            write(folder/'native-geometry.json',record)
            if export_display:export_display(current,folder/'target-display.stl')
    return shape,ref
