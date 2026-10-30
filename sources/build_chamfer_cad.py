"""Create and re-read standalone chamfer CAD before any NC generation."""
import json,math,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'step2gcode_demo/validation'))
from local_resource_limits import require_bounded_worker
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.chamfer_geometry import REFERENCE,ChamferReference
from genlaw_pipeline.native_stock import bounds
from genlaw_pipeline.run import write


def build(out):
    require_bounded_worker()
    import FreeCAD as App
    import Part
    specs=json.loads((out/'specifications.json').read_text())
    for spec in specs['cases']:
        case=out/spec['id'];case.mkdir();lo,hi=[spec['stock_bounds_mm'][k] for k in ['min','max']]
        L,W,H=[b-a for a,b in zip(lo,hi)];c=spec['chamfer_width_mm']
        stock=Part.makeBox(L,W,H,App.Vector(*lo))
        edges=[e for e in stock.Edges if all(abs(v.Point.z-hi[2])<1e-8 for v in e.Vertexes)]
        if len(edges)!=4:raise ValueError('Expected four original stock top edges')
        shape=stock.makeChamfer(c,edges);shape.exportBrep(str(case/'target.brep'));shape.exportStep(str(case/'target.step'))
        doc=App.newDocument('Chamfer_'+spec['id'].replace('-','_'));obj=doc.addObject('PartDesign::Feature','Target');obj.Shape=shape
        doc.recompute();doc.saveAs(str(case/'target.FCStd'));App.closeDocument(doc.Name)
        observed=Part.Shape();observed.read(str(case/'target.brep'));bound=bounds(observed)
        faces=['Face'+str(i) for i,face in enumerate(observed.Faces,1)
            if isinstance(face.Surface,Part.Plane) and abs(face.normalAt(0,0).z-math.sqrt(.5))<1e-8]
        if not observed.isValid() or len(observed.Solids)!=1 or len(observed.Faces)!=10 or len(faces)!=4:
            raise ValueError('Incomplete native chamfer target')
        if any(abs(bound[k][i]-spec['stock_bounds_mm'][k][i])>1e-8 for k in ['min','max'] for i in range(3)):
            raise ValueError('Chamfer altered original outside bounds')
        feature={'id':'C1','kind':'top_perimeter_chamfer_45','axis':[0,0,1],'faces':faces,'width_mm':c,'angle_degrees':45,
            'top_z_mm':hi[2],'thickness_mm':H,'bounds_xy_mm':{k:bound[k][:2] for k in ['min','max']}}
        reference={'sha256':digest(case/'target.brep'),'valid_single_solid':True,'volume_mm3':observed.Volume,
            'bounds':bound,'reference_class':REFERENCE,'features':[feature],'target_created_before_nc':True,
            'construction':'Native box makeChamfer on four complete top edges; original BREP re-read. CAM independently checks four triangular wedge cuts.',
            'freecad_version':list(App.Version())}
        if abs(ChamferReference(reference).analytic_volume-observed.Volume)>1e-8:raise ValueError('Native target volume mismatch')
        spec['features']=[feature];write(case/'reference.json',reference)
    (out/'specifications.json').write_text(json.dumps(specs,ensure_ascii=False,indent=2)+'\n')
    write(out/'cad-generation.json',{'status':'created','cases':len(specs['cases']),'target_created_before_nc':True})


if 'GENLAW_CHAMFER_FIXTURES' in os.environ:build(Path(os.environ.pop('GENLAW_CHAMFER_FIXTURES')))
