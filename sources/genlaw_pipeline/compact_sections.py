"""Bounded exact coalescence of axis-aligned material-section segments.

Only adjacent, nonoverlapping intervals on the same exact line may merge.
Global endpoint valences are externally sorted first so branch vertices remain.
The existing 1e-8 mm section interpolation rule is applied by the caller; this
module never rounds, fits, bridges, removes a component or changes a coordinate.
"""
import heapq
import math
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'web/tools')]
from mesh_metrics import np, require_bounded_worker
from shapely.geometry import Polygon
from shapely.ops import polygonize_full, unary_union
from genlaw_pipeline.chamfer_shards import ChamferSections
from genlaw_pipeline.sharded_measure import MAX_ACTIVE_TRIANGLES, MAX_SECTION_SEGMENTS

INPUT_CHUNK = MAX_SECTION_SEGMENTS // 2
POINT_DTYPE = np.dtype([('x', '<f8'), ('y', '<f8'), ('count', '<u4')])
LINE_DTYPE = np.dtype([('axis', 'u1'), ('constant', '<f8'), ('low', '<f8'), ('high', '<f8')])


def packed_chunks(chunks):
    """Bound merge fan-in even if the caller supplies many very small chunks."""
    buffer = np.empty((INPUT_CHUNK, 2, 2), dtype=np.float64)
    used, total = 0, 0
    for number, chunk in enumerate(chunks):
        if number >= MAX_ACTIVE_TRIANGLES:
            raise ValueError('Section input chunk count exceeds bounded iteration')
        points = np.asarray(chunk)
        if (points.ndim != 3 or points.shape[1:] != (2, 2) or len(points) > INPUT_CHUNK):
            raise ValueError('Section input chunk exceeds the unchanged bounded segment storage')
        if points.dtype != np.dtype('float64'):
            raise ValueError('Section chunks require the original float64 intersection coordinates')
        if not np.isfinite(points).all():
            raise ValueError('Nonfinite material section coordinate')
        total += len(points)
        if total > MAX_ACTIVE_TRIANGLES:
            raise ValueError('Section input exceeds the unchanged active triangle budget')
        start = 0
        while start < len(points):
            count = min(INPUT_CHUNK-used, len(points)-start)
            buffer[used:used+count] = points[start:start+count]
            used += count; start += count
            if used == INPUT_CHUNK:
                yield buffer.copy()
                used = 0
    if used:
        yield buffer[:used].copy()


def ordered_rows(paths, fields):
    """Merge sorted bounded files; retain one record per file in the heap."""
    arrays = [np.load(path, mmap_mode='r', allow_pickle=False) for path in paths]
    heap = []

    def value(array, index):
        return tuple(array[index][name].item() for name in fields)

    for i, array in enumerate(arrays):
        if len(array):
            heapq.heappush(heap, (value(array, 0), i, 0))
    while heap:
        row, i, index = heapq.heappop(heap)
        yield row
        index += 1
        if index < len(arrays[i]):
            heapq.heappush(heap, (value(arrays[i], index), i, index))


