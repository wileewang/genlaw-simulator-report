"""Prepare accepted connection displays and an archive, one bounded job at a time.

This controller must start after numerical completion. It does not publish,
retry failed work, overwrite existing phases, or change measured geometry.
"""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tools')]
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.cell_windows import checked_file, file_binding


def record(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2); stream.write('\n')


def prepare(run, displays, archive, qa):
    run, displays, archive, qa = [path.resolve() for path in (run, displays, archive, qa)]
    for path in (run, displays, archive, qa): path.relative_to(ROOT)
    completion = json.loads((run/'completion.json').read_text())
    if completion['status'] != 'reference_geometry_measured_with_requirement_results':
        raise ValueError('All numerical work must complete before delivery')
    for key in ('final', 'checkpoints', 'software_geometry', 'manufacturing_requirements'):
        checked_file(completion[key])
    if archive.exists(): raise ValueError('Inspect an existing archive; do not overwrite it')
    qa.mkdir(parents=True, exist_ok=False)
    sources = [Path(__file__), ROOT/'tools/build_connection_display.py',
               ROOT/'tools/split_connection_display.py', ROOT/'tools/pack_connection_reference.py']
    bindings = [file_binding(path) for path in sources]
    record(qa/'intent.json', {'created_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'completion': file_binding(run/'completion.json'), 'sources': bindings,
        'archive': str(archive.relative_to(ROOT)), 'native_resource_limits_changed': False,
        'remaining_display_states': ['after-02', 'after-03', 'after-04'], 'publishes_automatically': False})

    def stage(label, command, bounded=True):
        for binding in bindings: checked_file(binding)
        record(qa/(label+'-started.json'), {'command': command, 'bounded_child': bounded,
            'started_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()})
        print(json.dumps({'stage_started': label}), flush=True)
        try:
            if bounded:
                admitted_bounded(qa, label, command)
            else:
                # Display exporter admits each of its own children; nesting the
                # global heavy-job lock would reject otherwise valid work.
                with (qa/(label+'-controller.log')).open('x') as stream:
                    subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=True)
        except BaseException as error:
            record(qa/(label+'-failed.json'), {'type': type(error).__name__, 'message': str(error)})
            raise
        record(qa/(label+'-finished.json'), {'finished_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()})
        print(json.dumps({'stage_completed': label}), flush=True)

    for key in ['after-02', 'after-03', 'after-04']:
        stage('display-'+key, [sys.executable, str(ROOT/'tools/build_connection_display.py'),
            '--run', str(run), '--output', str(displays), '--case', key], bounded=False)
        stage('split-'+key, [sys.executable, str(ROOT/'tools/split_connection_display.py'),
            '--display', str(displays/key/'display.json'), '--output', str(displays/key/'browser')])
    def pack(phase, extra=()):
        return [sys.executable, str(ROOT/'tools/pack_connection_reference.py'), phase,
                '--run', str(run), '--displays', str(displays), '--output', str(archive), *extra]
    stage('archive-initialize', pack('initialize'))
    plan = json.loads((archive/'compression-plan.json').read_text())
    for start in range(0, len(plan['unique']), 8):
        stage(f'archive-compress-{start:03d}', pack('compress', ['--start', str(start)]))
    for key in ['final', 'after-01', 'after-02', 'after-03', 'after-04']:
        stage('archive-transport-'+key, pack('transport', ['--case', key]))
    stage('archive-finish', pack('finish'))
    record(qa/'completed.json', {'completed_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'manifest': file_binding(archive/'manifest.json'), 'summary': file_binding(archive/'summary.json')})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('run', 'displays', 'archive', 'evidence-dir'):
        parser.add_argument('--'+name, required=True, type=Path)
    args = parser.parse_args()
    prepare(args.run, args.displays, args.archive, args.evidence_dir)
