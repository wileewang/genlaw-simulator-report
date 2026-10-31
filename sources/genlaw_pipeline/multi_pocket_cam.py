"""Bind each original pocket floor and cumulative CAD before native toolpaths."""
import json
import math
from pathlib import Path
from .native_stock import bounds
from .native_planar_cam import write
from .preparation import digest
from .pocket_geometry import nominal_area
from .multi_pocket_geometry import REFERENCE, validate_combination, floor_matches, hole_wall_top


def bind_geometry(request,plan,export_display=None):
    import FreeCAD as App
    import Part
    out=Path(request['output']);root=Path(request['input']).parent;source=root/plan['cad_ref']['path']
    shape=Part.Shape();shape.read(str(source));lo,hi=[plan['target_bounds_mm'][k] for k in ('min','max')]
    features=[op['geometry'] for op in plan['operations']];top=features[0]
    holes=[f for f in features if f['kind']=='through_hole'];pockets=[f for f in features if f['kind']=='rounded_rectangular_pocket']
    locations=validate_combination(pockets,holes)
    box=Part.makeBox(*[b-a for a,b in zip(lo,hi)],App.Vector(*lo));cutters={}
    for f in pockets:
        x,y=f['center_xy_mm'];w,h,r,d=[f[k] for k in ('width_mm','height_mm','corner_radius_mm','depth_mm')];z=hi[2]-d
        # Independent union of rectangles and corner cylinders, rather than the
        # filleted prism used by the pre-NC original CAD / state oracle builder.
        cutter=Part.makeBox(w-2*r,h,d,App.Vector(x-w/2+r,y-h/2,z)).fuse(Part.makeBox(w,h-2*r,d,App.Vector(x-w/2,y-h/2+r,z)))
        for dx in (-1,1):
            for dy in (-1,1):cutter=cutter.fuse(Part.makeCylinder(r,d,App.Vector(x+dx*(w/2-r),y+dy*(h/2-r),z)))
        cutters[f['id']]=cutter.removeSplitter()
    for hole in holes:
        cutters[hole['id']]=Part.makeCylinder(hole['diameter_mm']/2,hi[2]-lo[2],App.Vector(*hole['center_xy_mm'],lo[2]))
    expected=box
    for cutter in cutters.values():expected=expected.cut(cutter)
    delta=shape.cut(expected).Volume+expected.cut(shape).Volume
    if not shape.isValid() or len(shape.Solids)!=1 or bounds(shape)!=plan['target_bounds_mm'] or delta>1e-8:
        raise ValueError('Original CAD disagrees with independent multiple-pocket composition')
    tops=[(i,f) for i,f in enumerate(shape.Faces,1) if isinstance(f.Surface,Part.Plane)
          and abs(f.normalAt(0,0).z-1)<1e-8 and f.BoundBox.ZLength<1e-8 and abs(f.BoundBox.ZMax-hi[2])<1e-8]
    if len(tops)!=1 or top['top_face']!='Face'+str(tops[0][0]):raise ValueError('Original top face differs')
    area=lambda hs:sum(math.pi*(h['diameter_mm']/2)**2 for h in hs)
    outside=[h for h in holes if locations[h['id']] is None]
    if (len(tops[0][1].Wires)!=1+len(pockets)+len(outside)
        or abs(tops[0][1].Area-((hi[0]-lo[0])*(hi[1]-lo[1])-sum(nominal_area(p) for p in pockets)-area(outside)))>1e-8):
        raise ValueError('Original top topology differs')
    verified={}
    for f in pockets:
        floors=floor_matches(shape,f,hi[2],Part)
        if len(floors)!=1 or f['floor_face']!='Face'+str(floors[0][0]):raise ValueError('Original individual pocket floor binding differs')
        face=floors[0][1];inside=[h for h in holes if locations[h['id']]==f['id']]
        if len(face.Wires)!=1+len(inside) or abs(face.Area-(nominal_area(f)-area(inside)))>1e-8:
            raise ValueError('Original individual pocket floor topology differs')
        whole=Part.Face(face.Wire1);z=hi[2]-f['depth_mm']
        cutter_floor=next(s for s in cutters[f['id']].Faces if isinstance(s.Surface,Part.Plane)
                          and s.BoundBox.ZLength<1e-8 and abs(s.BoundBox.ZMax-z)<1e-8)
        if abs(whole.Area-nominal_area(f))>1e-8 or whole.cut(cutter_floor).Area+cutter_floor.cut(whole).Area>1e-8:
            raise ValueError('PocketShape Wire1 is not the entire original individual cavity')
        verified[f['id']]={'floor_face':f['floor_face'],'outer_wire_verified':True,'hole_ids':[h['id'] for h in inside]}
    ref={'schema':'genlaw.native-binding.v13','status':'verified','reference_class':REFERENCE,'cad_sha256':digest(source),
         'bounds':bounds(shape),'volume_mm3':shape.Volume,'valid_single_solid':True,'incoming_stock_bounds':plan['stock']['bounds_mm'],
         'features':holes,'facing_feature':top,'pocket_features':pockets,'feature_bindings':features,'hole_pocket_ids':locations,
         'original_cad_reparsed':True,'symmetric_difference_mm3':delta,'pocket_outer_wire_verified':True,'pocket_floor_bindings':verified,
         'reference_for_iou':'Independent box / rounded rectangle union / cylinder composition, checked against original filleted-prism CAD.'}
    for h in holes:
        x,y=h['center_xy_mm'];r=h['diameter_mm']/2
        walls=[(i,f) for i,f in enumerate(shape.Faces,1) if isinstance(f.Surface,Part.Cylinder)
               and abs(f.Surface.Radius-r)<1e-8 and abs(f.Surface.Center.x-x)<1e-8 and abs(f.Surface.Center.y-y)<1e-8
               and abs(abs(f.Surface.Axis.z)-1)<1e-8]
        if (len(walls)!=1 or h['cylinder_face']!='Face'+str(walls[0][0]) or abs(walls[0][1].BoundBox.ZMin-lo[2])>1e-8
                or abs(walls[0][1].BoundBox.ZMax-hole_wall_top(ref,h))>1e-8):raise ValueError('Original hole wall binding / retained height differs')
    write(out/'native-geometry.json',ref)
    if export_display:export_display(shape,out/'target-display.stl')
    oracles=json.loads((root/plan['expected_states_ref']['path']).read_text())['states'];current=box
    for i,op in enumerate(plan['operations']):
        feature=op['geometry']
        if i:current=current.cut(cutters[feature['id']])
        oracle=oracles[i];path=root/oracle['cad']['path'];observed=Part.Shape();observed.read(str(path))
        difference=current.cut(observed).Volume+observed.cut(current).Volume
        if (digest(path)!=oracle['cad']['sha256'] or not observed.isValid() or len(observed.Solids)!=1 or difference>1e-8
                or abs(observed.Volume-oracle['volume_mm3'])>1e-8 or bounds(current)!=plan['target_bounds_mm']):
            raise ValueError('Independent archived cumulative CAD differs')
        completed=features[:i+1]
        record={'schema':'genlaw.native-binding.v13','status':'verified','reference_class':REFERENCE,
            'cad_sha256':digest(path),'oracle_ref':oracle['cad'],'symmetric_difference_mm3':difference,
            'bounds':bounds(current),'volume_mm3':current.Volume,'valid_single_solid':True,'incoming_stock_bounds':plan['stock']['bounds_mm'],
            'features':[f for f in completed if f['kind']=='through_hole'],'pocket_features':[f for f in completed if f['kind']=='rounded_rectangular_pocket'],
            'facing_feature':top,'feature_bindings':completed,'original_final_cad_sha256':digest(source),
            'completed_features':[f['id'] for f in completed],'reference_for_iou':'Frozen pre-NC independent cumulative CAD; never simulation stock.'}
        if i<len(plan['operations'])-1:
            folder=out/'checkpoints'/f'after-{i+1:02d}';folder.mkdir(parents=True)
            write(folder/'native-geometry.json',record)
            if export_display:export_display(current,folder/'target-display.stl')
    return shape,ref
