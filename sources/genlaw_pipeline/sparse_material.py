"""Experimental complete material partitions with native homogeneous proofs.

Every original integer cell belongs to one leaf. A leaf is either simulated by
the unchanged native cell kernel or omitted only because the same native field
proves every one of its vertices has a strict, homogeneous sign. No target CAD,
partial NC, artificial cap, cropped stock or synthetic empty STL is accepted.
"""
import json
import math
from pathlib import Path

from .admission_wait import admitted_bounded
from .cell_windows import (MAX_CELLS, MAX_WINDOWS, checked_file, expected_grid,
                           file_binding, project_binding, window_budget)
from .material_blocks import checked_queries, engine_for_inspection, inspect, validate_report
from .run import write

ROOT = Path(__file__).resolve().parents[1]
LOAD = lambda p: json.loads(p.read_text())
STATES = {'uncut_solid', 'empty_outside_stock', 'requires_native_cells'}
SOURCE_PATHS = ['genlaw_pipeline/sparse_material.py', 'genlaw_pipeline/material_blocks.py',
                'genlaw_pipeline/cell_windows.py']


def shell_partition(steps):
    """Peel two original cells per face; this is only a proposal, never a proof."""
    box = [0, 0, 0, *steps]
    if any(n <= 4 for n in steps):
        return [box]
    shells = []
    for axis in range(3):
        low, high = list(box), list(box)
        low[axis+3], high[axis] = 2, steps[axis]-2
        shells.extend([low, high])
        box[axis], box[axis+3] = 2, steps[axis]-2
    return shells + [box]


def split_box(box):
    widths = [box[i+3]-box[i] for i in range(3)]
    # A Z split cannot resolve the XY memory limit.
    axes = range(2) if (widths[0]+1)*(widths[1]+1) > 100_000 else range(3)
    axis = max(axes, key=lambda i: widths[i])
    if widths[axis] < 2:
        raise ValueError('No representable split inside the original cell block')
    left, right = list(box), list(box)
    middle = box[axis] + widths[axis]//2
    left[axis+3], right[axis] = middle, middle
    return left, right


def validate_cover(leaves, grid):
    """Disjoint integer boxes inside the root plus equal volume prove coverage."""
    if not isinstance(leaves, list) or not 1 <= len(leaves) <= MAX_WINDOWS:
        raise ValueError('Sparse partition exceeds the existing leaf-count budget')
    checked_queries([r['bounds'] for r in leaves], grid)
    counts = dict.fromkeys(sorted(STATES), 0)
    for i, row in enumerate(leaves):
        bounds, state = row['bounds'], row['state']
        if state not in STATES or row.get('index') != i:
            raise ValueError('Unknown or reordered sparse leaf')
        budget = window_budget(bounds, grid['steps'])
        if state == 'requires_native_cells' and not budget['allowed']:
            raise ValueError('An active sparse leaf exceeds the native computation budget')
        for other in leaves[:i]:
            if all(max(bounds[k], other['bounds'][k]) < min(bounds[k+3], other['bounds'][k+3])
                   for k in range(3)):
                raise ValueError('Overlapping sparse leaves')
        counts[state] += budget['cell_count']
    total = math.prod(grid['steps'])
    if sum(counts.values()) != total:
        raise ValueError('Missing original global cells in sparse partition')
    return {'global_cells': total, 'cells_by_state': counts,
            'leaves_by_state': {s: sum(r['state'] == s for r in leaves) for s in sorted(STATES)},
            'all_global_cells_covered_once': True}


def checked_resources(path, command):
    data = LOAD(path)
    if (data.get('command') != [str(v) for v in command]
            or data.get('status') != 'completed' or data.get('success') is not True
            or data.get('started') is not True or data.get('exit_code') != 0
            or not 0 < data['seconds'] <= data['wall_limit_seconds'] <= 60
            or not 0 < data['per_process_address_space_limit_bytes'] <= 2*1024**3
            or not 0 < data['observed_group_rss_limit_bytes'] <= 2*1024**3
            or not 0 <= data['peak_observed_group_rss_bytes'] <= 2*1024**3
            or not 0 < data['file_size_limit_bytes'] <= 64*1024**2
            or len(data['cpu_affinity']) != 1 or data['thread_environment_limit'] != 1):
        raise ValueError('Native sparse computation or its resource evidence differs')
    return data


