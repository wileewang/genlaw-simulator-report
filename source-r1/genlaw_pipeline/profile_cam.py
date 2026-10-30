"""Native FreeCAD Profile, bound to original CAD and independently checked solid."""
import json
from pathlib import Path
from .preparation import digest
from .profile_geometry import CAPABILITY,REFERENCE,endpoints,nominal_area
from .profile_nc import check_nc
from .sequence_nc import native_header
from .native_stock import set_box,bounds
from .native_planar_cam import write


def generate(request,plan,export_display=None):
    from local_resource_limits import require_bounded_worker
    require_bounded_worker()
    import FreeCAD as App
    import Part
    import Path as CamPath
    import Path.Main.Job as Job
    import Path.Op.Profile as Profile
    from Path.Tool.toolbit import ToolBit
    from Path.Post.scripts import linuxcnc_post
    if plan['capability']!=CAPABILITY:raise ValueError('Unsupported native external profile')
    out=Path(request['output']);source=Path(request['input']).parent/plan['cad_ref']['path']
    f=plan['operations'][0]['geometry'];p=plan['parameters'];lo,hi=[plan['target_bounds_mm'][k] for k in ['min','max']]
    shape=Part.Shape();shape.read(str(source));a,b=endpoints(f);r=f['width_mm']/2;t=hi[2]-lo[2]
    # Input used an arc/line extrusion. This check independently unions primitives.
    if f['major_axis']=='X':core=Part.makeBox(b[0]-a[0],2*r,t,App.Vector(a[0],a[1]-r,lo[2]))
    else:core=Part.makeBox(2*r,b[1]-a[1],t,App.Vector(a[0]-r,a[1],lo[2]))
    expected=core.fuse(Part.makeCylinder(r,t,App.Vector(*a,lo[2]))).fuse(Part.makeCylinder(r,t,App.Vector(*b,lo[2]))).removeSplitter()
    delta=shape.cut(expected).Volume+expected.cut(shape).Volume
    if (not shape.isValid() or len(shape.Solids)!=1 or len(shape.Faces)!=6 or delta>1e-8
        or abs(shape.Volume-nominal_area(f)*t)>1e-8
        or any(abs(bounds(shape)[k][i]-plan['target_bounds_mm'][k][i])>1e-8 for k in ['min','max'] for i in range(3))):
        raise ValueError('Original CAD disagrees with declared capsule geometry')
    tops=[i for i,face in enumerate(shape.Faces,1) if isinstance(face.Surface,Part.Plane)
          and abs(face.normalAt(0,0).z-1)<1e-8 and abs(face.BoundBox.ZMax-hi[2])<1e-8]
    if len(tops)!=1 or f['top_face']!='Face'+str(tops[0]):raise ValueError('Original top face binding differs')
    top=shape.Faces[tops[0]-1]
    if len(top.Wires)!=1 or len(top.Edges)!=4 or abs(top.Area-nominal_area(f))>1e-8:
        raise ValueError('Original capsule top boundary is incomplete')
    ref={'schema':'genlaw.native-binding.v10','status':'verified','cad_sha256':digest(source),'reference_class':REFERENCE,
         'bounds':bounds(shape),'incoming_stock_bounds':plan['stock']['bounds_mm'],'volume_mm3':shape.Volume,
         'valid_single_solid':True,'symmetric_difference_mm3':delta,'original_cad_reparsed':True,'features':[f],
         'reference_for_iou':'Original arc/line CAD checked against independent rectangle/cylinder union. All actual material, including outside waste, is compared without registration.'}
    write(out/'native-geometry.json',ref)
    if export_display:export_display(shape,out/'target-display.stl')
    doc=App.newDocument('GenLaw_Capsule_Profile')
    try:
        model=doc.addObject('PartDesign::Feature','Target');model.Shape=shape;doc.recompute();job=Job.Create('Job',[model],None)
        job.GeometryTolerance=p['path_tolerance_mm'];CamPath.Area.setDefaultParams(Accuracy=p['path_tolerance_mm'],Deflection=p['path_tolerance_mm'])
        stock=set_box(job,plan['stock']['bounds_mm'])
        tool=ToolBit.from_shape_id('endmill.fcstd',label='GenLaw_Profile_Endmill').attach_to_doc(doc=doc)
        tool.Diameter=plan['tool']['diameter_mm'];tool.Length=plan['tool']['length_mm'];tool.CuttingEdgeHeight=plan['tool']['cutting_length_mm']
        tc=job.Tools.Group[0];tc.Tool=tool;tc.ToolNumber=1;tc.Label='TC Profile T1';tc.SpindleSpeed=p['spindle_rpm']
        tc.HorizFeed=f"{p['feed_mm_min']} mm/min";tc.VertFeed=f"{p['plunge_mm_min']} mm/min";doc.recompute()
        op=Profile.Create('Profile',parentJob=job);op.Label='Profile';op.ToolController=tc;op.Base=[(job.Model.Group[0],[f['top_face']])]
        for name,value in [('StartDepth',hi[2]),('FinalDepth',plan['operations'][0]['final_z_mm']),('StepDown',p['step_down_mm']),
                           ('SafeHeight',p['safe_height_mm']),('ClearanceHeight',p['clearance_height_mm'])]:
            op.setExpression(name,None);setattr(op,name,value)
        for name,value in [('Side','Outside'),('Direction','CW'),('JoinType','Round'),('UseComp',True),('OffsetExtra',0),
                           ('NumPasses',1),('Stepover',0),('processPerimeter',True),('processHoles',False),('processCircles',False),
                           ('HandleMultipleFeatures','Collectively')]:setattr(op,name,value)
        doc.recompute()
        if (not op.Path.Commands or 'Invalid' in op.State or len(job.Operations.Group)!=1 or len(op.Base)!=1
            or list(op.Base[0][1])!=[f['top_face']]):raise ValueError('Native Profile has no valid original-face path')
        write(out/'native-path.json',{'cam_operation':op.Name,'step_id':plan['operations'][0]['step_id'],'feature_id':f['id'],
                                     'commands':[{'name':c.Name,'parameters':dict(c.Parameters)} for c in op.Path.Commands]})
        preamble=native_header(plan).rsplit('G21\n',1)[0].rstrip().replace('\n','\\n')
        args='--no-show-editor --no-tlo --precision 5 --preamble "'+preamble+'"';nc=out/'program.nc'
        linuxcnc_post.export([tc,op],str(nc),args)
        checked=check_nc(nc.read_text(),plan)
        if digest(source)!=plan['cad_ref']['sha256']:raise ValueError('Original CAD changed')
        if any(abs(bounds(job.Stock.Shape)[k][i]-plan['stock']['bounds_mm'][k][i])>1e-8 for k in ['min','max'] for i in range(3)):raise ValueError('Native stock changed')
        doc.saveAs(str(out/'cam-job.FCStd'))
        return {'schema':'genlaw.cam-result.v1','status':'generated_and_checked','native_cad_binding':ref,'nc_sha256':digest(nc),
                'native_operation':'Profile','native_stock':stock,'freecad_version':list(App.Version()),'gui_up':bool(App.GuiUp),
                'postprocessor':'FreeCAD bundled linuxcnc_post','post_arguments':args,'native_path_sha256':digest(out/'native-path.json'),
                'nc_check':checked,'operation_bindings':[{'step_id':plan['operations'][0]['step_id'],'feature_id':f['id'],
                    'cam_operation':op.Name,'selected_features':[{'object':obj.Name,'subelements':list(names)} for obj,names in op.Base]}],
                'parameters':{'side':op.Side,'direction':op.Direction,'cam_radius_compensation':bool(op.UseComp),'controller_radius_compensation':False,
                    'number_of_passes':op.NumPasses,'stepover_mm':op.Stepover.Value,'step_down_mm':op.StepDown.Value,
                    'final_depth_mm':op.FinalDepth.Value,'geometry_tolerance_mm':job.GeometryTolerance.Value,'native_area_parameters':op.AreaParams},
                'production_approved':False,'whole_process_card_complete':False}
    finally:App.closeDocument(doc.Name)


def run(request,plan,export_display):
    result=generate(request,plan,export_display)
    result['execution_plan_sha256']=json.loads(Path(request['preparation']).read_text())['execution_plan_sha256']
    return result
