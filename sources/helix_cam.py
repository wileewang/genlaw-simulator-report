"""Original CAD cylinder binding and native FreeCAD Helix, with unchanged output."""
import json
from pathlib import Path
from .preparation import digest
from .helix_geometry import CAPABILITY,REFERENCE
from .native_stock import set_box,bounds
from .native_planar_cam import write


def generate(request,plan,export_display=None):
    from local_resource_limits import require_bounded_worker
    require_bounded_worker()
    import FreeCAD as App
    import Part
    import Path.Main.Job as Job
    import Path.Op.Helix as Helix
    from Path.Tool.toolbit import ToolBit
    from Path.Post.scripts import linuxcnc_post
    if plan['capability']!=CAPABILITY or len(plan['operations'])!=1:raise ValueError('Unsupported native helical scope')
    out=Path(request['output']);source=Path(request['input']).parent/plan['cad_ref']['path']
    op_spec=plan['operations'][0];f=op_spec['geometry'];lo,hi=[plan['stock']['bounds_mm'][k] for k in ['min','max']]
    shape=Part.Shape();shape.read(str(source));x,y=f['center_xy_mm'];r=f['diameter_mm']/2
    expected=Part.makeBox(*[b-a for a,b in zip(lo,hi)],App.Vector(*lo)).cut(Part.makeCylinder(r,hi[2]-lo[2],App.Vector(x,y,lo[2])))
    delta=shape.cut(expected).Volume+expected.cut(shape).Volume
    if not shape.isValid() or len(shape.Solids)!=1 or len(shape.Faces)!=7 or delta>1e-8 or bounds(shape)!=plan['stock']['bounds_mm']:
        raise ValueError('Native CAD disagrees with the declared original through-hole')
    candidates=[i for i,face in enumerate(shape.Faces,1) if isinstance(face.Surface,Part.Cylinder)
                and abs(face.Surface.Radius-r)<1e-8 and abs(face.Surface.Center.x-x)<1e-8
                and abs(face.Surface.Center.y-y)<1e-8 and abs(abs(face.Surface.Axis.z)-1)<1e-8]
    if len(candidates)!=1 or f['cylinder_face']!='Face'+str(candidates[0]):raise ValueError('Original cylinder face binding differs')
    ref={'schema':'genlaw.native-binding.v8','status':'verified','cad_sha256':digest(source),
         'reference_class':REFERENCE,'bounds':bounds(shape),'volume_mm3':shape.Volume,'valid_single_solid':True,
         'features':[f],'symmetric_difference_mm3':delta,'original_cad_reparsed':True,
         'reference_for_iou':'Original CAD box minus through-cylinder checked by independent native boolean difference; milling intent does not change the CAD target.'}
    write(out/'native-geometry.json',ref)
    if export_display:export_display(shape,out/'target-display.stl')
    doc=App.newDocument('GenLaw_Helical_Hole')
    try:
        model=doc.addObject('PartDesign::Feature','Target');model.Shape=shape;doc.recompute()
        job=Job.Create('Job',[model],None);stock=set_box(job,plan['stock']['bounds_mm']);p=plan['parameters']
        job.GeometryTolerance=p['path_tolerance_mm']
        tool=ToolBit.from_shape_id('endmill.fcstd',label='GenLaw_Flat_Endmill').attach_to_doc(doc=doc)
        tool.Diameter=plan['tool']['diameter_mm'];tool.Length=plan['tool']['length_mm'];tool.CuttingEdgeHeight=plan['tool']['cutting_length_mm']
        tc=job.Tools.Group[0];tc.Tool=tool;tc.ToolNumber=1;tc.SpindleSpeed=p['spindle_rpm']
        tc.Label=f"TC: GenLaw {plan['tool']['diameter_mm']:g}mm Flat Endmill"
        tc.HorizFeed=f"{p['feed_mm_min']} mm/min";tc.VertFeed=f"{p['plunge_mm_min']} mm/min";doc.recompute()
        op=Helix.Create('Helix',parentJob=job);op.ToolController=tc;op.Base=[(job.Model.Group[0],[f['cylinder_face']])]
        for name,value in [('StartDepth',op_spec['start_z_mm']),('FinalDepth',op_spec['final_z_mm']),('StepDown',p['step_down_mm']),
                           ('SafeHeight',p['safe_height_mm']),('ClearanceHeight',p['clearance_height_mm'])]:
            op.setExpression(name,None);setattr(op,name,value)
        op.StepOver=int(p['step_over_percent']);op.StartRadius=plan['strategy']['start_radius_mm'];op.OffsetExtra=0
        op.StartSide=plan['strategy']['start_at'];op.CutMode=plan['strategy']['cut_mode'];doc.recompute()
        if not op.Path.Commands or 'Invalid' in op.State or len(job.Operations.Group)!=1:raise ValueError('Native Helix has no unique valid operation')
        if len(op.Base)!=1 or list(op.Base[0][1])!=[f['cylinder_face']] or bounds(job.Stock.Shape)!=plan['stock']['bounds_mm']:
            raise ValueError('Native selected geometry or stock changed')
        write(out/'native-path.json',{'cam_operation':op.Name,'step_id':op_spec['step_id'],'feature_id':f['id'],
              'commands':[{'name':c.Name,'parameters':dict(c.Parameters)} for c in op.Path.Commands]})
        args='--no-show-editor --no-tlo --precision 5';nc=out/'program.nc';linuxcnc_post.export([tc,op],str(nc),args)
        if digest(source)!=plan['cad_ref']['sha256']:raise ValueError('Original CAD changed')
        doc.saveAs(str(out/'cam-job.FCStd'))
        return {'schema':'genlaw.cam-result.v1','status':'native_generated_pending_nc_check','native_cad_binding':ref,
          'nc_sha256':digest(nc),'native_operation':'Helix','native_stock':stock,'freecad_version':list(App.Version()),
          'gui_up':bool(App.GuiUp),'postprocessor':'FreeCAD bundled linuxcnc_post','post_arguments':args,
          'native_path_sha256':digest(out/'native-path.json'),'operation_bindings':[{'step_id':op_spec['step_id'],'feature_id':f['id'],
              'cam_operation':op.Name,'selected_features':[{'object':o.Name,'subelements':list(names)} for o,names in op.Base]}],
          'parameters':{'start_depth_mm':op.StartDepth.Value,'final_depth_mm':op.FinalDepth.Value,'step_down_mm':op.StepDown.Value,
              'step_over_percent':op.StepOver,'start_radius_mm':op.StartRadius.Value,'offset_extra_mm':op.OffsetExtra.Value,
              'start_side':op.StartSide,'cut_mode':op.CutMode,'direction':op.Direction},
          'production_approved':False,'whole_process_card_complete':False}
    finally:App.closeDocument(doc.Name)


def run(request,plan,export_display):
    from .helix_nc import check_nc
    result=generate(request,plan,export_display);out=Path(request['output'])
    result['nc_check']=check_nc((out/'program.nc').read_text(),plan)
    result.update(status='generated_and_checked',execution_plan_sha256=json.loads(Path(request['preparation']).read_text())['execution_plan_sha256'])
    return result
