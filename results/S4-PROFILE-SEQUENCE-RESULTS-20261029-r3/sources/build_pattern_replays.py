"""Capture true intermediate material states for drilling and planar milling."""
import argparse
import copy
import json
import math
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'web/tools'))
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.programming import verify_binding,check_nc
from genlaw_pipeline.drilling_geometry import operation_parameters
from genlaw_pipeline.run import write,CAMOTICS,simulation_env
from genlaw_pipeline.admission_wait import admitted_bounded as bounded
from genlaw_pipeline.simulation_engine import selected_engine
from mesh_metrics import load_mesh,write_indexed,require_bounded_worker
from tools.pack_feature_results import compress


def load(path):return json.loads(path.read_text())


def export(case):
    require_bounded_worker();record=load(case/'capture.json');source=ROOT/record['final_run']
    volumes=[math.prod(b-a for a,b in zip(record['stock']['min'],record['stock']['max']))]
    for frame in record['frames']:
        raw=case/frame['stl'];verify_binding(raw,frame['stl_sha256'])
        vertices,faces,inspection=load_mesh(raw);mesh=case/(raw.stem+'.mesh')
        if record.get('compact_first_state_display') and frame['index']==1:
            from genlaw_pipeline.planar_display import compact_points,signed_volume,edge_census
            import numpy as np
            original=vertices[faces].astype('<f4');compact,patches=compact_points(original)
            _,before=edge_census(original);_,after=edge_census(compact)
            difference=abs(signed_volume(original)-signed_volume(compact))
            if before!=after or difference>1e-6:raise ValueError('Display retessellation changed actual surface boundaries or volume')
            flat=np.ascontiguousarray(compact.reshape(-1,3));_,first,inverse=np.unique(flat.view('V12').reshape(-1),return_index=True,return_inverse=True)
            vertices=flat[first];faces=inverse.reshape(-1,3).astype('<u4')
            frame['display_retessellation']={'method':'Existing exact axis-aligned planar retessellation, display copy only.',
                'original_stl_sha256':frame['stl_sha256'],'original_triangles':len(original),'display_triangles':len(faces),
                'boundary_edges_identical':True,'absolute_signed_volume_difference_mm3':difference,
                'nonplanar_triangles_retained':True,'quantitative_mesh_modified':False,'patches':patches,
                'source_code_sha256':digest(ROOT/'genlaw_pipeline/planar_display.py'),'exporter_source_code_sha256':digest(Path(__file__))}
        write_indexed(mesh,vertices,faces);compress(raw,case/(raw.name+'.gz'))
        frame.update(mesh=mesh.name,mesh_sha256=digest(mesh),watertight=inspection['watertight'],
                     volume_mm3=inspection['volume_mm3'],triangles=inspection['triangles'])
        volumes.append(inspection['volume_mm3'])
    verify_binding(source/'simulated.stl',record['final_mesh_sha256'])
    volumes.append(load(source/'metrics.json')['actual_mesh']['volume_mm3'])
    if not all(a>b for a,b in zip(volumes,volumes[1:])):raise ValueError('Non-decreasing material states: '+str(volumes))
    record.update(volumes_mm3=volumes,material_volume_monotonically_decreases=True)
    write(case/'frames.json',record)
    print(json.dumps({'case':case.name,'volumes':volumes}),flush=True)


