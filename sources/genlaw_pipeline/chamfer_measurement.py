"""Schedule the qualified chamfer observer for an ordinary card/CAD run.

Qualification and numerical workers keep the existing single-job limits. This
entry point has no negative-control bypass and never substitutes a saved result
for the material produced by the current NC.
"""
import argparse
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.cell_windows import file_binding, checked_file
from genlaw_pipeline.run import write

LOAD = lambda p: json.loads(p.read_text())
KINDS = {'chamfer_sections', 'chamfer_width', 'retained_planes', 'retained_thickness'}


def frozen_policy(input_path, execution_plan):
    """Bind all four declared acceptance rules before any NC is generated."""
    path = input_path.parent / 'software-policy.json'
    manifest = input_path.parent / 'manifest.json'
    binding = file_binding(path)
    if binding['sha256'] != LOAD(manifest)['files']['software-policy.json']['sha256']:
        raise ValueError('Chamfer acceptance policy differs from the frozen input')
    policy = LOAD(path)
    features = {op['feature_id'] for op in execution_plan['operations']}
    if (execution_plan['schema'] != 'genlaw.execution-plan.v14' or len(features) != 1
            or policy.get('schema') != 'genlaw.software-geometry-policy.v14'
            or policy.get('cad_sha256') != execution_plan['cad_ref']['sha256']
            or set(policy.get('features', {})) != features
            or any(set(rules) != KINDS for rules in policy['features'].values())
            or policy.get('production_approved') is not False):
        raise ValueError('A complete CAD-bound chamfer software policy is required')
    return {'policy': binding, 'manifest': file_binding(manifest)}


def check_qualification(qualification, engine, output):
    from genlaw_pipeline.chamfer_window_qualification import checked_qualification
    from genlaw_pipeline.chamfer_shards import require_bounded_worker
    require_bounded_worker()
    sources = {'qualification': file_binding(qualification), 'engine': file_binding(engine)}
    report = checked_qualification(qualification, engine)
    for binding in sources.values():
        checked_file(binding)
    write(output, {'schema': 'genlaw.chamfer-chain-qualification.v1', 'status': 'passed',
        **sources, 'normal_cases': len(report['normal_cases']), 'controls': len(report['controls']),
        'source_code': file_binding(Path(__file__)), 'production_approved': False})


def require_qualification(out, qualification, engine):
    if qualification is None:
        raise ValueError('Chamfer window run requires current v14 window regression; no native stage started')
    expected = {'qualification': file_binding(qualification), 'engine': file_binding(engine),
                'source_code': file_binding(Path(__file__))}
    output = out / 'chamfer-qualification.json'
    admitted_bounded(out, 'chamfer-qualification', [sys.executable, Path(__file__),
        'check-qualification', '--qualification', qualification,
        '--engine', engine, '--output', output])
    report = LOAD(output)
    if (report.get('schema') != 'genlaw.chamfer-chain-qualification.v1' or report.get('status') != 'passed'
            or any(report.get(k) != v for k, v in expected.items())):
        raise ValueError('Chamfer qualification receipt changed')
    for binding in expected.values():
        checked_file(binding)
    return file_binding(output)


