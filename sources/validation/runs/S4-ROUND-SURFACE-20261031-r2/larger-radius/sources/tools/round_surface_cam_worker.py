"""Native FreeCAD Surface probe driven by archived cards/CAD/resources."""
import json
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'step2gcode_demo/validation')]
from local_resource_limits import require_bounded_worker
from genlaw_pipeline.cell_windows import checked_file,file_binding
from genlaw_pipeline.round_surface_preparation import prepare
from genlaw_pipeline.round_surface_geometry import independent_native
from genlaw_pipeline.round_surface_nc import check_nc
from genlaw_pipeline.surface_feed_adapter import adapt
from genlaw_pipeline.sequence_nc import native_header
from genlaw_pipeline.native_stock import set_box
from genlaw_pipeline.run import write


def run(req):
    require_bounded_worker()
    import FreeCAD as App
    import Part
    import Path as NativePath
    import Path.Main.Job as Job
    import Path.Op.Surface as Surface
    import Path.Op.SurfaceSupport as Support
    from Path.Tool.toolbit import ToolBit
    from Path.Post.scripts import linuxcnc_post
    from genlaw_pipeline.freecad_worker import export_display
    out=Path(req['output']);plan=prepare(req['input'])
    if plan!=json.loads((out/'preparation-result.json').read_text()):raise ValueError('Round inputs changed')
    source=checked_file(plan['input_bindings']['CAD']);shape=Part.Shape();shape.read(str(source))
    ref=independent_native(shape,plan['specification']);ref['CAD']=file_binding(source)
    write(out/'native-geometry.json',ref);export_display(shape,out/'target-display.stl')
    doc=App.newDocument('NativeRoundSurface')
    try:
        model=doc.addObject('PartDesign::Feature','Target');model.Shape=shape;doc.recompute()
        job=Job.Create('Job',[model],None);p=plan['parameters'];t=plan['tool'];job.GeometryTolerance=p['geometry_tolerance_mm']
        stock=set_box(job,plan['stock']['bounds_mm'])
        tool=ToolBit.from_shape_id('ballend.fcstd',label='Explicit ball end mill').attach_to_doc(doc=doc)
        for key,value in [('Diameter',t['diameter_mm']),('Length',t['length_mm']),('CuttingEdgeHeight',t['cutting_length_mm'])]:setattr(tool,key,value)
        tc=job.Tools.Group[0];tc.Tool=tool;tc.ToolNumber=1;tc.Label='TC Ball T1';tc.SpindleSpeed=p['spindle_rpm']
        tc.HorizFeed=f"{p['feed_mm_min']} mm/min";tc.VertFeed=f"{p['plunge_mm_min']} mm/min";doc.recompute()
        op=Surface.Create('RoundSurface',parentJob=job);op.ToolController=tc
        values={'ScanType':'Planar','CutPattern':'Line','CutMode':'Climb','LayerMode':'Multi-pass','BoundBox':'Stock',
            'ProfileEdges':'None','BoundaryEnforcement':True,'BoundaryAdjustment':p['boundary_adjustment_mm'],
            'StepDown':p['step_down_mm'],'StepOver':p['stepover_percent'],'SampleInterval':p['sample_interval_mm'],
            'LinearDeflection':p['linear_deflection_mm'],'StartDepth':p['start_depth_mm'],'FinalDepth':p['final_depth_mm'],
            'SafeHeight':p['safe_height_mm'],'ClearanceHeight':p['clearance_height_mm'],'DepthOffset':0,
            'OptimizeLinearPaths':True,'OptimizeStepOverTransitions':False,'CircularUseG2G3':False,'CutPatternAngle':0}
        for key,value in values.items():op.setExpression(key,None);setattr(op,key,value)
        op.Label='RoundSurface';doc.recompute()
        if not op.Path.Commands or 'Invalid' in op.State or len(job.Operations.Group)!=1:
            raise ValueError('Native Surface did not produce a valid path')
        raw=[{'name':c.Name,'parameters':dict(c.Parameters)} for c in op.Path.Commands]
        write(out/'native-path.json',{'native_operation':'Surface','commands':raw,
              'properties':{k:(getattr(op,k).Value if hasattr(getattr(op,k),'Value') else getattr(op,k)) for k in values}})
        prepared,feed_check=adapt(raw,[0,0,p['clearance_height_mm']],p['feed_mm_min'],p['plunge_mm_min'])
        op.Path=NativePath.Path([NativePath.Command(c['name'],c['parameters']) for c in prepared])
        write(out/'prepared-path.json',{'commands':prepared,'feed_adaptation':feed_check,
            'raw_native_path':file_binding(out/'native-path.json')})
        preamble=native_header(plan).rsplit('G21\n',1)[0].rstrip().replace('\n','\\n')
        args='--no-show-editor --no-tlo --precision 5 --preamble "'+preamble+'"'
        nc=out/'program.nc';linuxcnc_post.export([tc,op],str(nc),args)
        result=check_nc(nc.read_text(),plan);doc.saveAs(str(out/'cam-job.FCStd'))
        write(out/'cam-result.json',{'schema':'genlaw.round-surface-cam.v1','status':'native_generated_and_checked',
            'input':plan['input'],'native_geometry':file_binding(out/'native-geometry.json'),'NC':file_binding(nc),
            'native_path':file_binding(out/'native-path.json'),'nc_check':result,'native_stock':stock,
            'prepared_path':file_binding(out/'prepared-path.json'),'feed_adaptation':feed_check,
            'backend_files':[file_binding(Path(m.__file__)) for m in [Surface,Support,linuxcnc_post]],
            'freecad_version':list(App.Version()),'postprocessor':'FreeCAD bundled linuxcnc_post','post_arguments':args,
            'no_linuxcnc_runtime':True,'NC_text_modified':False,'material_result_not_yet_measured':True,
            'whole_real_card_complete':False,'production_approved':False})
        if prepare(req['input'])!=plan:raise ValueError('Input changed during native CAM')
    finally:App.closeDocument(doc.Name)


if 'GENLAW_ROUND_CAM_REQUEST' in os.environ:
    request=json.loads(Path(os.environ.pop('GENLAW_ROUND_CAM_REQUEST')).read_text())
    try:run(request)
    except Exception as error:
        import traceback
        write(Path(request['output'])/'cam-failure.json',{'status':'failed','error_type':type(error).__name__,
            'error':str(error),'traceback':traceback.format_exc()})
        print(traceback.format_exc(),flush=True)
        raise
