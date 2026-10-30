"""Inspect additional dimensions of a previously executed, immutable NC result.

This entry point is measurement-only. It binds an existing normal CAM job (or
an explicitly rejected test control), its original CAD, stock and actual mesh.
It never modifies a source run or starts a new material-removal calculation.
"""
import argparse
import bisect
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'web/tools')]
from mesh_metrics import require_bounded_worker, load_mesh, Sections, np
from genlaw_pipeline.cell_windows import file_binding, checked_file, project_binding
from genlaw_pipeline.programming import checked_plan, check_nc, verify_binding
from genlaw_pipeline.planar_dimensions import compile_requirements, observe_section, evaluate_dimensions
from genlaw_pipeline.measure_worker import section_height
from genlaw_pipeline.sharded_measure import checked_surface, ShardedSections
from genlaw_pipeline.run import write

LOAD = lambda path: json.loads(Path(path).read_text())
SOURCE_FILES = [
    'genlaw_pipeline/dimension_inspection.py', 'genlaw_pipeline/planar_dimensions.py',
    'genlaw_pipeline/verification.py', 'genlaw_pipeline/measure_worker.py',
    'genlaw_pipeline/sharded_measure.py', 'genlaw_pipeline/sharded_surface.py',
    'genlaw_pipeline/cell_windows.py', 'web/tools/mesh_metrics.py',
]


def workspace(path):
    path = (ROOT / path).resolve()
    path.relative_to(ROOT)
    return path


def original_source(source):
    if source.get('schema') != 'genlaw.dimension-material-source.v1':
        raise ValueError('Unknown prior material-source contract')
    normal = workspace(source['normal_run']); material = workspace(source['material_run'])
    control = source.get('test_only_control')
    if type(control) is not bool or (normal != material) != control:
        raise ValueError('A separate material run must be an explicit test control')
    input_path = workspace(LOAD(normal / 'request.json')['input'])
    plan = checked_plan(input_path, normal / 'preparation-result.json')
    if plan['schema'] not in ('genlaw.execution-plan.v10', 'genlaw.execution-plan.v11'):
        raise ValueError('Original capsule NC qualification requires v10 or v11')
    reference = LOAD(normal / 'native-geometry.json'); cam = LOAD(normal / 'cam-result.json')
    if (reference.get('status') != 'verified' or not reference.get('valid_single_solid')
            or not reference.get('original_cad_reparsed') or reference['symmetric_difference_mm3'] > 1e-8
            or reference['cad_sha256'] != plan['cad_ref']['sha256']
            or cam['native_cad_binding'] != reference):
        raise ValueError('Original CAD is not independently native-bound to the CAM result')
    cad = input_path.parent / plan['cad_ref']['path']; verify_binding(cad, reference['cad_sha256'])
    verify_binding(normal / 'program.nc', cam['nc_sha256'])
    rejection = None
    try:
        check_nc((material / 'program.nc').read_text(), plan)
    except ValueError as error:
        if not control: raise
        rejection = str(error)
    if control and rejection is None:
        raise ValueError('Test control was accepted by the normal NC guard')
    project, binding = project_binding(material / 'simulation.camotics')
    if (binding['nc_files'] != [file_binding(material / 'program.nc')]
            or project['workpiece']['bounds'] != plan['stock']['bounds_mm']):
        raise ValueError('Dimension inspection cannot replace the NC or original stock')
    if control:
        original_project = LOAD(normal / 'simulation.camotics')
        if project != original_project:
            raise ValueError('This observer qualification holds stock, tools and resolution fixed')
    resolution = project['resolution']
    bindings = {key: file_binding(path) for key, path in {
        'input': input_path, 'preparation': normal / 'preparation-result.json',
        'native_cam': normal / 'cam-result.json', 'original_reference': normal / 'native-geometry.json',
        'original_cad': cad, 'nc': material / 'program.nc', 'project': material / 'simulation.camotics',
        'original_metrics': material / 'metrics.json',
    }.items()}
    return normal, material, reference, resolution, bindings, rejection


