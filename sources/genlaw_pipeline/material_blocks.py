"""Inspect bounded batches of original-grid blocks without allocating their cells.

This is a certificate primitive, not a material surface or manufacturing verdict.
No target CAD is accepted. Unknown blocks must still be simulated; a complete
sparse partition, its boundary surface and resulting geometry require validation.
"""
import json
import math
from pathlib import Path

from .admission_wait import admitted_bounded
from .cell_windows import candidate_engine, checked_file, file_binding, project_binding, expected_grid
from .run import write

ROOT = Path(__file__).resolve().parents[1]
MAX_QUERIES = 1024
MAX_AUDIT_VERTICES = 5_000_000
MAX_XY = 100_000
STATES = {'uncut_solid', 'empty_outside_stock', 'requires_native_cells'}
LOAD = lambda p: json.loads(p.read_text())


def checked_queries(blocks, grid, audit=False):
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= MAX_QUERIES:
        raise ValueError('Material inspection requires 1..1024 block queries')
    vertices = 0
    for block in blocks:
        if (not isinstance(block, list) or len(block) != 6 or any(type(v) is not int for v in block)
                or any(not 0 <= block[i] < block[i+3] <= grid['steps'][i] for i in range(3))):
            raise ValueError('Empty, noninteger or out-of-grid material block')
        widths = [block[i+3]-block[i] for i in range(3)]
        if math.prod(widths) > 2**64-1:
            raise ValueError('Material block count exceeds uint64')
        vertices += math.prod(w+1 for w in widths)
        if audit and (vertices > MAX_AUDIT_VERTICES or (widths[0]+1)*(widths[1]+1) > MAX_XY):
            raise ValueError('Material field audit exceeds the original sampling budget')
    return vertices


def engine_for_inspection(manifest_path):
    binary, env, manifest = candidate_engine(manifest_path)
    cap = manifest.get('material_block_inspection_capability', {})
    expected = {'schema': 'genlaw.material-block-inspection-capability.v1',
        'maximum_queries': MAX_QUERIES, 'maximum_audit_vertices': MAX_AUDIT_VERTICES,
        'maximum_audit_xy_samples': MAX_XY, 'sampling': 'global-integer-lattice-v1',
        'cell_interval': 'half-open', 'threads': 1, 'partial_nc_allowed': False,
        'reject_unknown_or_ignored_words': True, 'strict_interpreter_opt_in': True,
        'source_bound_build': True,
        'target_cad_used': False, 'surface_computed': False, 'grid_allocation_required': False,
        'removed_material_certificate_supported': False}
    if any(cap.get(k) != v for k, v in expected.items()):
        raise ValueError('An explicit bounded material-block inspection engine is required')
    for name in ['source', 'patch', 'freezer']:
        checked_file(cap[name])
    for binding in cap['strict_sources']:
        checked_file(binding)
    build = LOAD(checked_file(cap['build_record']))
    if build.get('status') != 'linked_source_bound' or build['binary_sha256'] != manifest['binary_sha256']:
        raise ValueError('Material-block binary is not the successfully linked source-bound build')
    return binary, env


def validate_report(report, blocks, grid, audit=False):
    checked_queries(blocks, grid, audit)
    if (report.get('schema') != 'genlaw.material-block-inspection.v1'
            or any(report.get(k) != grid[k] for k in ['resolution_mm', 'offset_mm', 'steps'])
            or report.get('sampling') != 'global-integer-lattice-v1'
            or report.get('cell_interval') != 'half-open' or report.get('nc_executed') is not True
            or report.get('surface_computed') is not False or report.get('target_cad_used') is not False
            or type(report.get('native_moves')) is not int or not 0 <= report['native_moves'] <= 200000
            or len(report.get('blocks', [])) != len(blocks)):
        raise ValueError('Native material inspection differs from its original grid or query')
    for i, (block, row) in enumerate(zip(blocks, report['blocks'])):
        widths = [block[k+3]-block[k] for k in range(3)]
        state = row.get('state')
        if (row.get('index') != i or row.get('bounds') != block or row.get('cell_count') != math.prod(widths)
                or state not in STATES or row.get('homogeneous_sign_proven') is not (state != 'requires_native_cells')):
            raise ValueError('Missing, reordered, changed or unsupported material certificate')
        if state == 'uncut_solid' and (row.get('strict_stock_interior') is not True or row.get('sweep_aabb_intersects') is not False):
            raise ValueError('Uncut material requires strict interior and no native sweep intersection')
        signs = row.get('audited_vertices')
        if audit:
            if (not isinstance(signs, dict) or set(signs) != {'positive', 'negative', 'zero'}
                    or any(type(v) is not int or v < 0 for v in signs.values())
                    or sum(signs.values()) != math.prod(w+1 for w in widths)
                    or (state == 'uncut_solid' and signs['negative']+signs['zero'])
                    or (state == 'empty_outside_stock' and signs['positive']+signs['zero'])):
                raise ValueError('Certificate disagrees with the complete native vertex audit')
        elif signs is not None:
            raise ValueError('Unexpected unrequested material field audit')
    return report


def inspect(project, engine, blocks, out, audit=False):
    project, engine, out = [p.resolve() for p in (project, engine, out)]
    for p in [project, engine, out]:
        p.relative_to(ROOT)
    config, binding = project_binding(project)
    grid = expected_grid(config)
    checked_queries(blocks, grid, audit)
    binary, env = engine_for_inspection(engine)
    out.mkdir(parents=True, exist_ok=False)
    inputs = [binding['project'], *binding['nc_files'], file_binding(engine), file_binding(binary), file_binding(Path(__file__))]
    write(out / 'request.json', {'schema': 'genlaw.material-block-request.v1', 'inputs': inputs,
        'blocks': blocks, 'audit': audit, 'target_cad_used': False, 'surface_computed': False})
    command = [binary, '--threads', '1', '--reduce=false', '--inspect-material', '--material-blocks',
        ';'.join(','.join(map(str, block)) for block in blocks)]
    if audit:
        command.append('--audit-material-blocks')
    command += [project, out / 'native.json']
    admitted_bounded(out, 'inspect', command, env)
    for item in inputs:
        checked_file(item)
    report = validate_report(LOAD(out / 'native.json'), blocks, grid, audit)
    write(out / 'result.json', {'schema': 'genlaw.material-block-result.v1', 'status': 'inspected',
        'request': file_binding(out / 'request.json'), 'native': file_binding(out / 'native.json'),
        'resources': file_binding(out / 'inspect-resources.json'),
        'counts': {s: sum(r['state'] == s for r in report['blocks']) for s in sorted(STATES)},
        'source_code': file_binding(Path(__file__)), 'complete_grid_coverage_verified': False,
        'machining_result_generated': False, 'production_approved': False})
    return report
