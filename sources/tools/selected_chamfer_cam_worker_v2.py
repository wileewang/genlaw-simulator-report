"""Reparse independent selected-edge CAD and export native Deburr NC unchanged."""
import json
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT/'step2gcode_demo/validation'))
from local_resource_limits import require_bounded_worker
from genlaw_pipeline.cell_windows import file_binding, checked_file
from genlaw_pipeline.freecad_deburr import load_overlay
from genlaw_pipeline.freecad_selected_deburr import checked_formal_backend
from genlaw_pipeline.selected_chamfer_geometry import SelectedChamferReference, wedge_points, selected_faces
from genlaw_pipeline.selected_chamfer_preparation_v2 import checked_archive
from genlaw_pipeline.selected_chamfer_nc import check_nc
from genlaw_pipeline.native_stock import set_box, bounds
from genlaw_pipeline.sequence_nc import native_header
from genlaw_pipeline.run import write

READ = lambda p: json.loads(Path(p).read_text())


def run(request):
    require_bounded_worker()
    import FreeCAD as App
    import Part
    import Path as CamPath
    import Path.Main.Job as Job
    from Path.Tool.toolbit import ToolBit
    from Path.Post.scripts import linuxcnc_post
    from genlaw_pipeline.freecad_worker import export_display
    out = Path(request['output']); plan, admission = checked_archive(request['input'], request['archive'])
    if plan != READ(out/'preparation-result.json'): raise ValueError('Preparation changed before CAM')
    for b in request['execution_sources']+request['preserved_sources']: checked_file(b)
    spec, p, tool_spec = plan['specification'], plan['parameters'], plan['tool']
    original_ref = READ(checked_file(plan['input_bindings']['CAD_reference']))
    source = checked_file(plan['input_bindings']['CAD']); shape = Part.Shape(); shape.read(str(source))
    lo, hi = spec['stock']['min'], spec['stock']['max']; sizes = [b-a for a, b in zip(lo, hi)]
    expected = Part.makeBox(*sizes, App.Vector(*lo))
    for side in spec['selected_sides']:
        points, direction = wedge_points(spec['stock'], spec['width_mm'], side)
        wire = Part.makePolygon([App.Vector(*v) for v in points+[points[0]]])
        expected = expected.cut(Part.Face(wire).extrude(App.Vector(*direction)))
    difference = shape.cut(expected).Volume+expected.cut(shape).Volume
    observed_bounds = bounds(shape); observed_faces = selected_faces(shape, spec['selected_sides'])
    if (not shape.isValid() or len(shape.Solids) != 1 or len(shape.Faces) != 6+len(spec['selected_sides'])
            or difference > 1e-8 or observed_faces != plan['faces_by_side']
            or any(abs(observed_bounds[k][i]-spec['stock'][k][i]) > 1e-8 for k in spec['stock'] for i in range(3))
            or abs(SelectedChamferReference(original_ref).analytic_volume-shape.Volume) > 1e-8):
        raise ValueError('Independent original-solid and selected-face binding failed')
    write(out/'native-geometry.json', {**original_ref, 'status': 'verified', 'original_CAD_reparsed': True,
        'symmetric_difference_mm3': difference, 'independent_check': 'Union of explicitly selected triangular wedge cuts',
        'no_unselected_edges_chamfered': True})
    export_display(shape, out/'target-display.stl')
    Deburr, overlay = load_overlay(checked_formal_backend(p['backend']))
    doc = App.newDocument('GenLaw_Selected_Chamfer')
    try:
        model = doc.addObject('PartDesign::Feature', 'Target'); model.Shape = shape; doc.recompute()
        job = Job.Create('Job', [model], None); job.GeometryTolerance = p['path_tolerance_mm']
        CamPath.Area.setDefaultParams(Accuracy=p['path_tolerance_mm'], Deflection=p['path_tolerance_mm'])
        stock = set_box(job, spec['stock'])
        tool = ToolBit.from_shape_id('chamfer.fcstd', label='Explicit finite 90 degree frustum').attach_to_doc(doc=doc)
        for key, value in [('Diameter', tool_spec['diameter_mm']), ('TipDiameter', tool_spec['tip_diameter_mm']),
            ('Length', tool_spec['length_mm']), ('ShankDiameter', tool_spec['diameter_mm']),
            ('CuttingEdgeHeight', tool_spec['cutting_length_mm']), ('CuttingEdgeAngle', tool_spec['included_angle_degrees'])]:
            setattr(tool, key, value)
        tc = job.Tools.Group[0]; tc.Tool = tool; tc.ToolNumber = 1; tc.Label = 'TC SelectedChamfer T1'
        tc.SpindleSpeed = p['spindle_rpm']; tc.HorizFeed = f"{p['feed_mm_min']} mm/min"; tc.VertFeed = f"{p['plunge_mm_min']} mm/min"
        doc.recompute(); op = Deburr.Create('SelectedChamferEdges', parentJob=job); op.ToolController = tc
        faces = [observed_faces[s] for s in spec['selected_sides']]; op.Base = [(job.Model.Group[0], faces)]
        for key, value in [('Width', spec['width_mm']), ('ExtraDepth', p['extra_depth_mm']), ('StepDown', 0),
                           ('SafeHeight', p['safe_height_mm']), ('ClearanceHeight', p['clearance_height_mm'])]:
            op.setExpression(key, None); setattr(op, key, value)
        op.Direction = 'CW'; op.EntryPoint = 0; doc.recompute(); op.Label = 'SelectedChamferEdges'
        if (not op.Path.Commands or 'Invalid' in op.State or len(job.Operations.Group) != 1
                or len(op.Base) != 1 or list(op.Base[0][1]) != faces):
            raise ValueError('Native Deburr did not retain the exact selected face list')
        write(out/'native-path.json', {'operation': op.Name, 'selected_sides': spec['selected_sides'],
            'selected_faces': faces, 'commands': [{'name': cmd.Name, 'parameters': dict(cmd.Parameters)} for cmd in op.Path.Commands]})
        preamble = native_header(plan).rsplit('G21\n', 1)[0].rstrip().replace('\n', '\\n')
        args = '--no-show-editor --no-tlo --precision 5 --preamble "'+preamble+'"'
        nc = out/'program.nc'; linuxcnc_post.export([tc, op], str(nc), args)
        doc.saveAs(str(out/'cam-job.FCStd'))
        checked = check_nc(nc.read_text(), plan)
        if any(abs(bounds(job.Stock.Shape)[k][i]-spec['stock'][k][i]) > 1e-8 for k in spec['stock'] for i in range(3)):
            raise ValueError('CAM silently changed the original stock')
        if checked_archive(request['input'], request['archive'])[0] != plan: raise ValueError('Archived inputs changed during CAM')
        for b in request['execution_sources']+request['preserved_sources']: checked_file(b)
        result = {'schema': 'genlaw.selected-chamfer-cam.v1', 'status': 'native_generated_and_checked',
            'admission': admission, 'preparation': file_binding(out/'preparation-result.json'),
            'native_geometry': file_binding(out/'native-geometry.json'), 'NC': file_binding(nc),
            'native_path': file_binding(out/'native-path.json'), 'CAM_job': file_binding(out/'cam-job.FCStd'),
            'native_operation': 'Deburr', 'backend': overlay, 'native_stock': stock, 'freecad_version': list(App.Version()),
            'postprocessor': 'FreeCAD bundled linuxcnc_post', 'post_arguments': args, 'linuxcnc_runtime_used': False,
            'nc_check': checked, 'NC_text_modified': False, 'feature_cohort_qualified': False,
            'whole_real_card_complete': False, 'production_approved': False}
        write(out/'cam-result.json', result)
        return result
    finally:
        App.closeDocument(doc.Name)


if 'GENLAW_SELECTED_CHAMFER_CAM' in os.environ:
    request = READ(os.environ.pop('GENLAW_SELECTED_CHAMFER_CAM'))
    try: run(request)
    except Exception as error:
        write(Path(request['output'])/'cam-failure.json', {'status': 'failed', 'error_type': type(error).__name__,
            'error': str(error), 'traceback': traceback.format_exc(), 'native_outputs_retained': True})
        raise