def result_status(plan_path, metrics_path, verification_path):
    """An IoU alone cannot pass the selected feature checks or a test control."""
    plan, metrics, report = (LOAD(p) for p in [plan_path, metrics_path, verification_path])
    binding = file_binding(plan_path)
    if (metrics.get('schema') != 'genlaw.chamfer-sharded-iou.v1'
            or report.get('schema') != 'genlaw.chamfer-sharded-verification.v1'
            or metrics.get('status') != 'measured_numerical_estimate' or report.get('status') != 'measured'
            or metrics.get('plan') != binding or report.get('plan') != binding
            or metrics.get('surface') != plan['surface'] or metrics.get('reference') != plan['reference']
            or metrics.get('source_code') != plan['source_code'] or report.get('source_code') != plan['source_code']
            or 'test_control' in plan or report.get('test_only_guard_bypass', False)
            or metrics.get('per_window_iou_averaged') is not False):
        raise ValueError('Normal chamfer metrics and observations must share the current material plan')
    iou = metrics.get('iou')
    if (type(iou) not in (int, float) or not math.isfinite(iou) or not 0 <= iou <= 1
            or metrics.get('iou_percent') != 100*iou):
        raise ValueError('Invalid global material IoU')
    policy = LOAD(checked_file(plan['policy']))
    expected = [(fid, kind, rule) for fid, rules in policy['features'].items() for kind, rule in rules.items()]
    rows = report.get('software_regression', [])
    actual = [(r.get('feature_id'), r.get('kind'), r.get('criterion')) for r in rows]
    if not expected or actual != expected:
        raise ValueError('Chamfer acceptance results are missing, reordered or use different rules')
    passed = all(r['status'] == 'numeric_pass' for r in rows)
    if report.get('all_software_geometry_checks_passed') is not passed:
        raise ValueError('Chamfer acceptance summary disagrees with individual checks')
    return {'status': 'declared_geometry_verified' if passed else 'declared_geometry_rejected',
        'all_software_geometry_checks_passed': passed,
        'feature_acceptance': 'passed_declared_software_checks' if passed else 'failed_or_indeterminate_software_checks'}


def recheck_qualification(receipt):
    """Cheap final source/hash check after the already bounded full validation."""
    from genlaw_pipeline.chamfer_window_qualification import source_bindings
    report = LOAD(checked_file(receipt))
    for key in ['qualification', 'engine', 'source_code']:
        checked_file(report[key])
    if LOAD(checked_file(report['qualification']))['sources'] != source_bindings():
        raise ValueError('Qualified chamfer measurement sources changed during the run')


def measure(out, surface, reference, policy, caches, metrics, verification):
    directory = out / 'chamfer-measurement'
    directory.mkdir(exist_ok=False)
    worker = ROOT / 'genlaw_pipeline/chamfer_shards.py'
    sources = [file_binding(worker), file_binding(Path(__file__))]
    plan = directory / 'plan.json'
    admitted_bounded(directory, 'plan', [sys.executable, worker, 'plan', '--surface', surface,
        '--reference', reference, '--policy', policy, '--caches', caches, '--output', plan])
    data = LOAD(plan)
    for mode, key in [('part', 'parts'), ('section', 'heights'), ('rays', 'ray_groups')]:
        for i in range(len(data[key])):
            label = f'{mode}-{i:03d}'
            admitted_bounded(directory, label, [sys.executable, worker, mode, '--plan', plan,
                '--index', str(i), '--output', directory / (label+'.json')])
        print(json.dumps({'chamfer_measurement': mode, 'completed_workers': len(data[key])}), flush=True)
    for mode, output in [('finish', metrics), ('verify', verification)]:
        admitted_bounded(directory, mode, [sys.executable, worker, mode, '--plan', plan,
            '--directory', directory, '--output', output])
    for binding in sources:
        checked_file(binding)
    status = result_status(plan, metrics, verification)
    write(directory / 'result.json', {'schema': 'genlaw.chamfer-chain-measurement.v1', **status,
        'plan': file_binding(plan), 'metrics': file_binding(metrics), 'verification': file_binding(verification),
        'sources': sources, 'test_only_guard_bypass': False, 'full_tolerance_acceptance': False,
        'production_approved': False, 'whole_process_card_complete': False})
    return {**status, 'measurement': file_binding(directory / 'result.json'),
            'feature_verification': file_binding(verification)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['check-qualification'])
    for name in ['qualification', 'engine', 'output']:
        parser.add_argument('--'+name, required=True, type=Path)
    args = parser.parse_args()
    check_qualification(args.qualification, args.engine, args.output)
