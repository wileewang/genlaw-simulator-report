"""Representative true material replay at the same resolution as qualification."""
import argparse,json,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.programming import check_nc,simulation_project,verify_binding
from genlaw_pipeline.run import write,CAMOTICS,simulation_env
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.simulation_engine import selected_engine
load=lambda p:json.loads(p.read_text())


def build(runs,out):
    runs=runs.resolve();out=out.resolve()
    if not out.is_relative_to(ROOT) or not runs.is_relative_to(ROOT):raise ValueError('Keep replay in project')
    suite=load(runs/'summary.json')
    if suite['schema']!='genlaw.chamfer-suite.v1' or not suite['all_regression_expectations_met']:raise ValueError('Qualification must pass before replay')
    source=runs/'baseline';plan=load(source/'execution-plan.json');sim=load(source/'simulation-result.json');engine=selected_engine()
    if sim['engine_binary_sha256']!=engine['manifest']['binary_sha256']:raise ValueError('Simulation engine changed')
    original=source/'program.nc';verify_binding(original,sim['nc_sha256_after']);checked=check_nc(original.read_text(),plan)
    config=load(source/'simulation.camotics')
    if config!=simulation_project(plan):raise ValueError('Qualification configuration changed')
    out.mkdir(parents=True,exist_ok=False);case=out/'baseline';case.mkdir()
    shutil.copy2(original,case/'program.nc');shutil.copy2(source/'simulation.camotics',case/'simulation.camotics')
    frames=[]
    for index,(fraction,caption) in enumerate(zip([.25,.55,.85],['倒角前段','沿外周加工','接近闭合']),1):
        seconds=round(checked['feed_time_seconds']*fraction,6);label='frame-'+str(index);target=case/(label+'.stl')
        admitted_bounded(case,label,[CAMOTICS,'--threads','1','--reduce=false','--binary','--time',str(seconds),
            case/'simulation.camotics',target],simulation_env(),case/'simulation.camotics')
        verify_binding(case/'program.nc',sim['nc_sha256_after']);verify_binding(case/'simulation.camotics',sim['project_sha256'])
        verify_binding(CAMOTICS,sim['engine_binary_sha256'])
        frames.append({'index':index,'kind':'native_time_cutoff','time_seconds':seconds,'label':caption,
            'resolution_mm':config['resolution'],'stl':target.name,'stl_sha256':digest(target),'nc_sha256':sim['nc_sha256_after']})
    write(case/'capture.json',{'schema':'genlaw.chamfer-replay.v1','case':'baseline','final_run':str(source.relative_to(ROOT)),
        'nc_sha256':sim['nc_sha256_after'],'project_sha256':sim['project_sha256'],'final_mesh_sha256':sim['mesh_sha256'],
        'final_resolution_mm':sim['resolution_mm'],'stock':config['workpiece']['bounds'],'engine_binary_sha256':sim['engine_binary_sha256'],
        'frames':frames,'final_result_reexecuted':False,'production_approved':False,
        'time_sample_selection':'Feed-duration fractions choose display samples only; native CAMotics executes each actual cutoff. These are not exact edge or operation boundaries.',
        'resolution_reduced_for_replay':False,'source_code_sha256':digest(Path(__file__))})
    admitted_bounded(case,'export',[sys.executable,ROOT/'tools/build_pattern_replays.py','--export',case])
    write(out/'summary.json',{'schema':'genlaw.chamfer-replay-set.v1','representative_case':'baseline','cases':[load(case/'frames.json')],
        'new_intermediate_simulations':3,'final_result_reexecuted':False,'resolution_reduced':False,'production_approved':False})
    print(json.dumps({'status':'replay_ready','case':'baseline','intermediate_frames':3,'resolution_mm':config['resolution']}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--runs',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();build(a.runs,a.output)
