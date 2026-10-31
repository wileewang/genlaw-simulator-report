"""Bounded CAMotics cells on one original stock, NC program and global lattice.

This is an explicit experimental backend. It does not change engine selection,
rescale a part, crop the stock, rewrite NC or average per-window IoUs.
"""
import json
import math
from pathlib import Path

from .preparation import digest
from .simulation_engine import workspace_environment, workspace_path

ROOT = Path(__file__).resolve().parents[1]
MAX_CELLS = 5_000_000
MAX_XY_SAMPLES = 100_000
MAX_WINDOWS = 1024


def relative(path):
    return str(Path(path).resolve().relative_to(ROOT))


def file_binding(path):
    return {'path': relative(path), 'sha256': digest(Path(path))}


def checked_file(binding):
    path = workspace_path(binding['path'])
    if digest(path) != binding['sha256']:
        raise ValueError('Window source binding changed: ' + binding['path'])
    return path


def candidate_engine(manifest_path):
    manifest_path = workspace_path(relative(manifest_path))
    manifest = json.loads(manifest_path.read_text())
    expected = {
        'schema': 'genlaw.camotics-cell-window.v1', 'sampling': 'global-integer-lattice-v1',
        'cell_interval': 'half-open', 'original_stock_and_nc_preserved': True,
        'artificial_partition_caps': False, 'inspection_executes_nc': False,
        'threads': 1, 'maximum_cells_per_window': MAX_CELLS,
        'maximum_xy_samples': MAX_XY_SAMPLES, 'render_mode': 'marching_cubes',
        'reduction_allowed': False,
    }
    cap = manifest.get('grid_window_capability', {})
    if (manifest.get('schema') != 'genlaw.camotics-build.v1'
            or not manifest.get('complete_nc_material_removal_engine')
            or not manifest.get('patched_vertical_conic_sweep')
            or not manifest.get('patched_horizontal_cylinder_sweep')
            or any(cap.get(k) != v for k, v in expected.items())):
        raise ValueError('A frozen complete engine with bounded global-cell support is required')
    binary = checked_file({'path': manifest['binary'], 'sha256': manifest['binary_sha256']})
    checked_file({'path': cap['patch_ref'], 'sha256': cap['patch_sha256']})
    # Include system libraries as well: all native executions bind the runtime.
    for library in manifest['linked_libraries']:
        if digest(Path(library['path'])) != library['sha256']:
            raise ValueError('Window engine runtime changed: ' + library['path'])
    env = workspace_environment()
    env.pop('LD_LIBRARY_PATH', None)
    if manifest['library_directories']:
        env['LD_LIBRARY_PATH'] = ':'.join(str(workspace_path(p)) for p in manifest['library_directories'])
    return binary, env, manifest


def project_binding(project_path):
    project_path = workspace_path(relative(project_path))
    project = json.loads(project_path.read_text())
    # No hidden automatic margins, alternate geometry inputs or TPL execution.
    if set(project) != {'units', 'resolution-mode', 'resolution', 'tools', 'workpiece', 'files'}:
        raise ValueError('Unsupported window project fields')
    if project['units'] != 'metric' or project['resolution-mode'] != 'manual':
        raise ValueError('Window execution requires explicit metric stock and manual resolution')
    stock = project['workpiece']
    if stock.get('automatic') is not False or stock.get('margin') != 0:
        raise ValueError('Window execution cannot change the original stock bounds')
    if not isinstance(project['files'], list) or not 1 <= len(project['files']) <= 8:
        raise ValueError('Window execution requires a bounded list of NC inputs')
    files = []
    for name in project['files']:
        path = (project_path.parent / name).resolve()
        if path.suffix.lower() not in ('.nc', '.ngc', '.gcode'):
            raise ValueError('Only frozen NC inputs are supported')
        files.append(file_binding(path))
    expected_grid(project)
    return project, {'project': file_binding(project_path), 'nc_files': files}


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def expected_grid(project):
    """Repeat the engine's unmodified padded Grid construction in double precision."""
    res = project['resolution']
    bounds = project['workpiece']['bounds']
    low, high = bounds['min'], bounds['max']
    if (not finite_number(res) or res <= 0 or len(low) != 3 or len(high) != 3
            or not all(finite_number(v) for v in low + high)):
        raise ValueError('Invalid explicit stock or resolution')
    steps, offset = [], []
    for a, b in zip(low, high):
        if b <= a or not math.isfinite((b-a)/res) or (b-a)/res > 2**32-5:
            raise ValueError('Invalid or unrepresentable grid extent')
        padded_low, padded_high = a - res * .9, b + res * .9
        width = padded_high - padded_low
        n = math.ceil(width / res)
        steps.append(n)
        offset.append(padded_low - (n * res - width) / 2)
    return {'schema': 'genlaw.camotics-grid.v1', 'units': 'mm', 'resolution_mm': res,
            'padding_resolution_factor': .9, 'offset_mm': offset, 'steps': steps,
            'upper_mm': [a+n*res for a, n in zip(offset, steps)],
            'nc_executed': False, 'surface_computed': False}