def make_plan(source_path, requirements_path, output):
    require_bounded_worker()
    source = LOAD(source_path)
    normal, material, reference, resolution, bindings, rejection = original_source(source)
    request = LOAD(requirements_path); compile_requirements(request, reference)
    metrics = LOAD(material / 'metrics.json')
    backend = source['backend']
    low, high = reference['bounds']['min'][2], reference['bounds']['max'][2]
    if backend == 'single_mesh':
        if (metrics.get('registration') != 'none; same CAD/G54 coordinates'
                or metrics['actual_mesh']['registration_or_alignment']
                or metrics['actual_mesh']['holes_filled']
                or metrics['actual_mesh']['vertex_displacement_mm'] != 0):
            raise ValueError('Prior volume measurement must retain original coordinates and material')
        report = LOAD(material / 'software-verification.json')
        if (report['cad_sha256'] != reference['cad_sha256']
                or report['native_geometry_sha256'] != bindings['original_reference']['sha256']
                or report['nc_sha256'] != bindings['nc']['sha256']
                or report['project_sha256'] != bindings['project']['sha256']
                or bool(report.get('test_only_control')) != source['test_only_control']
                or metrics['reference_cad'] != reference
                or metrics['native_geometry_sha256'] != bindings['original_reference']['sha256']):
            raise ValueError('Existing actual-mesh verification differs from the declared original run')
        mesh = material / 'simulated.stl'
        verify_binding(mesh, report['mesh_sha256'])
        if metrics['actual_mesh']['source_sha256'] != report['mesh_sha256']:
            raise ValueError('Volume and feature reports use different material')
        resources = LOAD(material / 'camotics-resources.json')
        if (not resources.get('success') or resources.get('status') != 'completed'
                or resources['wall_limit_seconds'] > 60 or len(resources['cpu_affinity']) != 1
                or resources['peak_observed_group_rss_bytes'] > 2 * 1024**3
                or resources['command'][-2:] != [str(material / 'simulation.camotics'), str(mesh)]):
            raise ValueError('Actual material execution has no matching bounded native proof')
        vertices, _, audit = load_mesh(mesh)
        if audit['exact_duplicate_faces_removed']:
            raise ValueError('Dimension inspection must not discard duplicate material faces')
        levels = np.unique(np.r_[vertices[:, 2].astype(float), low, high])
        bindings.update(mesh=file_binding(mesh), original_verification=file_binding(material / 'software-verification.json'),
                        native_resources=file_binding(material / 'camotics-resources.json'))
    elif backend == 'surface_shards':
        if metrics.get('registration') != 'none; original coordinates':
            raise ValueError('Prior volume measurement must retain original coordinates')
        if source['test_only_control']:
            raise ValueError('The sharded dimension source adapter currently requires a normal run')
        surface_path = material / 'surface.json'; surface = checked_surface(surface_path)
        measure_path = material / 'sharded-measure-plan.json'; measure = LOAD(measure_path)
        cell = LOAD(checked_file(surface['plan']))
        if (measure['surface'] != file_binding(surface_path) or measure['reference'] != bindings['original_reference']
                or metrics['surface'] != file_binding(surface_path) or metrics['reference'] != bindings['original_reference']
                or metrics['plan'] != file_binding(measure_path)
                or cell['binding']['project'] != bindings['project']
                or cell['binding']['nc_files'] != [bindings['nc']]
                or not metrics['all_vertex_height_intervals_preserved'] or metrics['per_window_iou_averaged']):
            raise ValueError('Sharded dimensions must use the complete original NC surface')
        checked_file(measure['source_code'])
        for cache in measure['caches']:
            row = LOAD(checked_file(cache)); checked_file(row['array'])
        levels = np.unique(np.r_[surface['z_levels_mm'], low, high])
        bindings.update(surface=file_binding(surface_path), measure_plan=file_binding(measure_path))
    else:
        raise ValueError('Unsupported actual material backend')
    heights = inspection_heights(levels, reference)
    write(output, {'schema': 'genlaw.planar-dimension-plan.v1', 'source': file_binding(source_path),
        'requirements': file_binding(requirements_path), 'bindings': bindings, 'backend': backend,
        'heights': heights, 'resolution_mm': resolution, 'source_code': [file_binding(ROOT / p) for p in SOURCE_FILES],
        'test_only_control': source['test_only_control'], 'normal_guard_rejection': rejection,
        'prior_iou_percent': metrics['iou_percent'], 'material_execution_reused_without_changes': True,
        'new_material_execution': False, 'requirements_added_after_existing_run': True,
        'scope': 'Additional measurement of frozen prior NC material; not a rerun or new manufacturing approval.'})
    print(json.dumps({'status': 'bound', 'backend': backend, 'sections': len(heights)}), flush=True)


def inspection_heights(levels, reference):
    if len(levels) > 4096: raise ValueError('Height selection exceeds the existing interval budget')
    low, high = reference['bounds']['min'][2], reference['bounds']['max'][2]
    return [{'requested_z_mm': low + (high - low) * float(f),
             'z_mm': section_height(levels, low + (high - low) * float(f))}
            for f in np.linspace(.05, .95, 11)]


def checked(plan_path):
    plan = LOAD(plan_path)
    if plan.get('schema') != 'genlaw.planar-dimension-plan.v1':
        raise ValueError('Unsupported dimension measurement plan')
    if plan['source_code'] != [file_binding(ROOT / p) for p in SOURCE_FILES]:
        raise ValueError('Dimension observer source changed after plan binding')
    source = LOAD(checked_file(plan['source']))
    if (source['backend'] != plan['backend'] or source['test_only_control'] != plan['test_only_control']
            or plan['resolution_mm'] != LOAD(checked_file(plan['bindings']['project']))['resolution']):
        raise ValueError('Dimension plan changed the declared material source or resolution')
    for binding in plan['bindings'].values(): checked_file(binding)
    reference = LOAD(checked_file(plan['bindings']['original_reference']))
    request = LOAD(checked_file(plan['requirements'])); compile_requirements(request, reference)
    if len(plan['heights']) != 11:
        raise ValueError('Missing original-CAD inspection heights')
    if plan['backend'] == 'surface_shards':
        surface = LOAD(checked_file(plan['bindings']['surface']))
        levels = np.unique(np.r_[surface['z_levels_mm'], reference['bounds']['min'][2], reference['bounds']['max'][2]])
        if plan['heights'] != inspection_heights(levels, reference):
            raise ValueError('Sharded inspection heights differ from the original CAD policy')
    return plan, reference, request


