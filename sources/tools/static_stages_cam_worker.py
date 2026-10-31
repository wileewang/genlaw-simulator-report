"""Verify the archived solid chain, then invoke the existing native CAM adapters."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT/'step2gcode_demo/validation'))
from local_resource_limits import require_bounded_worker
from genlaw_pipeline.cell_windows import checked_file, file_binding
from genlaw_pipeline.indexed_facing import IDENTITY, close_bounds
from genlaw_pipeline.native_stock import bounds
from genlaw_pipeline.run import write
from genlaw_pipeline.static_stages import prepare_chain


def run(request):
    require_bounded_worker()
    import FreeCAD as App
    import Part
    from genlaw_pipeline.freecad_worker import run as native_cam, export_display
    out = Path(request['output']); chain = prepare_chain(request['input'])
    if chain != json.loads((out/'preparation-result.json').read_text()):
        raise ValueError('Preparation changed before CAM')

    def box(bound):
        lo, hi = bound['min'], bound['max']
        return Part.makeBox(*[b-a for a,b in zip(lo,hi)], App.Vector(*lo))

    def placed(shape, pose):
        value = shape.copy()
        if pose != IDENTITY:
            value.rotate(App.Vector(0,0,0), App.Vector(1,0,0), 180)
            value.translate(App.Vector(*pose[9:]))
        return value

    def delta(a, b): return a.cut(b).Volume+b.cut(a).Volume
    previous = box(chain['original_stock']['bounds_mm']); records = []
    for i, stage in enumerate(chain['stages']):
        plan = chain['stage_plans'][i]
        path = Path(chain['stage_inputs'][i]).parent/plan['cad_ref']['path']
        original = Part.Shape(); original.read(str(path))
        observed = json.loads(checked_file(chain['stage_references'][i]).read_text())
        if (not original.isValid() or len(original.Solids) != 1
                or abs(original.Volume-observed['volume_mm3']) > 1e-8
                or not close_bounds(bounds(original),plan['target_bounds_mm'])):
            raise ValueError('Stage native CAD differs from the frozen target observation')
        incoming = placed(box(plan['stock']['bounds_mm']), stage['to_material'])
        continuity = delta(previous,incoming)
        target = placed(original,stage['to_material']); addition = target.cut(previous).Volume
        if continuity > 1e-8 or addition > 1e-8:
            raise ValueError('Full native CAD continuity failed; bounds alone are insufficient')
        records.append({'id': stage['id'], 'original_CAD': file_binding(path),
                        'reference': chain['stage_references'][i], 'nominal_incoming_difference_mm3': continuity,
                        'added_material_mm3': addition, 'target_volume_mm3': target.Volume,
                        'target_bounds_in_material': bounds(target)})
        previous = target
    final = Part.Shape(); final.read(str(checked_file(chain['final_cad'])))
    difference = delta(final,previous)
    if (not final.isValid() or len(final.Solids) != 1 or difference > 1e-8
            or not close_bounds(bounds(final),chain['stage_target_bounds_in_material'][-1])):
        raise ValueError('Final original CAD does not equal the last transformed target')
    write(out/'native-cad-chain.json', {'schema': 'genlaw.static-stage-cad-binding.v1',
        'preparation': file_binding(out/'preparation-result.json'), 'stages': records,
        'final_cad': chain['final_cad'], 'final_target_transform_difference_mm3': difference,
        'final_volume_mm3': final.Volume, 'CAD_reparsed_before_CAM': True,
        'nominal_CAM_stock_is_not_simulation_material': True, 'freecad_version': list(App.Version())})
    export_display(final,out/'target-display.stl')
    for i, stage in enumerate(chain['stages']):
        dest = out/stage['id']; dest.mkdir()
        write(dest/'preparation-result.json',chain['preparations'][i])
        write(dest/'execution-plan.json',chain['stage_plans'][i])
        req = {'input': chain['stage_inputs'][i], 'preparation': str(dest/'preparation-result.json'), 'output': str(dest)}
        write(dest/'request.json',req); result = native_cam(req)
        if result['status'] != 'generated_and_checked': raise ValueError('Native CAM did not finish')
        write(dest/'cam-result.json',result)
    if prepare_chain(request['input']) != chain: raise ValueError('Inputs changed during CAM')
    write(out/'cam-result.json', {'schema': 'genlaw.static-stage-cam.v1', 'status': 'native_jobs_generated_and_checked',
        'native_CAD_chain': file_binding(out/'native-cad-chain.json'),
        'stages': [file_binding(out/s['id']/'cam-result.json') for s in chain['stages']],
        'independent_program_tool_tables': True, 'controller_modal_state_carried_between_programs': False,
        'linuxcnc_runtime_used': False, 'continuous_rotary_motion': False,
        'whole_process_card_complete': False, 'production_approved': False})


if 'GENLAW_STATIC_STAGE_CAM_REQUEST' in os.environ:
    run(json.loads(Path(os.environ.pop('GENLAW_STATIC_STAGE_CAM_REQUEST')).read_text()))
