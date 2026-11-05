"""Measure one actual stage against independently frozen stage criteria."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'web/tools'))
from mesh_metrics import require_bounded_worker, load_mesh
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.pocket_verification import observations, evaluate


def verify(reference, actual, policy_path, stage, resolution, output):
    require_bounded_worker()
    ref = json.loads((reference / 'native-geometry.json').read_text())
    policy = json.loads(policy_path.read_text())
    if policy.get('schema') != 'genlaw.rough-finish-policy.v1' or stage not in ('rough', 'finish'):
        raise ValueError('Invalid independent stage policy')
    rule = policy['stages'][stage]
    if ref['status'] != 'verified' or rule['cad_sha256'] != ref['cad_sha256']:
        raise ValueError('Stage criterion is not bound to this original native reference')
    vertices, faces, inspection = load_mesh(actual)
    measured = observations(vertices, faces, ref, resolution)
    item = measured['features'][ref['features'][0]['id']]
    checks = {kind: evaluate(kind, item, rule[kind])
              for kind in ('profile_xy', 'pocket_depth', 'floor_geometry')}
    result = {'schema': 'genlaw.rough-finish-stage-verification.v1', 'stage': stage,
              'all_software_checks_passed': all(c['status'] == 'numeric_pass' for c in checks.values()),
              'checks': checks, 'observations': measured, 'mesh_inspection': inspection,
              'mesh_sha256': digest(actual), 'native_reference_sha256': digest(reference / 'native-geometry.json'),
              'policy_sha256': digest(policy_path), 'registration_applied': False,
              'production_approved': False, 'surface_roughness_verified': False}
    with output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'stage': stage, 'checks': {k: v['status'] for k, v in checks.items()}}), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--actual', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--stage', choices=['rough', 'finish'], required=True)
    parser.add_argument('--resolution', type=float, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    verify(args.reference, args.actual, args.policy, args.stage, args.resolution, args.output)
