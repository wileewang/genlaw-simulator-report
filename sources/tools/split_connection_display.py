"""Split an actual display into bounded files with identical ordered facets."""
import argparse
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'web/tools')]
from genlaw_pipeline.cell_windows import checked_file, file_binding
from genlaw_pipeline.run import write
from mesh_metrics import np, require_bounded_worker, write_indexed


def split(display_report, output):
    require_bounded_worker()
    display_report = display_report.resolve(); output = output.resolve()
    display_report.relative_to(ROOT); output.relative_to(ROOT)
    report = json.loads(display_report.read_text())
    if report['quantitative_mesh_modified'] or report['cad_used_to_generate_output']:
        raise ValueError('Require an actual-surface display')
    source = checked_file(report['display']); data = source.read_bytes()
    if len(data) < 16 or len(data) > 64*1024**2 or data[:8] != b'GLWMESH1':
        raise ValueError('Invalid display source')
    nv, nf = struct.unpack_from('<II', data, 8)
    if nf != report['triangles'] or not nv or not 0 < nf <= 1_500_000 or len(data) != 16+12*nv+12*nf:
        raise ValueError('Display size or triangle count differs')
    vertices = np.frombuffer(data, dtype='<f4', count=nv*3, offset=16).reshape(-1,3)
    faces = np.frombuffer(data, dtype='<u4', count=nf*3, offset=16+nv*12).reshape(-1,3)
    if faces.max() >= nv or not np.isfinite(vertices).all():
        raise ValueError('Invalid display vertices or indices')
    output.mkdir(parents=True, exist_ok=False); rows = []
    for start in range(0, nf, 800000):
        subset = faces[start:start+800000]
        ids, inverse = np.unique(subset.reshape(-1), return_inverse=True)
        part_vertices = vertices[ids]; part_faces = inverse.reshape(-1,3).astype('<u4')
        if not np.array_equal(vertices[subset].view('<u4'), part_vertices[part_faces].view('<u4')):
            raise ValueError('Splitting changed an ordered float32 facet')
        path = output/f'part-{len(rows):03d}.mesh'
        write_indexed(path, part_vertices, part_faces)
        # Re-read the written file, including the header, rather than accepting
        # only the in-memory arrays that were handed to the writer.
        actual = path.read_bytes(); observed_nv, observed_nf = struct.unpack_from('<II', actual, 8)
        if (actual[:8] != b'GLWMESH1' or observed_nv != len(part_vertices)
                or observed_nf != len(part_faces) or len(actual) != 16+12*observed_nv+12*observed_nf):
            raise ValueError('Written display part is incomplete')
        written_vertices = np.frombuffer(actual, dtype='<f4', count=observed_nv*3, offset=16).reshape(-1,3)
        written_faces = np.frombuffer(actual, dtype='<u4', count=observed_nf*3, offset=16+observed_nv*12).reshape(-1,3)
        if not np.array_equal(vertices[subset].view('<u4'), written_vertices[written_faces].view('<u4')):
            raise ValueError('Written display part changed the original facets')
        rows.append({'file': file_binding(path), 'first_triangle': start,
                     'triangles': len(part_faces), 'vertices': len(part_vertices)})
    if sum(row['triangles'] for row in rows) != nf:
        raise ValueError('Incomplete split surface')
    write(output/'parts.json', {'schema': 'genlaw.multipart-display.v1',
        'source_display_report': file_binding(display_report), 'source_mesh': report['display'],
        'triangles': nf, 'maximum_part_triangles': 800000, 'parts': rows,
        'exact_ordered_float32_facets_preserved': True, 'simplification_applied': False,
        'source_code': file_binding(Path(__file__))})
    print(json.dumps({'triangles': nf, 'parts': len(rows), 'facets_unchanged': True}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--display', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); split(args.display, args.output)
