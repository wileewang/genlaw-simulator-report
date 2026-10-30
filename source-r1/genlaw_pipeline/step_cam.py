"""Bind the original stepped solid and extend only its three outer floor edges."""
import json
from pathlib import Path
from .preparation import digest
from .step_geometry import STEP,CAPABILITY,floor_bounds
from .native_planar_cam import emit,write


def generate(request,plan,export_display=None):
    from local_resource_limits import require_bounded_worker
    require_bounded_worker()
    import FreeCAD as App
    import Part
    if plan['capability']!=CAPABILITY:raise ValueError('Unsupported step capability')
    out=Path(request['output']);source=Path(request['input']).parent/plan['cad_ref']['path'];feature=plan['operations'][0]['geometry']
    shape=Part.Shape();shape.read(str(source));bounds=plan['stock']['bounds_mm'];low=bounds['min'];high=bounds['max'];dims=[b-a for a,b in zip(low,high)]
    a,b=floor_bounds(feature,bounds);depth=feature['depth_mm'];z=high[2]-depth
    # Independent from the frozen input's union-of-slabs construction.
    cutter=Part.makeBox(b[0]-a[0],b[1]-a[1],depth,App.Vector(*a,z))
    expected=Part.makeBox(*dims,App.Vector(*low)).cut(cutter)
    delta=shape.cut(expected).Volume+expected.cut(shape).Volume
    if not shape.isValid() or len(shape.Solids)!=1 or delta>1e-8:raise ValueError('Native CAD disagrees with declared step: '+str(delta))
    floors=[(i,f) for i,f in enumerate(shape.Faces,1) if isinstance(f.Surface,Part.Plane) and abs(f.BoundBox.ZMin-z)<1e-8 and f.BoundBox.ZLength<1e-8]
    area=(b[0]-a[0])*(b[1]-a[1])
    if len(floors)!=1 or abs(floors[0][1].Area-area)>1e-8:raise ValueError('Cannot uniquely bind the step floor')
    floor_id='Face'+str(floors[0][0]);floor=floors[0][1];axis='XY'.index(feature['side'][0]);coordinate=feature['shoulder_coordinate_mm']
    open_edges=[];shoulder_edges=[]
    for edge in floor.Edges:
        same=[i for i,e in enumerate(shape.Edges,1) if e.isSame(edge)]
        if len(same)!=1:raise ValueError('Ambiguous original CAD edge identity')
        coords=[(v.Point.x,v.Point.y)[axis] for v in edge.Vertexes]
        if all(abs(c-coordinate)<1e-8 for c in coords):shoulder_edges+=same
        else:
            endpoints=[(v.Point.x,v.Point.y) for v in edge.Vertexes]
            if not any(all(abs(p[k]-value)<1e-8 for p in endpoints) for k in range(2) for value in (low[k],high[k])):
                raise ValueError('An open floor edge is not on an original stock boundary')
            open_edges+=same
    if len(open_edges)!=3 or len(shoulder_edges)!=1:raise ValueError('Step must have three stock edges and one retained shoulder')
    box=shape.BoundBox
    ref={'schema':'genlaw.native-binding.v5','status':'verified','cad_sha256':digest(source),'reference_class':STEP,
      'bounds':{'min':[box.XMin,box.YMin,box.ZMin],'max':[box.XMax,box.YMax,box.ZMax]},'volume_mm3':shape.Volume,
      'valid_single_solid':True,'symmetric_difference_mm3':delta,'original_cad_reparsed':True,'features':[feature],
      'reference_for_iou':'Native stock-minus-step subtraction checked against original union-of-slabs CAD.',
      'step_floor_face':floor_id,'open_stock_edges':['Edge'+str(i) for i in open_edges],'retained_shoulder_edge':'Edge'+str(shoulder_edges[0])}
    write(out/'native-geometry.json',ref)
    if export_display:export_display(shape,out/'target-display.stl')
    return emit(request,plan,shape,ref,floor_id,open_edges)


def run(request,plan,export_display):
    from .programming import check_nc
    record=generate(request,plan,export_display);out=Path(request['output'])
    record['nc_check']=check_nc((out/'program.nc').read_text(),plan)
    record.update(status='generated_and_checked',execution_plan_sha256=json.loads(Path(request['preparation']).read_text())['execution_plan_sha256'])
    return record
