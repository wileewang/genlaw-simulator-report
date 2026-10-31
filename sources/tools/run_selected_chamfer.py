"""Run one archived selected-edge case; never repeat a started native failure."""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.cell_windows import file_binding, checked_file
from genlaw_pipeline.selected_chamfer_preparation import checked_archive
from genlaw_pipeline.selected_chamfer_nc import check_nc
from genlaw_pipeline.native_stream import run as native_run
from genlaw_pipeline.sparse_material import checked_resources
from genlaw_pipeline.run import write, FREECAD, workspace_environment

READ = lambda p: json.loads(Path(p).read_text())
WORKER = ROOT/'tools/selected_chamfer_cam_worker.py'


def cam(entry, out):
    entry = entry.resolve(); out = out.resolve(); out.relative_to(ROOT)
    archive = file_binding(entry.parent.parent/'manifest.json'); plan, admission = checked_archive(entry, archive)
    out.mkdir(parents=True, exist_ok=False); write(out/'preparation-result.json', plan); write(out/'archive-admission.json', admission)
    sources = [Path(__file__), WORKER, *[ROOT/'genlaw_pipeline'/n for n in [
        'selected_chamfer_geometry.py', 'selected_chamfer_preparation.py', 'selected_chamfer_nc.py',
        'selected_chamfer_measurement.py', 'indexed_sections.py', 'reference_sections.py',
        'verification.py', 'freecad_deburr.py', 'sequence_nc.py', 'native_stock.py']],
        ROOT/'web/tools/mesh_metrics.py']
    frozen = []
    for source in sources:
        dest = out/'sources'/source.relative_to(ROOT); dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest); frozen.append(file_binding(dest))
    write(out/'request.json', {'input': str(entry), 'output': str(out), 'archive': archive,
        'execution_sources': [file_binding(p) for p in sources], 'preserved_sources': frozen})
    env = workspace_environment(); env.update(QT_QPA_PLATFORM='offscreen', GENLAW_SELECTED_CHAMFER_CAM=str(out/'request.json'))
    admitted_bounded(out, 'freecad', [str(FREECAD), 'freecadcmd', str(WORKER)], env)
    if not (out/'cam-result.json').is_file() or READ(out/'cam-result.json')['status'] != 'native_generated_and_checked':
        raise ValueError('CAM incomplete; retain and inspect native log/cam-failure.json')
    print(json.dumps({'native_CAM_completed': plan['specification']['id'], 'geometry_measured': False}), flush=True)


def material(out, build):
    out = out.resolve(); plan = READ(out/'preparation-result.json'); request = READ(out/'request.json')
    if checked_archive(request['input'], request['archive'])[0] != plan: raise ValueError('Archived input changed')
    for b in request['execution_sources']+request['preserved_sources']: checked_file(b)
    checked_resources(out/'freecad-resources.json', [str(FREECAD), 'freecadcmd', str(WORKER)])
    result = READ(out/'cam-result.json')
    if (result['status'] != 'native_generated_and_checked' or result['NC'] != file_binding(out/'program.nc')
            or check_nc((out/'program.nc').read_text(), plan) != result['nc_check']):
        raise ValueError('Require unchanged checked native NC')
    project = out/'simulation.camotics'
    if project.exists(): raise ValueError('Prior material attempt exists; inspect actual owner/results before recovery')
    write(project, plan['simulation_project']); native_run(project, build.resolve(), plan['window'], out/'material')
    print(json.dumps({'native_material_completed': plan['specification']['id'], 'geometry_measured': False}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('mode', choices=['cam', 'material'])
    parser.add_argument('--input', type=Path); parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--build', type=Path); args = parser.parse_args()
    if args.mode == 'cam':
        if args.input is None: parser.error('--input required for cam')
        cam(args.input, args.output)
    else:
        if args.build is None: parser.error('--build required for material')
        material(args.output, args.build)