def build(runs,out):
    out=out.resolve()
    if not out.is_relative_to(ROOT):raise ValueError('Outputs must remain in project')
    out.mkdir(parents=True,exist_ok=False);suite=load(runs/'summary.json');engine=selected_engine();records=[]
    if not suite['all_regression_expectations_met']:raise ValueError('Review suite findings before building presentation')
    for row in suite['normal_cases']:
        key=row['id'];source=runs/key;case=out/key;case.mkdir()
        plan=load(source/'execution-plan.json');sim=load(source/'simulation-result.json');count=len(plan['operations'])
        milling=(plan['schema'],plan.get('capability')) in {
            ('genlaw.execution-plan.v4','rounded_pocket_xy'),('genlaw.execution-plan.v5','edge_step_xy'),
            ('genlaw.execution-plan.v6','planar_floor_xy'),('genlaw.execution-plan.v7','top_facing_xy'),
            ('genlaw.execution-plan.v8','helical_hole_milling_xy'),('genlaw.execution-plan.v10','capsule_profile_xy')}
        if not milling and any(op['type']!='drilling' for op in plan['operations']):raise ValueError('No replay adapter for this operation')
        shutil.copy2(source/'program.nc',case/'program.nc')
        config=copy.deepcopy(load(source/'simulation.camotics'));config['resolution']=.05
        write(case/'simulation.camotics',config)
        if milling:
            checked=check_nc((source/'program.nc').read_text(),plan)
            times=[checked['feed_time_seconds']*fraction for fraction in [.25,.55,.85]]
            captions=['早期去料','分层加工','接近完成']
            if plan['schema']=='genlaw.execution-plan.v8':captions=['螺旋进入','孔内材料清除','接近贯穿完成']
            if plan['schema']=='genlaw.execution-plan.v10':captions=['外轮廓初层','逐层清除四角','接近全厚贯穿']
        elif plan['schema']=='genlaw.execution-plan.v3':
            parameters=[operation_parameters(plan,op) for op in plan['operations']];top=plan['stock']['bounds_mm']['max'][2]
            air=[(p['retract_plane_mm']-top)/(p['feed_mm_min']/60) for p in parameters]
            cutting=[(top-p['final_tip_z_mm'])/(p['feed_mm_min']/60) for p in parameters]
            duration=[a+c for a,c in zip(air,cutting)]
            if count==1:
                times=[air[0]+f*cutting[0] for f in [.35,.65,.9]]
                captions=['钻尖进入','形成圆柱段','接近目标孔深']
            else:
                times=[air[0]+.4*cutting[0],duration[0]+.5*air[1],sum(duration[:-1])+air[-1]+.9*cutting[-1]]
                captions=['首孔钻削','首孔完成，转入下一孔','最后一孔接近目标深度']
        else:
            params=plan['parameters'];feed_seconds=(params['retract_plane_mm']-params['final_tip_z_mm'])/(params['feed_mm_min']/60)
            times=[feed_seconds*.55,feed_seconds*1.12,feed_seconds*(count-.12)]
            captions=['首孔钻削','首孔完成，继续加工','最后一孔钻削']
        frames=[]
        for index,(seconds,caption) in enumerate(zip(times,captions),1):
            seconds=round(seconds,6);label='frame-'+str(index);target=case/(label+'.stl')
            bounded(case,label,[CAMOTICS,'--threads','1','--reduce=false','--binary','--time',str(seconds),
                               case/'simulation.camotics',target],simulation_env(),case/'simulation.camotics')
            verify_binding(case/'program.nc',sim['nc_sha256_after']);verify_binding(CAMOTICS,engine['manifest']['binary_sha256'])
            frames.append({'index':index,'time_seconds':seconds,'label':caption,'resolution_mm':.05,
                           'stl':target.name,'stl_sha256':digest(target),'nc_sha256':sim['nc_sha256_after']})
        write(case/'capture.json',{'schema':'genlaw.pattern-replay.v1','case':key,'final_run':str(source.relative_to(ROOT)),
              'nc_sha256':sim['nc_sha256_after'],'project_sha256':digest(case/'simulation.camotics'),
              'final_mesh_sha256':sim['mesh_sha256'],'final_resolution_mm':sim['resolution_mm'],
              'stock':config['workpiece']['bounds'],'engine_binary_sha256':engine['manifest']['binary_sha256'],
              'frames':frames,'final_result_reexecuted':False,'production_approved':False})
        bounded(case,'export',[sys.executable,Path(__file__),'--export',case])
        records.append(load(case/'frames.json'))
    write(out/'summary.json',{'schema':'genlaw.pattern-replay-set.v1','id':out.name,'cases':records,
                             'new_intermediate_simulations':len(records)*3,'production_approved':False})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runs',type=Path);p.add_argument('--output',type=Path);p.add_argument('--export',type=Path)
    args=p.parse_args()
    if args.export:export(args.export.resolve())
    elif args.runs and args.output:build(args.runs.resolve(),args.output)
    else:p.error('Supply --runs / --output or --export')
