"""Archive independent selected-edge native solids before CAM execution."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT/'step2gcode_demo/validation'))
from local_resource_limits import require_bounded_worker
from genlaw_pipeline.cell_windows import file_binding
from genlaw_pipeline.native_stock import bounds
from genlaw_pipeline.run import write
from genlaw_pipeline.selected_chamfer_geometry import (
    REFERENCE, SelectedChamferReference, dimensions, side_of_top_edge, selected_faces)


def build(out):
    require_bounded_worker()
    import FreeCAD as App
    import Part
    specs = json.loads((out/'specifications.json').read_text())
    for spec in specs['cases']:
        case = out/spec['id']; case.mkdir()
        bound = spec['stock']; width = spec['width_mm']; sides = spec['selected_sides']
        sizes = dimensions(bound, width, sides)
        stock = Part.makeBox(*sizes, App.Vector(*bound['min']))
        named = {side_of_top_edge(e, bound): e for e in stock.Edges
                 if side_of_top_edge(e, bound) is not None}
        if len(named) != 4: raise ValueError('Original stock does not have four named top edges')
        shape = stock.makeChamfer(width, [named[s] for s in sides])
        shape.exportBrep(str(case/'target.brep')); shape.exportStep(str(case/'target.step'))
        doc = App.newDocument('SelectedChamfer_'+spec['id'].replace('-', '_'))
        obj = doc.addObject('PartDesign::Feature', 'Target'); obj.Shape = shape
        doc.recompute(); doc.saveAs(str(case/'target.FCStd')); App.closeDocument(doc.Name)
        observed = Part.Shape(); observed.read(str(case/'target.brep'))
        if not observed.isValid() or len(observed.Solids) != 1 or len(observed.Faces) != 6+len(sides):
            raise ValueError('Invalid selected-edge native CAD')
        actual = bounds(observed)
        if any(abs(actual[k][i]-bound[k][i]) > 1e-8 for k in bound for i in range(3)):
            raise ValueError('Selected chamfer changed the original outside bounds')
        reference = {'schema': 'genlaw.selected-chamfer-reference.v1', 'reference_class': REFERENCE,
            'cad': file_binding(case/'target.brep'), 'bounds': actual, 'width_mm': width,
            'selected_sides': sides, 'faces_by_side': selected_faces(observed, sides),
            'valid_single_solid': True, 'volume_mm3': observed.Volume,
            'target_created_before_NC': True, 'freecad_version': list(App.Version()),
            'construction': 'OCC makeChamfer on geometrically named original top edges; no NC or simulation input.'}
        if abs(SelectedChamferReference(reference).analytic_volume-observed.Volume) > 1e-8:
            raise ValueError('Native CAD and independent section volume disagree')
        write(case/'reference.json', reference)
    write(out/'cad-generation.json', {'status': 'created', 'independent_CADs': len(specs['cases']),
        'all_CADs_created_before_NC': True})


if 'GENLAW_SELECTED_CHAMFER_CAD' in os.environ:
    build(Path(os.environ.pop('GENLAW_SELECTED_CHAMFER_CAD')))
