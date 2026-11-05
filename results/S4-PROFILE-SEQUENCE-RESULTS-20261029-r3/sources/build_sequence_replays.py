"""Actual timed NC states plus exact, already-measured operation checkpoints."""
import argparse,copy,json,math,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.programming import check_nc,verify_binding
from genlaw_pipeline.run import bounded,write,CAMOTICS,simulation_env
from genlaw_pipeline.simulation_engine import selected_engine
from tools.build_pattern_replays import export
load=lambda p:json.loads(p.read_text())


def sampling_times(checked):
    """Choose display samples; never use an estimated time as a step boundary."""
    ops=checked['operation_checks'];last=checked['step_map'][-1]['first_executable_block'];rapid=0.
    for point in checked['motion_trace']:
        if point['block']>=last:break
        if point['motion']=='G0':
            a=[0 if v is None else v for v in point['start']];b=[x if y is None else y for x,y in zip(a,point['end'])]
            rapid+=math.dist(a,b)/10000*60
    return [ops[0]['feed_time_seconds']*.55,sum(op['feed_time_seconds'] for op in ops[:-1])+rapid+.55*ops[-1]['feed_time_seconds']]


def build(runs,out,checkpoints_only=False):
    if not out.resolve().is_relative_to(ROOT):raise ValueError('Keep replay in project')
    out.mkdir(parents=True,exist_ok=False);suite=load(runs/'summary.json');engine=selected_engine();records=[];new=0;reused=0
    if not suite['all_regression_expectations_met']:raise ValueError('Suite must pass before presentation')
    for row in suite['normal_cases']:
        key=row['id'];source=runs/key;case=out/key;case.mkdir();plan=load(source/'execution-plan.json');sim=load(source/'simulation-result.json')
        checked=check_nc((source/'program.nc').read_text(),plan);times=[] if checkpoints_only else sampling_times(checked)
        shutil.copy2(source/'program.nc',case/'program.nc');config=copy.deepcopy(load(source/'simulation.camotics'));config['resolution']=.05
        if checkpoints_only:config['resolution']=sim['resolution_mm']
        write(case/'simulation.camotics',config);jobs=[] if checkpoints_only else [{'kind':'time_cutoff','time_seconds':times[0],'label':'面铣进行中'}]
        for checkpoint in load(source/'material-checkpoints.json')['checkpoints']:
            op=plan['operations'][checkpoint['completed_operations']-1]
            jobs.append({'kind':'cumulative_native_prefix','checkpoint_id':checkpoint['id'],'time_seconds':None,'label':op['feature_id']+' 完成 · 工步边界'})
        if not checkpoints_only:jobs.append({'kind':'time_cutoff','time_seconds':times[1],'label':'末工步加工中'})
        frames=[]
        for i,job in enumerate(jobs,1):
            label=f'frame-{i}';target=case/(label+'.stl')
            if job['kind']=='time_cutoff':
                seconds=round(job['time_seconds'],6);job['time_seconds']=seconds
                bounded(case,label,[CAMOTICS,'--threads','1','--reduce=false','--binary','--time',str(seconds),case/'simulation.camotics',target],simulation_env(),case/'simulation.camotics')
                resolution=.05;nc_hash=sim['nc_sha256_after'];new+=1
            else:
                original=source/'checkpoints'/job['checkpoint_id'];binding=load(original/'simulation-result.json');proof=load(original/'prefix-binding.json')
                verify_binding(original/'simulated.stl',binding['mesh_sha256']);verify_binding(original/'program.nc',proof['prefix_nc_sha256'])
                if proof['full_nc_sha256']!=sim['nc_sha256_after']:raise ValueError('Checkpoint belongs to a different full NC')
                shutil.copy2(original/'simulated.stl',target)
                for suffix in ['.log','-capacity.json','-resources.json']:shutil.copy2(original/('camotics'+suffix),case/(label+suffix))
                resolution=binding['resolution_mm'];nc_hash=proof['prefix_nc_sha256'];reused+=1
                job['prefix_binding_sha256']=digest(original/'prefix-binding.json');job['simulation_reused']=True
            verify_binding(case/'program.nc',sim['nc_sha256_after']);verify_binding(CAMOTICS,engine['manifest']['binary_sha256'])
            frames.append({**job,'index':i,'resolution_mm':resolution,'stl':target.name,'stl_sha256':digest(target),
                'nc_sha256':nc_hash,'full_nc_sha256':sim['nc_sha256_after']})
        write(case/'capture.json',{'schema':'genlaw.sequence-replay.v1','case':key,'final_run':str(source.relative_to(ROOT)),
            'nc_sha256':sim['nc_sha256_after'],'project_sha256':digest(case/'simulation.camotics'),'final_mesh_sha256':sim['mesh_sha256'],
            'final_resolution_mm':sim['resolution_mm'],'stock':config['workpiece']['bounds'],'engine_binary_sha256':engine['manifest']['binary_sha256'],
            'frames':frames,'final_result_reexecuted':False,'production_approved':False,
            **({'compact_first_state_display':True} if checkpoints_only else {}),
            'time_sample_selection':'Display-only estimated sample locations; the engine executes each actual cutoff. Step boundaries use proved cumulative native prefixes, never approximate time.',
            'rapid_estimate_mm_min':10000})
        bounded(case,'export',[sys.executable,Path(__file__),'--export',case]);records.append(load(case/'frames.json'))
    write(out/'summary.json',{'schema':'genlaw.sequence-replay-set.v1','id':out.name,'cases':records,
        'new_intermediate_simulations':new,'reused_exact_material_checkpoints':reused,'production_approved':False})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runs',type=Path);p.add_argument('--output',type=Path);p.add_argument('--export',type=Path);p.add_argument('--checkpoints-only',action='store_true');a=p.parse_args()
    if a.export:export(a.export.resolve())
    elif a.runs and a.output:build(a.runs.resolve(),a.output.resolve(),a.checkpoints_only)
    else:p.error('Supply --runs / --output or --export')
