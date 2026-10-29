"""Actual 0.05 mm operation-boundary playback from native cumulative NC exports.

These are explicitly display captures. They do not replace 0.025 mm quantitative
final validation or claim the original fine-resolution checkpoint gate passed.
"""
import argparse,copy,json,math,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.programming import checked_plan,verify_binding
from genlaw_pipeline.sequence_nc import check_prefix
from genlaw_pipeline.run import bounded,write
from genlaw_pipeline.cell_windows import inspect_and_plan,run_window,file_binding,checked_file

LOAD=lambda p:json.loads(p.read_text())


def postprocess(plan,case):
    from genlaw_pipeline.sharded_surface import audit_window
    from genlaw_pipeline.planar_display import compact_window
    audit_window(plan,case/'result.json',case/'audit.json')
    compact_window(plan,case/'result.json',case/'audit.json',case/'display.json')


def export(case):
    from genlaw_pipeline.planar_display import combine
    combine(case/'surface.json',sorted(case.glob('windows/window-*/display.json')),case/'actual.mesh',case/'display.json')


def build(run,out,engine,features):
    run,out,engine,features=[p.resolve() for p in (run,out,engine,features)]
    for p in (run,out,engine,features):p.relative_to(ROOT)
    out.mkdir(parents=True,exist_ok=False)
    final=LOAD(run/'run-result.json');report=LOAD(features);feature_plan=LOAD(checked_file(report['plan']))
    if (final['status']!='original_size_global_geometry_measured' or not report['all_software_geometry_checks_passed']
            or feature_plan['reference']!=file_binding(run/'native-geometry.json') or final['engine_manifest']!=file_binding(engine)):
        raise ValueError('Playback requires the completed original final CAD/geometry checks')
    request=LOAD(run/'request.json');plan=checked_plan(request['input'],run/'preparation-result.json');cam=LOAD(run/'cam-result.json')
    verify_binding(run/'program.nc',cam['nc_sha256']);config=LOAD(run/'simulation.camotics')
    frames=[];volumes=[math.prod(b-a for a,b in zip(config['workpiece']['bounds']['min'],config['workpiece']['bounds']['max']))]
    for index,record in enumerate(cam['checkpoint_prefixes'],1):
        source=run/'checkpoints'/record['id'];proof=LOAD(source/'prefix-binding.json')
        verify_binding(source/'prefix-binding.json',record['prefix_binding_sha256'])
        verify_binding(source/'program.nc',proof['prefix_nc_sha256'])
        verify_binding(source/'native-geometry.json',proof['expected_native_geometry_sha256'])
        observed=check_prefix((source/'program.nc').read_text(),(run/'program.nc').read_text(),plan,index)
        if any(proof.get(k)!=v for k,v in observed.items()) or proof['full_nc_sha256']!=cam['nc_sha256']:
            raise ValueError('Playback prefix does not match the unchanged original job')
        case=out/record['id'];case.mkdir();shutil.copy2(source/'program.nc',case/'program.nc')
        shutil.copy2(source/'prefix-binding.json',case/'prefix-binding.json')
        capture_config=copy.deepcopy(config);capture_config['resolution']=.05;write(case/'simulation.camotics',capture_config)
        cell_plan=inspect_and_plan(case/'simulation.camotics',engine,case/'windows')
        count=len(LOAD(cell_plan)['windows'])
        for i in range(count):
            window=cell_plan.parent/f'window-{i:03d}';run_window(cell_plan,i,window)
            bounded(window,'postprocess',[sys.executable,Path(__file__),'--postprocess',window,'--plan',cell_plan])
            print(json.dumps({'replay_boundary':record['id'],'completed_windows':i+1,'total':count}),flush=True)
        bounded(case,'aggregate',[sys.executable,ROOT/'genlaw_pipeline/sharded_surface.py','aggregate',
            '--plan',cell_plan,'--directory',cell_plan.parent,'--output',case/'surface.json'])
        bounded(case,'display',[sys.executable,Path(__file__),'--export',case])
        volume=LOAD(case/'surface.json')['volume_mm3'];volumes.append(volume)
        feature=plan['operations'][index-1]['feature_id']
        frame={'id':record['id'],'kind':'cumulative_native_prefix','time_seconds':None,
            'label':('面铣完成' if index==1 else feature+' 铣孔完成'),'completed_operations':index,
            'resolution_mm':.05,'display_only':True,'fine_checkpoint_acceptance':False,
            'mesh':file_binding(case/'actual.mesh'),'surface':file_binding(case/'surface.json'),
            'display':file_binding(case/'display.json'),'prefix_binding':file_binding(case/'prefix-binding.json'),
            'nc':file_binding(case/'program.nc'),'full_nc_sha256':cam['nc_sha256'],'actual_volume_mm3':volume}
        write(case/'frame.json',frame);frames.append(frame)
    volumes.append(LOAD(run/'surface.json')['volume_mm3'])
    if not all(a>b for a,b in zip(volumes,volumes[1:])):raise ValueError('Actual playback material volume is not decreasing')
    write(out/'frames.json',{'schema':'genlaw.window-replay.v1','frames':frames,'final_run':file_binding(run/'run-result.json'),
        'final_surface':file_binding(run/'surface.json'),'full_nc':file_binding(run/'program.nc'),
        'engine_manifest':file_binding(engine),'actual_volumes_mm3':volumes,'material_volume_monotonically_decreases':True,
        'final_resolution_mm':.025,'middle_resolution_mm':.05,'time_invented':False,
        'fine_checkpoint_acceptance':False,'production_approved':False,'source_code':file_binding(Path(__file__))})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('run','output','engine','features','postprocess','plan','export'):p.add_argument('--'+name,type=Path)
    a=p.parse_args()
    if a.postprocess and a.plan:postprocess(a.plan.resolve(),a.postprocess.resolve())
    elif a.export:export(a.export.resolve())
    elif all((a.run,a.output,a.engine,a.features)):build(a.run,a.output,a.engine,a.features)
    else:p.error('Supply run / output / engine / features, or one worker mode')
