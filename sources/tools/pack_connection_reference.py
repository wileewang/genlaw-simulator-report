"""Archive the completed connection reference separately from the Pages site.

Run each phase through the existing bounded worker. Raw STL shards are shared
only when their complete source bytes have the same SHA-256; every compressed
payload is decoded and checked before it is accepted. Customer PDFs are absent.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools'), str(ROOT / 'web/tools')]
from genlaw_pipeline.cell_windows import checked_file, file_binding
from genlaw_pipeline.checkpoint_context import checked_context
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.run import write
from mesh_metrics import require_bounded_worker, load_mesh, write_indexed

LOAD = lambda path: json.loads(path.read_text())
LABELS = ['面铣完成', '圆头外形完成', 'H1 铣孔完成', 'H2 铣孔完成']


def copy_exact(source, destination, out, bindings):
    source = source.resolve(); source.relative_to(ROOT)
    if destination.exists():
        raise ValueError('Archive file already exists: ' + str(destination))
    if source.stat().st_size > 64 * 1024**2:
        raise ValueError('Archive file exceeds the existing output budget')
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    key = str(source.relative_to(ROOT))
    bindings[key] = {'path': str(destination.relative_to(out)), 'sha256': digest(source)}


def initialize(run, displays, out):
    require_bounded_worker()
    from connection_reference_stages import checked
    checked(run)
    completion = LOAD(run / 'completion.json')
    if (completion['status'] != 'reference_geometry_measured_with_requirement_results'
            or completion['production_approved'] or completion['whole_process_card_complete']):
        raise ValueError('Expected the completed single-piece reference')
    for name in ('final', 'checkpoints', 'software_geometry', 'manufacturing_requirements'):
        checked_file(completion[name])
    checkpoints = LOAD(checked_file(completion['checkpoints']))
    if (len(checkpoints['checkpoints']) != 4 or not checkpoints['original_stock_used']
            or checkpoints['expected_cad_used_as_stock']
            or not checkpoints['material_volume_monotonically_decreases']):
        raise ValueError('Cumulative material evidence is incomplete')
    input_path = checked_file(LOAD(run/'intent.json')['input']).parent
    if LOAD(input_path/'source-review.json')['raw_customer_documents_included']:
        raise ValueError('Do not include raw customer documents in this archive')
    out.mkdir(parents=True, exist_ok=False)
    bindings = {}
    copy = lambda source, name: copy_exact(source, out/name, out, bindings)
    for name, entry in LOAD(input_path/'manifest.json')['files'].items():
        if digest(input_path/name) != entry['sha256']:
            raise ValueError('Frozen input changed')
        copy(input_path/name, name)
    copy(input_path/'manifest.json', 'input-manifest.json')
    # Preserve original compact records verbatim; reproducible section arrays
    # and temporary display STL parts remain local and are explicitly listed.
    for folder, category in [(run, 'run'), (displays, 'display')]:
        for source in sorted(folder.rglob('*')):
            if source.is_file() and source.suffix in ('.json', '.nc', '.camotics', '.edges'):
                copy(source, 'evidence/'+category+'/'+str(source.relative_to(folder)))
    for name in ('completion.json', 'program.nc', 'simulation.camotics', 'execution-plan.json',
                 'preparation-result.json', 'cam-result.json', 'native-geometry.json',
                 'cam-job.FCStd', 'metrics.json', 'requirement-verification.json'):
        copy(run/name, name)
    copy(run/'features/verification.json', 'verification.json')
    intent = LOAD(run/'intent.json')
    for key, name in [('engine_manifest', 'engine-build.json'),
                      ('base_engine_regression', 'engine-regression.json'),
                      ('profile_window_regression', 'profile-window-regression.json')]:
        copy(checked_file(intent[key]), name)
    engine = LOAD(checked_file(intent['engine_manifest']))
    patches = [
        ('patches/camotics-1.2.0-vertical-conic-sweep.patch', engine['numerical_patch_sha256']),
        ('patches/camotics-1.2.0-horizontal-cylinder-sweep.patch', engine['horizontal_cylinder_patch_sha256']),
        (engine['grid_window_capability']['patch_ref'], engine['grid_window_capability']['patch_sha256'])]
    for name, sha in patches:
        source = ROOT/name
        if digest(source) != sha:
            raise ValueError('Native engine patch differs from the frozen build')
        copy(source, 'sources/'+name)
    for entry in intent['sources']:
        copy(checked_file(entry), 'sources/'+entry['path'])
    for source in [Path(__file__), ROOT/'web/tools/mesh_metrics.py', ROOT/'tools/build_window_display.py',
                   ROOT/'tools/build_connection_display.py', ROOT/'tools/split_connection_display.py',
                   ROOT/'tools/prepare_connection_delivery.py']:
        if str(source.relative_to(ROOT)) not in bindings:
            copy(source, 'sources/'+str(source.relative_to(ROOT)))
    recovery = run/'recovered-interruptions.json'
    if recovery.exists():
        for index, binding in enumerate(LOAD(recovery)['records']):
            inspection = checked_file(binding)
            copy(inspection, f'recovery/inspection-{index:02d}.json')
            record = LOAD(inspection)
            copy(checked_file(record['preserved_partial_budget']), f'recovery/unstarted-budget-{index:02d}.json')
            source = checked_file(record['recovery_source'])
            if str(source.relative_to(ROOT)) not in bindings:
                copy(source, 'sources/'+str(source.relative_to(ROOT)))
    native_target = LOAD(run/'target-display.json')
    if digest(run/'target-display.stl') != native_target['sha256']:
        raise ValueError('Native target display changed')
    vertices, faces, inspection = load_mesh(run/'target-display.stl')
    if inspection['exact_duplicate_faces_removed']:
        raise ValueError('Unexpected duplicate target display faces')
    write_indexed(out/'target.mesh', vertices, faces)
    write(out/'target-display-binding.json', {
        'source': file_binding(run/'target-display.stl'), 'native_cad_sha256': digest(input_path/'target.brep'),
        'mesh_sha256': digest(out/'target.mesh'), 'inspection': inspection,
        'used_as_measurement_reference': False})

    unique = {}; rows = []; states = []; browser_paths = {}
    cases = [('final', run, None)] + [(item['id'], run/'fine-checkpoints'/item['id'], item)
                                    for item in checkpoints['checkpoints']]
    for index, (key, case, item) in enumerate(cases):
        folder = '' if key == 'final' else key+'/'
        surface = LOAD(case/'surface.json'); report = LOAD(case/'features/verification.json')
        display = LOAD(displays/key/'display.json')
        metrics = LOAD(case/'metrics.json')
        if (not report['all_software_geometry_checks_passed'] or not surface['watertight']
                or not surface['all_global_cells_covered_once']
                or display['actual_surface'] != file_binding(case/'surface.json')
                or metrics['surface'] != display['actual_surface']
                or display['quantitative_mesh_modified'] or display['cad_used_to_generate_output']):
            raise ValueError('Result, display and measured surface do not agree')
        copy(checked_file(display['display']), folder+'actual.mesh')
        copy(displays/key/'display.json', folder+'display.json')
        parts = LOAD(displays/key/'browser/parts.json')
        if (parts['source_display_report'] != file_binding(displays/key/'display.json')
                or parts['source_mesh'] != display['display']
                or parts['triangles'] != display['triangles']
                or not parts['exact_ordered_float32_facets_preserved'] or parts['simplification_applied']):
            raise ValueError('Browser parts differ from the complete actual display')
        names = []; count = 0
        for j, part in enumerate(parts['parts']):
            if part['first_triangle'] != count or not 0 < part['triangles'] <= 800000:
                raise ValueError('Browser part is missing, repeated or too large')
            count += part['triangles']; name = f'display-parts/{key}/part-{j:03d}.mesh'
            copy(checked_file(part['file']), name); names.append(name)
        if count != display['triangles']:
            raise ValueError('Browser display does not cover the complete actual shape')
        copy(displays/key/'browser/parts.json', folder+'browser-parts.json')
        browser_paths[key] = names
        if key != 'final':
            result = LOAD(checked_file(item['result']))
            context = checked_context(checked_file(result['context']))
            if not result['fine_checkpoint_acceptance']:
                raise ValueError('Checkpoint is not accepted')
            for name in ('checkpoint-result.json', 'metrics.json', 'native-geometry.json', 'program.nc',
                         'software-policy.json', 'prefix-binding.json'):
                copy(case/name, folder+name)
            copy(case/'features/verification.json', folder+'verification.json')
            copy(checked_file(context['files']['oracle_cad']), folder+'expected.brep')
            states.append({'id': key, 'label': LABELS[index-1], 'kind': 'cumulative_native_prefix',
                'iou_percent': metrics['iou_percent'], 'checks_passed': len(report['software_regression']),
                'completed_features': context['completed_features'], 'resolution_mm': .025,
                'fine_checkpoint_acceptance': True, 'display_only': False,
                'mesh_path': folder+'actual.mesh', 'browser_mesh_paths': names,
                'mesh_sha256': digest(out/folder/'actual.mesh'),
                'mesh_triangles': display['triangles']})
        for entry in surface['windows']:
            native = LOAD(checked_file(entry['result'])); source = native['surface']
            raw = checked_file(source); sha = source['sha256']
            unique.setdefault(sha, {'source': source, 'path': 'raw-shards/'+sha+'.stl.gz',
                                    'decoded_bytes': raw.stat().st_size})
            rows.append({'case': key, 'index': native['index'], 'window': native['window'],
                         'source': source, 'result': entry['result'], 'payload_path': unique[sha]['path']})
    if len(rows) != 320:
        raise ValueError('Expected five states, each with all 64 original windows')
    write(out/'compression-plan.json', {'unique': list(unique.values()), 'rows': rows})
    write(out/'bindings.json', {'schema': 'genlaw.public-source-bindings.v1', 'files': bindings,
        'omitted_reproducible_files': ['sections.npy', 'intermediate planar display STL parts'],
        'raw_shard_mapping': 'each case raw-shards.json',
        'runtime_binary': 'engine-build.json identifies the frozen local engine; binary is not included'})
    report = LOAD(run/'features/verification.json'); requirements = LOAD(run/'requirement-verification.json')
    dimensions = []
    for item in requirements['requirements']:
        if 'criterion' not in item:
            continue
        obs = report['observations']['features'][item['feature_id']]
        band = obs['diameter']['radial_band_mm'] if item['kind'] == 'diameter' else obs['thickness_band_mm']
        dimensions.append({k: item[k] for k in ('id', 'feature_id', 'kind', 'nominal', 'status', 'criterion')} | {
            'observed_band_mm': band, 'numerical_guard_mm': obs['numerical_guard_mm'],
            'sample_count': obs['section_count'] if item['kind'] == 'diameter' else obs['probe_count'],
            'sample_type': 'sections' if item['kind'] == 'diameter' else 'thickness_probes'})
    if len(dimensions) != requirements['defined_numerical_requirements']:
        raise ValueError('Dimension display must include every defined numerical requirement')
    summary = {
        'schema': 'genlaw.connection-reference-result.v1',
        'id': 'S4-CONNECTION-REFERENCE-RESULTS-20261029-r1',
        'status': completion['status'], 'resolution_mm': .025,
        'stock_bounds_mm': LOAD(run/'execution-plan.json')['stock']['bounds_mm'],
        'executable_nc_blocks': LOAD(run/'cam-result.json')['nc_check']['executable_blocks'],
        'final': {'metrics': LOAD(run/'metrics.json'), 'checks_passed': len(report['software_regression']),
                  'display_triangles': LOAD(displays/'final/display.json')['triangles'],
                  'browser_mesh_paths': browser_paths['final']},
        'checkpoints': states, 'actual_volumes_mm3': checkpoints['actual_volumes_mm3'],
        'manufacturing_requirements': {
            'defined_count': requirements['defined_numerical_requirements'],
            'passed_count': requirements['status_counts'].get('numeric_pass', 0),
            'unmapped_count': len(requirements['unmapped_source_annotations']),
            'status_counts': requirements['status_counts'],
            'all_requirements_passed': requirements['all_requirements_passed'],
            'evaluated_dimensions': dimensions},
        'production_approved': False, 'whole_process_card_complete': False,
        'source': file_binding(run/'completion.json'), 'original_raw_shards': 320,
        'unique_raw_shards': len(unique), 'raw_customer_documents_included': False}
    write(out/'summary-draft.json', summary)
    print(json.dumps({'unique_raw_shards': len(unique), 'raw_instances': len(rows)}), flush=True)


def compress(out, start):
    require_bounded_worker(); plan = LOAD(out/'compression-plan.json')
    if type(start) is not int or start < 0 or start >= len(plan['unique']):
        raise ValueError('Invalid compression batch start')
    rows = []
    for item in plan['unique'][start:start+8]:
        raw = checked_file(item['source']); dest = out/item['path']
        dest.parent.mkdir(parents=True, exist_ok=True)
        with raw.open('rb') as source, dest.open('xb') as output:
            with gzip.GzipFile(filename='', mode='wb', fileobj=output, compresslevel=6, mtime=0) as zipped:
                shutil.copyfileobj(source, zipped, 1024*1024)
        decoded = hashlib.sha256(); size = 0
        with gzip.open(dest, 'rb') as stream:
            for chunk in iter(lambda: stream.read(1024*1024), b''):
                decoded.update(chunk); size += len(chunk)
        if decoded.hexdigest() != item['source']['sha256'] or size != item['decoded_bytes']:
            raise ValueError('Compressed raw mesh does not preserve the source bytes')
        rows.append({'source_sha256': item['source']['sha256'], 'path': item['path'],
                     'sha256': digest(dest), 'bytes': dest.stat().st_size, 'decoded_bytes': size})
    write(out/f'raw-shards/part-{start:03d}.json', rows)


def transport(out, key):
    require_bounded_worker()
    if key not in ('final', 'after-01', 'after-02', 'after-03', 'after-04'):
        raise ValueError('Unknown display state')
    summary = LOAD(out/'summary-draft.json')
    paths = summary['final']['browser_mesh_paths']+['target.mesh'] if key == 'final' else next(
        row['browser_mesh_paths'] for row in summary['checkpoints'] if row['id'] == key)
    records = {}
    folder = out/'geometry'; folder.mkdir(exist_ok=True)
    for name in paths:
        source = out/name; decoded_sha = digest(source)
        dest = folder/(decoded_sha+'.mesh.gz')
        if not dest.exists():
            with source.open('rb') as raw, dest.open('xb') as stream:
                with gzip.GzipFile(filename='', mode='wb', fileobj=stream, compresslevel=6, mtime=0) as zipped:
                    shutil.copyfileobj(raw, zipped, 1024*1024)
        observed = hashlib.sha256(); size = 0
        with gzip.open(dest, 'rb') as stream:
            for chunk in iter(lambda: stream.read(1024*1024), b''):
                observed.update(chunk); size += len(chunk)
        if observed.hexdigest() != decoded_sha or size != source.stat().st_size:
            raise ValueError('Display delivery differs from the archived geometry')
        records[name] = {'encoding': 'gzip', 'path': str(dest.relative_to(out)),
                         'sha256': digest(dest), 'bytes': dest.stat().st_size,
                         'decoded_sha256': decoded_sha, 'decoded_bytes': size}
    write(out/('transport-'+key+'.json'), records)


def finish(run, displays, out):
    require_bounded_worker(); plan = LOAD(out/'compression-plan.json')
    compressed = {}
    for path in sorted((out/'raw-shards').glob('part-*.json')):
        for row in LOAD(path):
            if row['source_sha256'] in compressed or digest(out/row['path']) != row['sha256']:
                raise ValueError('Duplicate or changed compressed payload')
            compressed[row['source_sha256']] = row
    if set(compressed) != {row['source']['sha256'] for row in plan['unique']}:
        raise ValueError('Raw mesh archive is incomplete')
    for key in ['final', 'after-01', 'after-02', 'after-03', 'after-04']:
        rows = [row.copy() for row in plan['rows'] if row['case'] == key]
        if [row['index'] for row in rows] != list(range(64)):
            raise ValueError('Missing or duplicate window')
        for row in rows:
            row['payload'] = compressed[row['source']['sha256']]
        folder = out if key == 'final' else out/key
        write(folder/'raw-shards.json', {'schema': 'genlaw.public-sharded-mesh.v1',
            'resolution_mm': .025, 'closed_as_a_whole': True, 'individual_shards_are_not_capped': True,
            'original_coordinates': True, 'payload_paths_relative_to_archive_root': True, 'files': rows})
    resources = []
    for folder in (run, displays):
        for path in sorted(folder.rglob('*-resources.json')):
            data = LOAD(path)
            if not data['success'] or len(data['cpu_affinity']) != 1:
                raise ValueError('Inspect unsuccessful resource evidence before publishing')
            resources.append({'source': file_binding(path), 'seconds': data['seconds'],
                              'peak_rss_mib': data['peak_observed_group_rss_bytes']/1024**2})
    write(out/'resources-summary.json', {'single_cpu': True, 'memory_limit_mib': 2048,
        'job_wall_limit_seconds': 60, 'jobs': resources,
        'maximum_job_seconds': max(row['seconds'] for row in resources),
        'maximum_job_rss_mib': max(row['peak_rss_mib'] for row in resources)})
    delivery = {}
    for key in ['final', 'after-01', 'after-02', 'after-03', 'after-04']:
        for name, row in LOAD(out/('transport-'+key+'.json')).items():
            if name in delivery or digest(out/row['path']) != row['sha256'] or digest(out/name) != row['decoded_sha256']:
                raise ValueError('Duplicate or changed display transport')
            delivery[name] = row
    summary = LOAD(out/'summary-draft.json')
    expected = {'target.mesh', *summary['final']['browser_mesh_paths']}
    expected.update(path for row in summary['checkpoints'] for path in row['browser_mesh_paths'])
    if set(delivery) != expected:
        raise ValueError('Display delivery is incomplete')
    write(out/'geometry-transport.json', {'schema': 'genlaw.display-transport.v1', 'files': delivery})
    write(out/'summary.json', LOAD(out/'summary-draft.json'))
    manifest = {str(path.relative_to(out)): {'sha256': digest(path), 'bytes': path.stat().st_size}
                for path in sorted(out.rglob('*')) if path.is_file() and path.name != 'manifest.json'}
    write(out/'manifest.json', {'schema': 'genlaw.result-archive.v1', 'files': manifest})
    print(json.dumps({'files': len(manifest), 'bytes': sum(row['bytes'] for row in manifest.values())}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['initialize', 'compress', 'transport', 'finish'])
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--displays', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--start', type=int)
    parser.add_argument('--case')
    args = parser.parse_args()
    run, displays, out = [path.resolve() for path in (args.run, args.displays, args.output)]
    for path in (run, displays, out):
        path.relative_to(ROOT)
    if args.phase == 'initialize': initialize(run, displays, out)
    elif args.phase == 'compress': compress(out, args.start)
    elif args.phase == 'transport': transport(out, args.case)
    else: finish(run, displays, out)
