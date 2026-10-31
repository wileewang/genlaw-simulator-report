"""Keep final downloads and exact replay geometry without duplicate middle STLs."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from genlaw_pipeline.cell_windows import checked_file, file_binding
from genlaw_pipeline.planar_resume import process_identity
from genlaw_pipeline.run import write
from step2gcode_demo.validation.local_resource_limits import require_bounded_worker

READ = lambda p: json.loads(Path(p).read_text())


def revise(source, destination, checkpoint):
    require_bounded_worker()
    import numpy as np
    source.relative_to(ROOT)
    destination.relative_to(ROOT)
    prior = ROOT / 'docs/checkpoints/2026-10-31/cumulative-cam-01/review-export-01'
    owner = READ(prior / 'owner.json')['process']
    if process_identity(owner['pid']) == owner:
        raise ValueError('Original review export is still live')
    failure = READ(prior / 'assemble-archive-resources.json')
    if failure['status'] != 'process_failed' or '45 MB CDN budget' not in (prior / 'assemble-archive.log').read_text():
        raise ValueError('Require the diagnosed archive-budget rejection')
    summary = READ(source / 'summary.json')
    if len(summary['normal_cases']) != 4 or len(summary['controls']) != 3:
        raise ValueError('Original archive lacks completed case exports')
    for key in [r['id'] for r in summary['normal_cases'] + summary['controls']]:
        report = READ(prior / ('pack-' + key + '-resources.json'))
        if not report['success']:
            raise ValueError('Do not revise an incomplete geometry export')
    bindings = READ(source / 'baseline/archive-bindings.json')
    delivery = READ(source / 'geometry-delivery.json')
    omitted = {f'baseline/{stage}.stl.gz' for stage in 'ABC'}
    proof = []
    dtype = np.dtype([('normal', '<f4', (3,)), ('points', '<f4', (3, 3)), ('attribute', '<u2')])
    for stage in 'ABC':
        record = delivery[f'baseline/{stage}.mesh']
        payload = source / record['path']
        encoded = payload.read_bytes()
        raw = gzip.decompress(encoded)
        if (hashlib.sha256(encoded).hexdigest() != record['sha256']
                or hashlib.sha256(raw).hexdigest() != record['decoded_sha256'] or raw[:8] != b'GLWMESH1'):
            raise ValueError('Replay payload changed')
        vertices, triangles = struct.unpack_from('<II', raw, 8)
        if len(raw) != 16 + 12 * vertices + 12 * triangles:
            raise ValueError('Replay payload has missing or extra data')
        points = np.frombuffer(raw, dtype='<f4', count=3 * vertices, offset=16).reshape(-1, 3)
        indices = np.frombuffer(raw, dtype='<u4', count=3 * triangles, offset=16 + 12 * vertices).reshape(-1, 3)
        original = checked_file(bindings['display_equivalence'][stage]['source'])
        with original.open('rb') as stream:
            header = stream.read(84)
        if struct.unpack_from('<I', header, 80)[0] != triangles or original.stat().st_size != 84 + 50 * triangles:
            raise ValueError('Replay changed the number of original triangles')
        native = np.memmap(original, dtype=dtype, mode='r', offset=84, shape=(triangles,))
        if not np.array_equal(points[indices], native['points']):
            raise ValueError('Replay changed or reordered original triangle coordinates')
        archived = bindings['raw_geometry'][stage]
        raw_payload = checked_file(archived['payload'])
        if file_binding(source / (f'baseline/{stage}.stl.gz'))['sha256'] != file_binding(raw_payload)['sha256']:
            raise ValueError('The private lossless original was not preserved')
        proof.append({'stage_id': stage, 'triangles': triangles, 'all_original_triangle_coordinates_identical': True,
            'replay_payload': file_binding(payload), 'original_surface': file_binding(original),
            'private_lossless_payload': file_binding(raw_payload), 'private_qualification': archived['qualified_archive'],
            'original_export_download_copy': file_binding(source / (f'baseline/{stage}.stl.gz'))})
        del native, points, indices, raw
    destination.mkdir(parents=True, exist_ok=False)
    copied = []
    for path in sorted(source.rglob('*')):
        if not path.is_file() or str(path.relative_to(source)) in omitted:
            continue
        dest = destination / path.relative_to(source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        if file_binding(path)['sha256'] != file_binding(dest)['sha256']:
            raise ValueError('Existing export changed during delivery copy')
        copied.append(str(path.relative_to(source)))
    for stage in 'ABC':
        record = bindings['raw_geometry'][stage]
        record['public_download_included'] = False
        record['private_lossless_original_retained'] = True
        record.pop('archive_path')
    private_sources = {}
    for key, value in list(bindings['copied_sources'].items()):
        if value['path'] in omitted:
            private_sources[key] = bindings['copied_sources'].pop(key)
    bindings['retained_private_download_sources'] = private_sources
    (destination / 'baseline/archive-bindings.json').write_text(json.dumps(bindings, ensure_ascii=False, indent=2) + '\n')
    scope = READ(destination / 'archive-scope.json')
    scope.update(intermediate_geometry_delivery='lossless indexed coordinates and all original faces',
                 intermediate_raw_STL_download_copies_included=False,
                 intermediate_original_STL_and_lossless_archives_retained_privately=True)
    (destination / 'archive-scope.json').write_text(json.dumps(scope, ensure_ascii=False, indent=2) + '\n')
    shutil.copy2(Path(__file__), destination / 'sources' / Path(__file__).name)
    record = {'schema': 'genlaw.cumulative-review-delivery-revision.v1',
        'status': 'same_complete_geometry_without_duplicate_intermediate_STL_downloads',
        'original_export': str(source.relative_to(ROOT)), 'original_failure': file_binding(prior / 'assemble-archive-resources.json'),
        'original_error_log': file_binding(prior / 'assemble-archive.log'), 'proof': proof,
        'omitted_duplicate_public_copies': sorted(omitted), 'original_export_unchanged': True,
        'all_final_STL_downloads_retained': True, 'all_replay_faces_and_coordinates_retained': True,
        'numeric_results_unchanged': True, 'new_CAM_or_material_simulations': 0, 'source': file_binding(Path(__file__))}
    write(destination / 'delivery-revision.json', record)
    total = sum(p.stat().st_size for p in destination.rglob('*') if p.is_file())
    if total >= 43_000_000:
        raise ValueError('Revised delivery still cannot reserve 2 MB within the 45 MB budget')
    for name in ['summary.json', 'geometry-delivery.json', 'suite.json']:
        if (source / name).read_bytes() != (destination / name).read_bytes():
            raise ValueError('Delivery revision changed measured results or replay binding')
    write(checkpoint / 'result.json', {**record, 'archive': str(destination.relative_to(ROOT)),
        'archive_bytes_before_thumbnails': total, 'copied_files': len(copied),
        'summary': file_binding(destination / 'summary.json'), 'delivery': file_binding(destination / 'geometry-delivery.json'),
        'website_updated': False})
    print(json.dumps({'archive_bytes_before_thumbnails': total, 'verified_exact_replay_surfaces': 3,
                      'new_material_simulations': 0}), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ['source', 'output', 'checkpoint']:
        p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args()
    revise(a.source.resolve(), a.output.resolve(), a.checkpoint.resolve())