def checked_inspection(result_path, binding, grid, project, binary):
    result = LOAD(result_path)
    if (result.get('schema') != 'genlaw.material-block-result.v1' or result.get('status') != 'inspected'
            or result.get('source_code') != file_binding(ROOT / 'genlaw_pipeline/material_blocks.py')):
        raise ValueError('Foreign or incomplete native material inspection')
    checked_file(result['source_code'])
    request = LOAD(checked_file(result['request']))
    inputs = [binding['project'], *binding['nc_files'], binding['engine_manifest'],
              binding['engine_binary'], result['source_code']]
    if (request.get('schema') != 'genlaw.material-block-request.v1' or request.get('inputs') != inputs
            or type(request.get('audit')) is not bool or request.get('target_cad_used') is not False
            or request.get('surface_computed') is not False):
        raise ValueError('Inspection belongs to different stock, NC, engine or source')
    native = checked_file(result['native'])
    report = validate_report(LOAD(native), request['blocks'], grid, request['audit'])
    command = [binary, '--threads', '1', '--reduce=false', '--inspect-material', '--material-blocks',
               ';'.join(','.join(map(str, block)) for block in request['blocks'])]
    if request['audit']:
        command.append('--audit-material-blocks')
    checked_resources(checked_file(result['resources']), command + [project, native])
    return report


def create_plan(project, engine, out, refine_above_cells=500_000):
    """Refine uncertain blocks, preserving every native proof and all stock cells."""
    project, engine, out = [Path(p).resolve() for p in (project, engine, out)]
    for p in (project, engine, out):
        p.relative_to(ROOT)
    if type(refine_above_cells) is not int or not 1 <= refine_above_cells <= MAX_CELLS:
        raise ValueError('Invalid sparse refinement size; native budgets cannot be raised')
    config, binding = project_binding(project)
    grid = expected_grid(config)
    checked_queries([[0, 0, 0, *grid['steps']]], grid)
    binary, _ = engine_for_inspection(engine)
    binding.update(engine_manifest=file_binding(engine), engine_binary=file_binding(binary))
    sources = [file_binding(ROOT / name) for name in SOURCE_PATHS]
    out.mkdir(parents=True, exist_ok=False)
    write(out / 'intent.json', {'project': binding, 'sources': sources, 'grid': grid,
          'refine_above_cells': refine_above_cells, 'target_cad_used': False,
          'production_approved': False, 'experimental_only': True})
    leaves, inspections, pending = [], [], shell_partition(grid['steps'])
    try:
        while pending:
            batch = out / f'inspection-{len(inspections):03d}'
            report = inspect(project, engine, pending, batch)
            proof = file_binding(batch / 'result.json')
            inspections.append(proof)
            next_pending = []
            for i, row in enumerate(report['blocks']):
                budget = window_budget(row['bounds'], grid['steps'])
                uncertain = row['state'] == 'requires_native_cells'
                refine = uncertain and (not budget['allowed'] or budget['cell_count'] > refine_above_cells)
                after_split = len(leaves) + len(next_pending) + len(pending)-i + 1
                if refine and after_split > MAX_WINDOWS:
                    if not budget['allowed']:
                        raise ValueError('Complete sparse partition exceeds the existing leaf-count budget')
                    refine = False  # Safe unoptimized native computation within the same budget.
                if refine:
                    next_pending.extend(split_box(row['bounds']))
                else:
                    leaves.append({'index': len(leaves), 'bounds': row['bounds'], 'state': row['state'],
                                   'inspection': proof, 'query_index': i})
            pending = next_pending
        coverage = validate_cover(leaves, grid)
        for item in [binding['project'], *binding['nc_files'], binding['engine_manifest'],
                     binding['engine_binary'], *sources]:
            checked_file(item)
        plan = {'schema': 'genlaw.sparse-material-plan.v1', 'status': 'complete_partition',
                'binding': binding, 'grid': grid, 'leaves': leaves, 'inspections': inspections,
                'sources': sources, 'coverage': coverage, 'refine_above_cells': refine_above_cells,
                'target_cad_used': False, 'partial_nc_allowed': False, 'artificial_caps': False,
                'surface_computed': False, 'experimental_only': True, 'production_approved': False}
        write(out / 'plan.json', plan)
        checked_plan(out / 'plan.json')
    except Exception as error:
        write(out / 'failure.json', {'status': 'not_qualified', 'error': str(error),
              'inspections': inspections, 'surface_computed': False, 'production_approved': False})
        raise
    return out / 'plan.json'