def validate_grid(grid, project):
    if grid != expected_grid(project):
        raise ValueError('Native grid differs from the original stock / exact shared lattice')
    if any(type(v) is not int or v <= 0 for v in grid['steps']):
        raise ValueError('Invalid grid dimensions')


def window_budget(window, steps):
    if (not isinstance(window, list) or len(window) != 6
            or any(type(v) is not int for v in window)):
        raise ValueError('Cell window must contain six integer indices')
    if any(not 0 <= window[i] < window[i+3] <= steps[i] for i in range(3)):
        raise ValueError('Empty or out-of-grid cell window')
    widths = [window[i+3]-window[i] for i in range(3)]
    cells = math.prod(widths)
    xy = (widths[0]+1)*(widths[1]+1)
    return {'allowed': cells <= MAX_CELLS and xy <= MAX_XY_SAMPLES,
            'cell_count': cells, 'xy_sample_points': xy,
            'maximum_cells_per_window': MAX_CELLS, 'maximum_xy_samples': MAX_XY_SAMPLES,
            'original_stock_unchanged': True, 'nc_unchanged': True}


def partition(steps):
    pending = [[0, 0, 0, *steps]]
    windows = []
    while pending:
        window = pending.pop()
        if window_budget(window, steps)['allowed']:
            windows.append(window)
            continue
        widths = [window[i+3]-window[i] for i in range(3)]
        # If XY storage is limiting, splitting only Z cannot help.
        axes = range(2) if (widths[0]+1)*(widths[1]+1) > MAX_XY_SAMPLES else range(3)
        axis = max(axes, key=lambda i: widths[i])
        middle = window[axis] + widths[axis]//2
        left, right = list(window), list(window)
        left[axis+3] = middle
        right[axis] = middle
        pending.extend((right, left))
        if len(pending)+len(windows) > MAX_WINDOWS:
            raise ValueError('Global grid exceeds the bounded window-count budget')
    validate_partition(windows, steps)
    return windows


def validate_partition(windows, steps):
    if not isinstance(windows, list) or not 1 <= len(windows) <= MAX_WINDOWS:
        raise ValueError('Invalid window count')
    total = 0
    for index, window in enumerate(windows):
        budget = window_budget(window, steps)
        if not budget['allowed']:
            raise ValueError('Cell window exceeds the local compute budget')
        total += budget['cell_count']
        for other in windows[:index]:
            if all(max(window[i], other[i]) < min(window[i+3], other[i+3]) for i in range(3)):
                raise ValueError('Overlapping or duplicate cell windows')
    # Disjoint, inside the enclosing integer box, and equal total volume proves coverage.
    if total != math.prod(steps):
        raise ValueError('Missing global cells in window partition')