def compact_segments(chunks):
    """Return the identical line set with all original branch vertices intact."""
    require_bounded_worker()
    temp_root = ROOT / '.runtime/tmp'
    temp_root.mkdir(parents=True, exist_ok=True)
    stats = {'input_segments': 0, 'nonzero_segments': 0, 'zero_length_segments': 0,
             'output_segments': 0, 'merged_joints': 0, 'branch_vertices_retained': 0,
             'maximum_input_chunk': 0, 'maximum_endpoint_chunk': 0,
             'coordinate_displacement_mm': 0, 'additional_rounding': False,
             'gaps_bridged': False, 'components_discarded': False}
    diagonal, diagonal_keys = [], set()
    with tempfile.TemporaryDirectory(prefix='genlaw-exact-sections-', dir=temp_root) as temporary:
        folder = Path(temporary)
        point_files, line_files = [], []
        for chunk in packed_chunks(chunks):
            points = np.asarray(chunk, dtype=np.float64)
            if points.ndim != 3 or points.shape[1:] != (2, 2) or len(points) > INPUT_CHUNK:
                raise ValueError('Section input chunk exceeds the unchanged bounded segment storage')
            if not np.isfinite(points).all():
                raise ValueError('Nonfinite material section coordinate')
            stats['input_segments'] += len(points)
            if stats['input_segments'] > MAX_ACTIVE_TRIANGLES:
                raise ValueError('Section input exceeds the unchanged active triangle budget')
            stats['maximum_input_chunk'] = max(stats['maximum_input_chunk'], len(points))
            keep = np.any(points[:, 0] != points[:, 1], axis=1)
            stats['zero_length_segments'] += int((~keep).sum())
            points = np.array(points[keep], copy=True)
            if not len(points):
                continue
            points[points == 0] = 0  # Equal signed zeros name the same original point.
            stats['nonzero_segments'] += len(points)
            flat = points.reshape(-1, 2)
            stats['maximum_endpoint_chunk'] = max(stats['maximum_endpoint_chunk'], len(flat))
            names = np.empty(len(flat), dtype=POINT_DTYPE)
            names['x'], names['y'], names['count'] = flat[:, 0], flat[:, 1], 1
            names.sort(order=['x', 'y'])
            path = folder / f'points-{len(point_files):03d}.npy'
            with path.open('xb') as stream:
                np.save(stream, names, allow_pickle=False)
            point_files.append(path)
            horizontal = points[:, 0, 1] == points[:, 1, 1]
            vertical = points[:, 0, 0] == points[:, 1, 0]
            aligned = horizontal | vertical
            for segment in points[~aligned]:
                a, b = tuple(segment[0]), tuple(segment[1])
                key = tuple(sorted((a, b)))
                if key in diagonal_keys:
                    raise ValueError('Duplicate nonaligned section segment')
                diagonal_keys.add(key)
                diagonal.append(segment.copy())
                if len(diagonal) > MAX_SECTION_SEGMENTS:
                    raise ValueError('Unmerged section exceeds the unchanged segment budget')
            lines = np.empty(int(aligned.sum()), dtype=LINE_DTYPE)
            axes = np.where(horizontal[aligned], 0, 1)
            selected = points[aligned]
            ids = np.arange(len(selected))
            lines['axis'] = axes
            lines['constant'] = selected[ids, 0, 1-axes]
            lines['low'] = np.minimum(selected[ids, 0, axes], selected[ids, 1, axes])
            lines['high'] = np.maximum(selected[ids, 0, axes], selected[ids, 1, axes])
            lines.sort(order=['axis', 'constant', 'low', 'high'])
            path = folder / f'lines-{len(line_files):03d}.npy'
            with path.open('xb') as stream:
                np.save(stream, lines, allow_pickle=False)
            line_files.append(path)

        protected = set()
        previous, valence = None, 0

        def finish_vertex(point, degree):
            if point is None:
                return
            if degree % 2:
                raise ValueError('Open or odd-valence material section endpoint')
            if degree != 2:
                protected.add(point)
                if len(protected) > MAX_SECTION_SEGMENTS:
                    raise ValueError('Section branch count exceeds the unchanged segment budget')

        for x, y, count in ordered_rows(point_files, ['x', 'y', 'count']):
            point = (x, y)
            if point != previous:
                finish_vertex(previous, valence)
                previous, valence = point, 0
            valence += count
        finish_vertex(previous, valence)
        stats['branch_vertices_retained'] = len(protected)

        result = diagonal

        def emit(row):
            if row is None:
                return
            axis, constant, low, high = row
            result.append(np.array([[low, constant], [high, constant]] if axis == 0 else
                                   [[constant, low], [constant, high]], dtype=np.float64))
            if len(result) > MAX_SECTION_SEGMENTS:
                raise ValueError('Compacted section exceeds the unchanged segment budget')

        current = None
        for axis, constant, low, high in ordered_rows(line_files, ['axis', 'constant', 'low', 'high']):
            row = (axis, constant, low, high)
            if current is not None and row[:2] == current[:2]:
                if low < current[3]:
                    raise ValueError('Overlapping or duplicate aligned section intervals')
                joint = (low, constant) if axis == 0 else (constant, low)
                if low == current[3] and joint not in protected:
                    current = (axis, constant, current[2], high)
                    stats['merged_joints'] += 1
                    continue
            emit(current)
            current = row
        emit(current)
        stats['output_segments'] = len(result)
        stats['sorted_point_runs'] = len(point_files)
        stats['maximum_merge_fan_in'] = max(len(point_files), len(line_files))
        if len(result) + stats['merged_joints'] != stats['nonzero_segments']:
            raise ValueError('Section interval accounting differs')
        return np.asarray(result, dtype=np.float64).reshape(-1, 2, 2), stats


def material_polygon(points):
    """Use the existing section polygonization and nesting rule, without repair."""
    if not len(points):
        return Polygon()
    polygons, cuts, dangles, invalid = polygonize_full(points)
    if not cuts.is_empty or not dangles.is_empty or not invalid.is_empty:
        raise ValueError('Unclosed or invalid compacted material section')
    pieces = list(polygons.geoms)
    rings = [Polygon(piece.exterior) for piece in pieces]
    selected = [piece for piece in pieces if
                sum(ring.contains(piece.representative_point()) for ring in rings) % 2 == 1]
    section = unary_union(selected)
    if not section.is_valid:
        raise ValueError('Invalid compacted material section')
    return section


class CompactedSections(ChamferSections):
    """Keep the qualified float64 interval selection and original cache binding."""
    def __init__(self, plan, low, high):
        super().__init__(plan, low, high)
        self.maximum_compacted_segments = 0
        self.last_stats = None

    def at(self, z):
        require_bounded_worker()
        if not math.isfinite(z):
            raise ValueError('Nonfinite section height')
        self.calls += 1

        def chunks():
            for start in range(0, len(self.triangles), INPUT_CHUNK):
                stop = start+INPUT_CHUNK
                mask = (self.zmin[start:stop] < z) & (self.zmax[start:stop] > z)
                tri = self.triangles[start:stop][mask]
                if not len(tri):
                    continue
                points = np.empty((len(tri), 2, 2), dtype=np.float64)
                added = np.zeros(len(tri), dtype=np.int8)
                for i, j in ((0, 1), (1, 2), (2, 0)):
                    a, b = tri[:, i], tri[:, j]
                    hit = ((a[:, 2] < z) & (b[:, 2] > z)) | ((b[:, 2] < z) & (a[:, 2] > z))
                    ids = np.flatnonzero(hit)
                    ratio = (z-a[hit, 2])/(b[hit, 2]-a[hit, 2])
                    points[ids, added[ids]] = a[hit, :2]+ratio[:, None]*(b[hit, :2]-a[hit, :2])
                    added[ids] += 1
                if not np.all(added == 2):
                    raise ValueError('Ambiguous compacted section edge')
                yield np.round(points, 8)  # The existing section rule, unchanged.

        points, stats = compact_segments(chunks())
        self.last_stats = stats
        self.maximum_observed_segments = max(self.maximum_observed_segments, stats['input_segments'])
        self.maximum_compacted_segments = max(self.maximum_compacted_segments, stats['output_segments'])
        return material_polygon(points)
