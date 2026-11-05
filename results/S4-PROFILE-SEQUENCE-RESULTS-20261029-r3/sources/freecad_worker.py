"""Native FreeCAD worker. Run only through the bounded pipeline launcher."""
import json
import os
from pathlib import Path as FilePath
import struct
import sys
import traceback

ROOT = FilePath(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'step2gcode_demo/validation'))
from local_resource_limits import require_bounded_worker
from genlaw_pipeline.preparation import digest, load_json
from genlaw_pipeline.programming import checked_plan, check_nc
from genlaw_pipeline.drilling_geometry import SINGLE, MULTI, DRILLED, operations, operation_parameters


def write(path, value):
    with path.open('x') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2); stream.write('\n')


def bounds(shape):
    b=shape.BoundBox
    return {'min':[b.XMin,b.YMin,b.ZMin],'max':[b.XMax,b.YMax,b.ZMax]}


def export_display(shape, path):
    attempts=[]
    for deflection in [.001,.002,.004]:
        # Keep display triangulation separate from the native shape used by CAM.
        display=shape.copy(True,False)
        vertices, faces=display.tessellate(deflection)
        attempts.append({'deflection_mm':deflection,'triangles':len(faces)})
        if len(faces)<=50000:break
    else:raise ValueError('Target display exceeds triangle budget')
    with path.open('xb') as stream:
        stream.write(b'GenLaw original CAD display'.ljust(80,b'\0'))
        stream.write(struct.pack('<I',len(faces)))
        for face in faces:
            a,b,c=(vertices[i] for i in face)
            normal=(b-a).cross(c-a)
            if normal.Length: normal.normalize()
            stream.write(struct.pack('<12fH',*normal,*a,*b,*c,0))
    write(path.with_suffix('.json'),{'schema':'genlaw.cad-display.v1','representation':'display-only triangulation of original CAD',
          'deflection_mm':deflection,'triangles':len(faces),'attempts':attempts,'triangle_budget':50000,
          'sha256':digest(path),'used_as_measurement_reference':False,'native_cad_unchanged':True})


