"""Audit actual sparse native surfaces without joining the complete mesh.

Each native leaf is checked under the original per-worker budget. Complete
facet and edge records are partitioned on disk; bucket workers check duplicate
facets and global incidence, including edges already closed inside one leaf.
Homogeneous certificates remain certificates and never acquire synthetic STLs.
"""
import argparse
import json
import math
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'web/tools')]
from mesh_metrics import np, MAX_TRIANGLES, require_bounded_worker
from genlaw_pipeline.cell_windows import checked_file, file_binding, MAX_WINDOWS
from genlaw_pipeline.simulation_engine import workspace_path
from genlaw_pipeline.sparse_material import checked_plan, checked_resources
from genlaw_pipeline.sharded_surface import triangles
from genlaw_pipeline.surface_records import audit_points, bucket_count, write_buckets, check_bucket, DTYPES, LIMITS
from genlaw_pipeline.run import write

LOAD = lambda p: json.loads(p.read_text())
SOURCES = ['genlaw_pipeline/sparse_stream_surface.py', 'genlaw_pipeline/surface_records.py',
           'genlaw_pipeline/sparse_material.py', 'genlaw_pipeline/material_blocks.py',
           'genlaw_pipeline/cell_windows.py', 'genlaw_pipeline/sharded_surface.py', 'web/tools/mesh_metrics.py']


def header_count(path):
    with path.open('rb') as stream:
        header = stream.read(84)
    if len(header) != 84:
        raise ValueError('Truncated sparse STL header')
    count = struct.unpack_from('<I', header, 80)[0]
    if count > MAX_TRIANGLES or path.stat().st_size != 84+50*count:
        raise ValueError('Invalid or over-budget sparse native STL')
    return count


def native_metadata(path, sparse_binding, sparse, project, binary, verify_payload=False):
    """The original sparse-result contract, with full payload checks per leaf."""
    row = LOAD(path); index = row.get('index')
    if (row.get('schema') != 'genlaw.sparse-material-leaf.v1' or row.get('status') != 'executed'
            or type(index) is not int or not 0 <= index < len(sparse['leaves'])
            or sparse['leaves'][index]['state'] != 'requires_native_cells'
            or row.get('plan') != sparse_binding or row.get('leaf') != sparse['leaves'][index]
            or row.get('source_code') != file_binding(ROOT / 'genlaw_pipeline/sparse_material.py')
            or row.get('end_time_seconds') is not None):
        raise ValueError('Missing, foreign or inapplicable native sparse result')
    mesh = checked_file(row['surface']) if verify_payload else workspace_path(row['surface']['path'])
    command = [binary, '--threads', '1', '--reduce=false', '--binary', '--cell-window',
               ','.join(map(str, row['leaf']['bounds'])), project, mesh]
    checked_resources(checked_file(row['resources']), command)
    return {'index': index, 'result': file_binding(path), 'surface': row['surface'],
            'triangles': header_count(mesh)}, mesh


def prepare(sparse_path, results, output):
    require_bounded_worker()
    sparse, project, binary, _ = checked_plan(sparse_path)
    binding = file_binding(sparse_path)
    rows = [native_metadata(p, binding, sparse, project, binary)[0] for p in results]
    rows.sort(key=lambda r: r['index'])
    expected = [r['index'] for r in sparse['leaves'] if r['state'] == 'requires_native_cells']
    if [r['index'] for r in rows] != expected:
        raise ValueError('Missing or duplicate native material leaves')
    total = sum(r['triangles'] for r in rows)
    if total == 0:
        raise ValueError('Empty complete material requires a separate acceptance contract')
    write(output, {'schema': 'genlaw.sparse-stream-plan.v1', 'sparse_plan': binding,
        'native_leaves': rows, 'triangles': total,
        'bucket_counts': {'facets': bucket_count(total, 'facets'), 'edges': bucket_count(3*total, 'edges')},
        'sources': [file_binding(ROOT / name) for name in SOURCES],
        'coverage': sparse['coverage'], 'record_limits': LIMITS,
        'whole_mesh_assembled': False, 'native_payload_check': 'each_leaf_worker',
        'production_approved': False})


