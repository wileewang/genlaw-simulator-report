"""Measure complete native stock against original selected-edge CAD, without fitting."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]; sys.path[:0] = [str(ROOT), str(ROOT/'web/tools')]
from mesh_metrics import require_bounded_worker, load_mesh, np
from genlaw_pipeline.indexed_sections import IndexedSections
from genlaw_pipeline.cell_windows import checked_file, file_binding
from genlaw_pipeline.selected_chamfer_geometry import SIDES, SelectedChamferReference
from genlaw_pipeline.selected_chamfer_preparation import checked_archive
from genlaw_pipeline.selected_chamfer_nc import check_nc
from genlaw_pipeline.native_stream import checked_result
from genlaw_pipeline.sparse_material import checked_resources
from genlaw_pipeline.measure_worker import section_height
from genlaw_pipeline.reference_sections import integrate_variable
from genlaw_pipeline.region_verification import topology
from genlaw_pipeline.verification import ray_hits
from genlaw_pipeline.run import FREECAD, write

READ = lambda p: json.loads(Path(p).read_text())
CONTROLS = {'omitted-opposite-edge': ('one-edge', 'opposite-long'),
            'wrong-edge-axis': ('opposite-short', 'opposite-long'),
            'additional-unselected-edge': ('opposite-long', 'one-edge')}


def checked_run(run):
    request = READ(run/'request.json'); plan = READ(run/'preparation-result.json')
    if checked_archive(request['input'], request['archive'])[0] != plan:
        raise ValueError('Run inputs no longer match their pre-NC archive')
    if Path(request['output']).resolve() != run: raise ValueError('Foreign CAM output binding')
    for b in request['execution_sources']+request['preserved_sources']: checked_file(b)
    checked_resources(run/'freecad-resources.json',
        [str(FREECAD), 'freecadcmd', str(ROOT/'tools/selected_chamfer_cam_worker.py')])
    cam = READ(run/'cam-result.json'); nc = run/'program.nc'
    if (cam['status'] != 'native_generated_and_checked' or cam['NC'] != file_binding(nc)
            or cam['preparation'] != file_binding(run/'preparation-result.json')
            or check_nc(nc.read_text(), plan) != cam['nc_check']):
        raise ValueError('CAM result and original NC disagree')
    ref_path = checked_file(cam['native_geometry'])
    if ref_path != run/'native-geometry.json': raise ValueError('Foreign native CAD observation')
    ref = READ(ref_path); target = SelectedChamferReference(ref)
    archive_ref = READ(checked_file(plan['input_bindings']['CAD_reference']))
    if (ref['status'] != 'verified' or ref['original_CAD_reparsed'] is not True
            or any(ref.get(k) != v for k, v in archive_ref.items())
            or ref['cad'] != plan['input_bindings']['CAD'] or ref['valid_single_solid'] is not True
            or not 0 <= ref['symmetric_difference_mm3'] <= 1e-8
            or abs(target.analytic_volume-ref['volume_mm3']) > 1e-8):
        raise ValueError('Reference is not the independently checked original CAD')
    return plan, ref


def feature_observations(vertices, faces, reference, limits):
    from shapely.geometry import LineString
    target = SelectedChamferReference(reference); lo, hi = target.low, target.high; width = target.width
    sections = IndexedSections(vertices, faces)
    levels = np.unique(np.r_[vertices[:, 2].astype(float), target.breaks])
    requests = [('chamfer', hi[2]-width+width*float(u)) for u in np.linspace(.05, .95, 11)]
    requests += [('retained_wall', lo[2]+u*(hi[2]-width-lo[2])) for u in [.2, .5, .8]]
    contours = []; samples = []
    for kind, requested in requests:
        z = section_height(levels, requested) if min(abs(levels-requested)) < 1e-10 else requested
        actual, expected = sections.at(z), target.at(z)
        same_topology = topology(actual) == topology(expected)
        error = float(actual.boundary.hausdorff_distance(expected.boundary)) if not actual.is_empty else None
        contours.append({'region': kind, 'z_mm': z, 'topology_matches': same_topology,
            'expected_topology': topology(expected), 'observed_topology': topology(actual),
            'boundary_deviation_mm': error, 'missing_material_area_mm2': expected.difference(actual).area,
            'excess_material_area_mm2': actual.difference(expected).area})
        if kind != 'chamfer': continue
        rect = expected.bounds
        for side in SIDES:
            axis = 'xy'.index(side[0]); along = 1-axis; selected = side in target.sides
            for u in [.15, .325, .5, .675, .85]:
                coordinate = rect[along]+u*(rect[along+2]-rect[along])
                a, b = list(lo[:2]), list(hi[:2]); a[axis] -= 1; b[axis] += 1
                a[along] = b[along] = coordinate
                line = actual.intersection(LineString([a, b])); valid = line.geom_type == 'LineString' and not line.is_empty
                inset = observed_width = deviation = None
                if valid:
                    if side.endswith('min'): inset = line.bounds[axis]-lo[axis]
                    else: inset = hi[axis]-line.bounds[axis+2]
                    observed_width = inset+hi[2]-z if selected else None
                    deviation = abs(observed_width-width) if selected else abs(inset)
                samples.append({'side': side, 'selected_for_chamfer': selected, 'z_mm': z,
                    'along_coordinate_mm': coordinate, 'complete': valid, 'inset_mm': inset,
                    'observed_chamfer_width_mm': observed_width, 'deviation_mm': deviation})
    # Use points within the independently defined retained top rectangle.
    low, high = list(lo[:2]), list(hi[:2])
    for side in target.sides:
        axis = 'xy'.index(side[0])
        if side.endswith('min'): low[axis] += width
        else: high[axis] -= width
    planes = []
    for u in [.2, .35, .5, .65, .8]:
        for v in [.2, .35, .5, .65, .8]:
            xy = [low[i]+t*(high[i]-low[i]) for i, t in enumerate((u, v))]
            zs = ray_hits(sections.triangles, xy, (lo[2]-.1, hi[2]+.1)); valid = len(zs) == 2
            error = max(abs(zs[0]-lo[2]), abs(zs[1]-hi[2])) if valid else None
            planes.append({'xy_mm': xy, 'surface_z_mm': zs, 'plane_error_mm': error,
                           'passed': valid and error <= limits['bounds_deviation_mm_max']})
    def maximum(rows, key):
        return max(r[key] for r in rows) if rows and all(r[key] is not None for r in rows) else None
    profile_error = maximum(contours, 'boundary_deviation_mm')
    selected_error = maximum([s for s in samples if s['selected_for_chamfer']], 'deviation_mm')
    retained_error = maximum([s for s in samples if not s['selected_for_chamfer']], 'deviation_mm')
    guard = limits['numerical_guard_mm']
    return {'method': '14 complete sections; 220 named-edge cross-lines including every unselected side; 25 retained top/bottom rays',
        'contours': contours, 'edge_samples': samples, 'retained_plane_probes': planes,
        'maximum_boundary_deviation_mm': profile_error, 'maximum_selected_width_deviation_mm': selected_error,
        'maximum_unselected_edge_deviation_mm': retained_error,
        'section_pass': profile_error is not None and profile_error+guard <= limits['section_deviation_mm_max']
                        and all(r['topology_matches'] for r in contours),
        'selected_edges_pass': selected_error is not None and selected_error+guard <= limits['selected_edge_width_deviation_mm_max'],
        'unselected_edges_pass': retained_error is not None and retained_error+guard <= limits['retained_surface_deviation_mm_max'],
        'retained_planes_pass': all(p['passed'] for p in planes), 'numerical_guard_mm': guard,
        'guard_is_certified_uncertainty': False, 'manufacturing_angle_acceptance': False}


def measure(source_run, target_run, kind, output):
    require_bounded_worker(); source_run = source_run.resolve(); target_run = target_run.resolve()
    source_plan, _ = checked_run(source_run); target_plan, reference = checked_run(target_run)
    normal = kind == 'normal'
    if normal:
        if source_run != target_run: raise ValueError('A normal result must measure its own current material')
        rejection = None
    else:
        pair = (source_plan['specification']['id'], target_plan['specification']['id'])
        if CONTROLS.get(kind) != pair: raise ValueError('Unsupported explicit geometry counterexample')
        if READ(source_run/'request.json')['archive'] != READ(target_run/'request.json')['archive']:
            raise ValueError('Control must reuse the same frozen cohort')
        for key in ['stock', 'tool', 'parameters', 'simulation_project', 'grid', 'window']:
            if source_plan[key] != target_plan[key]: raise ValueError('Control changed more than its declared edge selection')
        try: check_nc((source_run/'program.nc').read_text(), target_plan)
        except ValueError as error: rejection = str(error)
        else: raise ValueError('Target NC admission failed to reject wrong edge selection')
    native, request = checked_result(source_run/'material/result.json')
    if (request['window'] != source_plan['window'] or request['grid'] != source_plan['grid']
            or checked_file(request['inputs']['project']) != source_run/'simulation.camotics'
            or READ(source_run/'simulation.camotics') != source_plan['simulation_project']
            or len(native['shards']) != 1):
        raise ValueError('Require the full original window and one bounded native surface')
    mesh = checked_file(native['shards'][0]['surface']); vertices, faces, inspection = load_mesh(mesh)
    if inspection['exact_duplicate_faces_removed']: raise ValueError('Do not discard duplicate native faces')
    target = SelectedChamferReference(reference); limits = target_plan['specification']['acceptance']
    levels = np.unique(np.r_[vertices[:, 2].astype(float), target.breaks])
    if len(levels) > 512: raise ValueError('Complete measurement exceeds this observer interval budget')
    sections = IndexedSections(vertices, faces)
    quadrature = [integrate_variable(sections, levels, target, n) for n in limits['quadrature_orders']]
    coarse, fine = quadrature
    errors = {'quadrature_iou_difference': abs(coarse['iou']-fine['iou']),
        'target_integral_error_mm3': abs(fine['target_volume_mm3']-target.analytic_volume),
        'mesh_integral_error_mm3': abs(fine['actual_volume_mm3']-inspection['volume_mm3'])}
    stable = all(errors[k] <= limits[k+'_max'] for k in errors)
    observed = feature_observations(vertices, faces, reference, limits)
    bounds_error = max(abs(inspection['bounds'][k][i]-reference['bounds'][k][i]) for k in ['min', 'max'] for i in range(3))
    volume_error = abs(inspection['volume_mm3']-target.analytic_volume)
    geometry_pass = (stable and fine['iou']*100 >= limits['iou_percent_min']
        and bounds_error <= limits['bounds_deviation_mm_max'] and volume_error <= limits['volume_deviation_mm3_max']
        and all(observed[k] for k in ['section_pass', 'selected_edges_pass', 'unselected_edges_pass', 'retained_planes_pass']))
    expectation = geometry_pass if normal else stable and not geometry_pass
    sources = [Path(__file__), ROOT/'genlaw_pipeline/selected_chamfer_geometry.py',
        ROOT/'genlaw_pipeline/indexed_sections.py', ROOT/'genlaw_pipeline/reference_sections.py',
        ROOT/'genlaw_pipeline/verification.py', ROOT/'web/tools/mesh_metrics.py']
    report = {'schema': 'genlaw.selected-chamfer-metrics.v1', 'kind': kind, 'negative_control': not normal,
        'source_run': str(source_run.relative_to(ROOT)), 'target_run': str(target_run.relative_to(ROOT)),
        'source_preparation': file_binding(source_run/'preparation-result.json'),
        'target_preparation': file_binding(target_run/'preparation-result.json'),
        'source_CAM': file_binding(source_run/'cam-result.json'), 'target_CAM': file_binding(target_run/'cam-result.json'),
        'native_material': file_binding(source_run/'material/result.json'), 'mesh': file_binding(mesh),
        'original_target_CAD': target_plan['input_bindings']['CAD'],
        'reference': file_binding(target_run/'native-geometry.json'), 'mesh_inspection': inspection,
        'target_NC_guard_rejection': rejection, 'new_simulation_for_this_measurement': False,
        'source_NC_modified': False, 'quadrature': quadrature, 'numerical_errors': errors,
        'numerical_stability_pass': stable, 'iou_percent': fine['iou']*100, 'acceptance': limits,
        'maximum_bounds_error_mm': bounds_error, 'volume_deviation_mm3': volume_error,
        'feature_observations': observed, 'geometry_pass': geometry_pass, 'regression_expectation_met': expectation,
        'complete_original_material_measured': True, 'registration_applied': False, 'material_discarded': False,
        'source_code': [file_binding(p) for p in sources], 'feature_cohort_qualified': False,
        'production_approved': False, 'whole_real_card_complete': False}
    write(output, report)
    print(json.dumps({'kind': kind, 'source': source_plan['specification']['id'], 'target': target_plan['specification']['id'],
        'iou_percent': report['iou_percent'], 'geometry_pass': geometry_pass, 'expectation_met': expectation}), flush=True)
    if not expectation: raise ValueError('Selected-edge geometry did not meet the frozen expectation')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-run', type=Path, required=True); p.add_argument('--target-run', type=Path, required=True)
    p.add_argument('--kind', choices=['normal', *CONTROLS], default='normal'); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); measure(a.source_run, a.target_run, a.kind, a.output)