def run(request):
    require_bounded_worker()
    import FreeCAD as App
    import Part
    plan=checked_plan(request['input'],request['preparation'])
    if plan['schema']=='genlaw.execution-plan.v10':
        from genlaw_pipeline.profile_cam import run as run_profile
        return run_profile(request,plan,export_display)
    if plan['schema'] in ('genlaw.execution-plan.v9','genlaw.execution-plan.v11'):
        from genlaw_pipeline.sequence_cam import run as run_sequence
        return run_sequence(request,plan,export_display)
    if plan['schema']=='genlaw.execution-plan.v8':
        from genlaw_pipeline.helix_cam import run as run_helix
        return run_helix(request,plan,export_display)
    if plan['schema']=='genlaw.execution-plan.v7':
        from genlaw_pipeline.facing_cam import run as run_facing
        return run_facing(request,plan,export_display)
    if plan['schema']=='genlaw.execution-plan.v4':
        from genlaw_pipeline.pocket_cam import run as run_pocket
        return run_pocket(request,plan,export_display)
    if plan['schema']=='genlaw.execution-plan.v5':
        from genlaw_pipeline.step_cam import run as run_step
        return run_step(request,plan,export_display)
    if plan['schema']=='genlaw.execution-plan.v6':
        from genlaw_pipeline.region_cam import run as run_region
        return run_region(request,plan,export_display)
    source=FilePath(request['input']).parent/plan['cad_ref']['path']
    out=FilePath(request['output'])
    shape=Part.Shape(); shape.read(str(source))
    planned = operations(plan)
    drilled=plan['schema']=='genlaw.execution-plan.v3'
    blind_count=sum(not item['through'] for item in planned)
    if not shape.isValid() or len(shape.Solids)!=1 or len(shape.Faces)!=6+len(planned)+blind_count:
        raise ValueError('Native CAD is outside the declared disjoint drilled-hole scope')
    low,high=(plan['stock']['bounds_mm'][k] for k in ['min','max'])
    dims=[b-a for a,b in zip(low,high)]
    expected=Part.makeBox(*dims,App.Vector(*low))
    for item in planned:
        x,y=item['hole_center_xy_mm']; radius=item['diameter_mm']/2
        if item['through']:
            cutter=Part.makeCylinder(radius,dims[2],App.Vector(x,y,low[2]))
        else:
            p=operation_parameters(plan,item);shoulder=high[2]-item['cylindrical_depth_mm']
            cutter=Part.makeCylinder(radius,item['cylindrical_depth_mm'],App.Vector(x,y,shoulder))
            cone=Part.makeCone(0,radius,p['drill_tip_length_mm'],App.Vector(x,y,p['final_tip_z_mm']))
            cutter=cutter.fuse(cone)
        expected=expected.cut(cutter)
    difference=shape.cut(expected).Volume + expected.cut(shape).Volume
    if difference>1e-8:
        raise ValueError('Native CAD disagrees with the planned hole/stock: symmetric difference '+str(difference))
    cylinders=[(i,f) for i,f in enumerate(shape.Faces,1) if isinstance(f.Surface,Part.Cylinder)]
    if len(cylinders)!=len(planned) or sum(isinstance(f.Surface,Part.Plane) for f in shape.Faces)!=6:
        raise ValueError('Unsupported native CAD surfaces')
    if sum(isinstance(f.Surface,Part.Cone) for f in shape.Faces)!=blind_count:
        raise ValueError('Native conical bottom count differs from the planned blind holes')
    bindings=[]
    for item in planned:
        x,y=item['hole_center_xy_mm']; radius=item['diameter_mm']/2
        matches=[i for i,f in cylinders if abs(f.Surface.Radius-radius)<=1e-8
                 and abs(f.Surface.Center.x-x)<=1e-8 and abs(f.Surface.Center.y-y)<=1e-8
                 and abs(abs(f.Surface.Axis.z)-1)<=1e-8]
        if len(matches)!=1:raise ValueError('Native hole binding mismatch: '+item['feature_id'])
        bindings.append({'id':item['feature_id'],'center_xy_mm':[x,y],'diameter_mm':2*radius,
                         'kind':'through_hole' if item['through'] else 'blind_hole','axis':[0,0,1],'cylinder_face':'Face'+str(matches[0])})
        if not item['through']:
            bindings[-1].update(cylindrical_depth_mm=item['cylindrical_depth_mm'],bottom_angle_degrees=item['bottom_angle_degrees'])
    if len({b['cylinder_face'] for b in bindings})!=len(planned):raise ValueError('Native face reused for multiple features')
    first=bindings[0];x,y=first['center_xy_mm'];radius=first['diameter_mm']/2
    geometry={'schema':'genlaw.native-binding.v1','status':'verified','cad_sha256':digest(source),
              'reference_class':SINGLE,
              'bounds':bounds(shape),'volume_mm3':shape.Volume,'valid_single_solid':True,
              'hole_center_xy':[x,y],'hole_radius_mm':radius,'cylinder_face':first['cylinder_face'],
              'symmetric_difference_mm3':difference,'original_cad_reparsed':True,
              'reference_for_iou':'Analytic box minus cylinder checked by native BREP boolean difference.'}
    if plan['schema'] in ('genlaw.execution-plan.v2','genlaw.execution-plan.v3'):
        geometry.update(schema='genlaw.native-binding.v3' if drilled else 'genlaw.native-binding.v2',
                        reference_class=DRILLED if drilled else MULTI,features=bindings,
                        reference_for_iou=('Analytic box minus drilled-hole volumes checked by native BREP boolean difference.' if drilled else
                                           'Analytic box minus disjoint cylinders checked by native BREP boolean difference.'))
        for key in ['hole_center_xy','hole_radius_mm','cylinder_face']:del geometry[key]
    write(out/'native-geometry.json',geometry)
    export_display(shape,out/'target-display.stl')
    doc=App.newDocument('GenLaw_Drilling_Execution')
    try:
        import Path.Main.Job as Job
        import Path.Op.Drilling as Drilling
        from Path.Tool.toolbit import ToolBit
        from Path.Post.scripts import linuxcnc_post
        model=doc.addObject('PartDesign::Feature','Target'); model.Shape=shape; doc.recompute()
        job=Job.Create('Job',[model],None)
        for prop in ('ExtXneg','ExtXpos','ExtYneg','ExtYpos','ExtZneg','ExtZpos'):
            setattr(job.Stock,prop,0)
        tool=ToolBit.from_shape_id('drill.fcstd',label='GenLaw_Drill').attach_to_doc(doc=doc)
        tool.Diameter=plan['tool']['diameter_mm']
        tool.TipAngle=plan['tool']['tip_angle_degrees']; tool.Length=plan['tool']['length_mm']
        tc=job.Tools.Group[0]; tc.Label='TC GenLaw Drill'; tc.Tool=tool; tc.ToolNumber=1
        p=plan['parameters']
        tc.SpindleSpeed=p['spindle_rpm']; tc.HorizFeed=f"{p['feed_mm_min']} mm/min"; tc.VertFeed=f"{p['feed_mm_min']} mm/min"
        doc.recompute()
        cam_operations=[];operation_bindings=[]
        # CircularHoleBase sorts holes spatially. One native operation per card
        # step preserves the prescribed order without rewriting exported NC.
        for item,binding in zip(planned,bindings):
            p=operation_parameters(plan,item)
            op=Drilling.Create('Drilling',parentJob=job); op.ToolController=tc
            op.Proxy.findAllHoles(op)
            bases=[obj for obj,subs in op.Base if binding['cylinder_face'] in subs]
            if len(bases)!=1:raise ValueError('CAM cannot bind feature '+binding['id'])
            op.Base=[(bases[0],[binding['cylinder_face']])];op.Locations=[]
            for name,value in [('StartDepth',high[2]),('FinalDepth',p['cam_final_depth_mm']),
                               ('SafeHeight',p['safe_height_mm']),('ClearanceHeight',p['clearance_height_mm']),
                               ('RetractHeight',p['retract_plane_mm'])]:
                op.setExpression(name,None); setattr(op,name,value)
            op.ExtraOffset='Drill Tip'; op.PeckEnabled=False;op.KeepToolDown=False;doc.recompute()
            selected=[{'object':obj.Name,'subelements':list(subs)} for obj,subs in op.Base]
            if len(selected)!=1 or selected[0]['subelements']!=[binding['cylinder_face']]:
                raise ValueError('FreeCAD selected a different feature: '+str(selected))
            cam_operations.append(op)
            operation_bindings.append({'step_id':item['step_id'],'feature_id':item['feature_id'],
                                       'cam_operation':op.Name,'selected_features':selected})
        if bounds(job.Stock.Shape)!=bounds(shape) or len(job.Operations.Group)!=len(planned):
            raise ValueError('Unexpected CAM stock or operations')
        args='--no-show-editor --no-tlo --precision 5'
        nc=out/'program.nc'
        linuxcnc_post.export([tc,*cam_operations],str(nc),args)
        validation=check_nc(nc.read_text(),plan)
        if digest(source)!=plan['cad_ref']['sha256']:
            raise ValueError('Original CAD changed during generation')
        doc.saveAs(str(out/'cam-job.FCStd'))
        return {'schema':'genlaw.cam-result.v1','status':'generated_and_checked',
                'execution_plan_sha256':load_json(FilePath(request['preparation']))['execution_plan_sha256'],
                'native_cad_binding':geometry,'nc_sha256':digest(nc),'nc_check':validation,
                'freecad_version':list(App.Version()),'gui_up':bool(App.GuiUp),
                'postprocessor':'FreeCAD bundled linuxcnc_post','post_arguments':args,
                'postprocessor_sha256':digest(FilePath(linuxcnc_post.__file__)),
                'drilling_module_sha256':digest(FilePath(Drilling.__file__)),
                'selected_features':[s for b in operation_bindings for s in b['selected_features']],
                'operation_bindings':operation_bindings,'tool_number_mapping':{plan['tool']['id']:1},
                'path_commands':[{'name':c.Name,'parameters':dict(c.Parameters)} for op in cam_operations for c in op.Path.Commands],
                'linuxcnc_runtime_used':False,'production_approved':False}
    finally:
        App.closeDocument(doc.Name)


# FreeCAD's command-file loader does not set __name__ to '__main__'.
if __name__=='__main__' or 'GENLAW_CAM_REQUEST' in os.environ:
    request=load_json(FilePath(os.environ.pop('GENLAW_CAM_REQUEST')))
    try:
        result=run(request)
    except Exception as error:
        result={'schema':'genlaw.cam-result.v1','status':'failed','error':str(error),'traceback':traceback.format_exc()}
    write(FilePath(request['output'])/'cam-result.json',result)
    print(json.dumps({'status':result['status'],'error':result.get('error')},ensure_ascii=False),flush=True)
    if result['status']=='failed':
        raise RuntimeError(result['error'])
