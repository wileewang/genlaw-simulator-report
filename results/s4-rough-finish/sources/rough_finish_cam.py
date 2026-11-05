"""Bound both native pocket operations to the same original final CAD face."""
import json
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'step2gcode_demo/validation'))
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.rough_finish_preparation import checked_plan
from genlaw_pipeline.rough_finish_nc import label, check_nc, check_prefix
from genlaw_pipeline.native_planar_cam import write


def bind_shape(source, feature, stock, out, export_display):
    import FreeCAD as App
    import Part
    from genlaw_pipeline.pocket_geometry import POCKET, nominal_area
    from genlaw_pipeline.native_stock import bounds
    shape = Part.Shape()
    shape.read(str(source))
    lo, hi = stock['min'], stock['max']
    x, y = feature['center_xy_mm']
    w, h, r, d = [feature[k] for k in ('width_mm', 'height_mm', 'corner_radius_mm', 'depth_mm')]
    z = hi[2] - d
    # Independent of the archived filleted-prism construction.
    cutter = Part.makeBox(w - 2*r, h, d, App.Vector(x-w/2+r, y-h/2, z)).fuse(
        Part.makeBox(w, h-2*r, d, App.Vector(x-w/2, y-h/2+r, z)))
    for dx in (-1, 1):
        for dy in (-1, 1):
            cutter = cutter.fuse(Part.makeCylinder(r, d, App.Vector(x+dx*(w/2-r), y+dy*(h/2-r), z)))
    expected = Part.makeBox(*[b-a for a, b in zip(lo, hi)], App.Vector(*lo)).cut(cutter.removeSplitter())
    delta = shape.cut(expected).Volume + expected.cut(shape).Volume
    if not shape.isValid() or len(shape.Solids) != 1 or delta > 1e-8:
        raise ValueError('Original stage CAD disagrees with the declared feature')
    floors = [(i, face) for i, face in enumerate(shape.Faces, 1)
              if isinstance(face.Surface, Part.Plane) and abs(face.BoundBox.ZMin-z) < 1e-8
              and face.BoundBox.ZLength < 1e-8]
    if len(floors) != 1 or abs(floors[0][1].Area - nominal_area(feature)) > 1e-8:
        raise ValueError('Cannot uniquely bind the original pocket floor')
    ref = {'schema': 'genlaw.native-binding.v4', 'status': 'verified',
           'cad_sha256': digest(source), 'reference_class': POCKET, 'bounds': bounds(shape),
           'volume_mm3': shape.Volume, 'valid_single_solid': True, 'original_cad_reparsed': True,
           'symmetric_difference_mm3': delta, 'features': [feature],
           'pocket_floor_face': 'Face' + str(floors[0][0]),
           'reference_for_iou': 'Independent union construction checked against archived native CAD.'}
    write(out / 'native-geometry.json', ref)
    export_display(shape, out / 'target-display.stl')
    return shape, ref


