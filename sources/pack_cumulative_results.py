"""Package measured cumulative cases and representative actual stage replay."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'web/tools')]
from genlaw_pipeline.cell_windows import checked_file, file_binding
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.run import write
from mesh_metrics import require_bounded_worker, load_mesh, write_indexed
from tools.pack_pocket_sequence import compressed

CP = ROOT / 'docs/checkpoints/2026-10-31/cumulative-cam-01'
RUNS = ROOT / 'validation/runs/S4-CUMULATIVE-CAM-20261031-r1'
INPUTS = ROOT / 'validation/feature-inputs/S4-CUMULATIVE-CAM-20261031-r1'
GEOMETRY = ROOT / 'validation/feature-inputs/S4-CUMULATIVE-TARGET-20261031-r1'
NORMALS = ['baseline', 'larger-round', 'wider-bottom', 'shifted-datum']
CONTROLS = ['omitted-round', 'wrong-second-setup', 'omitted-chamfer']
READ = lambda p: json.loads(Path(p).read_text())


def initialize(out, cad):
    suite = READ(RUNS / 'summary.json')
    if (suite['status'] != 'four_normal_three_controls_qualified'
            or [r['id'] for r in suite['normal_cases']] != NORMALS
            or [r['id'] for r in suite['controls']] != CONTROLS
            or suite['whole_real_card_complete'] or suite['production_approved']):
        raise ValueError('Require a qualified, accurately scoped suite')
    for directory in [INPUTS, GEOMETRY]:
        for binding in READ(directory / 'manifest.json')['files']:
            checked_file(binding)
    display = READ(cad / 'result.json')
    if display['suite'] != file_binding(RUNS / 'summary.json') or display['used_for_measurement']:
        raise ValueError('Display CAD is not bound to this suite')
    for row in display['cases']:
        for field in ['original_CAD', 'display', 'display_record']:
            checked_file(row[field])
    out.mkdir(parents=True, exist_ok=False)
    for source, name in [(RUNS / 'summary.json', 'suite.json'), (cad / 'result.json', 'display-cad.json'),
                         (INPUTS / 'manifest.json', 'inputs-manifest.json'),
                         (CP / 'execution-paper-review.json', 'paper-review.json'),
                         (CP / 'material-cohort-01/guards/result.json', 'input-and-NC-checks.json')]:
        shutil.copy2(source, out / name)
    (out / 'sources').mkdir()
    for source in [Path(__file__), ROOT / 'tools/export_cumulative_review_cad.py',
                   ROOT / 'tools/summarize_cumulative_cohort.py']:
        shutil.copy2(source, out / 'sources' / source.name)
    with zipfile.ZipFile(out / 'input-cards-and-cad.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for key in NORMALS:
            for directory, label in [(INPUTS / key, 'execution'), (GEOMETRY / key, 'geometry')]:
                for source in sorted(directory.rglob('*')):
                    if source.is_file() and 'negative-control-only' not in source.parts:
                        archive.write(source, str(Path(key) / label / source.relative_to(directory)))
    write(out / 'archive-scope.json', {'schema': 'genlaw.cumulative-review-scope.v1',
        'development_dimension': 'part_features_and_operation_composition',
        'customer_original_documents_included': False, 'representative_replay': 'baseline',
        'all_final_actual_surfaces_included': True, 'intermediate_actual_surfaces_included_for': ['baseline'],
        'all_intermediate_numeric_reports_included': True, 'new_simulations_for_review': 0,
        'other_intermediate_surfaces_preserved_in_private_qualified_archives': True,
        'whole_real_card_complete': False, 'production_approved': False,
        'new_machine_models': 0, 'new_material_physics': 0})


def pack_case(out, key):
    suite = READ(out / 'suite.json')
    row = next(r for r in suite['normal_cases'] + suite['controls'] if r['id'] == key)
    normal = key in NORMALS
    parent = key if normal else 'baseline'
    case = out / key
    case.mkdir()
    records, delivery, equivalence, raw_geometry = {}, {}, {}, {}
    payloads = {}
    for binding in suite['geometry_archives']:
        manifest = READ(checked_file(binding))
        for material in manifest['stages']:
            surface = material['original_complete_surface']
            payloads[(surface['path'], surface['sha256'])] = (binding, material)

    def copy(source, name):
        source = Path(source)
        dest = case / name
        if dest.exists():
            raise ValueError('Duplicate review archive target: ' + name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix == '.json' and source.stat().st_size > 300_000:
            dest = dest.with_name(dest.name + '.gz')
            transport = compressed(source, dest)
        else:
            shutil.copy2(source, dest)
            transport = None
        records[str(source.relative_to(ROOT))] = {
            'path': str(dest.relative_to(out)), 'source_sha256': digest(source), 'transport': transport}

    def geometry(source, name):
        vertices, faces, inspection = load_mesh(source)
        if inspection['exact_duplicate_faces_removed']:
            raise ValueError('Do not discard original surface faces')
        temp = case / (name + '.mesh')
        write_indexed(temp, vertices, faces)
        record = compressed(temp, case / (name + '.mesh.gz'))
        record['path'] = key + '/' + name + '.mesh.gz'
        delivery[key + '/' + name + '.mesh'] = record
        equivalence[name] = {'source': file_binding(source), 'inspection': inspection,
            'decoded_display_sha256': digest(temp), 'vertices_moved': False, 'faces_discarded': False}
        temp.unlink()

    for source in (INPUTS / parent).iterdir():
        if source.is_file():
            copy(source, 'input/' + source.name)
    for source in (GEOMETRY / parent).rglob('*'):
        if source.is_file() and 'negative-control-only' not in source.parts:
            copy(source, 'input/geometry/' + str(source.relative_to(GEOMETRY / parent)))
    qualification = checked_file(row['qualification'])
    copy(qualification, 'run/qualification.json')
    display_row = next(r for r in READ(out / 'display-cad.json')['cases'] if r['id'] == parent)
    reference = row['states'][-1]['reference_CAD'] if normal else row['reference_CAD']
    if reference != display_row['original_CAD']:
        raise ValueError('Wrong independent target display')
    geometry(checked_file(display_row['display']), 'target')
    copy(checked_file(display_row['display_record']), 'run/target-display.json')
    if normal:
        run = RUNS / key
        for name in ['preparation-result.json', 'current-native-CAD-check.json', 'cam-result.json',
                     'result.json', 'request.json', 'freecad-resources.json']:
            copy(run / name, 'run/' + name)
        nc_checks = []
        for stage in 'ABCD':
            for name in ['program.nc', 'simulation.camotics', 'execution-plan.json', 'result.json',
                         'cam-job.FCStd', 'native-path.json', 'prepared-path.json']:
                source = run / stage / name
                if source.exists():
                    copy(source, 'programs/' + stage + '/' + name)
            cam = READ(run / stage / 'result.json')
            nc_checks.append({'stage_id': stage, 'source': file_binding(run / stage / 'result.json'),
                'native_operation': cam['native_operation'], 'NC': cam['NC'],
                'CAM_uses_full_incoming_CAD': cam['CAM_uses_full_incoming_CAD'],
                'CAM_uses_full_cumulative_target_CAD': cam['CAM_uses_full_cumulative_target_CAD'],
                'NC_text_modified': cam['NC_text_modified'],
                'checks': {k: v for k, v in cam['nc_check'].items() if k != 'motion_trace'}})
        write(case / 'run/nc-checks.json', {'schema': 'genlaw.cumulative-nc-review.v1', 'stages': nc_checks,
            'complete_motion_traces_preserved_in_original_stage_results': True,
            'note': 'Readable projection of the original checks; no new NC generation or numerical validation.'})
        stages = row['states']
    else:
        stages = [row]
    frames = []
    observations = []
    for stage in stages:
        stage_id = stage['stage_id']
        metrics = READ(checked_file(stage['metrics']))
        inspection = READ(checked_file(metrics['inspection']))
        if metrics['geometry_pass'] != normal or not metrics['numerical_stability_pass']:
            raise ValueError('Measured result changed')
        dest = 'run/measurement/' + stage_id + '/'
        copy(checked_file(stage['metrics']), dest + 'metrics.json')
        copy(checked_file(metrics['inspection']), dest + 'inspection.json')
        copy(checked_file(inspection['request']), dest + 'request.json')
        for part in metrics['parts']:
            copy(checked_file(part), dest + Path(part['path']).name)
        material = READ(checked_file(stage['material']))
        copy(checked_file(stage['material']), dest + 'material-result.json')
        copy(checked_file(material['request']), dest + 'material-request.json')
        material_request = READ(checked_file(material['request']))
        copy(checked_file(material_request['stages']), dest + 'executed-stages.json')
        if key == 'baseline' or stage_id == 'D':
            source = checked_file(stage['surface'])
            name = 'actual' if stage_id == 'D' else stage_id
            geometry(source, name)
            archive_binding, raw = payloads[(stage['surface']['path'], stage['surface']['sha256'])]
            payload = checked_file(raw['payload'])
            if (raw['transport']['decoded_sha256'] != stage['surface']['sha256']
                    or raw['transport']['decoded_bytes'] != source.stat().st_size
                    or not raw['every_original_shard_reconstruction_sha256_checked']):
                raise ValueError('Existing lossless payload is not the measured complete material')
            copy(payload, name + '.stl.gz')
            if digest(case / (name + '.stl.gz')) != raw['payload']['sha256']:
                raise ValueError('Lossless material payload changed while copying')
            raw_geometry[name] = {'qualified_archive': archive_binding, 'payload': raw['payload'],
                'transport': raw['transport'], 'archive_path': key + '/' + name + '.stl.gz',
                'recompressed': False}
            if stage_id != 'D':
                frames.append({'mesh': key + '/' + name + '.mesh', 'label': {
                    'A': 'A 第一面面铣完成', 'B': 'B 两条上侧圆角完成',
                    'C': 'C 翻面后的另一面面铣完成'}[stage_id],
                    'kind': 'cumulative_native_prefix', 'time_seconds': None, 'resolution_mm': .025,
                    'fine_checkpoint_acceptance': True})
        observations.append({'stage_id': stage_id, 'iou_percent': stage['iou_percent'],
                             'geometry_pass': metrics['geometry_pass'],
                             'feature_checks': {k: v for k, v in inspection['feature_observations'].items()
                                                if k.endswith('_pass')},
                             'metrics': key + '/' + dest + 'metrics.json',
                             'inspection': key + '/' + dest + 'inspection.json'})
    spec = next(r['specification'] for r in suite['normal_cases'] if r['id'] == parent)
    summary = {'id': key, 'name': row['name'] if normal else {
        'omitted-round': '漏掉第一面圆角', 'wrong-second-setup': '第二次装夹姿态错误',
        'omitted-chamfer': '漏掉末步倒角'}[key],
        'case_kind': 'normal' if normal else 'negative_control', 'geometry_pass': normal,
        'iou_percent': stages[-1]['iou_percent'], 'numerical_stability_pass': True,
        'specification': spec, 'resolution_mm': .025, 'frames': frames, 'states': observations,
        'new_native_material_for_this_control': row.get('new_native_material') if not normal else None,
        'original_final_CAD': key + '/input/geometry/D/target.step',
        'paper_card': key + '/input/process-card.pdf'}
    write(case / 'archive-bindings.json', {'copied_sources': records, 'display_equivalence': equivalence,
        'raw_geometry': raw_geometry, 'geometry_delivery': delivery, 'summary': summary})
    print(json.dumps({'packed': key, 'actual_surfaces': len(equivalence) - 1}), flush=True)


def assemble(out):
    rows, delivery = [], {}
    for key in NORMALS + CONTROLS:
        record = READ(out / key / 'archive-bindings.json')
        rows.append(record['summary'])
        delivery.update(record['geometry_delivery'])
    write(out / 'summary.json', {'schema': 'genlaw.cumulative-review-summary.v1',
        'normal_cases': rows[:4], 'controls': rows[4:], 'input_and_NC_rejections': 20,
        'software_acceptance': READ(out / 'suite.json')['software_acceptance'],
        'representative_id': 'baseline', 'representative_actual_states_including_stock': 5,
        'qualified_scope': 'Four synthetic cumulative facing, round, static-flip facing and selected-chamfer cases',
        'whole_real_card_complete': False, 'production_approved': False,
        'new_machine_models': 0, 'new_material_physics': 0})
    write(out / 'geometry-delivery.json', delivery)
    total = sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
    if total >= 43_000_000:
        raise ValueError('Reserve 2 MB for thumbnails within the 45 MB CDN budget')
    print(json.dumps({'archive_bytes_before_thumbnails': total, 'remaining_to_45MB': 45_000_000 - total}))


def finish(out, qa):
    (out / 'thumbnails').mkdir()
    captures = []
    for key in NORMALS + CONTROLS:
        source = qa / 'thumbnails' / (key + '.png')
        resource = qa / ('thumbnail-' + key + '-resources.json')
        record = READ(resource)
        if not record['ok'] or record['wall_limit_seconds'] != 35 or record['rss_limit_bytes'] != 2 * 1024**3:
            raise ValueError('Require bounded actual browser captures')
        shutil.copy2(source, out / 'thumbnails' / source.name)
        captures.append({'id': key, 'image': file_binding(source), 'resources': file_binding(resource)})
    write(out / 'thumbnail-provenance.json', {'actual_CAD_and_native_output': True, 'captures': captures})
    files = {str(p.relative_to(out)): {'bytes': p.stat().st_size, 'sha256': digest(p)}
             for p in sorted(out.rglob('*')) if p.is_file()}
    total = sum(row['bytes'] for row in files.values())
    if total >= 45_000_000:
        raise ValueError('Immutable archive exceeds the unchanged 45 MB CDN budget')
    write(out / 'manifest.json', {'schema': 'genlaw.cumulative-review-archive.v1', 'files': files, 'bytes': total})
    print(json.dumps({'archive_files': len(files), 'archive_bytes': total}), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['initialize', 'case', 'assemble', 'finish'])
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--cad', type=Path)
    p.add_argument('--qa', type=Path)
    p.add_argument('--case', choices=NORMALS + CONTROLS)
    a = p.parse_args()
    require_bounded_worker()
    out = a.output.resolve()
    out.relative_to(ROOT)
    if a.mode == 'initialize':
        initialize(out, a.cad.resolve())
    elif a.mode == 'case':
        pack_case(out, a.case)
    elif a.mode == 'assemble':
        assemble(out)
    else:
        finish(out, a.qa.resolve())
