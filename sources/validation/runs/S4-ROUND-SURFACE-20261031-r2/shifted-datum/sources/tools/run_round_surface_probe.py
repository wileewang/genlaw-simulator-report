"""Run one archived round coupon; each native step is bounded and never retried."""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.cell_windows import checked_file,file_binding
from genlaw_pipeline.round_surface_preparation import prepare
from genlaw_pipeline.round_surface_nc import check_nc
from genlaw_pipeline.run import write,FREECAD,workspace_environment
from genlaw_pipeline.native_stream import run as native_run
from genlaw_pipeline.sparse_material import checked_resources

READ=lambda p:json.loads(Path(p).read_text())


def cam(input_path,out):
    input_path=input_path.resolve();out=out.resolve();out.relative_to(ROOT)
    archive_path=input_path.parent.parent/'manifest.json';archive=READ(archive_path)
    if archive['schema']!='genlaw.round-surface-input-archive.v1' or not archive['all_inputs_saved_before_NC']:
        raise ValueError('Archive original paper and CAD before native CAM')
    for binding in archive['files']:checked_file(binding)
    out.mkdir(parents=True,exist_ok=False);plan=prepare(input_path)
    write(out/'preparation-result.json',plan)
    sources=[ROOT/name for name in ['tools/run_round_surface_probe.py','tools/round_surface_cam_worker.py',
        'genlaw_pipeline/round_surface_geometry.py','genlaw_pipeline/round_surface_preparation.py',
        'genlaw_pipeline/round_surface_nc.py','genlaw_pipeline/surface_feed_adapter.py',
        'genlaw_pipeline/sequence_nc.py','genlaw_pipeline/native_stock.py']]
    frozen=[]
    for source in sources:
        dest=out/'sources'/source.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,dest);frozen.append(file_binding(dest))
    write(out/'request.json',{'input':str(input_path),'output':str(out),'archive':file_binding(archive_path),
        'execution_sources':[file_binding(p) for p in sources],'preserved_sources':frozen})
    env=workspace_environment();env.update(QT_QPA_PLATFORM='offscreen',GENLAW_ROUND_CAM_REQUEST=str(out/'request.json'))
    command=[str(FREECAD),'freecadcmd',str(ROOT/'tools/round_surface_cam_worker.py')]
    admitted_bounded(out,'freecad',command,env)
    if not (out/'cam-result.json').is_file():raise ValueError('FreeCAD did not complete CAM; retain native output and inspect cam-failure.json')
    if READ(out/'cam-result.json')['status']!='native_generated_and_checked':raise ValueError('Native CAM incomplete')
    print(json.dumps({'native_CAM_completed':True,'case':plan['specification']['id'],'output':str(out)}),flush=True)


def material(out,build):
    out=out.resolve();plan=READ(out/'preparation-result.json');request=READ(out/'request.json')
    if prepare(checked_file(plan['input']))!=plan:raise ValueError('Archived round inputs changed')
    for binding in request['execution_sources']+request['preserved_sources']:checked_file(binding)
    command=[str(FREECAD),'freecadcmd',str(ROOT/'tools/round_surface_cam_worker.py')]
    checked_resources(out/'freecad-resources.json',command)
    cam_result=READ(out/'cam-result.json')
    if cam_result['status']!='native_generated_and_checked' or cam_result['NC']!=file_binding(out/'program.nc'):
        raise ValueError('Require unchanged checked native NC')
    if check_nc((out/'program.nc').read_text(),plan)!=cam_result['nc_check']:raise ValueError('NC evidence changed')
    project=out/'simulation.camotics'
    if project.exists():raise ValueError('Retain any prior material attempt and inspect before continuing')
    write(project,plan['simulation_project'])
    native_run(project,build,plan['window'],out/'material',500000)
    print(json.dumps({'native_material_executed':True,'case':plan['specification']['id'],
                      'geometry_measured':False}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['cam','material'])
    p.add_argument('--input',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--build',type=Path);a=p.parse_args()
    if a.mode=='cam':
        if a.input is None:p.error('--input required for cam')
        cam(a.input,a.output)
    else:
        if a.build is None:p.error('--build required for material')
        material(a.output,a.build)
