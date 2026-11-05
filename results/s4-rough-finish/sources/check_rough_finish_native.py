"""Process defects against frozen CAD/criteria and the real material engine."""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.rough_finish_preparation import checked_plan
from genlaw_pipeline.rough_finish_nc import check_nc
from genlaw_pipeline.rough_finish_run import simulate, measure_stage
from genlaw_pipeline.run import write
from genlaw_pipeline.simulation_engine import selected_engine


def framed(text, label):
    pattern = r'\(begin operation: ' + re.escape(label) + r'\).*?\(finish operation: ' + re.escape(label) + r'\)'
    values = re.findall(pattern, text, re.S)
    if len(values) != 1:
        raise ValueError('Expected one native framed operation: ' + label)
    return values[0]


def controls(normal, out):
    out.mkdir(parents=True, exist_ok=False)
    (out / 'source.py').write_bytes(Path(__file__).read_bytes())
    baseline = json.loads((normal / 'run-result.json').read_text())
    if baseline['status'] != 'software_stage_checks_passed':
        raise ValueError('Require actual measured normal before negative controls')
    input_path = Path(baseline['input'])
    plan = checked_plan(input_path, normal / 'preparation-result.json')
    policy = input_path.parent / plan['source_bindings']['stage_policy']['path']
    full = (normal / 'program.nc').read_text()
    prefix = (normal / 'rough/program.nc').read_text()
    rough, finish = framed(full, 'RF_01_ROUGH'), framed(full, 'RF_02_FINISH')
    overcut, replacements = re.subn(r'Z(-\d+\.\d+)', lambda m: f'Z{float(m[1])-.25:.5f}', finish)
    if replacements < 3:
        raise ValueError('No finite finish-depth defect was inserted')
    wrong_allowance = prefix.replace(rough, finish.replace('RF_02_FINISH', 'RF_01_ROUGH'), 1)
    reordered = full.replace(rough, '__ROUGH_PLACEHOLDER__', 1).replace(finish, rough, 1).replace('__ROUGH_PLACEHOLDER__', finish, 1)
    cases = [
        ('omit-finish', prefix, 'finish', 2, False, 'Exact native rough prefix stops before the required finish.'),
        ('wrong-rough-allowance', wrong_allowance, 'rough', 1, False, 'Rough body incorrectly uses the zero-allowance finish path.'),
        ('finish-before-rough', reordered, 'finish', 2, True, 'Two actual bodies execute in reverse order; final shape alone may match.'),
        ('finish-overcut', full.replace(finish, overcut, 1), 'finish', 2, False, 'Finish cutting Z values are 0.25 mm too deep.'),
    ]
    engine = selected_engine()
    results = []
    for name, text, stage, completed, expected_geometry_pass, explanation in cases:
        folder = out / name
        folder.mkdir()
        (folder / 'program.nc').write_text(text)
        try:
            check_nc(text, plan, completed=completed)
        except ValueError as error:
            rejection = str(error)
        else:
            raise AssertionError('Unsafe process defect was not rejected: ' + name)
        write(folder / 'guard-rejection.json', {'status': 'rejected', 'reason': rejection,
            'original_input_sha256': baseline['input_sha256'], 'baseline_nc_sha256': digest(normal / 'program.nc'),
            'control_nc_sha256': digest(folder / 'program.nc'), 'fault': explanation,
            'original_cad_and_criteria_unchanged': True, 'normal_workflow_would_stop_before_simulation': True,
            'diagnostic_simulation_explicitly_bypasses_guard': True,
            'control_nc_modified_from_native_export': name != 'omit-finish'})
        reference = normal / 'rough' if stage == 'rough' else normal
        for filename in ('native-geometry.json', 'target-display.stl', 'target-display.json'):
            (folder / filename).write_bytes((reference / filename).read_bytes())
        simulate(folder, plan, engine)
        measured = measure_stage(folder, plan, policy, stage)
        assert measured['all_software_checks_passed'] is expected_geometry_pass, (name, measured)
        result = {'id': name, 'nc_rejected': True, 'rejection_reason': rejection, 'stage': stage,
                  'diagnostic_geometry': measured, 'expected_geometry_pass': expected_geometry_pass,
                  'expectation_met': True, 'production_approved': False,
                  'guard_rejection_sha256': digest(folder / 'guard-rejection.json'),
                  'simulation_result_sha256': digest(folder / 'simulation-result.json')}
        write(folder / 'control-result.json', result)
        results.append(result)
        print(json.dumps({'control': name, 'nc_rejected': True, 'iou_percent': measured['iou_percent'],
                          'geometry_checks_passed': measured['all_software_checks_passed']}, ensure_ascii=False), flush=True)
    # Defeat comment-only validation: swap bodies, retain the expected labels.
    spoof = reordered.replace('RF_02_FINISH', '__FINISH__').replace('RF_01_ROUGH', 'RF_02_FINISH').replace('__FINISH__', 'RF_01_ROUGH')
    try:
        check_nc(spoof, plan)
    except ValueError as error:
        spoof_rejection = str(error)
    else:
        raise AssertionError('Correct labels must not conceal wrong geometry')
    checked_plan(input_path, normal / 'preparation-result.json')
    result = {'schema': 'genlaw.rough-finish-native-controls.v1', 'status': 'passed', 'controls': results,
              'label_spoof_rejected': True, 'label_spoof_reason': spoof_rejection,
              'baseline_run_sha256': digest(normal / 'run-result.json'),
              'frozen_stage_policy_sha256': digest(policy), 'raw_pdf_agent_integration_tested': False,
              'all_controls_are_diagnostic_only': True, 'production_approved': False}
    write(out / 'result.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--normal', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    controls(args.normal.resolve(), args.output.resolve())
