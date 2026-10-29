"""Inspect the observed queue interruption, then run only its unfinished stages.

The observed interruption left after-03 window 056 with a budget file only.
This recovery is deliberately specific; a different partial state is rejected.
"""
import argparse
import datetime
import fcntl
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tools'), str(ROOT/'web/tools')]
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.cell_windows import checked_file, checked_plan, file_binding
from mesh_metrics import require_bounded_worker
from connection_reference_stages import checked_case


def load(path): return json.loads(path.read_text())


def record(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2); stream.write('\n')


def inspect(run, queue, out):
    require_bounded_worker()
    case = checked_case(run, 'after-03'); plan_path = case/'windows/plan.json'
    plan, *_ = checked_plan(plan_path)
    intent = load(queue/'intent.json'); commands = intent['commands']
    if commands[34]['label'] != 'after-03-windows-056-064':
        raise ValueError('Unexpected unfinished stage')
    for index, item in enumerate(commands[:34]):
        receipt = load(queue/f'{index:03d}-{item["label"]}'/'returned.json')
        if receipt['returncode'] != 0 or receipt['command'] != item['command']:
            raise ValueError('Earlier queue stage did not complete')
    stage = queue/'034-after-03-windows-056-064'
    if (stage/'returned.json').exists() or (queue/'completed.json').exists():
        raise ValueError('Inspect the changed queue state before recovery')
    if load(stage/'started.json')['command'] != commands[34]['command']:
        raise ValueError('Interrupted command differs from intent')
    completed = []
    for index in range(56):
        folder = case/'windows'/f'window-{index:03d}'
        result = load(folder/'result.json'); audit = load(folder/'audit.json'); cache = load(folder/'sections.json')
        binding = file_binding(folder/'result.json')
        if (result['index'] != index or result['window'] != plan['windows'][index]
                or result['plan'] != file_binding(plan_path) or result['end_time_seconds'] is not None
                or audit['result'] != binding or cache['result'] != binding
                or audit['surface'] != result['surface'] or cache['original_surface'] != result['surface']
                or cache['geometry_modified'] or audit['artificial_caps']):
            raise ValueError('Existing actual window does not match its plan and reports')
        for entry in (result['surface'], result['resources'], result['budget'], result['source_code'],
                      audit['boundary_file'], audit['source_code'], cache['array'], cache['source_code']):
            checked_file(entry)
        for label in ('camotics', 'postprocess'):
            resources = load(folder/(label+'-resources.json'))
            if not resources['success'] or len(resources['cpu_affinity']) != 1:
                raise ValueError('An earlier actual computation was unsuccessful')
        completed.append({'index': index, 'result': binding, 'audit': file_binding(folder/'audit.json'),
                          'sections': file_binding(folder/'sections.json')})
    partial = case/'windows/window-056'
    if sorted(path.name for path in partial.iterdir()) != ['window-budget.json']:
        raise ValueError('Only the inspected, unstarted budget-only window may be moved')
    budget = load(partial/'window-budget.json')
    if budget['index'] != 56 or budget['plan'] != file_binding(plan_path) or budget['window'] != plan['windows'][56]:
        raise ValueError('Interrupted budget does not match the native plan')
    if any((case/'windows'/f'window-{i:03d}').exists() for i in range(57,64)):
        raise ValueError('Unexpected later windows exist')
    original = file_binding(partial/'window-budget.json')
    preserved = out/'interrupted-window-056'; partial.rename(preserved)
    observation = {'at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'schema': 'genlaw.connection-interruption-recovery.v1', 'tool_session': 93343,
        'tool_shell_exit_code': 143, 'cause': 'unknown', 'original_queue': str(queue.relative_to(ROOT)),
        'controller_observed_running_after_shell_exit_then_absent': True,
        'completed_original_windows_verified': 56, 'completed_original_windows': completed,
        'unstarted_partial_budget': original, 'preserved_partial_budget': file_binding(preserved/'window-budget.json'),
        'unfinished_start': 56, 'native_window_056_started_before_interruption': False,
        'actual_material_recomputed': False, 'frozen_numerical_sources_unchanged': True,
        'resource_limits_changed': False, 'recovery_source': file_binding(Path(__file__))}
    record(out/'inspection.json', observation)
    record(run/'recovered-interruptions.json', {'records': [file_binding(out/'inspection.json')],
        'scope': 'One terminated queue shell; all completed material retained; next native window had not started.'})
    print(json.dumps({'verified_windows': 56, 'budget_only_partial_preserved': True}), flush=True)


def continue_queue(run, queue, out):
    out.mkdir(parents=True, exist_ok=False)
    with (ROOT/'.runtime/connection-reference-queue.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        source = file_binding(Path(__file__))
        record(out/'intent.json', {'source': source, 'original_queue': file_binding(queue/'intent.json'),
            'begins_at_original_stage': 34, 'automatic_retry': False})
        admitted_bounded(out, 'inspect', [sys.executable, str(Path(__file__)), 'inspect',
            '--run', str(run), '--queue', str(queue), '--output', str(out)])
        for index, item in enumerate(load(queue/'intent.json')['commands'][34:], 34):
            checked_file(source)
            stage = out/f'{index:03d}-{item["label"]}'; stage.mkdir()
            record(stage/'started.json', dict(item, started_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat()))
            print(json.dumps({'stage_started': item['label'], 'original_stage_index': index}), flush=True)
            with (stage/'controller.log').open('x') as stream:
                completed = subprocess.run(item['command'], cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
            record(stage/'returned.json', dict(item, returncode=completed.returncode,
                finished_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat()))
            if completed.returncode:
                raise SystemExit(1)
            print(json.dumps({'stage_completed': item['label'], 'original_stage_index': index}), flush=True)
        record(out/'completed.json', {'completion': file_binding(run/'completion.json'),
            'completed_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['inspect', 'continue'])
    for name in ('run', 'queue', 'output'): parser.add_argument('--'+name, required=True, type=Path)
    args = parser.parse_args()
    run, queue, out = [path.resolve() for path in (args.run, args.queue, args.output)]
    for path in (run, queue, out): path.relative_to(ROOT)
    (inspect if args.phase == 'inspect' else continue_queue)(run, queue, out)