def inspect_and_plan(project_path, manifest_path, out, windows=None):
    from .run import bounded, write
    out = workspace_path(relative(out))
    out.mkdir(parents=True, exist_ok=False)
    project, binding = project_binding(project_path)
    binding['engine_manifest'] = file_binding(manifest_path)
    binary, env, manifest = candidate_engine(manifest_path)
    binding['engine_binary'] = file_binding(binary)
    # This explicit mode returns before NC interpretation or material computation.
    bounded(out, 'inspect-grid', [binary, '--inspect-grid', '--threads', '1',
                                project_path, out/'grid.json'], env)
    grid = json.loads((out/'grid.json').read_text())
    validate_grid(grid, project)
    for value in (binding['project'], binding['engine_manifest'], binding['engine_binary'], *binding['nc_files']):
        checked_file(value)
    binding['grid_report'] = file_binding(out/'grid.json')
    windows = partition(grid['steps']) if windows is None else windows
    validate_partition(windows, grid['steps'])
    plan = {'schema': 'genlaw.cell-window-plan.v1', 'binding': binding, 'grid': grid,
            'windows': windows, 'global_cells': math.prod(grid['steps']),
            'maximum_cells_per_window': MAX_CELLS, 'maximum_xy_samples': MAX_XY_SAMPLES,
            'source_code': file_binding(Path(__file__)), 'engine_id': manifest['id'],
            'sampling': 'global-integer-lattice-v1', 'cell_interval': 'half-open',
            'original_stock_and_nc_preserved': True, 'production_approved': False}
    write(out/'plan.json', plan)
    return out/'plan.json'


def checked_plan(plan_path):
    plan = json.loads(plan_path.read_text())
    if (plan.get('schema') != 'genlaw.cell-window-plan.v1'
            or plan.get('sampling') != 'global-integer-lattice-v1'
            or plan.get('cell_interval') != 'half-open'):
        raise ValueError('Unsupported cell window plan')
    binding = plan['binding']
    project_path = checked_file(binding['project'])
    project, expected = project_binding(project_path)
    if expected['nc_files'] != binding['nc_files']:
        raise ValueError('Window NC file binding differs')
    manifest_path = checked_file(binding['engine_manifest'])
    checked_file(binding['engine_binary'])
    binary, env, manifest = candidate_engine(manifest_path)
    if file_binding(binary) != binding['engine_binary']:
        raise ValueError('Window engine binary differs from manifest')
    grid = json.loads(checked_file(binding['grid_report']).read_text())
    if grid != plan['grid']:
        raise ValueError('Cell window plan grid differs from native inspection')
    validate_grid(grid, project)
    validate_partition(plan['windows'], grid['steps'])
    checked_file(plan['source_code'])
    return plan, project_path, binary, env


def run_window(plan_path, index, out, end_time=None):
    from .run import bounded, write
    plan_path = workspace_path(relative(plan_path))
    out = workspace_path(relative(out))
    plan, project_path, binary, env = checked_plan(plan_path)
    if type(index) is not int or not 0 <= index < len(plan['windows']):
        raise ValueError('Invalid window index')
    if end_time is not None and (not finite_number(end_time) or end_time <= 0):
        raise ValueError('Invalid partial execution time')
    out.mkdir(parents=True, exist_ok=False)
    window = plan['windows'][index]
    budget = window_budget(window, plan['grid']['steps'])
    if not budget['allowed']:
        raise ValueError('Window computation exceeds the resource budget')
    write(out/'window-budget.json', {**budget, 'window': window,
          'plan': file_binding(plan_path), 'index': index, 'global_cells': plan['global_cells']})
    command = [binary, '--threads', '1', '--reduce=false', '--binary',
               '--cell-window', ','.join(map(str, window))]
    if end_time is not None:
        command += ['--time', str(end_time)]
    command += [project_path, out/'surface.stl']
    # The stock-wide scheduling proxy does not apply to this bounded cell kernel.
    # The checked plan above and native kernel both enforce the cell/XY budgets.
    # All wall, CPU, RSS, file-size and exclusive-worker limits remain unchanged.
    bounded(out, 'camotics', command, env)
    checked_plan(plan_path)
    record = {'schema': 'genlaw.cell-window-result.v1', 'status': 'executed',
              'plan': file_binding(plan_path), 'index': index, 'window': window,
              'end_time_seconds': end_time, 'surface': file_binding(out/'surface.stl'),
              'resources': file_binding(out/'camotics-resources.json'),
              'budget': file_binding(out/'window-budget.json'),
              'source_code': file_binding(Path(__file__)), 'production_approved': False}
    write(out/'result.json', record)
    return record
