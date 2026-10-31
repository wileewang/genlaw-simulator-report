"""Execute frozen paper/CAD chains through native CAM and cumulative material checks."""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.cell_windows import checked_file,file_binding
from genlaw_pipeline.indexed_facing import IDENTITY
from genlaw_pipeline.static_stages import prepare_chain,read
from genlaw_pipeline.native_stream import checked_build
from genlaw_pipeline.programming import check_nc
from genlaw_pipeline.run import write,FREECAD,workspace_environment


def engine(build_path):
    build=read(build_path)
    if build['schema']!='genlaw.static-stage-stream-build.v1':raise ValueError('Wrong native engine')
    for binding in [build['binary'],*build['source_code'],*build['reused_objects']]:checked_file(binding)
    parent=read(checked_file(build['reference_build']))
    _,env=checked_build(checked_file(parent['reference_build']))
    return checked_file(build['binary']),env


def run(inputs,build_path,qualification,out):
    inputs=inputs.resolve();build_path=build_path.resolve();qualification=qualification.resolve();out=out.resolve()
    out.relative_to(ROOT);out.mkdir(parents=True,exist_ok=False)
    archive=read(inputs/'manifest.json');qualified=read(qualification)
    if archive['schema']!='genlaw.static-stage-input-archive.v1' or not archive['all_targets_frozen_before_NC']:
        raise ValueError('Require archived paper inputs and original CAD before CAM')
    if (qualified.get('schema')!='genlaw.static-stage-engine-qualification.v1' or qualified.get('status')!='passed'
            or qualified['build']!=file_binding(build_path) or qualified.get('exact_geometry_cases')!=3):
        raise ValueError('Require completed exact native geometry regression')
    for binding in archive['files']:checked_file(binding)
    for row in qualified['cases']:checked_file(row['comparison'])
    binary,engine_env=engine(build_path)
    originals=[*sorted((ROOT/'genlaw_pipeline').glob('*.py')),Path(__file__),ROOT/'tools/static_stages_cam_worker.py',
               ROOT/'web/tools/mesh_metrics.py']
    source_bindings=[file_binding(p) for p in originals];frozen=[]
    for p in originals:
        dest=out/'sources'/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(p,dest);frozen.append(file_binding(dest))
    write(out/'request.json',{'schema':'genlaw.static-stage-suite-request.v1','input_manifest':file_binding(inputs/'manifest.json'),
        'build':file_binding(build_path),'engine_qualification':file_binding(qualification),
        'execution_sources':source_bindings,'preserved_sources':frozen,'limits_changed':False,'public_site_updated':False})
    rows=[];controls=None
    for spec in read(inputs/'specifications.json')['cases']:
        key=spec['id'];case=out/key;case.mkdir();chain=prepare_chain(inputs/key/'input.json')
        write(case/'preparation-result.json',chain)
        write(case/'request.json',{'input':str(inputs/key/'input.json'),'output':str(case)})
        env=workspace_environment();env.update(QT_QPA_PLATFORM='offscreen',GENLAW_STATIC_STAGE_CAM_REQUEST=str(case/'request.json'))
        cam_command=[str(FREECAD),'freecadcmd',str(ROOT/'tools/static_stages_cam_worker.py')]
        admitted_bounded(case,'freecad',cam_command,env)
        cam=read(case/'cam-result.json')
        if cam['status']!='native_jobs_generated_and_checked':raise ValueError('CAM incomplete')
        bound=[file_binding(build_path),file_binding(case/'native-cad-chain.json'),file_binding(case/'cam-result.json')]
        for i,stage in enumerate(chain['stages']):
            dest=case/stage['id'];result=read(dest/'cam-result.json');nc=dest/'program.nc'
            if result['nc_sha256']!=file_binding(nc)['sha256']:raise ValueError('Native NC changed')
            check_nc(nc.read_text(),chain['stage_plans'][i]);write(dest/'simulation.camotics',chain['simulation_projects'][i])
            write(dest/'material-role.json',{'nominal_CAM_incoming':chain['stage_plans'][i]['stock']['bounds_mm'],
                'original_envelope_for_native_accumulation':chain['simulation_projects'][i]['workpiece']['bounds'],
                'nominal_target_is_not_simulated_material':True,'predecessor':stage['predecessor'],
                'setup_id':stage['setup_id'],'program_specific_tool_table':True})
            bound.extend(file_binding(dest/n) for n in ['program.nc','simulation.camotics','cam-result.json','execution-plan.json'])

        def execute(label,kind,index,indices,reuse=None):
            dest=case/label;dest.mkdir();source=case/reuse if reuse else dest
            if reuse:
                prior=read(source/'measurement-request.json');cmd=prior['native_command'];request_binding=prior['stage_request']
            else:
                selected=[]
                for i in indices:
                    stage=chain['stages'][i];pose=IDENTITY if kind=='wrong-chamfer-pose' and i==len(chain['stages'])-1 else stage['to_material']
                    selected.append({'id':stage['id'],'project':str(case/stage['id']/'simulation.camotics'),'to_material':pose})
                write(dest/'stages.json',{'schema':'genlaw.native-static-stage-request.v1','stages':selected})
                request_binding=file_binding(dest/'stages.json')
                cmd=[str(binary),'--cell-window',','.join(map(str,chain['window'])),'--facets-per-file','500000',
                     str(dest/'stages.json'),str(dest/'native')]
                admitted_bounded(dest,'native',cmd,engine_env)
            request={'schema':'genlaw.static-stage-measurement-request.v1','case_kind':kind,'reference_stage_index':index,
                'preparation':file_binding(case/'preparation-result.json'),'CAD_chain':file_binding(case/'native-cad-chain.json'),
                'cam_request':file_binding(case/'request.json'),'cam_command':cam_command,'cam_resources':file_binding(case/'freecad-resources.json'),
                'build':file_binding(build_path),'native_manifest':file_binding(source/'native/native.json'),
                'native_resources':file_binding(source/'native-resources.json'),'native_command':cmd,'stage_request':request_binding,
                'bound_artifacts':bound,'source_code':frozen,'native_reused':reuse is not None}
            write(dest/'measurement-request.json',request)
            admitted_bounded(dest,'measure',[sys.executable,ROOT/'genlaw_pipeline/static_stage_measurement.py',
                '--request',dest/'measurement-request.json','--output',dest/'metrics.json'])
            m=read(dest/'metrics.json')
            if not m['regression_expectation_met']:raise ValueError('Frozen geometry criterion failed')
            return {'metrics':file_binding(dest/'metrics.json'),'iou_percent':m['iou_percent'],
                    'geometry_pass':m['geometry_pass'],'native_reused':reuse is not None}

        states=[]
        for i,stage in enumerate(chain['stages']):
            value=execute('after-'+stage['id'],'normal',i,list(range(i+1)))
            if not value['geometry_pass']:raise ValueError('Do not execute the next stage after a failed predecessor')
            states.append({'stage_id':stage['id'],'setup_id':stage['setup_id'],**value})
        row={'id':key,'paper_card':chain['paper_card'],'original_final_CAD':chain['final_cad'],
             'states':states,'final_iou_percent':states[-1]['iou_percent'],
             'CAM_jobs':[file_binding(case/s['id']/'cam-job.FCStd') for s in chain['stages']]}
        if key=='baseline':
            controls={kind:execute(kind,kind,len(chain['stages'])-1,indices,reuse) for kind,indices,reuse in [
                ('omitted-chamfer',[0,1],'after-B'),('wrong-chamfer-pose',[0,1,2],None),('omitted-second-facing',[0,2],None)]}
            row['negative_controls']=controls
        for binding in source_bindings+archive['files']:checked_file(binding)
        write(case/'result.json',row);rows.append(row)
        print(json.dumps({'case_completed':key,'stage_iou_percent':[v['iou_percent'] for v in states]}),flush=True)
    if len(rows)!=4 or controls is None:raise ValueError('Incomplete predeclared cohort')
    write(out/'summary.json',{'schema':'genlaw.static-stage-suite.v1','status':'four_three_program_two_setup_chains_measured',
        'request':file_binding(out/'request.json'),'normal_cases':rows,'negative_controls':controls,
        'NC_generated_by_FreeCAD':True,'NC_text_modified':False,'one_original_material_across_all_stages':True,
        'predecessor_geometry_gates_next_material_execution':True,'programs_per_case':3,'static_setups_per_case':2,
        'continuous_five_axis_supported':False,'physical_fixture_or_toolholder_collision_verified':False,
        'material_physics_verified':False,'whole_process_card_complete':False,'production_approved':False,'public_site_updated':False})
    print(json.dumps({'suite_completed':True,'normal_cases':4,'geometry_controls':3}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for k in ['inputs','build','qualification','output']:p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();run(a.inputs,a.build,a.qualification,a.output)
