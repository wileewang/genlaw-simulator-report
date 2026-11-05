"""Native PocketShape adapter, with an independently constructed CAD check."""
from pathlib import Path
import json
from .preparation import digest
from .pocket_geometry import POCKET,CAPABILITY,nominal_area


def write(path,value):
    with path.open('x') as stream:json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')


def generate(request,plan,export_display=None):
    from local_resource_limits import require_bounded_worker
    require_bounded_worker()
    import FreeCAD as App
    import Part
    if plan['capability']!=CAPABILITY:raise ValueError('Unsupported milling capability')
    out=Path(request['output']);source=Path(request['input']).parent/plan['cad_ref']['path'];op_spec=plan['operations'][0];f=op_spec['geometry']
    shape=Part.Shape();shape.read(str(source));lo,hi=[plan['stock']['bounds_mm'][k] for k in ['min','max']];dims=[b-a for a,b in zip(lo,hi)]
    x,y=f['center_xy_mm'];w,h,r,d=[f[k] for k in ['width_mm','height_mm','corner_radius_mm','depth_mm']];z=hi[2]-d
    # Different construction from the archived target's filleted prism.
    cutter=Part.makeBox(w-2*r,h,d,App.Vector(x-w/2+r,y-h/2,z)).fuse(Part.makeBox(w,h-2*r,d,App.Vector(x-w/2,y-h/2+r,z)))
    for dx in [-1,1]:
        for dy in [-1,1]:cutter=cutter.fuse(Part.makeCylinder(r,d,App.Vector(x+dx*(w/2-r),y+dy*(h/2-r),z)))
    cutter=cutter.removeSplitter();expected=Part.makeBox(*dims,App.Vector(*lo)).cut(cutter)
    delta=shape.cut(expected).Volume+expected.cut(shape).Volume
    if not shape.isValid() or len(shape.Solids)!=1 or delta>1e-8:raise ValueError('Native CAD disagrees with declared pocket: '+str(delta))
    floors=[(i,face) for i,face in enumerate(shape.Faces,1) if isinstance(face.Surface,Part.Plane) and abs(face.BoundBox.ZMin-z)<1e-8 and face.BoundBox.ZLength<1e-8]
    if len(floors)!=1 or abs(floors[0][1].Area-nominal_area(f))>1e-8:raise ValueError('Cannot uniquely bind the pocket floor')
    floor_id='Face'+str(floors[0][0]);b=shape.BoundBox
    ref={'schema':'genlaw.native-binding.v4','status':'verified','cad_sha256':digest(source),'reference_class':POCKET,'bounds':{'min':[b.XMin,b.YMin,b.ZMin],'max':[b.XMax,b.YMax,b.ZMax]},
      'volume_mm3':shape.Volume,'valid_single_solid':True,'symmetric_difference_mm3':delta,'original_cad_reparsed':True,'features':[f],
      'reference_for_iou':'Independent union of rectangles and circular corners, native boolean difference checked against original CAD.', 'pocket_floor_face':floor_id}
    write(out/'native-geometry.json',ref)
    if export_display:export_display(shape,out/'target-display.stl')
    from .native_planar_cam import emit
    return emit(request,plan,shape,ref,floor_id)


def run(request,plan,export_display):
    from .programming import check_nc
    record=generate(request,plan,export_display);out=Path(request['output'])
    record['nc_check']=check_nc((out/'program.nc').read_text(),plan)
    record.update(status='generated_and_checked',execution_plan_sha256=json.loads(Path(request['preparation']).read_text())['execution_plan_sha256'])
    return record
