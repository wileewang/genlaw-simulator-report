"""Reparse the frozen CAD chain and generate two independent native CAM jobs."""
import json
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT/'step2gcode_demo/validation'))
from local_resource_limits import require_bounded_worker
from genlaw_pipeline.cell_windows import checked_file, file_binding
from genlaw_pipeline.indexed_facing import prepare_chain, close_bounds
from genlaw_pipeline.native_stock import bounds
from genlaw_pipeline.run import write


def run(request):
    require_bounded_worker()
    import FreeCAD as App
    import Part
    from genlaw_pipeline.freecad_worker import run as native_cam, export_display
    out = Path(request['output']); chain = prepare_chain(Path(request['input']))
    if chain != json.loads((out/'preparation-result.json').read_text()):
        raise ValueError('Indexed preparation changed before native CAM')

    def read_box(path, expected):
        shape = Part.Shape(); shape.read(str(path))
        lo, hi = expected['min'], expected['max']
        nominal = Part.makeBox(*[b-a for a, b in zip(lo, hi)], App.Vector(*lo))
        delta = shape.cut(nominal).Volume + nominal.cut(shape).Volume
        if not shape.isValid() or len(shape.Solids) != 1 or delta > 1e-8 or not close_bounds(bounds(shape), expected):
            raise ValueError('Independent saved CAD is not the declared box')
        return shape, delta

    def flipped(shape):
        copied = shape.copy()
        copied.rotate(App.Vector(0, 0, 0), App.Vector(1, 0, 0), 180)
        copied.translate(App.Vector(*chain['setups'][1]['to_material'][9:]))
        return copied

    actual = []
    for i, plan in enumerate(chain['stage_plans']):
        path = Path(chain['stage_inputs'][i]).parent/plan['cad_ref']['path']
        shape, delta = read_box(path, plan['target_bounds_mm'])
        actual.append((shape, delta, file_binding(path)))
    final_bounds = chain['stage_target_bounds_in_material'][1]
    final_shape, final_delta = read_box(checked_file(chain['final_cad']), final_bounds)
    stock = chain['stage_plans'][1]['stock']['bounds_mm']; lo, hi = stock['min'], stock['max']
    planned_stock = flipped(Part.makeBox(*[b-a for a, b in zip(lo, hi)], App.Vector(*lo)))
    first_shape = actual[0][0]; transformed_final = flipped(actual[1][0])
    prior_difference = first_shape.cut(planned_stock).Volume + planned_stock.cut(first_shape).Volume
    final_difference = final_shape.cut(transformed_final).Volume + transformed_final.cut(final_shape).Volume
    if prior_difference > 1e-8 or final_difference > 1e-8:
        raise ValueError('Native CAD does not join across the declared flip')
    write(out/'native-cad-chain.json', {'schema': 'genlaw.indexed-facing-cad-binding.v1',
        'preparation': file_binding(out/'preparation-result.json'),
        'stage_cad': [row[2] for row in actual], 'final_cad': chain['final_cad'],
        'stage_box_symmetric_differences_mm3': [row[1] for row in actual],
        'final_box_symmetric_difference_mm3': final_delta,
        'nominal_stock_continuity_difference_mm3': prior_difference,
        'final_target_transform_difference_mm3': final_difference,
        'stage_target_bounds_in_material': chain['stage_target_bounds_in_material'],
        'final_volume_mm3': final_shape.Volume, 'CAD_reparsed_before_CAM': True,
        'nominal_CAM_stock_is_not_simulation_material': True, 'freecad_version': list(App.Version())})
    export_display(final_shape, out/'target-display.stl')
    for i, stage in enumerate(chain['setups']):
        dest = out/stage['id']; dest.mkdir()
        write(dest/'preparation-result.json', chain['preparations'][i])
        write(dest/'execution-plan.json', chain['stage_plans'][i])
        req = {'input': chain['stage_inputs'][i], 'preparation': str(dest/'preparation-result.json'), 'output': str(dest)}
        write(dest/'request.json', req)
        result = native_cam(req)
        if result['status'] != 'generated_and_checked':
            raise ValueError('Native facing CAM did not finish')
        write(dest/'cam-result.json', result)
    if prepare_chain(Path(request['input'])) != chain:
        raise ValueError('Input changed during native CAM')
    write(out/'cam-result.json', {'schema': 'genlaw.indexed-facing-cam.v1',
        'status': 'two_native_CAM_jobs_generated_and_checked',
        'native_CAD_chain': file_binding(out/'native-cad-chain.json'),
        'stages': [file_binding(out/s['id']/'cam-result.json') for s in chain['setups']],
        'linuxcnc_runtime_used': False, 'continuous_rotary_motion': False,
        'whole_process_card_complete': False, 'production_approved': False})


if 'GENLAW_INDEXED_CAM_REQUEST' in os.environ:
    request = json.loads(Path(os.environ.pop('GENLAW_INDEXED_CAM_REQUEST')).read_text())
    try:
        run(request)
    except Exception:
        traceback.print_exc()
        raise
