"""Export independent final CAD in its original frame for human review only."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from genlaw_pipeline.cell_windows import checked_file, file_binding
from genlaw_pipeline.freecad_worker import export_display
from genlaw_pipeline.run import write
from step2gcode_demo.validation.local_resource_limits import require_bounded_worker


def run(request):
    require_bounded_worker()
    import Part
    suite_path = checked_file(request['suite'])
    suite = json.loads(suite_path.read_text())
    if suite['status'] != 'four_normal_three_controls_qualified':
        raise ValueError('Require the complete qualified cumulative cohort')
    out = Path(request['output']).resolve()
    out.relative_to(ROOT)
    out.mkdir(parents=True, exist_ok=False)
    rows = []
    for row in suite['normal_cases']:
        source = checked_file(row['states'][-1]['reference_CAD'])
        shape = Part.Shape()
        shape.read(str(source))
        if not shape.isValid() or len(shape.Solids) != 1 or shape.Volume <= 0:
            raise ValueError('Invalid original final CAD')
        metrics = json.loads(checked_file(row['states'][-1]['metrics']).read_text())
        inspection = json.loads(checked_file(metrics['inspection']).read_text())
        if abs(shape.Volume - inspection['original_CAD_volume_mm3']) > 1e-8:
            raise ValueError('Review CAD volume differs from the independent measurement reference')
        target = out / (row['id'] + '.stl')
        export_display(shape, target)
        rows.append({'id': row['id'], 'original_CAD': file_binding(source),
                     'display': file_binding(target), 'display_record': file_binding(target.with_suffix('.json')),
                     'native_volume_mm3': shape.Volume, 'placement_applied': False})
    write(out / 'result.json', {'schema': 'genlaw.cumulative-review-cad.v1', 'suite': file_binding(suite_path),
        'cases': rows, 'source': file_binding(Path(__file__)), 'used_for_measurement': False,
        'new_CAM_programs': 0, 'new_material_simulations': 0})


if 'GENLAW_CUMULATIVE_REVIEW_REQUEST' in os.environ:
    run(json.loads(Path(os.environ.pop('GENLAW_CUMULATIVE_REVIEW_REQUEST')).read_text()))
