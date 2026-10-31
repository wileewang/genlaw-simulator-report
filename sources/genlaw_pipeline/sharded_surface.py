"""Validate an uncapped partitioned CAMotics surface without loading it all at once."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'web/tools'))
from mesh_metrics import np, STL_DTYPE, MAX_TRIANGLES, require_bounded_worker
from genlaw_pipeline.cell_windows import (checked_plan, checked_file, file_binding,
                                         window_budget, MAX_CELLS, MAX_XY_SAMPLES)

MAX_BOUNDARY_EDGES = 1_000_000


def write(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def triangles(path):
    with path.open('rb') as stream:
        header = stream.read(84)
    if len(header) != 84:
        raise ValueError('Truncated window STL header')
    count = struct.unpack_from('<I', header, 80)[0]
    if count > MAX_TRIANGLES or path.stat().st_size != 84+50*count:
        raise ValueError('Invalid or over-budget window STL')
    if not count:
        return np.empty((0, 3, 3), dtype='<f4')
    raw = np.memmap(path, dtype=STL_DTYPE, mode='r', offset=84, shape=(count,))
    points = np.ascontiguousarray(raw['points'])
    if not np.isfinite(points).all():
        raise ValueError('Non-finite window surface')
    points[points == 0] = 0  # Canonicalize only signed zero; no welding/displacement.
    return points


def canonical_facets(points):
    """Sorted exact float32 triangles, preserving winding and duplicate counts."""
    if not len(points):
        return np.empty(0, dtype='V36')
    first = np.lexsort((points[:, :, 2], points[:, :, 1], points[:, :, 0]), axis=1)[:, 0]
    ids = (first[:, None] + np.arange(3)) % 3
    canonical = np.take_along_axis(points, ids[:, :, None], axis=1)
    return np.sort(np.ascontiguousarray(canonical).reshape(-1, 9).view('V36').reshape(-1))


def checked_result(plan_path, path, expected_time=None):
    plan, project, binary, _ = checked_plan(plan_path)
    record = json.loads(path.read_text())
    if (record.get('schema') != 'genlaw.cell-window-result.v1'
            or record.get('status') != 'executed'
            or record.get('plan') != file_binding(plan_path)
            or record.get('end_time_seconds') != expected_time
            or record.get('source_code') != plan['source_code']):
        raise ValueError('Foreign, incomplete or mismatched window result')
    index = record['index']
    if (type(index) is not int or not 0 <= index < len(plan['windows'])
            or record['window'] != plan['windows'][index]):
        raise ValueError('Window result index or bounds changed')
    mesh = checked_file(record['surface'])
    budget = json.loads(checked_file(record['budget']).read_text())
    expected_budget = {**window_budget(record['window'], plan['grid']['steps']),
                       'window': record['window'], 'plan': file_binding(plan_path),
                       'index': index, 'global_cells': plan['global_cells']}
    if budget != expected_budget or not budget['allowed']:
        raise ValueError('Window execution budget differs')
    resources = json.loads(checked_file(record['resources']).read_text())
    expected_command = [str(binary), '--threads', '1', '--reduce=false', '--binary',
                        '--cell-window', ','.join(map(str, record['window']))]
    if expected_time is not None:
        expected_command += ['--time', str(expected_time)]
    expected_command += [str(project), str(mesh)]
    if (not resources.get('success') or resources.get('status') != 'completed'
            or resources.get('command') != expected_command
            or resources['wall_limit_seconds'] > 60
            or resources['seconds'] > 60
            or resources['per_process_address_space_limit_bytes'] > 2*1024**3
            or resources['peak_observed_group_rss_bytes'] > 2*1024**3
            or resources['file_size_limit_bytes'] > 64*1024**2
            or len(resources['cpu_affinity']) != 1):
        raise ValueError('Window native execution or resource proof differs')
    return record, mesh


def validate_results(plan_path, paths, expected_time=None):
    plan, _, _, _ = checked_plan(plan_path)
    if len(paths) != len(plan['windows']):
        raise ValueError('Missing or duplicate window results')
    records = {}
    for path in paths:
        record, mesh = checked_result(plan_path, path, expected_time)
        if record['index'] in records:
            raise ValueError('Duplicate window result index')
        records[record['index']] = (path, record, mesh)
    if set(records) != set(range(len(plan['windows']))):
        raise ValueError('Incomplete global surface coverage')
    return [records[i] for i in range(len(records))]


def audit_window(plan_path, result_path, output, expected_time=None):
    require_bounded_worker()
    record, mesh = checked_result(plan_path, result_path, expected_time)
    points = triangles(mesh)
    edge_path = output.with_suffix('.edges')
    if not len(points):
        with edge_path.open('xb'):
            pass
        data = {'triangles': 0, 'vertices': 0, 'boundary_edges': 0,
                'signed_volume_mm3': 0., 'bounds': None, 'z_levels_mm': []}
    else:
        flat = points.reshape(-1, 3)
        _, first, inverse = np.unique(flat.view('V12').reshape(-1), return_index=True, return_inverse=True)
        vertices = flat[first]
        faces = inverse.reshape(-1, 3).astype('<u4')
        unordered = np.ascontiguousarray(np.sort(faces, axis=1)).view('V12').reshape(-1)
        if len(np.unique(unordered)) != len(faces):
            raise ValueError('Duplicate triangle in canonical window surface')
        tri = points.astype(np.float64)
        normals = np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0])
        if np.any(np.linalg.norm(normals, axis=1) <= 1e-16):
            raise ValueError('Degenerate window triangle')
        edges = np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]))
        signs = np.where(edges[:, 0] < edges[:, 1], 1, -1)
        edges.sort(axis=1)
        _, first_edge, ids, counts = np.unique(np.ascontiguousarray(edges).view('V8').reshape(-1),
                                               return_index=True, return_inverse=True, return_counts=True)
        winding = np.bincount(ids, weights=signs)
        if np.any(counts > 2) or np.any(winding[counts == 2] != 0):
            raise ValueError('Nonmanifold or inconsistently oriented window surface')
        boundary = counts == 1
        boundary_edges = edges[first_edge[boundary]]
        boundary_signs = winding[boundary]
        if len(boundary_edges) > MAX_BOUNDARY_EDGES:
            raise ValueError('Boundary audit exceeds the bounded edge budget')
        # Vertex IDs are local; carry exact float32 endpoint bytes across shards.
        with edge_path.open('xb') as stream:
            for pair, sign in zip(boundary_edges, boundary_signs):
                a, b = vertices[pair[0]].tobytes(), vertices[pair[1]].tobytes()
                if b < a:
                    a, b, sign = b, a, -sign
                stream.write(a+b+struct.pack('<i', int(sign)))
        volume = math.fsum((np.einsum('ij,ij->i', tri[:, 0], np.cross(tri[:, 1], tri[:, 2])) / 6).tolist())
        data = {'triangles': len(points), 'vertices': len(vertices),
                'boundary_edges': len(boundary_edges), 'signed_volume_mm3': volume,
                'bounds': {'min': vertices.min(axis=0).tolist(), 'max': vertices.max(axis=0).tolist()},
                'z_levels_mm': np.unique(vertices[:, 2]).astype(float).tolist()}
    write(output, {'schema': 'genlaw.cell-window-surface-audit.v1', 'result': file_binding(result_path),
                   'surface': record['surface'], 'index': record['index'], **data,
                   'boundary_file': file_binding(edge_path), 'source_code': file_binding(Path(__file__)),
                   'vertex_displacement_mm': 0, 'artificial_caps': False,
                   'duplicates_removed': 0, 'holes_filled': False})
    print(json.dumps({'audited_window': record['index'], 'triangles': data['triangles'],
                      'boundary_edges': data['boundary_edges']}), flush=True)


def aggregate(plan_path, result_paths, audit_paths, output, expected_time=None):
    require_bounded_worker()
    records = validate_results(plan_path, result_paths, expected_time)
    if len(audit_paths) != len(records):
        raise ValueError('Missing surface audits')
    audits = {}
    for path in audit_paths:
        row = json.loads(path.read_text())
        index = row['index']
        if (row.get('schema') != 'genlaw.cell-window-surface-audit.v1' or index in audits
                or not 0 <= index < len(records) or row['result'] != file_binding(records[index][0])
                or row['surface'] != records[index][1]['surface']
                or row['source_code'] != file_binding(Path(__file__))):
            raise ValueError('Foreign, duplicate or stale surface audit')
        checked_file(row['boundary_file'])
        audits[index] = (path, row)
    edges = {}
    volumes, bounds, levels = [], [], set()
    triangle_count = 0
    for index in range(len(records)):
        _, row = audits[index]
        edge_path = checked_file(row['boundary_file'])
        if edge_path.stat().st_size != row['boundary_edges'] * 28:
            raise ValueError('Truncated boundary evidence')
        with edge_path.open('rb') as stream:
            for _ in range(row['boundary_edges']):
                entry = stream.read(28)
                key, sign = entry[:24], struct.unpack('<i', entry[24:])[0]
                if sign not in (-1, 1):
                    raise ValueError('Invalid edge orientation')
                count, winding = edges.get(key, (0, 0))
                count, winding = count+1, winding+sign
                if count > 2 or (count == 2 and winding):
                    raise ValueError('Nonmanifold or inconsistent shard seam')
                edges[key] = (count, winding)
                if len(edges) > MAX_BOUNDARY_EDGES:
                    raise ValueError('Global seam audit exceeds bounded edge storage')
        volumes.append(row['signed_volume_mm3'])
        if row['bounds'] is not None:
            bounds.append(row['bounds'])
        levels.update(row['z_levels_mm'])
        triangle_count += row['triangles']
    if any(count != 2 or sign != 0 for count, sign in edges.values()):
        raise ValueError('Open global surface: unpaired partition boundary')
    volume = math.fsum(volumes)
    if not math.isfinite(volume) or volume <= 0 or not bounds:
        raise ValueError('Non-positive global oriented volume')
    if len(levels) > 4096:
        raise ValueError('Global sectional measurement level budget exceeded')
    write(output, {'schema': 'genlaw.sharded-surface.v1', 'status': 'closed_oriented_surface',
                   'plan': file_binding(plan_path), 'end_time_seconds': expected_time,
                   'windows': [{'result': file_binding(records[i][0]), 'audit': file_binding(audits[i][0])}
                               for i in range(len(records))],
                   'triangles': triangle_count, 'volume_mm3': volume,
                   'bounds': {'min': [min(b['min'][i] for b in bounds) for i in range(3)],
                              'max': [max(b['max'][i] for b in bounds) for i in range(3)]},
                   'z_levels_mm': sorted(levels), 'watertight': True, 'consistent_winding': True,
                   'seam_edge_pairs': len(edges), 'all_global_cells_covered_once': True,
                   'vertex_displacement_mm': 0, 'holes_filled': False, 'artificial_caps': False,
                   'source_code': file_binding(Path(__file__)), 'production_approved': False})
    print(json.dumps({'windows': len(records), 'triangles': triangle_count,
                      'closed_oriented': True, 'volume_mm3': volume}), flush=True)


def compare_small(surface_path, canonical_path, output, combined_path):
    """Regression only: exact multiset equality to a full canonical-window output."""
    require_bounded_worker()
    surface = json.loads(surface_path.read_text())
    checked_file(surface['source_code'])
    if surface['status'] != 'closed_oriented_surface' or surface['triangles'] > MAX_TRIANGLES:
        raise ValueError('Comparison exceeds the small-mesh regression budget')
    plan_path = checked_file(surface['plan'])
    result_paths = [checked_file(w['result']) for w in surface['windows']]
    records = validate_results(plan_path, result_paths, surface['end_time_seconds'])
    pieces = [triangles(mesh) for _, _, mesh in records]
    joined = np.concatenate(pieces)
    canonical = triangles(canonical_path)
    left, right = canonical_facets(joined), canonical_facets(canonical)
    equal = np.array_equal(left, right)
    result = {'schema': 'genlaw.window-partition-invariance.v1', 'equal_oriented_float32_facets': equal,
              'partitioned_triangles': len(left), 'full_window_triangles': len(right),
              'partitioned_facets_sha256': hashlib.sha256(left.tobytes()).hexdigest(),
              'full_window_facets_sha256': hashlib.sha256(right.tobytes()).hexdigest(),
              'surface': file_binding(surface_path), 'canonical': file_binding(canonical_path),
              'registration': 'none', 'source_code': file_binding(Path(__file__))}
    write(output, result)
    if not equal:
        raise ValueError('Partition changed the actual oriented triangle multiset')
    with combined_path.open('xb') as stream:
        stream.write(b'GenLaw exact concatenation of audited uncapped CAMotics windows'.ljust(80, b' '))
        stream.write(struct.pack('<I', len(left)))
        for _, _, mesh in records:
            with mesh.open('rb') as source:
                source.seek(84)
                while block := source.read(1024*1024):
                    stream.write(block)
    print(json.dumps({'exact_partition_invariance': True, 'triangles': len(left)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    window = sub.add_parser('window')
    window.add_argument('--plan', required=True, type=Path)
    window.add_argument('--result', required=True, type=Path)
    window.add_argument('--output', required=True, type=Path)
    window.add_argument('--time', type=float)
    total = sub.add_parser('aggregate')
    total.add_argument('--plan', required=True, type=Path)
    total.add_argument('--directory', required=True, type=Path)
    total.add_argument('--output', required=True, type=Path)
    total.add_argument('--time', type=float)
    compare = sub.add_parser('compare')
    compare.add_argument('--surface', required=True, type=Path)
    compare.add_argument('--canonical', required=True, type=Path)
    compare.add_argument('--output', required=True, type=Path)
    compare.add_argument('--combined', required=True, type=Path)
    args = parser.parse_args()
    if args.action == 'window':
        audit_window(args.plan, args.result, args.output, args.time)
    elif args.action == 'aggregate':
        aggregate(args.plan, sorted(args.directory.glob('window-*/result.json')),
                  sorted(args.directory.glob('window-*/audit.json')), args.output, args.time)
    else:
        compare_small(args.surface, args.canonical, args.output, args.combined)


if __name__ == '__main__':
    main()