def checked_stream_plan(path):
    plan = LOAD(path)
    if (plan.get('schema') != 'genlaw.sparse-stream-plan.v1' or plan.get('record_limits') != LIMITS
            or plan.get('whole_mesh_assembled') is not False
            or plan.get('native_payload_check') != 'each_leaf_worker'
            or plan.get('sources') != [file_binding(ROOT / name) for name in SOURCES]):
        raise ValueError('Unsupported or changed sparse stream plan')
    sparse_path = checked_file(plan['sparse_plan'])
    sparse, project, binary, _ = checked_plan(sparse_path)
    expected = [r['index'] for r in sparse['leaves'] if r['state'] == 'requires_native_cells']
    rows = plan['native_leaves']
    if ([r['index'] for r in rows] != expected or plan['coverage'] != sparse['coverage']
            or any(type(r['triangles']) is not int or not 0 <= r['triangles'] <= MAX_TRIANGLES for r in rows)
            or sum(r['triangles'] for r in rows) != plan['triangles'] or plan['triangles'] <= 0
            or plan['bucket_counts'] != {'facets': bucket_count(plan['triangles'], 'facets'),
                                         'edges': bucket_count(3*plan['triangles'], 'edges')}):
        raise ValueError('Sparse stream plan dropped or changed original material')
    return plan, sparse, project, binary


def audit_leaf(plan_path, index, out):
    require_bounded_worker()
    plan, sparse, project, binary = checked_stream_plan(plan_path)
    matches = [r for r in plan['native_leaves'] if r['index'] == index]
    if type(index) is not int or len(matches) != 1:
        raise ValueError('Only a declared native leaf can have a surface audit')
    entry = matches[0]
    current, mesh = native_metadata(checked_file(entry['result']), plan['sparse_plan'],
                                    sparse, project, binary, verify_payload=True)
    if current != entry:
        raise ValueError('Native leaf changed after stream preparation')
    out = out.resolve(); out.relative_to(ROOT); out.mkdir(parents=True, exist_ok=False)
    points = triangles(mesh)
    stats, facets, edges = audit_points(points)
    partitions = {kind: write_buckets(records, plan['bucket_counts'][kind], kind, out)
                  for kind, records in [('facets', facets), ('edges', edges)]}
    del facets, edges
    side = points[np.ptp(points[:, :, 2], axis=1) > 0] if len(points) else points
    ranges = np.column_stack((side[:, :, 2].min(axis=1), side[:, :, 2].max(axis=1)))
    for name, array in [('sections', side), ('heights', ranges)]:
        with (out / (name+'.npy')).open('xb') as stream:
            np.save(stream, array, allow_pickle=False)
    cache = {'schema': 'genlaw.sparse-section-cache.v1', 'stream_plan': file_binding(plan_path),
        'index': index, 'native_result': entry['result'], 'original_surface': entry['surface'],
        'triangles': len(side), 'omitted_exactly_horizontal_triangles': len(points)-len(side),
        'array': file_binding(out / 'sections.npy'), 'heights': file_binding(out / 'heights.npy'),
        'geometry_modified': False, 'source_code': file_binding(Path(__file__))}
    write(out / 'cache.json', cache)
    write(out / 'audit.json', {'schema': 'genlaw.sparse-leaf-audit.v1',
        'stream_plan': file_binding(plan_path), 'index': index, 'native_result': entry['result'],
        'original_surface': entry['surface'], 'statistics': stats, 'records': partitions,
        'cache': file_binding(out / 'cache.json'), 'source_code': file_binding(Path(__file__))})
    print(json.dumps({'audited_leaf': index, 'triangles': len(points), 'section_triangles': len(side)}), flush=True)


