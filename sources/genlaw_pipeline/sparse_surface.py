"""Bounded regression of actual sparse material against a saved complete mesh.

This qualifies sparse skipping without fabricating surfaces for homogeneous
leaves. The existing 500,000-triangle comparison limit remains in effect; large
streamed surface acceptance is a separate prerequisite, not implied here.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'web/tools')]
from mesh_metrics import MAX_TRIANGLES, load_mesh, np, require_bounded_worker
from genlaw_pipeline.cell_windows import checked_file, checked_plan as checked_dense_plan, file_binding
from genlaw_pipeline.sharded_surface import canonical_facets, checked_result, triangles
from genlaw_pipeline.sparse_material import checked_results
from genlaw_pipeline.run import write

LOAD = lambda p: json.loads(p.read_text())


def verify_small(plan_path, result_paths, canonical_result, out):
    require_bounded_worker()
    plan, records = checked_results(plan_path, result_paths)
    old = LOAD(canonical_result)
    old_plan_path = checked_file(old['plan'])
    old_plan, _, _, _ = checked_dense_plan(old_plan_path)
    old, canonical = checked_result(old_plan_path, canonical_result)
    if (old['window'] != [0, 0, 0, *plan['grid']['steps']]
            or old_plan['binding']['project'] != plan['binding']['project']
            or old_plan['binding']['nc_files'] != plan['binding']['nc_files']
            or old_plan['grid'] != plan['grid']):
        raise ValueError('The saved complete material is from a different stock, NC or grid')
    total = 0
    for _, _, mesh in records:
        with mesh.open('rb') as stream:
            header = stream.read(84)
        if len(header) != 84:
            raise ValueError('Truncated native sparse surface')
        total += struct.unpack_from('<I', header, 80)[0]
    if not 0 < total <= MAX_TRIANGLES:
        raise ValueError('Sparse comparison exceeds the existing complete-mesh regression budget')
    pieces = [triangles(mesh) for _, _, mesh in records]
    joined = np.concatenate(pieces)
    left = canonical_facets(joined)
    right = canonical_facets(triangles(canonical))
    equal = np.array_equal(left, right)
    proof = {'schema': 'genlaw.sparse-material-invariance.v1',
             'equal_oriented_float32_facets': bool(equal), 'triangles': len(left),
             'canonical_triangles': len(right),
             'sparse_facets_sha256': hashlib.sha256(left.tobytes()).hexdigest(),
             'canonical_facets_sha256': hashlib.sha256(right.tobytes()).hexdigest(),
             'canonical_result': file_binding(canonical_result), 'canonical_mesh': file_binding(canonical),
             'plan': file_binding(plan_path), 'native_results': [file_binding(p) for p, _, _ in records],
             'coverage': plan['coverage'], 'registration': 'none', 'vertex_displacement_mm': 0,
             'artificial_caps': False, 'holes_filled': False, 'synthetic_empty_stls': 0,
             'native_complete_reference_regenerated': False,
             'sources': [file_binding(ROOT / name) for name in ['genlaw_pipeline/sparse_surface.py',
                 'genlaw_pipeline/sharded_surface.py', 'web/tools/mesh_metrics.py']]}
    write(out / 'invariance.json', proof)
    if not equal:
        raise ValueError('Sparse skipping changed actual oriented native triangle geometry')
    del pieces, joined, left, right
    combined = out / 'combined.stl'
    with combined.open('xb') as stream:
        stream.write(b'GenLaw exact native sparse leaf concatenation; homogeneous proofs separate'.ljust(80, b' '))
        stream.write(struct.pack('<I', total))
        for _, _, mesh in records:
            with mesh.open('rb') as source:
                source.seek(84)
                while block := source.read(1024*1024):
                    stream.write(block)
    _, _, geometry = load_mesh(combined)
    if geometry['exact_duplicate_faces_removed'] != 0:
        raise ValueError('Sparse output contains duplicate native facets; no removal is allowed')
    for item in proof['sources']:
        checked_file(item)
    write(out / 'surface.json', {'schema': 'genlaw.sparse-material-small-surface.v1',
          'status': 'closed_surface_equal_to_saved_full_grid', 'plan': file_binding(plan_path),
          'invariance': file_binding(out / 'invariance.json'), 'surface': file_binding(combined),
          'mesh': geometry, 'all_global_cells_covered_once': True, 'all_material_retained': True,
          'streamed_large_surface_qualified': False, 'production_approved': False})
    print(json.dumps({'exact_sparse_invariance': True, 'triangles': total,
                      'native_leaves': len(records), 'cells': plan['coverage']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--canonical-result', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    verify_small(args.plan, sorted(args.directory.glob('leaf-*/result.json')), args.canonical_result, args.output)