def section(plan_path, index, output):
    require_bounded_worker(); plan, reference, _ = checked(plan_path)
    if plan['backend'] != 'surface_shards' or not 0 <= index < len(plan['heights']):
        raise ValueError('Invalid sharded dimension section')
    measure = LOAD(checked_file(plan['bindings']['measure_plan']))
    height = plan['heights'][index]; z = height['z_mm']; levels = measure['levels_mm']
    i = bisect.bisect_right(levels, z) - 1
    if not 0 <= i < len(levels) - 1: raise ValueError('Inspection plane outside material intervals')
    provider = ShardedSections(measure, levels[i], levels[i + 1])
    observation = observe_section(provider.at(z), reference, plan['resolution_mm'])
    write(output, {'schema': 'genlaw.planar-dimension-section.v1', 'plan': file_binding(plan_path),
                   'index': index, **height, 'observation': observation,
                   'source_code': plan['source_code'], 'active_triangles': len(provider.triangles),
                   'section_segments': provider.maximum_observed_segments})


def finish(plan_path, directory, output):
    require_bounded_worker(); plan, reference, request = checked(plan_path)
    parts = []
    if plan['backend'] == 'single_mesh':
        vertices, faces, audit = load_mesh(checked_file(plan['bindings']['mesh']))
        if audit['exact_duplicate_faces_removed']: raise ValueError('Actual duplicate material faces must be preserved')
        levels = np.unique(np.r_[vertices[:, 2].astype(float), reference['bounds']['min'][2], reference['bounds']['max'][2]])
        if plan['heights'] != inspection_heights(levels, reference):
            raise ValueError('Inspection heights differ from the original CAD policy')
        provider = Sections(vertices, faces)
        observations = [observe_section(provider.at(h['z_mm']), reference, plan['resolution_mm']) for h in plan['heights']]
    else:
        if directory is None: raise ValueError('Sharded inspection requires its complete section directory')
        observations = []
        for index, height in enumerate(plan['heights']):
            path = directory / f'section-{index:02d}.json'; row = LOAD(path)
            if (row.get('schema') != 'genlaw.planar-dimension-section.v1' or row['index'] != index
                    or row['plan'] != file_binding(plan_path) or row['source_code'] != plan['source_code']
                    or any(row[k] != height[k] for k in height)):
                raise ValueError('Missing, duplicated or foreign dimension section')
            observations.append(row['observation']); parts.append(file_binding(path))
    result = evaluate_dimensions(request, reference, observations, plan['resolution_mm'])
    result.update(schema='genlaw.planar-dimension-verification.v1', status='evaluated',
        plan=file_binding(plan_path), source_code=plan['source_code'], section_parts=parts,
        section_observations=[dict(height=h, observation=o) for h, o in zip(plan['heights'], observations)],
        source_bindings=plan['bindings'], original_iou_percent=plan['prior_iou_percent'],
        original_iou_recomputed=False, source_material_execution_reused=True,
        test_only_control=plan['test_only_control'], normal_guard_rejection=plan['normal_guard_rejection'],
        original_geometry_and_process_checks_replaced=False,
        limits=['Relative lengths do not replace original CAD position, contour, thickness or process-order checks.',
                'Finite cross-sections and fitted circular features; guards are not certified measurement uncertainty.',
                'Declared source dimensions only; identity, revision, surface treatment, GD&T and resources remain separate.'])
    write(output, result)
    print(json.dumps({'dimensions': len(result['requirements']), 'passed': result['dimension_inspection_passed'],
                      'statuses': {r['id']: r['status'] for r in result['requirements']}}), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='mode', required=True)
    first = sub.add_parser('plan')
    for key in ('source', 'requirements', 'output'): first.add_argument('--' + key, type=Path, required=True)
    worker = sub.add_parser('section'); worker.add_argument('--index', type=int, required=True)
    for key in ('plan', 'output'): worker.add_argument('--' + key, type=Path, required=True)
    last = sub.add_parser('finish'); last.add_argument('--directory', type=Path)
    for key in ('plan', 'output'): last.add_argument('--' + key, type=Path, required=True)
    a = p.parse_args()
    for value in vars(a).values():
        if isinstance(value, Path): value.resolve().relative_to(ROOT)
    if a.mode == 'plan': make_plan(a.source, a.requirements, a.output)
    elif a.mode == 'section': section(a.plan, a.index, a.output)
    else: finish(a.plan, a.directory, a.output)
