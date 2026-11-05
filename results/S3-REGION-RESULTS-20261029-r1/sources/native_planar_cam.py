"""Native PocketShape emission shared by closed pockets and explicitly extended steps."""
import json
from pathlib import Path
from .preparation import digest


def write(path,value):
    with path.open('x') as stream:json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')


def emit(request,plan,shape,ref,floor_id,open_edge_ids=None,open_edge_groups=None):
    from local_resource_limits import require_bounded_worker
    require_bounded_worker()
    import FreeCAD as App
    import Path as CamPath
    import Path.Main.Job as Job
    import Path.Op.PocketShape as Pocket
    from Path.Tool.toolbit import ToolBit
    from Path.Post.scripts import linuxcnc_post
    out=Path(request['output']);source=Path(request['input']).parent/plan['cad_ref']['path'];op_spec=plan['operations'][0];f=op_spec['geometry']
    hi=plan['stock']['bounds_mm']['max'];z=op_spec['final_z_mm']
    doc=App.newDocument('GenLaw_Planar_Milling')
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
        extensions=[]
        if open_edge_ids is not None and open_edge_groups is not None:raise ValueError('Conflicting extension declarations')
        if open_edge_ids is not None:
            if len(open_edge_ids)!=3 or not plan['strategy']['extension_corners']:raise ValueError('Exactly three connected stock edges must be declared')
            wire='Wire['+','.join(str(n) for n in open_edge_ids)+']'
            extensions=[floor_id+':'+wire]
            op.setExpression('ExtensionLengthDefault',None);op.ExtensionLengthDefault=p['edge_extension_mm']
            op.ExtensionCorners=True;op.ExtensionFeature=[(job.Model.Group[0],extensions)]
        if open_edge_groups:
            if plan.get('capability')!='planar_floor_xy' or not plan['strategy']['extension_corners']:raise ValueError('Unregistered native floor extension strategy')
            extensions=[floor_id+':Wire['+','.join(str(n) for n in group)+']' for group in open_edge_groups]
            op.setExpression('ExtensionLengthDefault',None);op.ExtensionLengthDefault=p['edge_extension_mm']
            op.ExtensionCorners=True;op.ExtensionFeature=[(job.Model.Group[0],extensions)]
        doc.recompute()
        if extensions and (len(op.ExtensionFeature)!=1 or list(op.ExtensionFeature[0][1])!=extensions):raise ValueError('Native open-edge bindings changed')
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
          'native_path_sha256':digest(out/'native-path.json'),'operation_bindings':[{'step_id':op_spec['step_id'],'feature_id':f['id'],'cam_operation':op.Name,'selected_features':[{'object':obj.Name,'subelements':list(names)} for obj,names in op.Base],**({'open_edge_extensions':extensions,'extension_length_mm':op.ExtensionLengthDefault.Value} if extensions else {})}],
          'parameters':{'step_down_mm':op.StepDown.Value,'step_over_percent':op.StepOver,'final_depth_mm':op.FinalDepth.Value,'start_at':op.StartAt,'pattern':op.ClearingPattern,'geometry_tolerance_mm':job.GeometryTolerance.Value,'native_area_parameters':op.AreaParams},
          'production_approved':False,'whole_process_card_complete':False}
    finally:App.closeDocument(doc.Name)
