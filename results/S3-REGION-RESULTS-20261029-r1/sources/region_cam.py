"""Bind one original planar floor with concavity, islands or declared open ends."""
import json
from pathlib import Path
from .preparation import digest
from .region_geometry import REGION,CAPABILITY,nominal_area
from .native_region import read_floor,make_face
from .native_planar_cam import emit,write


def generate(request,plan,export_display=None):
    from local_resource_limits import require_bounded_worker
    require_bounded_worker()
    import FreeCAD as App
    import Part
    if plan['capability']!=CAPABILITY:raise ValueError('Unsupported planar floor capability')
    out=Path(request['output']);source=Path(request['input']).parent/plan['cad_ref']['path'];f=plan['operations'][0]['geometry']
    shape=Part.Shape();shape.read(str(source));bounds=plan['stock']['bounds_mm'];low=bounds['min'];high=bounds['max'];depth=f['depth_mm']
    observed,floor=read_floor(shape,depth,bounds)
    if observed['floor_face']!=f['floor_face'] or observed['open_edge_groups']!=f['open_edge_groups']:raise ValueError('Native floor face or open-edge groups disagree with declarations')
    declared=make_face(f['profile'],high[2]-depth)
    profile_delta=floor.cut(declared).Area+declared.cut(floor).Area
    if profile_delta>1e-8 or abs(nominal_area(f)-floor.Area)>1e-8:raise ValueError('Native CAD disagrees with declared floor profile')
    stock=Part.makeBox(*[b-a for a,b in zip(low,high)],App.Vector(*low))
    expected=stock.cut(floor.extrude(App.Vector(0,0,depth)))
    delta=shape.cut(expected).Volume+expected.cut(shape).Volume
    if not shape.isValid() or len(shape.Solids)!=1 or delta>1e-8:raise ValueError('Native CAD disagrees with one constant-depth floor region: '+str(delta))
    ref={'schema':'genlaw.native-binding.v6','status':'verified','cad_sha256':digest(source),'reference_class':REGION,
      'bounds':bounds,'volume_mm3':shape.Volume,'valid_single_solid':True,'symmetric_difference_mm3':delta,'original_cad_reparsed':True,
      'features':[f],'native_floor_face':observed['floor_face'],'native_floor_area_mm2':floor.Area,'profile_symmetric_difference_mm2':profile_delta,
      'open_edge_groups':observed['open_edge_groups'],'island_count':len(f['profile']['islands']),
      'reference_for_iou':'Exact original CAD floor curves and independent stock-minus-native-floor extrusion check; no NC-derived target.'}
    write(out/'native-geometry.json',ref)
    if export_display:export_display(shape,out/'target-display.stl')
    return emit(request,plan,shape,ref,f['floor_face'],open_edge_groups=f['open_edge_groups'])


def run(request,plan,export_display):
    from .programming import check_nc
    record=generate(request,plan,export_display);out=Path(request['output'])
    record['nc_check']=check_nc((out/'program.nc').read_text(),plan)
    record.update(status='generated_and_checked',execution_plan_sha256=json.loads(Path(request['preparation']).read_text())['execution_plan_sha256'])
    return record
