"""Original filleted-prism CAD and independent cumulative states, before NC."""
import json
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT/'step2gcode_demo/validation'))
from local_resource_limits import require_bounded_worker
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.native_stock import bounds
from genlaw_pipeline.multi_pocket_geometry import REFERENCE, validate_combination, floor_matches


def build(out):
    require_bounded_worker()
    import FreeCAD as App
    import Part
    specs=json.loads((out/'specifications.json').read_text())
    def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    for spec in specs['cases']:
        case=out/spec['id'];case.mkdir()
        lo,hi=[spec['target_bounds_mm'][k] for k in ('min','max')]
        locations=validate_combination(spec['pockets'],spec['holes'])
        box=Part.makeBox(*[b-a for a,b in zip(lo,hi)],App.Vector(*lo));cutters={}
        for f in spec['pockets']:
            x,y=f['center_xy_mm'];w,h,d=[f[k] for k in ('width_mm','height_mm','depth_mm')]
            block=Part.makeBox(w,h,d,App.Vector(x-w/2,y-h/2,hi[2]-d))
            vertical=[e for e in block.Edges if abs(e.BoundBox.ZLength-d)<1e-8]
            if len(vertical)!=4:raise ValueError('Independent prism topology differs')
            cutters[f['id']]=block.makeFillet(f['corner_radius_mm'],vertical)
        for h in spec['holes']:
            cutters[h['id']]=Part.makeCylinder(h['diameter_mm']/2,hi[2]-lo[2],App.Vector(*h['center_xy_mm'],lo[2]))
        shape=box
        for cutter in cutters.values():shape=shape.cut(cutter)
        shape.exportBrep(str(case/'target.brep'));shape.exportStep(str(case/'target.step'))
        doc=App.newDocument('MultiPocket_'+spec['id'].replace('-','_'))
        try:
            obj=doc.addObject('PartDesign::Feature','Target');obj.Shape=shape
            doc.recompute();doc.saveAs(str(case/'target.FCStd'))
        finally:App.closeDocument(doc.Name)
        original=Part.Shape();original.read(str(case/'target.brep'))
        if not original.isValid() or len(original.Solids)!=1 or bounds(original)!=spec['target_bounds_mm']:
            raise ValueError('Original solid / bounds differ')
        tops=[i for i,face in enumerate(original.Faces,1) if isinstance(face.Surface,Part.Plane)
              and abs(face.normalAt(0,0).z-1)<1e-8 and face.BoundBox.ZLength<1e-8 and abs(face.BoundBox.ZMax-hi[2])<1e-8]
        if len(tops)!=1:raise ValueError('Ambiguous original top')
        top={'id':'P1','kind':'planar_top','axis':[0,0,1],'top_face':'Face'+str(tops[0]),
             'top_z_mm':hi[2],'thickness_mm':hi[2]-lo[2],'bounds_xy_mm':{'min':lo[:2],'max':hi[:2]}}
        for f in spec['pockets']:
            floors=floor_matches(original,f,hi[2],Part)
            if len(floors)!=1:raise ValueError('Ambiguous original floor identity')
            f['floor_face']='Face'+str(floors[0][0])
        for hole in spec['holes']:
            x,y=hole['center_xy_mm'];r=hole['diameter_mm']/2
            matches=[i for i,face in enumerate(original.Faces,1) if isinstance(face.Surface,Part.Cylinder)
                     and abs(face.Surface.Radius-r)<1e-8 and abs(face.Surface.Center.x-x)<1e-8
                     and abs(face.Surface.Center.y-y)<1e-8 and abs(abs(face.Surface.Axis.z)-1)<1e-8]
            if len(matches)!=1:raise ValueError('Ambiguous original hole wall')
            hole['cylinder_face']='Face'+str(matches[0])
        spec['features']=[top]+spec['pockets']+spec['holes']
        save(case/'reference.json',{'sha256':digest(case/'target.brep'),'valid_single_solid':True,
            'bounds':bounds(original),'volume_mm3':original.Volume,'reference_class':REFERENCE,
            'features':spec['features'],'hole_pocket_ids':locations,'target_created_before_nc':True,
            'freecad_version':list(App.Version()),'construction':'Box minus independent filleted prisms and full-height cylinders; each original floor identified by height AND XY extent.'})
        states=[];current=box;completed=[]
        for i,fid in enumerate(spec['order'],1):
            completed.append(fid)
            if fid!=top['id']:current=current.cut(cutters[fid])
            name=f'expected-state-{i:02d}.brep';current.exportBrep(str(case/name))
            reread=Part.Shape();reread.read(str(case/name))
            if not reread.isValid() or len(reread.Solids)!=1:raise ValueError('Invalid independent cumulative solid')
            states.append({'id':f'after-{i:02d}','completed_features':list(completed),'bounds_mm':bounds(reread),
                           'volume_mm3':reread.Volume,'cad':{'path':name,'sha256':digest(case/name)}})
        if current.cut(original).Volume+original.cut(current).Volume>1e-8:raise ValueError('Final oracle differs')
        save(case/'expected-states.json',{'schema':'genlaw.independent-state-oracles.v1','created_before_nc':True,
            'role':'Independent validation only, never simulation stock.','target_cad_sha256':digest(case/'target.brep'),'states':states})
    save(out/'specifications.json',specs)
    save(out/'cad-generation.json',{'status':'created','cases':len(specs['cases']),'target_created_before_nc':True})


if 'GENLAW_SEQUENCE_FIXTURES' in os.environ:
    out=Path(os.environ.pop('GENLAW_SEQUENCE_FIXTURES'))
    try:build(out)
    except Exception:
        import traceback
        (out/'cad-generation-failure.txt').write_text(traceback.format_exc())
        raise
