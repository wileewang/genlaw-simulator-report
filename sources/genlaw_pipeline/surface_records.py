"""Bounded exact surface records for streaming manifold and facet audits.

Coordinates remain native float32. A facet is represented by its three sorted
vertex byte strings and the parity of their original orientation. Seam edges
carry two sorted vertices and an oriented sign. Equal geometry cannot evade
the global audit by being put in another hash bucket.
"""
import hashlib
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'web/tools')]
from mesh_metrics import np, MAX_TRIANGLES, require_bounded_worker
from .cell_windows import MAX_WINDOWS, file_binding
from .simulation_engine import workspace_path
from .sharded_surface import MAX_BOUNDARY_EDGES

FACE = np.dtype([('key', 'V36'), ('winding', '<i4')])
EDGE = np.dtype([('key', 'V24'), ('winding', '<i4'), ('incidence', '<u4')])
DTYPES = {'facets': FACE, 'edges': EDGE}
LIMITS = {'facets': MAX_TRIANGLES, 'edges': MAX_BOUNDARY_EDGES}


def bucket_count(total, kind):
    if kind not in LIMITS or type(total) is not int or total < 0:
        raise ValueError('Invalid surface record count')
    upper = MAX_TRIANGLES*MAX_WINDOWS*(3 if kind == 'edges' else 1)
    if total > upper:
        raise ValueError('Surface records exceed the existing native leaf and triangle budgets')
    count = 1
    while count*(LIMITS[kind]//2) < total:
        count *= 2
    return count


def bucket_ids(records, count):
    if type(count) is not int or count < 1 or count & (count-1) or count > 4*MAX_WINDOWS:
        raise ValueError('Invalid bounded surface bucket count')
    if records.dtype not in (FACE, EDGE) or len(records) > max(LIMITS.values()):
        raise ValueError('Invalid or over-budget surface record array')
    words = np.ascontiguousarray(records['key']).view('<u4').reshape(len(records), records.dtype['key'].itemsize//4)
    value = np.full(len(records), np.uint64(14695981039346656037), dtype=np.uint64)
    for index in range(words.shape[1]):
        value = (value ^ words[:, index].astype(np.uint64))*np.uint64(1099511628211)
    value ^= value >> np.uint64(30)
    value *= np.uint64(0xbf58476d1ce4e5b9)
    value ^= value >> np.uint64(27)
    value *= np.uint64(0x94d049bb133111eb)
    value ^= value >> np.uint64(31)
    return (value & np.uint64(count-1)).astype(np.uint32)


def audit_points(points):
    require_bounded_worker()
    if points.dtype != np.dtype('<f4') or points.shape != (len(points), 3, 3) or len(points) > MAX_TRIANGLES:
        raise ValueError('Invalid or over-budget native triangle array')
    if not np.isfinite(points).all():
        raise ValueError('Nonfinite native triangle coordinate')
    points = np.array(points, copy=True); points[points == 0] = 0
    if not len(points):
        return {'triangles': 0, 'vertices': 0, 'boundary_edges': 0, 'signed_volume_mm3': 0.,
                'bounds': None, 'z_levels_mm': []}, np.empty(0, dtype=FACE), np.empty(0, dtype=EDGE)
    flat = points.reshape(-1, 3)
    _, first, inverse = np.unique(flat.view('V12').reshape(-1), return_index=True, return_inverse=True)
    vertices = flat[first]; faces = inverse.reshape(-1, 3).astype('<u4')
    ordered_faces = np.sort(faces, axis=1)
    unordered = np.ascontiguousarray(ordered_faces).view('V12').reshape(-1)
    if len(np.unique(unordered)) != len(faces):
        raise ValueError('Duplicate triangle in native leaf')
    tri = points.astype(np.float64)
    normals = np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0])
    if np.any(np.linalg.norm(normals, axis=1) <= 1e-16):
        raise ValueError('Degenerate native triangle')
    edge_pairs = np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]))
    signs = np.where(edge_pairs[:, 0] < edge_pairs[:, 1], 1, -1); edge_pairs.sort(axis=1)
    _, first_edge, ids, counts = np.unique(np.ascontiguousarray(edge_pairs).view('V8').reshape(-1),
        return_index=True, return_inverse=True, return_counts=True)
    winding = np.bincount(ids, weights=signs)
    if np.any(counts > 2) or np.any(winding[counts == 2] != 0):
        raise ValueError('Nonmanifold or inconsistently oriented native leaf')
    boundary = counts == 1; pairs = edge_pairs[first_edge]
    if len(pairs) > MAX_BOUNDARY_EDGES:
        raise ValueError('Native leaf edge records exceed the existing edge budget')
    edges = np.empty(len(pairs), dtype=EDGE)
    edges['key'] = np.ascontiguousarray(vertices[pairs]).reshape(-1, 6).view('V24').reshape(-1)
    edges['winding'] = winding.astype('<i4')
    edges['incidence'] = counts.astype('<u4')
    facets = np.empty(len(faces), dtype=FACE)
    facets['key'] = np.ascontiguousarray(vertices[ordered_faces]).reshape(-1, 9).view('V36').reshape(-1)
    inversions = ((faces[:, 0] > faces[:, 1]).astype(np.int8)
                  + (faces[:, 0] > faces[:, 2]).astype(np.int8)
                  + (faces[:, 1] > faces[:, 2]).astype(np.int8))
    facets['winding'] = np.where(inversions % 2, -1, 1)
    volume = math.fsum((np.einsum('ij,ij->i', tri[:, 0], np.cross(tri[:, 1], tri[:, 2]))/6).tolist())
    levels = np.unique(vertices[:, 2]).astype(float).tolist()
    if len(levels) > 4096:
        raise ValueError('Native leaf exceeds the existing measurement-height budget')
    data = {'triangles': len(points), 'vertices': len(vertices), 'boundary_edges': int(boundary.sum()),
        'signed_volume_mm3': volume,
        'bounds': {'min': vertices.min(axis=0).tolist(), 'max': vertices.max(axis=0).tolist()}, 'z_levels_mm': levels}
    return data, facets, edges


