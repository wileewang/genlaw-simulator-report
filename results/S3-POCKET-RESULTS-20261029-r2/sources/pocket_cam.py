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
    import Path as CamPath
    import Path.Main.Job as Job
    import Path.Op.PocketShape as Pocket
    from Path.Tool.toolbit import ToolBit
    from Path.Post.scripts import linuxcnc_post
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
    doc=App.newDocument('GenLaw_Pocket_Execution')
    try:
        model=doc.addObject('PartDesign::Feature','Target');model.Shape=shape;doc.recompute();job=Job.Create('Job',[model],None)
        job.GeometryTolerance=plan['parameters']['path_tolerance_mm']
        CamPath.Area.setDefaultParams(Accuracy=plan['parameters']['path_tolerance_mm'],Deflection=plan['parameters']['path_tolerance_mm'])
        for prop in ['ExtXneg','ExtXpos','ExtYneg','ExtYpos','ExtZneg','ExtZpos']:setattr(job.Stock,prop,0)
        tool=ToolBit.from_shape_id('endmill.fcstd',label='GenLaw_Flat_Endmill').attach_to_doc(doc=doc)
        tool.Diameter=plan['tool']['diameter_mm'];tool.Length=plan['tool']['length_mm'];tool.CuttingEdgeHeight=plan['tool']['cutting_length_mm']
        tc=job.Tools.Group[0];tc.Label='TC GenLaw Flat Endmill';tc.Tool=tool;tc.ToolNumber=1;p=plan['parameters'];tc.SpindleSpeed=p['spindle_rpm'];tc.HorizFeed=f"{p['feed_mm_min']} mm/min";tc.VertFeed=f"{p['plunge_mm_min']} mm/min";doc.recompute()
        op=Pocket.Create('Pocket',parentJob=job);op.ToolController=tc
        # Use the model clone owned by the CAM Job, as required by native operations.
        op.Base=[(job.Model.Group[0],[floor_id])]
        for name,value in [('StartDepth',hi[2]),('FinalDepth',z),('StepDown',p['step_down_mm']),('FinishDepth',0),('SafeHeight',p['safe_height_mm']),('ClearanceHeight',p['clearance_height_mm'])]:
            op.setExpression(name,None);setattr(op,name,value)
        if not p['step_over_percent'].is_integer():raise ValueError('Native StepOver requires an integer percentage')
        op.StepOver=int(p['step_over_percent']);op.ClearingPattern=plan['strategy']['clearing_pattern'];op.CutMode=plan['strategy']['cut_mode'];op.StartAt=plan['strategy']['start_at'];op.KeepToolDown=False;op.ExtraOffset=0;op.UseOutline=False
        doc.recompute()
        if not op.Path.Commands or 'Invalid' in op.State:raise ValueError('Native PocketShape produced no valid path')
        stock=job.Stock.Shape.BoundBox
        if {'min':[stock.XMin,stock.YMin,stock.ZMin],'max':[stock.XMax,stock.YMax,stock.ZMax]}!=plan['stock']['bounds_mm']:
            raise ValueError('Native CAM stock changed')
        if len(job.Operations.Group)!=1 or len(op.Base)!=1 or list(op.Base[0][1])!=[floor_id]:raise ValueError('Native CAM selected a different operation or face')
        commands=[{'name':c.Name,'parameters':dict(c.Parameters)} for c in op.Path.Commands]
        write(out/'native-path.json',{'cam_operation':op.Name,'step_id':op_spec['step_id'],'feature_id':op_spec['feature_id'],'commands':commands})
        args='--no-show-editor --no-tlo --precision 5';nc=out/'program.nc';linuxcnc_post.export([tc,op],str(nc),args)
        if digest(source)!=plan['cad_ref']['sha256']:raise ValueError('Original CAD changed')
        doc.saveAs(str(out/'cam-job.FCStd'))
        return {'schema':'genlaw.cam-result.v1','status':'native_generated_pending_nc_check','native_cad_binding':ref,'nc_sha256':digest(nc),
          'freecad_version':list(App.Version()),'gui_up':bool(App.GuiUp),'postprocessor':'FreeCAD bundled linuxcnc_post','post_arguments':args,
          'native_path_sha256':digest(out/'native-path.json'),'operation_bindings':[{'step_id':op_spec['step_id'],'feature_id':f['id'],'cam_operation':op.Name,'selected_features':[{'object':obj.Name,'subelements':list(names)} for obj,names in op.Base]}],
          'parameters':{'step_down_mm':op.StepDown.Value,'step_over_percent':op.StepOver,'final_depth_mm':op.FinalDepth.Value,'start_at':op.StartAt,'pattern':op.ClearingPattern,'geometry_tolerance_mm':job.GeometryTolerance.Value,'native_area_parameters':op.AreaParams},
          'production_approved':False,'whole_process_card_complete':False}
    finally:App.closeDocument(doc.Name)


def run(request,plan,export_display):
    from .programming import check_nc
    record=generate(request,plan,export_display);out=Path(request['output'])
    record['nc_check']=check_nc((out/'program.nc').read_text(),plan)
    record.update(status='generated_and_checked',execution_plan_sha256=json.loads(Path(request['preparation']).read_text())['execution_plan_sha256'])
    return record
