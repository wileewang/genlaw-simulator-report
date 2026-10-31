"""Export bound whole-material and per-role CAD for display, in original coordinates."""
import json
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.cell_windows import checked_file,file_binding
from genlaw_pipeline.freecad_worker import export_display
from genlaw_pipeline.run import write
from step2gcode_demo.validation.local_resource_limits import require_bounded_worker
READ=lambda p:json.loads(Path(p).read_text())


def run(req):
    require_bounded_worker()
    import Part
    suite_path=checked_file(req['suite']);suite=READ(suite_path)
    if suite['status']!='four_batch_normals_and_two_native_fault_controls_verified':raise ValueError('Batch geometry suite incomplete')
    out=Path(req['output']).resolve();out.relative_to(ROOT);out.mkdir(parents=True,exist_ok=False);rows=[]
    for case in suite['cases']:
        if case['kind']!='normal':continue
        metrics=READ(checked_file(case['metrics']));inspection=READ(checked_file(metrics['inspection']))
        if not metrics['geometry_pass'] or not metrics['role_assignment_complete']:raise ValueError('Normal case did not pass per-role acceptance')
        folder=out/case['id'];folder.mkdir();roles={}
        for role,observation in inspection['feature_observations'].items():
            source=checked_file(observation['original_CAD']);shape=Part.Shape();shape.read(str(source))
            count=3 if role=='whole' else 1
            if (not shape.isValid() or len(shape.Solids)!=count
                    or abs(shape.Volume-observation['original_CAD_volume_mm3'])>1e-8):
                raise ValueError('Original role CAD changed since measurement')
            target=folder/(role+'.stl');export_display(shape,target)
            roles[role]={'original_CAD':file_binding(source),'display':file_binding(target),
                'display_record':file_binding(target.with_suffix('.json')),'native_volume_mm3':shape.Volume,
                'placement_applied':False,'solids':count}
        rows.append({'id':case['id'],'roles':roles})
    write(out/'result.json',{'schema':'genlaw.batch-review-cad.v1','suite':file_binding(suite_path),
        'cases':rows,'source':file_binding(Path(__file__)),'used_for_measurement':False,
        'new_CAM_programs':0,'new_material_simulations':0})


if 'GENLAW_BATCH_REVIEW_REQUEST' in os.environ:
    run(READ(Path(os.environ.pop('GENLAW_BATCH_REVIEW_REQUEST'))))