def write_buckets(records, count, kind, folder):
    require_bounded_worker()
    folder = folder.resolve(); folder.relative_to(ROOT)
    if kind not in DTYPES or records.dtype != DTYPES[kind] or len(records) > LIMITS[kind]:
        raise ValueError('Invalid or over-budget local surface records')
    ids = bucket_ids(records, count); order = np.argsort(ids, kind='stable'); ordered = ids[order]
    starts = np.r_[0, np.flatnonzero(ordered[1:] != ordered[:-1])+1, len(records)] if len(records) else []
    rows = []; path = folder / (kind+'.bin')
    with path.open('xb') as stream:
        for start, end in zip(starts[:-1], starts[1:]):
            index = int(ordered[start]); payload = records[order[start:end]].tobytes()
            stream.write(payload)
            rows.append({'index': index, 'records': int(end-start),
                         'offset': int(start)*records.dtype.itemsize,
                         'sha256': hashlib.sha256(payload).hexdigest()})
    if sum(row['records'] for row in rows) != len(records):
        raise ValueError('Surface record partition dropped entries')
    return {'file': file_binding(path), 'records': len(records), 'buckets': rows}


def validate_bucket_records(records, count, index, kind):
    require_bounded_worker()
    if kind not in DTYPES or records.dtype != DTYPES[kind] or len(records) > LIMITS[kind]:
        raise ValueError('Global surface bucket exceeds the unchanged record budget')
    if type(index) is not int or not 0 <= index < count or np.any(bucket_ids(records, count) != index):
        raise ValueError('Surface record assigned to a foreign bucket')
    keys = np.ascontiguousarray(records['key'])
    words = keys.view('<u4').reshape(len(records), keys.dtype.itemsize//4)
    coords = words.view('<f4')
    if not np.isfinite(coords).all() or np.any(words == np.uint32(0x80000000)):
        raise ValueError('Nonfinite or noncanonical surface coordinate')
    vertices = keys.view('V12').reshape(len(records), 3 if kind == 'facets' else 2)
    if (not np.array_equal(np.sort(vertices, axis=1), vertices)
            or np.any(vertices[:, 1:] == vertices[:, :-1])):
        raise ValueError('Invalid canonical surface record')
    if kind == 'facets':
        valid = np.isin(records['winding'], [-1, 1])
    else:
        valid = ((records['incidence'] == 1) & np.isin(records['winding'], [-1, 1])) | ((records['incidence'] == 2) & (records['winding'] == 0))
    if not valid.all():
        raise ValueError('Invalid canonical surface incidence or orientation')
    ordered = records[np.argsort(records['key'], kind='stable')]
    _, first, counts = np.unique(ordered['key'], return_index=True, return_counts=True)
    if kind == 'facets':
        if np.any(counts != 1):
            raise ValueError('Duplicate native triangle across leaves')
    elif (np.any(np.add.reduceat(ordered['incidence'], first) != 2)
          or np.any(np.add.reduceat(ordered['winding'], first) != 0)):
        raise ValueError('Open or inconsistently oriented global seam')
    return {'records': len(records), 'unique_keys': len(first),
            'sorted_records_sha256': hashlib.sha256(ordered.tobytes()).hexdigest()}


def check_bucket(files, count, index, kind):
    require_bounded_worker()
    if kind not in DTYPES or len(files) > MAX_WINDOWS:
        raise ValueError('Invalid surface bucket source list')
    total = sum(row['records'] for row in files)
    if total > LIMITS[kind] or any(type(row['records']) is not int or row['records'] <= 0 or row['index'] != index for row in files):
        raise ValueError('Global surface bucket exceeds the unchanged record budget')
    paths = [workspace_path(row['file']['path']) for row in files]
    if len(set(paths)) != len(paths):
        raise ValueError('Duplicate bucket evidence file')
    pieces = []
    for path, row in zip(paths, files):
        length = DTYPES[kind].itemsize*row['records']; offset = row['offset']
        if (type(offset) is not int or offset < 0 or offset % DTYPES[kind].itemsize
                or offset+length > path.stat().st_size or path.stat().st_size > LIMITS[kind]*DTYPES[kind].itemsize):
            raise ValueError('Truncated or over-budget surface record bucket')
        with path.open('rb') as stream:
            stream.seek(offset); payload = stream.read(length)
        if len(payload) != length or hashlib.sha256(payload).hexdigest() != row['sha256']:
            raise ValueError('Surface bucket payload binding changed')
        pieces.append(np.frombuffer(payload, dtype=DTYPES[kind]))
    records = np.concatenate(pieces) if pieces else np.empty(0, dtype=DTYPES[kind])
    return validate_bucket_records(records, count, index, kind)