def generate(request):
    from local_resource_limits import require_bounded_worker
    require_bounded_worker()
    import FreeCAD as App
    import Path as CamPath
    import Path.Main.Job as Job
    import Path.Op.PocketShape as Pocket
    from Path.Tool.toolbit import ToolBit
    from Path.Post.scripts import linuxcnc_post
    from genlaw_pipeline.freecad_worker import export_display
    from genlaw_pipeline.native_stock import set_box, bounds
    plan = checked_plan(Path(request['input']), Path(request['preparation']))
    if plan['operations'][0]['parameters'] != plan['operations'][1]['parameters']:
        raise ValueError('Different stage presets need explicit native controller-transition support')
    out = Path(request['output'])
    source_dir = Path(request['input']).parent
    rough_dir = out / 'rough'
    rough_dir.mkdir(exist_ok=False)
    shape, ref = bind_shape(source_dir / plan['cad_ref']['path'], plan['operations'][1]['geometry'],
                            plan['stock']['bounds_mm'], out, export_display)
    rough_shape, rough_ref = bind_shape(source_dir / plan['rough_cad_ref']['path'],
        plan['operations'][0]['expected_feature'], plan['stock']['bounds_mm'], rough_dir, export_display)
    if shape.cut(rough_shape).Volume > 1e-8 or rough_shape.Volume <= shape.Volume:
        raise ValueError('Rough oracle must strictly contain the final target')
    doc = App.newDocument('GenLaw_Rough_Finish')
    try:
        model = doc.addObject('PartDesign::Feature', 'Target')
        model.Shape = shape
        doc.recompute()
        job = Job.Create('Job', [model], None)
        p = plan['operations'][0]['parameters']
        job.GeometryTolerance = p['path_tolerance_mm']
        CamPath.Area.setDefaultParams(Accuracy=p['path_tolerance_mm'], Deflection=p['path_tolerance_mm'])
        stock = set_box(job, plan['stock']['bounds_mm'])
        tool = ToolBit.from_shape_id('endmill.fcstd', label='GenLaw_RF_Flat_Endmill').attach_to_doc(doc=doc)
        tool.Diameter = plan['tool']['diameter_mm']
        tool.Length = plan['tool']['length_mm']
        tool.CuttingEdgeHeight = plan['tool']['cutting_length_mm']
        tc = job.Tools.Group[0]
        tc.Label = 'TC_RF_T1'
        tc.Tool = tool
        tc.ToolNumber = 1
        tc.SpindleSpeed = p['spindle_rpm']
        tc.HorizFeed = f'{p["feed_mm_min"]} mm/min'
        tc.VertFeed = f'{p["plunge_mm_min"]} mm/min'
        doc.recompute()
        objects, bindings, paths = [tc], [], []
        for i, spec in enumerate(plan['operations']):
            p = spec['parameters']
            op = Pocket.Create(label(i, spec), parentJob=job)
            op.Label = label(i, spec)
            op.ToolController = tc
            # Both stages select the ORIGINAL final CAD, never the rough oracle.
            op.Base = [(job.Model.Group[0], [ref['pocket_floor_face']])]
            for name, value in [('StartDepth', plan['stock']['bounds_mm']['max'][2]),
                                ('FinalDepth', spec['final_z_mm']), ('StepDown', p['step_down_mm']),
                                ('FinishDepth', 0), ('SafeHeight', p['safe_height_mm']),
                                ('ClearanceHeight', p['clearance_height_mm'])]:
                op.setExpression(name, None)
                setattr(op, name, value)
            op.StepOver = int(p['step_over_percent'])
            op.ClearingPattern = 'Offset'
            op.CutMode = 'Climb'
            op.StartAt = 'Center'
            op.KeepToolDown = False
            op.ExtraOffset = spec['allowance_mm']['radial']
            op.UseOutline = False
            doc.recompute()
            if not op.Path.Commands or 'Invalid' in op.State:
                raise ValueError('Native rough/finish operation has no valid path')
            if len(op.Base) != 1 or list(op.Base[0][1]) != [ref['pocket_floor_face']]:
                raise ValueError('Native operation lost the original final CAD face')
            objects.append(op)
            bindings.append({'step_id': spec['step_id'], 'stage': spec['stage'], 'cam_operation': op.Name,
                'original_cad_sha256': ref['cad_sha256'], 'selected_face': ref['pocket_floor_face'],
                'radial_extra_offset_mm': op.ExtraOffset.Value, 'start_z_mm': op.StartDepth.Value,
                'final_z_mm': op.FinalDepth.Value, 'step_down_mm': op.StepDown.Value,
                'native_area_parameters': op.AreaParams})
            paths.append({'step_id': spec['step_id'], 'stage': spec['stage'],
                          'commands': [{'name': c.Name, 'parameters': dict(c.Parameters)} for c in op.Path.Commands]})
        if bounds(job.Stock.Shape) != plan['stock']['bounds_mm'] or list(job.Operations.Group) != objects[1:]:
            raise ValueError('Native stock or operation order changed')
        write(out / 'native-path.json', {'operations': paths})
        args = '--no-show-editor --no-tlo --precision 5'
        nc, prefix = out / 'program.nc', rough_dir / 'program.nc'
        linuxcnc_post.export(objects, str(nc), args)
        linuxcnc_post.export(objects[:2], str(prefix), args)
        checked = check_nc(nc.read_text(), plan)
        proof = check_prefix(prefix.read_text(), nc.read_text(), plan)
        proof.update(full_nc_sha256=digest(nc), prefix_nc_sha256=digest(prefix))
        write(rough_dir / 'prefix-binding.json', proof)
        checked_plan(Path(request['input']), Path(request['preparation']))
        doc.saveAs(str(out / 'cam-job.FCStd'))
        return {'schema': 'genlaw.rough-finish-cam.v1', 'status': 'generated_and_checked',
                'nc_sha256': digest(nc), 'rough_nc_sha256': digest(prefix), 'nc_check': checked,
                'native_stock': stock, 'operation_bindings': bindings,
                'native_path_sha256': digest(out / 'native-path.json'),
                'native_cad_binding': ref, 'rough_cad_binding': rough_ref,
                'freecad_version': list(App.Version()), 'gui_up': bool(App.GuiUp),
                'postprocessor': 'FreeCAD bundled linuxcnc_post', 'post_arguments': args,
                'rough_oracle_used_as_cam_model_or_stock': False,
                'native_freecad_extension_required': False,
                'finish_strategy': 'Full pocket clearing after roughing; may revisit cleared space.',
                'production_approved': False, 'whole_process_card_complete': False}
    finally:
        App.closeDocument(doc.Name)


if os.environ.get('GENLAW_ROUGH_FINISH_CAM_REQUEST'):
    request = json.loads(Path(os.environ['GENLAW_ROUGH_FINISH_CAM_REQUEST']).read_text())
    try:
        result = generate(request)
    except Exception as error:
        traceback.print_exc()
        result = {'status': 'failed', 'error': str(error)}
    write(Path(request['output']) / 'cam-result.json', result)
