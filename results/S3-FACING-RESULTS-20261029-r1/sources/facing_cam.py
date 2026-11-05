"""Bind the original solid/top face before native FreeCAD MillFace generation."""
import json
from pathlib import Path
from .preparation import digest
from .facing_geometry import FACING,CAPABILITY
from .native_planar_cam import emit,write
from .native_stock import bounds


def generate(request,plan,export_display=None):
    from local_resource_limits import require_bounded_worker
    require_bounded_worker()
    import FreeCAD as App
    import Part
    if plan['capability']!=CAPABILITY:raise ValueError('Unsupported facing capability')
    out=Path(request['output']);source=Path(request['input']).parent/plan['cad_ref']['path']
    feature=plan['operations'][0]['geometry'];declared=plan['target_bounds_mm'];low,high=declared['min'],declared['max']
    shape=Part.Shape();shape.read(str(source))
    if not shape.isValid() or len(shape.Solids)!=1:raise ValueError('Need one valid original solid')
    expected=Part.makeBox(*[b-a for a,b in zip(low,high)],App.Vector(*low))
    delta=shape.cut(expected).Volume+expected.cut(shape).Volume
    if delta>1e-8 or any(abs(bounds(shape)[k][i]-declared[k][i])>1e-8 for k in ['min','max'] for i in range(3)):
        raise ValueError('Native CAD disagrees with declared faced box')
    candidates=[i for i,face in enumerate(shape.Faces,1) if isinstance(face.Surface,Part.Plane)
                and abs(face.normalAt(0,0).z-1)<1e-8 and abs(face.BoundBox.ZMax-high[2])<1e-8]
    if len(candidates)!=1 or feature['top_face']!='Face'+str(candidates[0]):raise ValueError('Original top face binding differs')
    face=shape.Faces[candidates[0]-1]
    if len(face.Wires)!=1 or abs(face.Area-(high[0]-low[0])*(high[1]-low[1]))>1e-8:
        raise ValueError('Facing requires the complete original rectangular top')
    ref={'schema':'genlaw.native-binding.v7','status':'verified','cad_sha256':digest(source),'reference_class':FACING,
         'bounds':bounds(shape),'incoming_stock_bounds':plan['stock']['bounds_mm'],'volume_mm3':shape.Volume,
         'valid_single_solid':True,'symmetric_difference_mm3':delta,'original_cad_reparsed':True,'features':[feature],
         'native_top_face':feature['top_face'],'native_top_area_mm2':face.Area,
         'reference_for_iou':'Original target box checked by independent native boolean difference; incoming stock remains separate, not an NC-derived target.'}
    write(out/'native-geometry.json',ref)
    if export_display:export_display(shape,out/'target-display.stl')
    return emit(request,plan,shape,ref,feature['top_face'],native_kind='MillFace')


def run(request,plan,export_display):
    from .programming import check_nc
    record=generate(request,plan,export_display);out=Path(request['output'])
    record['nc_check']=check_nc((out/'program.nc').read_text(),plan)
    record.update(status='generated_and_checked',execution_plan_sha256=json.loads(Path(request['preparation']).read_text())['execution_plan_sha256'])
    return record