def checked_plan(plan_path):
    plan = LOAD(plan_path)
    if (plan.get('schema') != 'genlaw.sparse-material-plan.v1' or plan.get('status') != 'complete_partition'
            or plan.get('target_cad_used') is not False or plan.get('partial_nc_allowed') is not False
            or plan.get('artificial_caps') is not False or plan.get('surface_computed') is not False
            or plan.get('experimental_only') is not True):
        raise ValueError('Unsupported sparse material plan')
    if plan['sources'] != [file_binding(ROOT / name) for name in SOURCE_PATHS]:
        raise ValueError('Sparse material implementation changed')
    binding = plan['binding']
    project = checked_file(binding['project'])
    config, expected = project_binding(project)
    if binding['nc_files'] != expected['nc_files'] or plan['grid'] != expected_grid(config):
        raise ValueError('Sparse stock, original NC or global lattice changed')
    binary, env = engine_for_inspection(checked_file(binding['engine_manifest']))
    if binding['engine_binary'] != file_binding(binary):
        raise ValueError('Sparse inspector and material kernel differ')
    reports = {}
    if not 1 <= len(plan['inspections']) <= 2*MAX_WINDOWS:
        raise ValueError('Invalid sparse inspection batch count')
    for proof in plan['inspections']:
        key = (proof['path'], proof['sha256'])
        if key in reports:
            raise ValueError('Duplicate sparse inspection')
        reports[key] = checked_inspection(checked_file(proof), binding, plan['grid'], project, binary)
    if validate_cover(plan['leaves'], plan['grid']) != plan['coverage']:
        raise ValueError('Sparse coverage accounting differs')
    for leaf in plan['leaves']:
        proof, i = leaf['inspection'], leaf['query_index']
        report = reports.get((proof['path'], proof['sha256']))
        if report is None or type(i) is not int or not 0 <= i < len(report['blocks']):
            raise ValueError('Missing native certificate for a sparse leaf')
        row = report['blocks'][i]
        if leaf['bounds'] != row['bounds'] or leaf['state'] != row['state']:
            raise ValueError('A sparse leaf disagrees with its native certificate')
    return plan, project, binary, env


def run_leaf(plan_path, index, out):
    """Only active leaves get a native material job; certificates get no STL."""
    plan, project, binary, env = checked_plan(plan_path)
    if type(index) is not int or not 0 <= index < len(plan['leaves']):
        raise ValueError('Invalid sparse leaf index')
    leaf = plan['leaves'][index]
    if leaf['state'] != 'requires_native_cells':
        raise ValueError('A certified homogeneous leaf must not masquerade as a native surface')
    out = out.resolve(); out.relative_to(ROOT); out.mkdir(parents=True, exist_ok=False)
    command = [binary, '--threads', '1', '--reduce=false', '--binary', '--cell-window',
               ','.join(map(str, leaf['bounds'])), project, out / 'surface.stl']
    admitted_bounded(out, 'camotics', command, env)
    checked_plan(plan_path)
    record = {'schema': 'genlaw.sparse-material-leaf.v1', 'status': 'executed',
              'plan': file_binding(plan_path), 'index': index, 'leaf': leaf,
              'surface': file_binding(out / 'surface.stl'), 'resources': file_binding(out / 'camotics-resources.json'),
              'source_code': file_binding(Path(__file__)), 'end_time_seconds': None,
              'production_approved': False}
    write(out / 'result.json', record)
    return record


def checked_results(plan_path, paths):
    plan, project, binary, _ = checked_plan(plan_path)
    active = {r['index'] for r in plan['leaves'] if r['state'] == 'requires_native_cells'}
    records = {}
    for path in paths:
        row = LOAD(path)
        i = row.get('index')
        if (row.get('schema') != 'genlaw.sparse-material-leaf.v1' or row.get('status') != 'executed'
                or type(i) is not int or i not in active or i in records
                or row.get('plan') != file_binding(plan_path) or row.get('leaf') != plan['leaves'][i]
                or row.get('source_code') != file_binding(Path(__file__)) or row.get('end_time_seconds') is not None):
            raise ValueError('Missing, duplicate, foreign or inapplicable sparse surface result')
        mesh = checked_file(row['surface'])
        command = [binary, '--threads', '1', '--reduce=false', '--binary', '--cell-window',
                   ','.join(map(str, row['leaf']['bounds'])), project, mesh]
        checked_resources(checked_file(row['resources']), command)
        records[i] = (path, row, mesh)
    if set(records) != active:
        raise ValueError('Missing original material: an active sparse leaf has no native result')
    return plan, [records[i] for i in sorted(active)]
