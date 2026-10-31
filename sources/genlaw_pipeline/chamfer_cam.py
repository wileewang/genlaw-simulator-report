"""Original CAD -> native Deburr -> unchanged bundled-post NC, with explicit tool."""
import json,math
from pathlib import Path
from .preparation import digest
from .chamfer_geometry import CAPABILITY,REFERENCE,ChamferReference
from .freecad_deburr import checked_formal_backend,load_overlay
from .chamfer_nc import check_nc
from .sequence_nc import native_header
from .native_stock import set_box,bounds
from .native_planar_cam import write


def run(request,plan,export_display):
    from local_resource_limits import require_bounded_worker
    require_bounded_worker()
    import FreeCAD as App
    import Part
    import Path as CamPath
    import Path.Main.Job as Job
    from Path.Tool.toolbit import ToolBit
    from Path.Post.scripts import linuxcnc_post
    if plan['capability']!=CAPABILITY:raise ValueError('Unsupported native chamfer')
    Deburr,overlay=load_overlay(checked_formal_backend(plan['cam_backend']))
    out=Path(request['output']);source=Path(request['input']).parent/plan['cad_ref']['path']
    f=plan['operations'][0]['geometry'];p=plan['parameters'];lo,hi=[plan['target_bounds_mm'][k] for k in ['min','max']]
    shape=Part.Shape();shape.read(str(source));L,W,H=[b-a for a,b in zip(lo,hi)];c=f['width_mm']
    # Input creation uses native makeChamfer. This independent check removes four
    # triangular wedges and never replaces the input solid used by native CAM.
    expected=Part.makeBox(L,W,H,App.Vector(*lo));x,y,z=lo[0],lo[1],hi[2]
    triangles=[([(x,y,z),(x+c,y,z),(x,y,z-c)],(0,W,0)),
        ([(x+L,y,z),(x+L-c,y,z),(x+L,y,z-c)],(0,W,0)),
        ([(x,y,z),(x,y+c,z),(x,y,z-c)],(L,0,0)),
        ([(x,y+W,z),(x,y+W-c,z),(x,y+W,z-c)],(L,0,0))]
    for points,direction in triangles:
        wire=Part.makePolygon([App.Vector(*v) for v in points+[points[0]]])
        expected=expected.cut(Part.Face(wire).extrude(App.Vector(*direction)))
    delta=shape.cut(expected).Volume+expected.cut(shape).Volume
    faces=['Face'+str(i) for i,face in enumerate(shape.Faces,1)
           if isinstance(face.Surface,Part.Plane) and abs(face.normalAt(0,0).z-math.sqrt(.5))<1e-8]
    ref={'schema':'genlaw.native-binding.v14','status':'verified','cad_sha256':digest(source),'reference_class':REFERENCE,
        'bounds':bounds(shape),'incoming_stock_bounds':plan['stock']['bounds_mm'],'volume_mm3':shape.Volume,
        'valid_single_solid':True,'symmetric_difference_mm3':delta,'original_cad_reparsed':True,'features':[f],
        'reference_for_iou':'Original native CAD independently checked by four triangular wedge cuts and analytic volume; no fitted registration or discarded material.'}
    if (not shape.isValid() or len(shape.Solids)!=1 or len(shape.Faces)!=10 or delta>1e-8
        or len(faces)!=4 or set(faces)!=set(f['faces']) or abs(ChamferReference(ref).analytic_volume-shape.Volume)>1e-8
        or any(abs(ref['bounds'][k][i]-plan['target_bounds_mm'][k][i])>1e-8 for k in ['min','max'] for i in range(3))):
        raise ValueError('Original CAD or four-face binding disagrees with declared chamfer')
    write(out/'native-geometry.json',ref);export_display(shape,out/'target-display.stl')
    doc=App.newDocument('GenLaw_Rectangular_Chamfer')
    try:
        model=doc.addObject('PartDesign::Feature','Target');model.Shape=shape;doc.recompute();job=Job.Create('Job',[model],None)
        job.GeometryTolerance=p['path_tolerance_mm'];CamPath.Area.setDefaultParams(Accuracy=p['path_tolerance_mm'],Deflection=p['path_tolerance_mm'])
        stock=set_box(job,plan['stock']['bounds_mm']);t=plan['tool']
        tool=ToolBit.from_shape_id('chamfer.fcstd',label='Explicit 90 degree finite frustum').attach_to_doc(doc=doc)
        for key,value in [('Diameter',t['diameter_mm']),('TipDiameter',t['tip_diameter_mm']),('Length',t['length_mm']),
            ('ShankDiameter',t['diameter_mm']),('CuttingEdgeHeight',t['cutting_length_mm']),('CuttingEdgeAngle',t['included_angle_degrees'])]:setattr(tool,key,value)
        tc=job.Tools.Group[0];tc.Tool=tool;tc.ToolNumber=1;tc.Label='TC Chamfer T1';tc.SpindleSpeed=p['spindle_rpm']
        tc.HorizFeed=f"{p['feed_mm_min']} mm/min";tc.VertFeed=f"{p['plunge_mm_min']} mm/min";doc.recompute()
        op=Deburr.Create('ChamferEdges',parentJob=job);op.ToolController=tc;op.Base=[(job.Model.Group[0],f['faces'])]
        for key,value in [('Width',c),('ExtraDepth',p['extra_depth_mm']),('StepDown',0),('SafeHeight',p['safe_height_mm']),('ClearanceHeight',p['clearance_height_mm'])]:
            op.setExpression(key,None);setattr(op,key,value)
        op.Direction='CW';op.EntryPoint=0;doc.recompute();op.Label='ChamferEdges'
        if (not op.Path.Commands or 'Invalid' in op.State or len(job.Operations.Group)!=1 or len(op.Base)!=1
            or set(op.Base[0][1])!=set(f['faces'])):raise ValueError('Native Deburr has no complete original-face path')
        write(out/'native-path.json',{'cam_operation':op.Name,'step_id':plan['operations'][0]['step_id'],'feature_id':f['id'],
            'commands':[{'name':cmd.Name,'parameters':dict(cmd.Parameters)} for cmd in op.Path.Commands]})
        preamble=native_header(plan).rsplit('G21\n',1)[0].rstrip().replace('\n','\\n')
        args='--no-show-editor --no-tlo --precision 5 --preamble "'+preamble+'"';nc=out/'program.nc'
        linuxcnc_post.export([tc,op],str(nc),args);checked=check_nc(nc.read_text(),plan)
        if digest(source)!=plan['cad_ref']['sha256']:raise ValueError('Original CAD changed')
        if any(abs(bounds(job.Stock.Shape)[k][i]-plan['stock']['bounds_mm'][k][i])>1e-8 for k in ['min','max'] for i in range(3)):raise ValueError('Native stock changed')
        doc.saveAs(str(out/'cam-job.FCStd'))
        return {'schema':'genlaw.cam-result.v1','status':'generated_and_checked','native_cad_binding':ref,
            'execution_plan_sha256':json.loads(Path(request['preparation']).read_text())['execution_plan_sha256'],
            'nc_sha256':digest(nc),'native_operation':'Deburr','deburr_overlay':overlay,'native_stock':stock,
            'freecad_version':list(App.Version()),'gui_up':bool(App.GuiUp),'postprocessor':'FreeCAD bundled linuxcnc_post','post_arguments':args,
            'native_path_sha256':digest(out/'native-path.json'),'nc_check':checked,
            'operation_bindings':[{'step_id':plan['operations'][0]['step_id'],'feature_id':f['id'],'cam_operation':op.Name,
                'selected_features':[{'object':obj.Name,'subelements':list(names)} for obj,names in op.Base]}],
            'parameters':{'width_mm':op.Width.Value,'extra_depth_mm':op.ExtraDepth.Value,'side':op.Side,'direction':op.Direction,
                'single_pass_step_down_mm':op.StepDown.Value,'depth_offset':list(Deburr.toolDepthAndOffset(c,p['extra_depth_mm'],tool,False)),
                'geometry_tolerance_mm':job.GeometryTolerance.Value},'production_approved':False,'whole_process_card_complete':False}
    finally:App.closeDocument(doc.Name)
