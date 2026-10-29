"""One native FreeCAD job, original CAD, explicit stock and ordered operations."""
import json
from pathlib import Path
from .preparation import digest
from .sequence_geometry import CAPABILITY, REFERENCE, label
from .native_stock import set_box, bounds
from .native_planar_cam import write
from .sequence_nc import check_nc, check_prefix, native_header, native_shutdown
from .coordinate_setup import enabled, program_view, place_native_model
from .profile_sequence_geometry import CAPABILITY as PROFILED, REFERENCE as PROFILED_REFERENCE
from .pocket_sequence_geometry import CAPABILITY as POCKETED


def generate(request,plan,export_display=None):
    from local_resource_limits import require_bounded_worker
    require_bounded_worker()
    import FreeCAD as App
    import Part
    import Path as CamPath
    import Path.Main.Job as Job
    import Path.Op.MillFace as MillFace
    import Path.Op.Helix as Helix
    import Path.Op.Profile as Profile
    import Path.Op.PocketShape as PocketShape
    import Path.Tool.Controller as ToolController
    from Path.Tool.toolbit import ToolBit
    from Path.Post.scripts import linuxcnc_post
    from .facing_geometry import FACING
    profiled=plan['capability']==PROFILED
    pocketed=plan['capability']==POCKETED
    if plan['capability'] not in (CAPABILITY,PROFILED,POCKETED):raise ValueError('Unsupported native sequence')
    version='v11' if profiled else 'v9';reference=PROFILED_REFERENCE if profiled else REFERENCE
    out=Path(request['output']);input_root=Path(request['input']).parent;source=input_root/plan['cad_ref']['path']
    if pocketed:
        from .pocket_sequence_cam import bind_geometry
        shape,ref=bind_geometry(request,plan,export_display)
    else:
        shape=Part.Shape();shape.read(str(source));lo,hi=[plan['target_bounds_mm'][k] for k in ['min','max']]
        box=Part.makeBox(*[b-a for a,b in zip(lo,hi)],App.Vector(*lo));expected=box
        features=[op['geometry'] for op in plan['operations']];top=features[0];holes=[f for f in features if f['kind']=='through_hole'];cylinders={}
        outline=next((f for f in features if f['kind']=='capsule_outline'),None)
        if profiled:
            from .profile_geometry import endpoints
            a,b=endpoints(outline);r=outline['width_mm']/2;t=hi[2]-lo[2]
            core=(Part.makeBox(b[0]-a[0],2*r,t,App.Vector(a[0],a[1]-r,lo[2])) if outline['major_axis']=='X'
                  else Part.makeBox(2*r,b[1]-a[1],t,App.Vector(a[0]-r,a[1],lo[2])))
            capsule=core.fuse(Part.makeCylinder(r,t,App.Vector(*a,lo[2]))).fuse(Part.makeCylinder(r,t,App.Vector(*b,lo[2]))).removeSplitter()
            expected=capsule
        for f in holes:
            cylinders[f['id']]=Part.makeCylinder(f['diameter_mm']/2,hi[2]-lo[2],App.Vector(*f['center_xy_mm'],lo[2]))
            expected=expected.cut(cylinders[f['id']])
        delta=shape.cut(expected).Volume+expected.cut(shape).Volume
        bound_mismatch=(any(abs(bounds(shape)[k][i]-plan['target_bounds_mm'][k][i])>1e-8 for k in ['min','max'] for i in range(3))
                        if profiled else bounds(shape)!=plan['target_bounds_mm'])
        if not shape.isValid() or len(shape.Solids)!=1 or len(shape.Faces)!=6+len(holes) or delta>1e-8 or bound_mismatch:
            raise ValueError('Original CAD disagrees with declared final face-and-hole geometry')
        tops=[i for i,face in enumerate(shape.Faces,1) if isinstance(face.Surface,Part.Plane)
              and abs(face.normalAt(0,0).z-1)<1e-8 and abs(face.BoundBox.ZMax-hi[2])<1e-8]
        if len(tops)!=1 or top['top_face']!='Face'+str(tops[0]):raise ValueError('Original top face binding differs')
        if profiled and outline['top_face']!=top['top_face']:raise ValueError('Original profile and top face binding differs')
        top_face=shape.Faces[tops[0]-1]
        if len(top_face.Wires)!=1+len(holes) or abs(top_face.Area-expected.Volume/(hi[2]-lo[2]))>1e-8:
            raise ValueError('Original perforated top topology differs')
        for f in holes:
            x,y=f['center_xy_mm'];r=f['diameter_mm']/2
            matches=[i for i,face in enumerate(shape.Faces,1) if isinstance(face.Surface,Part.Cylinder)
                     and abs(face.Surface.Radius-r)<1e-8 and abs(face.Surface.Center.x-x)<1e-8
                     and abs(face.Surface.Center.y-y)<1e-8 and abs(abs(face.Surface.Axis.z)-1)<1e-8]
            if len(matches)!=1 or f['cylinder_face']!='Face'+str(matches[0]):raise ValueError('Original cylinder face binding differs')
        ref={'schema':'genlaw.native-binding.'+version,'status':'verified','cad_sha256':digest(source),'reference_class':reference,
             'bounds':plan['target_bounds_mm'] if profiled else bounds(shape),'incoming_stock_bounds':plan['stock']['bounds_mm'],'volume_mm3':shape.Volume,
             'valid_single_solid':True,'symmetric_difference_mm3':delta,'original_cad_reparsed':True,
             'features':holes,'facing_feature':top,'feature_bindings':features,
             'reference_for_iou':'Original final CAD checked by native symmetric difference; all operations share separately declared original stock.'}
        if profiled:ref.update(profile_feature=outline,native_observed_bounds_mm=bounds(shape),bounds_comparison_tolerance_mm=1e-8)
        write(out/'native-geometry.json',ref)
        if export_display:export_display(shape,out/'target-display.stl')
        # Native-independent state oracles are verified before any path generation.
        oracles=json.loads((input_root/plan['expected_states_ref']['path']).read_text())['states'] if plan['expected_states_ref'] else None
        state_refs=[];current=box;finished=[];completed_holes=[];profile_done=False
        for i,op in enumerate(plan['operations']):
            f=op['geometry'];finished.append(f['id'])
            if f['kind']=='through_hole':
                current=current.cut(cylinders[f['id']]);completed_holes.append(f)
            elif f['kind']=='capsule_outline':
                current=current.common(capsule);profile_done=True
            record={'schema':'genlaw.native-binding.'+version,'status':'verified','reference_class':PROFILED_REFERENCE if profile_done else REFERENCE if i else FACING,
                'bounds':plan['target_bounds_mm'] if profiled else bounds(current),'volume_mm3':current.Volume,'valid_single_solid':True,
                'incoming_stock_bounds':plan['stock']['bounds_mm'],'features':list(completed_holes) if i else [top],
                'facing_feature':top,'feature_bindings':features[:i+1],
                'original_final_cad_sha256':digest(source),'completed_features':list(finished),
                'reference_for_iou':'Independent pre-NC expected state, verified against original final CAD dimensions; never used as simulation stock.'}
            if profile_done:record['profile_feature']=outline
            if profiled:
                if any(abs(bounds(current)[k][j]-plan['target_bounds_mm'][k][j])>1e-8 for k in ['min','max'] for j in range(3)):raise ValueError('Intermediate native bounds differ from declared original geometry')
                record.update(native_observed_bounds_mm=bounds(current),bounds_comparison_tolerance_mm=1e-8)
            if oracles:
                oracle=oracles[i];path=input_root/oracle['cad']['path'];observed=Part.Shape();observed.read(str(path))
                difference=current.cut(observed).Volume+observed.cut(current).Volume
                if digest(path)!=oracle['cad']['sha256'] or not observed.isValid() or len(observed.Solids)!=1 or difference>1e-8 or abs(observed.Volume-oracle['volume_mm3'])>1e-8:
                    raise ValueError('Frozen independent intermediate CAD disagrees')
                record.update(cad_sha256=digest(path),oracle_ref=oracle['cad'],symmetric_difference_mm3=difference)
            else:record.update(cad_sha256=digest(source),derived_from_original_features_before_nc=True)
            state_refs.append(record)
            if i<len(plan['operations'])-1:
                folder=out/'checkpoints'/f'after-{i+1:02d}';folder.mkdir(parents=True)
                write(folder/'native-geometry.json',record)
                if export_display:export_display(current,folder/'target-display.stl')
    doc=App.newDocument('GenLaw_Ordered_Milling')
    try:
        model=doc.addObject('PartDesign::Feature','Target');model.Shape=shape;doc.recompute();job=Job.Create('Job',[model],None)
        work=program_view(plan)
        if enabled(plan):write(out/'work-frame-binding.json',place_native_model(job,shape,plan))
        stock=set_box(job,work['stock']['bounds_mm']);controllers={}
        tolerance=min(op['parameters']['path_tolerance_mm'] for op in plan['operations']);job.GeometryTolerance=tolerance
        CamPath.Area.setDefaultParams(Accuracy=tolerance,Deflection=tolerance)
        for i,t in enumerate(plan['tools']):
            tool=ToolBit.from_shape_id('endmill.fcstd',label=f'GenLaw_Endmill_{t["number"]}').attach_to_doc(doc=doc)
            tool.Diameter=t['diameter_mm'];tool.Length=t['length_mm'];tool.CuttingEdgeHeight=t['cutting_length_mm']
            tc=job.Tools.Group[0] if i==0 else ToolController.Create(name=f'TC_{t["number"]:02d}',tool=tool,toolNumber=t['number'])
            p=next(op['parameters'] for op in plan['operations'] if op['tool_id']==t['id'])
            tc.Tool=tool;tc.ToolNumber=t['number'];tc.Label=f'TC_{t["number"]:02d}';tc.SpindleSpeed=p['spindle_rpm']
            tc.HorizFeed=f"{p['feed_mm_min']} mm/min";tc.VertFeed=f"{p['plunge_mm_min']} mm/min";controllers[t['id']]=tc
        doc.recompute();objects=[];prefix_lists=[];paths=[];bindings=[];last_tool=None
        for i,spec in enumerate(work['operations']):
            p=spec['parameters'];s=spec['strategy'];f=spec['geometry'];facing=i==0;external=f['kind']=='capsule_outline';pocket=f['kind']=='rounded_rectangular_pocket'
            tc=controllers[spec['tool_id']];op=(MillFace if facing else Profile if external else PocketShape if pocket else Helix).Create(label(i,spec),parentJob=job)
            # FreeCAD 1.1.4's headless first-op default picker has no choice for
            # multiple controllers. Register additional controllers on first
            # explicit use, after creating the op; never let the picker decide
            # the actual tool. All final bindings and T words are checked below.
            if tc not in job.Tools.Group:job.Proxy.addToolController(tc)
            op.Label=label(i,spec);op.ToolController=tc;selected=f['top_face'] if facing or external else f['floor_face'] if pocket else f['cylinder_face']
            op.Base=[(job.Model.Group[0],[selected])]
            for name,value in [('StartDepth',spec['start_z_mm']),('FinalDepth',spec['final_z_mm']),('StepDown',p['step_down_mm']),
                               ('SafeHeight',p['safe_height_mm']),('ClearanceHeight',p['clearance_height_mm'])]:
                op.setExpression(name,None);setattr(op,name,value)
            if not external:op.StepOver=int(p['step_over_percent']);op.CutMode=s['cut_mode']
            if facing:
                op.setExpression('FinishDepth',None);op.FinishDepth=0;op.ClearingPattern=s['clearing_pattern'];op.StartAt=s['start_at']
                op.KeepToolDown=False;op.ExtraOffset=0;op.BoundaryShape=s['boundary_shape'];op.ClearEdges=s['clear_edges'];op.ExcludeRaisedAreas=s['exclude_raised_areas']
            elif pocket:
                op.setExpression('FinishDepth',None);op.FinishDepth=0
                op.ClearingPattern=s['clearing_pattern'];op.StartAt=s['start_at'];op.KeepToolDown=False
                op.ExtraOffset=0;op.UseOutline=True
            elif external:
                for name,value in [('Side','Outside'),('Direction','CW'),('JoinType','Round'),('UseComp',True),('OffsetExtra',0),
                                   ('NumPasses',1),('Stepover',0),('processPerimeter',True),('processHoles',False),('processCircles',False),
                                   ('HandleMultipleFeatures','Collectively')]:setattr(op,name,value)
            else:op.StartRadius=s['start_radius_mm'];op.OffsetExtra=0;op.StartSide=s['start_at']
            doc.recompute()
            if not op.Path.Commands or 'Invalid' in op.State or len(op.Base)!=1 or list(op.Base[0][1])!=[selected]:
                raise ValueError('Native ordered operation has no valid bound path')
            if last_tool!=spec['tool_id']:objects.append(tc);last_tool=spec['tool_id']
            objects.append(op);prefix_lists.append(list(objects))
            paths.append({'cam_operation':op.Name,'step_id':spec['step_id'],'feature_id':f['id'],'tool_number':tc.ToolNumber,
                'commands':[{'name':c.Name,'parameters':dict(c.Parameters)} for c in op.Path.Commands]})
            bindings.append({'step_id':spec['step_id'],'feature_id':f['id'],'cam_operation':op.Name,'native_operation':'MillFace' if facing else 'Profile' if external else 'PocketShape' if pocket else 'Helix',
                'tool_id':spec['tool_id'],'tool_number':tc.ToolNumber,'selected_features':[{'object':obj.Name,'subelements':list(names)} for obj,names in op.Base],
                'start_depth_mm':op.StartDepth.Value,'final_depth_mm':op.FinalDepth.Value,'step_down_mm':op.StepDown.Value,
                **({'step_over_percent':op.StepOver} if not external else {'direction':op.Direction,'side':op.Side,'cam_radius_compensation':bool(op.UseComp),'holes_excluded':not op.processHoles}),
                **({'use_outline':bool(op.UseOutline),'whole_floor_outer_wire_verified':ref['pocket_outer_wire_verified']} if pocket else {}),
                'material_before_id':spec['material_before_id'],'material_after_id':spec['material_after_id']})
        if len(job.Operations.Group)!=len(plan['operations']) or [op.Label for op in job.Operations.Group]!=[label(i,op) for i,op in enumerate(plan['operations'])]:
            raise ValueError('Native job reordered operations')
        if bounds(job.Stock.Shape)!=work['stock']['bounds_mm']:raise ValueError('Native stock changed')
        write(out/'native-path.json',{'schema':'genlaw.ordered-native-path.v1','operations':paths})
        # Use the bundled post's supported preamble argument, not NC rewriting.
        preamble=native_header(plan).rsplit('G21\n',1)[0].rstrip().replace('\n','\\n')
        from .tool_length_setup import enabled as tool_lengths
        args='--no-show-editor '+('' if tool_lengths(plan) else '--no-tlo ')+'--precision 5 --preamble "'+preamble+'"'
        if enabled(plan):args+=' --postamble "'+native_shutdown(plan).rstrip().replace('\n','\\n')+'"'
        nc=out/'program.nc';linuxcnc_post.export(objects,str(nc),args)
        checked=check_nc(nc.read_text(),plan);prefixes=[]
        for i,group in enumerate(prefix_lists[:-1],1):
            folder=out/'checkpoints'/f'after-{i:02d}';prefix=folder/'program.nc'
            linuxcnc_post.export(group,str(prefix),args);proof=check_prefix(prefix.read_text(),nc.read_text(),plan,i)
            proof.update(full_nc_sha256=digest(nc),prefix_nc_sha256=digest(prefix),expected_native_geometry_sha256=digest(folder/'native-geometry.json'))
            write(folder/'prefix-binding.json',proof);prefixes.append({'id':f'after-{i:02d}','prefix_binding_sha256':digest(folder/'prefix-binding.json')})
        if digest(source)!=plan['cad_ref']['sha256']:raise ValueError('Original CAD changed')
        doc.saveAs(str(out/'cam-job.FCStd'))
        return {'schema':'genlaw.cam-result.v1','status':'generated_and_checked','native_cad_binding':ref,
            'nc_sha256':digest(nc),'native_operation':'ordered_MillFace_PocketShape_Helix' if pocketed else 'ordered_MillFace_Profile_Helix' if profiled else 'MillFace_then_Helix','native_stock':stock,
            'freecad_version':list(App.Version()),'gui_up':bool(App.GuiUp),'postprocessor':'FreeCAD bundled linuxcnc_post','post_arguments':args,
            'native_path_sha256':digest(out/'native-path.json'),'operation_bindings':bindings,'nc_check':checked,
            'checkpoint_prefixes':prefixes,'production_approved':False,'whole_process_card_complete':False,
            **({'work_frame_binding_sha256':digest(out/'work-frame-binding.json'),
                'native_cam_frame':'program_work_coordinates','original_reference_frame':'original_cad'} if enabled(plan) else {})}
    finally:App.closeDocument(doc.Name)


def run(request,plan,export_display):
    result=generate(request,plan,export_display)
    result['execution_plan_sha256']=json.loads(Path(request['preparation']).read_text())['execution_plan_sha256']
    return result
