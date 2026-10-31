"""Lossless review bundle for the qualified selected-edge cohort, without new CAM."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]; sys.path[:0] = [str(ROOT), str(ROOT/'web/tools')]
from genlaw_pipeline.cell_windows import checked_file, file_binding
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.run import write
from mesh_metrics import require_bounded_worker, load_mesh, write_indexed
from tools.pack_pocket_sequence import compressed

CP = ROOT/'docs/checkpoints/2026-10-31/selected-chamfer-01'
INPUTS = ROOT/'validation/feature-inputs/S4-SELECTED-CHAMFER-20261031-r2'
RUNS = ROOT/'validation/runs/S4-SELECTED-CHAMFER-20261031-r2'
NORMALS = ['one-edge', 'opposite-long', 'opposite-short', 'shifted-wider']
CONTROLS = ['omitted-opposite-edge', 'wrong-edge-axis', 'additional-unselected-edge']
READ = lambda p: json.loads(Path(p).read_text())


def initialize(out):
    complete = READ(CP/'completion.json'); suite = READ(checked_file(complete['suite']))
    if complete['status'] != 'passed_for_declared_synthetic_cohort' or suite['status'] != 'four_normal_three_controls_qualified':
        raise ValueError('Require completed selected-edge qualification')
    for binding in READ(checked_file(complete['input_archive']))['files']: checked_file(binding)
    replay = READ(checked_file(complete['replay']))
    if replay['status'] != 'actual_prefix_material_ready' or len(replay['frames']) != 3:
        raise ValueError('Require the measured executable-prefix replay')
    out.mkdir(parents=True, exist_ok=False)
    for source, name in [(RUNS/'summary.json', 'suite.json'), (CP/'completion.json', 'qualification.json'),
        (CP/'replay-prefix.json', 'replay-prefix.json'), (CP/'native-correction-regression.json', 'native-correction-regression.json'),
        (CP/'native-side-diagnosis-01.json', 'native-side-diagnosis.json'), (CP/'input-review-02.json', 'paper-review.json'),
        (checked_file(complete['guard_checks']), 'guard-checks.json'), (INPUTS/'manifest.json', 'inputs-manifest.json'),
        (INPUTS/'specifications.json', 'specifications.json')]: shutil.copyfile(source, out/name)
    shutil.copytree(CP/'qualified-source', out/'sources')
    shutil.copyfile(Path(__file__), out/'sources'/Path(__file__).name)
    with zipfile.ZipFile(out/'input-cards-and-cad.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for key in NORMALS:
            for source in sorted((INPUTS/key).iterdir()): archive.write(source, str(source.relative_to(INPUTS)))
    write(out/'archive-scope.json', {'customer_original_documents_included': False,
        'input_kind': 'Synthetic PDF cards, manually structured attachments and independent native CAD',
        'new_CAM_or_simulation_runs_for_export': 0, 'whole_real_card_complete': False,
        'production_approved': False, 'new_machine_models': 0, 'new_material_physics': 0})


def pack_case(out, key):
    if key not in NORMALS: raise ValueError('Only normal cases own distinct geometry in this archive')
    run, case = RUNS/key, out/key; case.mkdir(); shutil.copytree(INPUTS/key, case/'input')
    metrics = READ(run/'metrics.json')
    if not metrics['geometry_pass'] or not metrics['numerical_stability_pass']:
        raise ValueError('Normal geometry result changed')
    copied = {}
    def copy(source, name):
        dest = case/name; dest.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, dest)
        copied[str(source.relative_to(ROOT))] = {'path': str(dest.relative_to(out)), 'sha256': digest(source)}
    for name in ['program.nc', 'simulation.camotics']:
        copy(run/name, name)
    for name in ['preparation-result.json', 'archive-admission.json', 'cam-result.json', 'native-geometry.json',
        'native-path.json', 'cam-job.FCStd', 'request.json', 'freecad-resources.json', 'metrics.json', 'measure-resources.json']:
        copy(run/name, 'run/'+name)
    for source in sorted((run/'material').rglob('*.json')):
        copy(source, 'run/material/'+str(source.relative_to(run/'material')))
    equivalence = {}; delivery = {}
    for source, name in [(run/'target-display.stl', 'target'), (checked_file(metrics['mesh']), 'actual')]:
        vertices, faces, inspection = load_mesh(source)
        if inspection['exact_duplicate_faces_removed']: raise ValueError('Do not discard native faces')
        temp = case/(name+'.mesh'); write_indexed(temp, vertices, faces)
        record = compressed(temp, case/(name+'.mesh.gz')); record['path'] = key+'/'+name+'.mesh.gz'
        delivery[key+'/'+name+'.mesh'] = record
        equivalence[name] = {'source': file_binding(source), 'inspection': inspection,
            'decoded_display_sha256': digest(temp), 'vertices_moved': False, 'faces_discarded': False}
        temp.unlink()
    raw = compressed(checked_file(metrics['mesh']), case/'actual.stl.gz')
    spec = READ(INPUTS/key/'input.json')['specification']; obs = metrics['feature_observations']
    frames = []
    if key == 'opposite-long':
        frame = READ(CP/'replay-prefix.json')['frames'][1]
        if frame['surface'] != READ(RUNS/'one-edge/metrics.json')['mesh']:
            raise ValueError('Replay intermediate differs from the exact executed prefix')
        frames = [{'mesh': 'one-edge/actual.mesh', 'label': frame['label'],
            'kind': 'executed_exact_NC_prefix', 'resolution_mm': .025,
            'fine_checkpoint_acceptance': True, 'feed_time_seconds': frame['feed_time_seconds']}]
    summary = {'id': key, 'name': spec['name'], 'case_kind': 'normal', 'iou_percent': metrics['iou_percent'],
        'geometry_pass': True, 'numerical_stability_pass': True, 'stock': spec['stock'],
        'width_mm': spec['width_mm'], 'selected_sides': spec['selected_sides'], 'resolution_mm': .025,
        'target_case': key, 'material_case': key, 'frames': frames,
        **{k: obs[k] for k in ['section_pass', 'selected_edges_pass', 'unselected_edges_pass', 'retained_planes_pass']},
        'maximum_selected_width_deviation_mm': obs['maximum_selected_width_deviation_mm'],
        'maximum_unselected_edge_deviation_mm': obs['maximum_unselected_edge_deviation_mm']}
    write(case/'archive-bindings.json', {'copied_sources': copied, 'raw_material': raw,
        'display_equivalence': equivalence, 'geometry_delivery': delivery, 'summary': summary})
    print(json.dumps({'packed': key, 'iou_percent': summary['iou_percent']}), flush=True)


def assemble(out):
    normals = []; delivery = {}
    for key in NORMALS:
        value = READ(out/key/'archive-bindings.json'); normals.append(value['summary']); delivery.update(value['geometry_delivery'])
    controls = []
    titles = {'omitted-opposite-edge': '漏加工另一条指定边', 'wrong-edge-axis': '倒角方向选错',
              'additional-unselected-edge': '多加工一条未选边'}
    for key in CONTROLS:
        case = out/key; case.mkdir(); source = RUNS/key/'metrics.json'; metrics = READ(source)
        if metrics['geometry_pass'] or not metrics['numerical_stability_pass'] or not metrics['target_NC_guard_rejection']:
            raise ValueError('Wrong selected edges were not rejected')
        shutil.copyfile(source, case/'metrics.json')
        target = Path(metrics['target_run']).name; material = Path(metrics['source_run']).name
        spec = READ(INPUTS/target/'input.json')['specification']; obs = metrics['feature_observations']
        controls.append({'id': key, 'name': titles[key], 'case_kind': 'negative_control',
            'iou_percent': metrics['iou_percent'], 'geometry_pass': False, 'numerical_stability_pass': True,
            'stock': spec['stock'], 'width_mm': spec['width_mm'], 'selected_sides': spec['selected_sides'],
            'resolution_mm': .025, 'target_case': target, 'material_case': material, 'frames': [],
            **{k: obs[k] for k in ['section_pass', 'selected_edges_pass', 'unselected_edges_pass', 'retained_planes_pass']},
            'normal_NC_guard_rejection': metrics['target_NC_guard_rejection'], 'existing_actual_material_reused': True})
    write(out/'summary.json', {'schema': 'genlaw.selected-chamfer-review-summary.v1', 'normal_cases': normals,
        'controls': controls, 'input_rejections': 21, 'NC_rejections': 8,
        'acceptance': READ(INPUTS/'specifications.json')['cases'][0]['acceptance'],
        'qualified_scope': 'One or two parallel full 45 degree top edges on four synthetic box coupons',
        'whole_real_card_complete': False, 'production_approved': False,
        'real_C3_part_size_tested': False, 'new_machine_models': 0, 'new_material_physics': 0})
    write(out/'geometry-delivery.json', delivery)


def finish(out, qa):
    (out/'thumbnails').mkdir(); captures = []
    for key in NORMALS+CONTROLS:
        source = qa/'thumbnails'/(key+'.png'); resource = qa/('thumbnail-'+key+'-resources.json')
        value = READ(resource)
        if not value['ok'] or value['wall_limit_seconds'] != 35 or value['rss_limit_bytes'] != 2*1024**3:
            raise ValueError('Missing actual bounded browser capture')
        shutil.copyfile(source, out/'thumbnails'/source.name); captures.append({'image': file_binding(source), 'resource': file_binding(resource)})
    write(out/'thumbnail-provenance.json', {'actual_CAD_and_material': True, 'captures': captures})
    files = {str(p.relative_to(out)): {'bytes': p.stat().st_size, 'sha256': digest(p)} for p in sorted(out.rglob('*')) if p.is_file()}
    total = sum(r['bytes'] for r in files.values())
    if total >= 45_000_000: raise ValueError('Review archive exceeds existing CDN budget')
    write(out/'manifest.json', {'schema': 'genlaw.selected-chamfer-review-archive.v1', 'files': files, 'bytes': total})
    print(json.dumps({'files': len(files), 'bytes': total}), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('mode', choices=['initialize', 'case', 'assemble', 'finish'])
    p.add_argument('--output', type=Path, required=True); p.add_argument('--case'); p.add_argument('--qa', type=Path)
    a = p.parse_args(); require_bounded_worker(); out = a.output.resolve(); out.relative_to(ROOT)
    if a.mode == 'initialize': initialize(out)
    elif a.mode == 'case': pack_case(out, a.case)
    elif a.mode == 'assemble': assemble(out)
    else: finish(out, a.qa.resolve())
