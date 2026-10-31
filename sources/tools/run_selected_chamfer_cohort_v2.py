"""One finite four-case native phase, then three declared geometry controls."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.cell_windows import file_binding, checked_file
from genlaw_pipeline.planar_resume import process_identity
from genlaw_pipeline.run import write
from tools.run_selected_chamfer_v2 import cam, material

READ = lambda p: json.loads(Path(p).read_text())


def run(inputs, output, build, phase, review):
    for p in [inputs, output, build, phase, review]: p.relative_to(ROOT)
    phase.mkdir(parents=True, exist_ok=False)
    archive = file_binding(inputs/'manifest.json'); review_value = READ(review)
    if review_value['status'] != 'software_inputs_reviewed_before_NC' or review_value['archive'] != archive:
        raise ValueError('Require completed paper/CAD input review before the native phase')
    frozen = [file_binding(Path(__file__)), file_binding(ROOT/'tools/check_selected_chamfer_v2.py'),
              file_binding(ROOT/'genlaw_pipeline/selected_chamfer_measurement_v2.py')]
    write(phase/'owner.json', {'process': process_identity(os.getpid()), 'archive': archive,
        'build': file_binding(build), 'input_review': file_binding(review), 'sources': frozen,
        'finite_cases': 4, 'geometry_controls': 3})
    def current(stage, case):
        temp = phase/'current-next.json'
        write(temp, {'stage': stage, 'case': case, 'at_utc': datetime.now(timezone.utc).isoformat()})
        temp.replace(phase/'current.json')
    rows = []
    try:
        specs = READ(inputs/'specifications.json')['cases']
        if [s['id'] for s in specs] != ['one-edge', 'opposite-long', 'opposite-short', 'shifted-wider']:
            raise ValueError('Unrecognized finite cohort order')
        for spec in specs:
            case = output/spec['id']; current('CAM', spec['id'])
            # The first CAM was inspected before scheduling this finite phase.
            # Any other prior directory is ambiguous and is never retried here.
            if spec['id'] == 'one-edge' and case.exists():
                if not (case/'cam-result.json').is_file() or (case/'simulation.camotics').exists():
                    raise ValueError('Only the completed first CAM without a material attempt may be reused')
                reused_CAM = True
            else:
                cam(inputs/spec['id']/'input.json', case); reused_CAM = False
            current('native_material', spec['id']); material(case, build)
            current('full_geometry_measurement', spec['id'])
            admitted_bounded(case, 'measure', [sys.executable, ROOT/'genlaw_pipeline/selected_chamfer_measurement_v2.py',
                '--source-run', case, '--target-run', case, '--output', case/'metrics.json'])
            m = READ(case/'metrics.json')
            if not m['geometry_pass'] or not m['regression_expectation_met']:
                raise ValueError('Normal selected-edge case failed')
            row = {'id': spec['id'], 'metrics': file_binding(case/'metrics.json'),
                'iou_percent': m['iou_percent'], 'geometry_pass': True, 'prior_CAM_reused': reused_CAM}
            rows.append(row); write(case/'case-result.json', row)
            print(json.dumps({'normal_completed': spec['id'], 'iou_percent': m['iou_percent']}), flush=True)
        controls = []
        for kind, source, target in [('omitted-opposite-edge', 'one-edge', 'opposite-long'),
            ('wrong-edge-axis', 'opposite-short', 'opposite-long'),
            ('additional-unselected-edge', 'opposite-long', 'one-edge')]:
            current('geometry_control', kind); dest = output/kind; dest.mkdir()
            admitted_bounded(dest, 'measure', [sys.executable, ROOT/'genlaw_pipeline/selected_chamfer_measurement_v2.py',
                '--source-run', output/source, '--target-run', output/target, '--kind', kind, '--output', dest/'metrics.json'])
            m = READ(dest/'metrics.json')
            if m['geometry_pass'] or not m['regression_expectation_met'] or not m['target_NC_guard_rejection']:
                raise ValueError('Wrong edge selection was not rejected')
            controls.append({'id': kind, 'metrics': file_binding(dest/'metrics.json'),
                'iou_percent': m['iou_percent'], 'geometry_rejected': True, 'native_material_reused': True})
            print(json.dumps({'geometry_control': kind, 'iou_percent': m['iou_percent'], 'rejected': True}), flush=True)
        current('input_and_NC_rejections', None)
        admitted_bounded(phase, 'checks', [sys.executable, ROOT/'tools/check_selected_chamfer_v2.py',
            '--inputs', inputs, '--runs', output, '--output', phase/'checks'])
        for binding in frozen+[archive, file_binding(build)]: checked_file(binding)
        checks = READ(phase/'checks/result.json')
        if len(checks['input_rejections']) != 21 or len(checks['native_NC_rejections']) != 8:
            raise ValueError('Incomplete rejection matrix')
        result = {'schema': 'genlaw.selected-chamfer-suite.v1', 'status': 'four_normal_three_controls_qualified',
            'owner': file_binding(phase/'owner.json'), 'archive': archive, 'normal_cases': rows,
            'geometry_controls': controls, 'guard_checks': file_binding(phase/'checks/result.json'),
            'native_CAM_operation': 'Deburr', 'NC_text_modified': False,
            'complete_original_material_measured': True, 'feature_cohort_qualified': True,
            'development_dimension': 'part_features', 'scope': 'One or two parallel full top edges on synthetic rectangular coupons; fixed Z and explicit finite 90 degree frustum.',
            'actual_machine_or_material_physics_added': False, 'real_C3_part_size_tested': False,
            'whole_real_card_complete': False, 'production_approved': False, 'public_site_updated': False}
        write(output/'summary.json', result); write(phase/'result.json', {'status': 'completed', 'summary': file_binding(output/'summary.json')})
        current('completed', None); print(json.dumps({'cohort_completed': True, 'normal_cases': 4, 'geometry_controls': 3}), flush=True)
    except Exception as error:
        write(phase/'stopped.json', {'status': 'stopped_on_failure', 'error_type': type(error).__name__,
            'error': str(error), 'traceback': traceback.format_exc(), 'automatic_retry': False,
            'completed_normals': rows, 'public_site_updated': False})
        raise


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for key in ['inputs', 'output', 'build', 'phase', 'review']: p.add_argument('--'+key, type=Path, required=True)
    a = p.parse_args(); run(*[getattr(a, k).resolve() for k in ['inputs', 'output', 'build', 'phase', 'review']])
