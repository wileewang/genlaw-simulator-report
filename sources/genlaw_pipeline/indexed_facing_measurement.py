"""Measure a complete indexed-engine surface against independently bound CAD."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT/'web/tools'))
from genlaw_pipeline.cell_windows import checked_file, file_binding
from genlaw_pipeline.indexed_facing import prepare_chain
from genlaw_pipeline.sparse_material import checked_resources
from genlaw_pipeline.run import write
from mesh_metrics import require_bounded_worker, load_mesh, Sections, integrate, np, box, Point


def measure(request_path, out):
    require_bounded_worker()
    req = json.loads(request_path.read_text())
    if req.get('schema') != 'genlaw.indexed-facing-measurement-request.v1':
        raise ValueError('Require an explicit indexed-engine measurement request')
    chain = json.loads(checked_file(req['preparation']).read_text())
    if chain != prepare_chain(checked_file(chain['input'])):
        raise ValueError('Indexed inputs or preparation changed')
    for binding in req['bound_artifacts'] + req['source_code']:
        checked_file(binding)
    checked_resources(checked_file(req['cam_resources']), req['cam_command'])
    cad = json.loads(checked_file(req['CAD_chain']).read_text())
    if (cad['schema'] != 'genlaw.indexed-facing-cad-binding.v1'
            or cad['preparation'] != req['preparation'] or not cad['CAD_reparsed_before_CAM']
            or cad['final_cad'] != chain['final_cad']
            or cad['stage_target_bounds_in_material'] != chain['stage_target_bounds_in_material']):
        raise ValueError('Native CAD chain does not match measurement inputs')
    for binding in [cad['final_cad'], *cad['stage_cad']]: checked_file(binding)
    for name in ['final_box_symmetric_difference_mm3', 'nominal_stock_continuity_difference_mm3',
                 'final_target_transform_difference_mm3']:
        if not 0 <= cad[name] <= 1e-8: raise ValueError('Native CAD continuity was not verified')
    if any(not 0 <= x <= 1e-8 for x in cad['stage_box_symmetric_differences_mm3']):
        raise ValueError('Native stage targets were not verified')
    checked_resources(checked_file(req['native_resources']), req['native_command'])
    manifest_path = checked_file(req['native_manifest']); native = json.loads(manifest_path.read_text())
    kind = req['case_kind']
    if kind not in ['first', 'final', 'wrong-pose', 'omitted-second']:
        raise ValueError('Unsupported indexed measurement case')
    setups = 2 if kind in ['final', 'wrong-pose'] else 1
    from genlaw_pipeline.indexed_facing import IDENTITY
    pose = (chain['setups'][1]['to_material'] if kind == 'final' else IDENTITY) if setups == 2 else None
    expected = {'schema': 'genlaw.indexed-stream-window.v1', 'status': 'complete_window',
        'nc_executed': True, 'complete_nc': True, 'surface_computed': True, 'target_cad_used': False,
        'reduction': False, 'threads': 1, 'sampling': 'global-integer-lattice-v1', 'units': 'mm',
        'cell_interval': 'half-open', 'resolution_mm': chain['grid']['resolution_mm'],
        'offset_mm': chain['grid']['offset_mm'], 'steps': chain['grid']['steps'],
        'cell_window': chain['window'], 'setups_evaluated': setups, 'second_setup_to_material': pose,
        'stock_reset_between_setups': False, 'continuous_rotary_motion': False,
        'visited_native_cells': chain['budget']['cell_count'], 'whole_stock_surface_verified': False}
    if any(native.get(k) != v for k, v in expected.items()):
        raise ValueError('Native indexed receipt differs from the executed request')
    if len(native['shards']) != 1 or not 0 < native['triangle_count'] <= 500000:
        raise ValueError('This adapter requires one bounded complete native STL')
    shard = native['shards'][0]
    if shard['index'] != 0 or shard['file'] != 'surface-0000.stl' or shard['triangles'] != native['triangle_count']:
        raise ValueError('Invalid complete native surface membership')
    mesh = manifest_path.parent/shard['file']
    if (mesh.stat().st_size != shard['bytes'] or shard['bytes'] != 84+50*native['triangle_count']
            or {p.name for p in manifest_path.parent.iterdir()} != {'native.json', shard['file']}):
        raise ValueError('Missing or unexplained native surface files')
    vertices, faces, inspection = load_mesh(mesh)
    stage = 0 if kind == 'first' else 1
    expected_bounds = chain['stage_target_bounds_in_material'][stage]
    lo, hi = expected_bounds['min'], expected_bounds['max']; limits = chain['acceptance']
    levels = np.unique(np.concatenate((vertices[:, 2].astype(np.float64), [lo[2], hi[2]])))
    if len(levels) > 192: raise ValueError('Indexed measurement exceeds the bounded interval budget')
    sections = Sections(vertices, faces); polygon = box(*lo[:2], *hi[:2])
    coarse = integrate(sections, levels, polygon, (lo[2], hi[2]), 2)
    fine = integrate(sections, levels, polygon, (lo[2], hi[2]), 3)
    if (abs(fine['iou']-coarse['iou']) >= limits['quadrature_iou_difference_max']
            or abs(fine['actual_volume_mm3']-inspection['volume_mm3']) >= limits['mesh_vs_integrated_volume_mm3_max']):
        raise ValueError('Independent sectional integration and mesh volume disagree')
    original = chain['original_stock']['bounds_mm']
    final = chain['stage_target_bounds_in_material'][1]
    zs = [(original['max'][2]+final['max'][2])/2,
          (final['max'][2]+final['min'][2])/2,
          (original['min'][2]+final['min'][2])/2]
    probes = []
    for z in zs:
        section = sections.at(z)
        probes.append([bool(section.contains(Point(lo[0]+u*(hi[0]-lo[0]), lo[1]+v*(hi[1]-lo[1]))))
                       for u in [.2, .5, .8] for v in [.2, .5, .8]])
    expected_probes = [False, True, stage == 0]
    probe_pass = all(all(value == wanted for value in group) for group, wanted in zip(probes, expected_probes))
    volume = float(np.prod(np.array(hi)-lo))
    bound_error = max(abs(inspection['bounds'][k][i]-expected_bounds[k][i])
                      for k in ('min', 'max') for i in range(3))
    normal_pass = (fine['iou'] >= limits['normal_iou_min'] and probe_pass
                   and abs(inspection['volume_mm3']-volume) <= limits['volume_error_mm3_max']
                   and bound_error <= limits['bounds_error_mm_max'])
    is_control = kind in ['wrong-pose', 'omitted-second']
    expected_result = (not normal_pass and fine['iou'] < limits['control_iou_max']) if is_control else normal_pass
    report = {'schema': 'genlaw.indexed-facing-metrics.v1', 'request': file_binding(request_path),
        'case_kind': kind, 'mesh': file_binding(mesh), 'mesh_inspection': inspection,
        'reference_CAD': cad['stage_cad'][0] if stage == 0 else cad['final_cad'],
        'reference_bounds_in_original_material_frame': expected_bounds,
        'reference_was_checked_against_native_CAD': True, 'reference_is_reconstructed_from_NC': False,
        'iou': fine['iou'], 'iou_percent': fine['iou']*100, 'volumes': fine, 'coarse_quadrature': coarse,
        'maximum_bounds_error_mm': bound_error, 'expected_volume_mm3': volume,
        'probe_z_mm': zs, 'material_at_27_probes': probes, 'expected_material_by_height': expected_probes,
        'geometry_pass': normal_pass, 'regression_expectation_met': expected_result,
        'negative_control': is_control, 'production_approved': False, 'whole_process_card_complete': False}
    for binding in req['bound_artifacts'] + req['source_code']: checked_file(binding)
    write(out, report)
    if not expected_result: raise ValueError('Indexed geometry did not meet its frozen software expectation')
    print(json.dumps({'case_kind': kind, 'iou_percent': report['iou_percent'], 'geometry_pass': normal_pass}), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--request', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True); args = p.parse_args()
    measure(args.request.resolve(), args.output.resolve())