def checked_audit(path, plan_path, plan, entry):
    row = LOAD(path)
    if (row.get('schema') != 'genlaw.sparse-leaf-audit.v1' or row.get('index') != entry['index']
            or row.get('stream_plan') != file_binding(plan_path) or row.get('native_result') != entry['result']
            or row.get('original_surface') != entry['surface']
            or row.get('source_code') != file_binding(Path(__file__))):
        raise ValueError('Missing, duplicate or foreign sparse leaf audit')
    stats = row['statistics']; n = entry['triangles']
    if (stats['triangles'] != n or not math.isfinite(stats['signed_volume_mm3'])
            or type(stats['boundary_edges']) is not int or not 0 <= stats['boundary_edges'] <= 3*n
            or len(stats['z_levels_mm']) > 4096
            or not all(math.isfinite(v) for v in stats['z_levels_mm'])
            or stats['z_levels_mm'] != sorted(set(stats['z_levels_mm']))):
        raise ValueError('Invalid sparse leaf statistics')
    bounds = stats['bounds']
    if n:
        if (not isinstance(bounds, dict) or not stats['z_levels_mm']
                or any(len(bounds[k]) != 3 for k in ['min', 'max'])
                or not all(math.isfinite(v) for k in ['min', 'max'] for v in bounds[k])
                or any(a > b for a, b in zip(bounds['min'], bounds['max']))
                or [bounds['min'][2], bounds['max'][2]] != [stats['z_levels_mm'][0], stats['z_levels_mm'][-1]]):
            raise ValueError('Invalid sparse leaf bounds')
    elif bounds is not None or stats['z_levels_mm'] or stats['signed_volume_mm3'] != 0:
        raise ValueError('Invalid empty native leaf statistics')
    expected_records = {'facets': n, 'edges': (3*n+stats['boundary_edges'])//2}
    for kind, part in row['records'].items():
        if kind not in DTYPES or part['records'] != expected_records[kind] or part['records'] > LIMITS[kind]:
            raise ValueError('Invalid sparse record partition count')
        offset = 0; previous = -1
        for bucket in part['buckets']:
            if (type(bucket['index']) is not int or not previous < bucket['index'] < plan['bucket_counts'][kind]
                    or bucket['offset'] != offset or type(bucket['records']) is not int or bucket['records'] <= 0):
                raise ValueError('Missing, overlapping or reordered surface bucket range')
            offset += bucket['records']*DTYPES[kind].itemsize; previous = bucket['index']
        if offset != part['records']*DTYPES[kind].itemsize:
            raise ValueError('Sparse bucket partition dropped records')
    if set(row['records']) != set(DTYPES):
        raise ValueError('Missing facet or edge partition')
    cache_path = checked_file(row['cache']); cache = LOAD(cache_path)
    if (cache.get('schema') != 'genlaw.sparse-section-cache.v1'
            or cache.get('stream_plan') != file_binding(plan_path) or cache.get('index') != entry['index']
            or cache.get('native_result') != entry['result'] or cache.get('original_surface') != entry['surface']
            or cache.get('source_code') != file_binding(Path(__file__)) or cache.get('geometry_modified') is not False
            or type(cache['triangles']) is not int or not 0 <= cache['triangles'] <= n
            or cache['triangles']+cache['omitted_exactly_horizontal_triangles'] != n):
        raise ValueError('Foreign or modified sparse section cache')
    return row


def catalog(plan_path, audit_paths, out):
    require_bounded_worker()
    plan, sparse, project, binary = checked_stream_plan(plan_path)
    if len(audit_paths) != len(plan['native_leaves']):
        raise ValueError('Missing native leaf audits')
    out = out.resolve(); out.relative_to(ROOT); out.mkdir(parents=True, exist_ok=False)
    requests = {kind: [[] for _ in range(count)] for kind, count in plan['bucket_counts'].items()}
    records = []; statistics = []; totals = dict.fromkeys(DTYPES, 0)
    for path, entry in zip(audit_paths, plan['native_leaves']):
        # Original native metadata and execution must still match. Payloads were
        # hashed by their individual leaf workers, never by one global join.
        current, _ = native_metadata(checked_file(entry['result']), plan['sparse_plan'], sparse, project, binary)
        if current != entry:
            raise ValueError('Original sparse result changed')
        row = checked_audit(path, plan_path, plan, entry); binding = file_binding(path)
        statistics.append(row['statistics'])
        records.append({'index': entry['index'], 'audit': binding, 'cache': row['cache'],
                        'files': {k: v['file'] for k, v in row['records'].items()}})
        for kind, part in row['records'].items():
            totals[kind] += part['records']
            for bucket in part['buckets']:
                requests[kind][bucket['index']].append({**bucket, 'file': part['file'],
                    'leaf_index': entry['index'], 'audit': binding})
    levels = sorted({z for row in statistics for z in row['z_levels_mm']})
    volume = math.fsum(row['signed_volume_mm3'] for row in statistics)
    boxes = [row['bounds'] for row in statistics if row['bounds'] is not None]
    if not 2 <= len(levels) <= 4096 or not math.isfinite(volume) or volume <= 0 or not boxes:
        raise ValueError('Invalid or over-budget complete material statistics')
    bounds = {k: [fn(b[k][axis] for b in boxes) for axis in range(3)] for k, fn in [('min', min), ('max', max)]}
    bound_requests = {}
    for kind, groups in requests.items():
        bound_requests[kind] = []
        for index, files in enumerate(groups):
            count = sum(r['records'] for r in files)
            if count > LIMITS[kind]:
                raise ValueError('Hash partition exceeds unchanged bucket budget; no worker started')
            path = out / f'{kind}-{index:04d}-request.json'
            write(path, {'schema': 'genlaw.surface-bucket-request.v1', 'stream_plan': file_binding(plan_path),
                'kind': kind, 'index': index, 'files': files, 'records': count})
            bound_requests[kind].append(file_binding(path))
    write(out / 'catalog.json', {'schema': 'genlaw.sparse-audit-catalog.v1', 'stream_plan': file_binding(plan_path),
        'leaves': records, 'requests': bound_requests, 'record_totals': totals,
        'statistics': {'triangles': plan['triangles'], 'volume_mm3': volume,
                       'bounds': bounds, 'z_levels_mm': levels},
        'source_code': file_binding(Path(__file__))})


def checked_catalog(path):
    data = LOAD(path)
    if data.get('schema') != 'genlaw.sparse-audit-catalog.v1' or data.get('source_code') != file_binding(Path(__file__)):
        raise ValueError('Unsupported sparse surface catalog')
    plan_path = checked_file(data['stream_plan']); plan, _, _, _ = checked_stream_plan(plan_path)
    if ([r['index'] for r in data['leaves']] != [r['index'] for r in plan['native_leaves']]
            or set(data['requests']) != set(DTYPES)
            or any(len(data['requests'][kind]) != plan['bucket_counts'][kind] for kind in DTYPES)
            or data['record_totals']['facets'] != plan['triangles']):
        raise ValueError('Incomplete sparse audit catalog')
    return data, plan


def audit_bucket(catalog_path, kind, index, output):
    require_bounded_worker(); data, plan = checked_catalog(catalog_path)
    if kind not in DTYPES or type(index) is not int or not 0 <= index < plan['bucket_counts'][kind]:
        raise ValueError('Invalid sparse bucket selection')
    request_path = checked_file(data['requests'][kind][index]); request = LOAD(request_path)
    if (request.get('schema') != 'genlaw.surface-bucket-request.v1' or request.get('kind') != kind
            or request.get('index') != index or request.get('stream_plan') != data['stream_plan']):
        raise ValueError('Foreign surface bucket request')
    leaves = {r['index']: r for r in data['leaves']}; indices = []
    for row in request['files']:
        leaf = leaves.get(row['leaf_index']); indices.append(row['leaf_index'])
        if leaf is None or row['audit'] != leaf['audit'] or row['file'] != leaf['files'][kind]:
            raise ValueError('Foreign surface bucket source')
    if indices != sorted(set(indices)) or sum(r['records'] for r in request['files']) != request['records']:
        raise ValueError('Duplicate or incomplete bucket sources')
    checked = check_bucket(request['files'], plan['bucket_counts'][kind], index, kind)
    write(output, {'schema': 'genlaw.surface-bucket-audit.v1', 'catalog': file_binding(catalog_path),
        'request': file_binding(request_path), 'kind': kind, 'index': index, **checked,
        'source_code': file_binding(Path(__file__))})


def finish(catalog_path, results, output):
    require_bounded_worker(); data, plan = checked_catalog(catalog_path)
    expected = [(kind, i) for kind in sorted(DTYPES) for i in range(plan['bucket_counts'][kind])]
    rows = {}; bindings = []
    for path in results:
        row = LOAD(path); key = (row.get('kind'), row.get('index'))
        if (row.get('schema') != 'genlaw.surface-bucket-audit.v1' or key not in expected or key in rows
                or row.get('catalog') != file_binding(catalog_path)
                or row.get('source_code') != file_binding(Path(__file__))
                or row.get('request') != data['requests'][key[0]][key[1]]):
            raise ValueError('Missing, duplicate or foreign global surface bucket')
        request = LOAD(checked_file(row['request']))
        if row['records'] != request['records'] or not 0 <= row['unique_keys'] <= row['records']:
            raise ValueError('Bucket audit dropped records')
        rows[key] = row; bindings.append(file_binding(path))
    if set(rows) != set(expected):
        raise ValueError('Missing global facet or edge audit')
    for kind in DTYPES:
        if sum(r['records'] for k, r in rows.items() if k[0] == kind) != data['record_totals'][kind]:
            raise ValueError('Global surface audit lost material records')
    facets = sum(r['unique_keys'] for k, r in rows.items() if k[0] == 'facets')
    edges = sum(r['unique_keys'] for k, r in rows.items() if k[0] == 'edges')
    if facets != plan['triangles'] or 2*edges != 3*facets:
        raise ValueError('Complete triangle surface is not closed')
    write(output, {'schema': 'genlaw.sparse-stream-surface.v1', 'status': 'closed_oriented_surface',
        'catalog': file_binding(catalog_path), 'stream_plan': data['stream_plan'],
        'sparse_plan': plan['sparse_plan'], 'bucket_audits': bindings, **data['statistics'],
        'unique_edges': edges, 'coverage': plan['coverage'],
        'caches': [r['cache'] for r in data['leaves']], 'source_code': file_binding(Path(__file__)),
        'whole_mesh_assembled': False, 'all_global_cells_covered_once': True,
        'artificial_caps': False, 'geometry_modified': False, 'synthetic_empty_stls': 0,
        'production_approved': False, 'whole_process_card_complete': False})
    print(json.dumps({'streamed_surface': 'closed_oriented', 'triangles': facets, 'unique_edges': edges}), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='action', required=True)
    a = sub.add_parser('prepare'); a.add_argument('--sparse-plan', type=Path, required=True)
    a.add_argument('--results', nargs='+', type=Path, required=True); a.add_argument('--output', type=Path, required=True)
    a = sub.add_parser('leaf'); a.add_argument('--plan', type=Path, required=True)
    a.add_argument('--index', type=int, required=True); a.add_argument('--output', type=Path, required=True)
    a = sub.add_parser('catalog'); a.add_argument('--plan', type=Path, required=True)
    a.add_argument('--audits', nargs='+', type=Path, required=True); a.add_argument('--output', type=Path, required=True)
    a = sub.add_parser('bucket'); a.add_argument('--catalog', type=Path, required=True)
    a.add_argument('--kind', choices=sorted(DTYPES), required=True); a.add_argument('--index', type=int, required=True)
    a.add_argument('--output', type=Path, required=True)
    a = sub.add_parser('finish'); a.add_argument('--catalog', type=Path, required=True)
    a.add_argument('--results', nargs='+', type=Path, required=True); a.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.action == 'prepare': prepare(a.sparse_plan, a.results, a.output)
    elif a.action == 'leaf': audit_leaf(a.plan, a.index, a.output)
    elif a.action == 'catalog': catalog(a.plan, a.audits, a.output)
    elif a.action == 'bucket': audit_bucket(a.catalog, a.kind, a.index, a.output)
    else: finish(a.catalog, a.results, a.output)
