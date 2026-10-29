"""Export one accepted actual connection-reference state under existing limits."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tools')]
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.cell_windows import file_binding
from genlaw_pipeline.run import write
from connection_reference_stages import checked_case
import build_window_display


def build(run, output, key):
    run = run.resolve(); output = output.resolve()
    run.relative_to(ROOT); output.relative_to(ROOT)
    case = checked_case(run, key)
    report = json.loads((case/'features/verification.json').read_text())
    metrics = json.loads((case/'metrics.json').read_text())
    if (not report['all_software_geometry_checks_passed']
            or metrics['surface'] != file_binding(case/'surface.json')):
        raise ValueError('Only an accepted, bound actual state may be displayed')
    output.mkdir(parents=True, exist_ok=True)
    write(output/(key+'-intent.json'), {
        'case': key, 'surface': file_binding(case/'surface.json'),
        'metrics': file_binding(case/'metrics.json'),
        'verification': file_binding(case/'features/verification.json'),
        'source_code': file_binding(Path(__file__)),
        'display_builder': file_binding(ROOT/'tools/build_window_display.py'),
        'numerical_mesh_changed': False})
    build_window_display.bounded = admitted_bounded
    build_window_display.build(case/'surface.json', output/key)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case', choices=['final', 'after-01', 'after-02', 'after-03', 'after-04'], required=True)
    args = parser.parse_args()
    build(args.run, args.output, args.case)
