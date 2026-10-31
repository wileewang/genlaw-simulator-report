"""Two true native time samples, on the qualified original lattice and NC.

The pre-existing bounded-window binary supports --time. The qualified stream
binary is linked from that same frozen native engine; the final surface is
reused. No display animation is used as geometry evidence.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'web/tools')]
from genlaw_pipeline.cell_windows import (checked_file, file_binding, candidate_engine,
    project_binding, expected_grid, inspect_and_plan, run_window, checked_plan)
from genlaw_pipeline.small_partition_material import windows
from genlaw_pipeline.native_stream import checked_build
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.planar_resume import process_identity
from genlaw_pipeline.sparse_material import checked_resources
from genlaw_pipeline.run import write
from mesh_metrics import require_bounded_worker, load_mesh

READ = lambda p: json.loads(Path(p).read_text())
CP = ROOT/'docs/checkpoints/2026-10-31/round-surface-01'
BASE = ROOT/'validation/runs/S4-ROUND-SURFACE-20261031-r2/baseline'


def join(out, key):
    require_bounded_worker()
    request = READ(out/'request.json')
    if request['source_code'] != file_binding(Path(__file__)):
        raise ValueError('Replay producer changed')
    spec = next(r for r in request['frames'] if r['id'] == key)
    plan_path = checked_file(request['window_plan'])
    plan, project, binary, env = checked_plan(plan_path)
    frame = out/key
    rows, total = [], 0
    for index, window in enumerate(plan['windows']):
        path = frame/f'window-{index:02d}'/'result.json'
        row = READ(path)
        if (row['schema'] != 'genlaw.cell-window-result.v1' or row['status'] != 'executed'
                or row['plan'] != request['window_plan'] or row['index'] != index
                or row['window'] != window or row['end_time_seconds'] != spec['time_seconds']):
            raise ValueError('Foreign or incomplete time sample')
        mesh = checked_file(row['surface'])
        command = [str(binary), '--threads', '1', '--reduce=false', '--binary', '--cell-window',
            ','.join(map(str, window)), '--time', str(spec['time_seconds']), str(project), str(mesh)]
        checked_resources(checked_file(row['resources']), command)
        checked_file(row['source_code'])
        with mesh.open('rb') as source:
            source.seek(80); count = struct.unpack('<I', source.read(4))[0]
        if mesh.stat().st_size != 84+50*count:
            raise ValueError('Truncated native window')
        total += count
        rows.append({'result': file_binding(path), 'surface': row['surface'], 'triangles': count})
    if not 0 < total <= 500000:
        raise ValueError('Unchanged complete small-surface limit exceeded')
    combined = frame/'surface.stl'
    with combined.open('xb') as dest:
        dest.write(b'Original native time-cutoff window facets'.ljust(80, b'\0'))
        dest.write(struct.pack('<I', total))
        for row in rows:
            with checked_file(row['surface']).open('rb') as source:
                source.seek(84); shutil.copyfileobj(source, dest, 1024*1024)
    vertices, faces, inspection = load_mesh(combined)
    if inspection['exact_duplicate_faces_removed'] or not inspection['watertight'] or not inspection['consistent_winding']:
        raise ValueError('Time sample is not the unchanged complete closed surface')
    write(frame/'frame.json', {**spec, 'kind': 'native_time_cutoff', 'surface': file_binding(combined),
        'request': file_binding(out/'request.json'), 'native_windows': rows, 'inspection': inspection,
        'resolution_mm': plan['grid']['resolution_mm'], 'faces_discarded': False, 'vertices_moved': False,
        'artificial_caps': False, 'full_original_stock': True, 'independent_operation_acceptance': False})


def build(out):
    out = out.resolve(); out.relative_to(ROOT)
    completion = READ(CP/'completion.json'); suite = READ(checked_file(completion['suite']))
    if suite['status'] != 'four_normal_two_geometry_controls_qualified' or not suite['feature_cohort_qualified']:
        raise ValueError('Qualify results before representative replay')
    baseline = next(r for r in suite['normal_cases'] if r['id'] == 'baseline')
    metric = READ(checked_file(baseline['metrics'])); inspection = READ(checked_file(metric['inspection']))
    cam = READ(checked_file(baseline['CAM'])); plan = READ(checked_file(baseline['preparation']))
    project = BASE/'simulation.camotics'; config, binding = project_binding(project)
    if config != plan['simulation_project'] or binding['nc_files'] != [baseline['NC']]:
        raise ValueError('Replay changed original stock, cutter or NC')
    qualification = READ(checked_file(completion['partition_qualification']))
    build_path = checked_file(qualification['build']); stream = READ(build_path); checked_build(build_path)
    manifest = checked_file(stream['reference_manifest']); binary, env, _ = candidate_engine(manifest)
    out.mkdir(parents=True, exist_ok=False)
    shutil.copy2(Path(__file__), out/'replay-source.py')
    write(out/'owner.json', {'process': process_identity(os.getpid()), 'source_code': file_binding(Path(__file__))})
    window_plan = inspect_and_plan(project, manifest, out/'plan', windows(expected_grid(config)['steps']))
    frames = [{'id': 'frame-01', 'label': '加工中间状态 1', 'time_seconds': round(cam['nc_check']['feed_time_seconds']*.30, 6)},
              {'id': 'frame-02', 'label': '加工中间状态 2', 'time_seconds': round(cam['nc_check']['feed_time_seconds']*.70, 6)}]
    write(out/'request.json', {'schema': 'genlaw.round-surface-replay-request.v1',
        'qualification': file_binding(CP/'completion.json'), 'final_metrics': baseline['metrics'],
        'final_surface': baseline['mesh'], 'original_inputs': binding, 'window_plan': file_binding(window_plan),
        'stream_build': qualification['build'], 'native_reference_manifest': stream['reference_manifest'],
        'frames': frames, 'time_selection': '30% and 70% of guarded feed duration; native engine applies actual time cutoffs including its motion timing. These are display samples, not operation boundaries.',
        'source_code': file_binding(Path(__file__)), 'preserved_source': file_binding(out/'replay-source.py'),
        'target_CAD_used_for_removal': False, 'resolution_reduced': False, 'final_material_reexecuted': False})
    for spec in frames:
        frame = out/spec['id']; frame.mkdir()
        for index in range(8):
            run_window(window_plan, index, frame/f'window-{index:02d}', spec['time_seconds'])
            print(json.dumps({'frame': spec['id'], 'native_window': index+1, 'windows': 8}), flush=True)
        admitted_bounded(frame, 'join', [sys.executable, Path(__file__), 'join', '--output', out, '--frame', spec['id']])
    rows = [READ(out/r['id']/'frame.json') for r in frames]
    volumes = [r['inspection']['volume_mm3'] for r in rows] + [inspection['mesh_inspection']['volume_mm3']]
    if not all(a > b+1e-6 for a, b in zip(volumes, volumes[1:])):
        raise ValueError('Display samples did not retain distinct decreasing material states')
    write(out/'capture.json', {'schema': 'genlaw.round-surface-replay.v1', 'status': 'two_native_time_samples_ready',
        'request': file_binding(out/'request.json'), 'frames': [file_binding(out/r['id']/'frame.json') for r in frames],
        'final_surface': baseline['mesh'], 'remaining_volumes_mm3': volumes,
        'resolution_mm': config['resolution'], 'new_partial_native_windows': 16,
        'new_final_simulations': 0, 'independent_operation_acceptance': False})
    print(json.dumps({'replay_frames': 2, 'remaining_volumes_mm3': volumes}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['run', 'join']); parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--frame'); args = parser.parse_args()
    if args.mode == 'run': build(args.output)
    else: join(args.output.resolve(), args.frame)
