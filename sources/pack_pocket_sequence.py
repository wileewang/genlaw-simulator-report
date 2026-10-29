"""Lossless case-sized archives of verified pocket composition and actual states."""
import argparse
import gzip
import hashlib
import json
import shutil
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'web/tools')]
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.programming import verify_binding
from genlaw_pipeline.run import write
from mesh_metrics import require_bounded_worker,load_mesh,write_indexed
LOAD=lambda path:json.loads(path.read_text())


def compressed(source,destination):
    if destination.exists():raise ValueError('Do not overwrite archived results')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with source.open('rb') as inp,destination.open('xb') as raw:
        with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as encoded:shutil.copyfileobj(inp,encoded,1024*1024)
    sha=hashlib.sha256();size=0
    with gzip.open(destination,'rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):sha.update(chunk);size+=len(chunk)
    if sha.hexdigest()!=digest(source) or size!=source.stat().st_size:raise ValueError('Lossless archive verification failed')
    return {'encoding':'gzip','bytes':destination.stat().st_size,'sha256':digest(destination),
            'decoded_bytes':size,'decoded_sha256':sha.hexdigest()}


def initialize(inputs,runs,out):
    suite=LOAD(runs/'summary.json')
    if not suite['all_regression_expectations_met'] or suite['production_approved'] or suite['whole_process_card_complete']:
        raise ValueError('Need completed software qualification with explicit manufacturing limits')
    for name,value in LOAD(inputs/'manifest.json')['files'].items():verify_binding(inputs/name,value['sha256'])
    for row in suite['normal_cases']:
        for name,sha in LOAD(runs/row['id']/'run-result.json')['source_code_sha256'].items():verify_binding(ROOT/'genlaw_pipeline'/name,sha)
    out.mkdir(parents=True,exist_ok=False)
    write(out/'suite.json',suite)
    (out/'sources').mkdir()
    for path in sorted((ROOT/'genlaw_pipeline').glob('*.py')):shutil.copy2(path,out/'sources'/path.name)
    tool_lengths=suite['schema']=='genlaw.tool-length-suite.v1'
    multi_pocket=suite['schema']=='genlaw.multi-pocket-suite.v1'
    framed=suite['schema']=='genlaw.work-coordinate-suite.v1' or tool_lengths
    extra=(('build_multi_pocket_inputs.py','build_multi_pocket_cad.py','run_multi_pocket_suite.py','finalize_multi_pocket_integration.py') if multi_pocket else
           ('build_tool_length_inputs.py','run_tool_length_suite.py') if tool_lengths else
           ('build_work_coordinate_inputs.py','run_work_coordinate_suite.py') if framed else ())
    for name in ('build_pocket_sequence_cad.py','build_pocket_sequence_inputs.py','build_sequence_inputs.py','run_pocket_sequence_suite.py','pack_pocket_sequence.py')+extra:
        shutil.copy2(ROOT/'tools'/name,out/'sources'/name)
    shutil.copy2(inputs/'manifest.json',out/'inputs-manifest.json')
    shutil.copy2(inputs/'specifications.json',out/'specifications.json')
    from genlaw_pipeline.coordinate_backend import engine_for_plan
    engine=engine_for_plan(LOAD(runs/suite['normal_cases'][0]['id']/'execution-plan.json'))
    shutil.copy2(engine['manifest_path'],out/'engine-build.json')
    selection_path=ROOT/('engines/camotics/tool-length-engine.json' if tool_lengths else
                         'engines/camotics/coordinate-engine.json' if framed else 'engines/camotics/selected-engine.json')
    shutil.copy2(selection_path,out/'engine-selection.json')
    if framed:
        (out/'engine').mkdir()
        selection=LOAD(selection_path)
        shutil.copy2(ROOT/selection['qualification']['path'],out/'engine/probe-qualification.json')
        for name in ['work-coordinates.patch','material-frame.patch','grid-window.patch']+(['tool-length.patch'] if tool_lengths else []):
            shutil.copy2(engine['manifest_path'].parent/name,out/'engine'/name)
    write(out/'archive-scope.json',{'inputs':str(inputs.relative_to(ROOT)),'runs':str(runs.relative_to(ROOT)),
        'raw_actual_meshes_retained_losslessly':True,'no_result_registration_or_trimming':True,
        'actual_material_checkpoints_reused':True,'extra_simulations_for_animation':0,
        'source_pdf_kind':'Synthetic archived software-reference cards; no customer PDFs.',
        'production_approved':False,'whole_process_card_complete':False})


def pack_case(inputs,runs,out,key):
    suite=LOAD(out/'suite.json');row=next(r for r in suite['normal_cases']+suite['controls'] if r['id']==key)
    control='parent_case' in row;parent=row.get('parent_case',key);source=runs/key;case=out/key;case.mkdir()
    report=LOAD(source/'software-verification.json')
    for name,field in [('simulated.stl','mesh_sha256'),('program.nc','nc_sha256'),('simulation.camotics','project_sha256')]:verify_binding(source/name,report[field])
    if not control:
        from genlaw_pipeline.sequence_checkpoints import verify_checkpoint_bindings
        verify_checkpoint_bindings(source,LOAD(source/'cam-result.json'))
        manifest=inputs/key/'manifest.json'
        local_files=(LOAD(manifest)['files'] if manifest.exists() else
                     {name[len(key)+1:]:value for name,value in LOAD(inputs/'manifest.json')['files'].items() if name.startswith(key+'/')})
        for name,value in local_files.items():verify_binding(inputs/key/name,value['sha256'])
    bindings={};delivery={}
    def copy(path,name):
        dest=case/name
        if dest.exists():raise ValueError('Duplicate archive target: '+name)
        dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
        bindings[str(path.relative_to(ROOT))]={'path':str(dest.relative_to(out)),'sha256':digest(path)}
    if not control:
        for path in sorted((inputs/key).iterdir()):
            if path.is_file():copy(path,'input-manifest.json' if path.name=='manifest.json' else path.name)
    else:
        copy(inputs/parent/'target.step','target.step');copy(inputs/parent/'target.brep','target.brep')
    for name in ('program.nc','simulation.camotics','metrics.json','suite-case.json'):
        copy(source/name,name)
    for name in ('setup-registers.nc','work-frame-binding.json','normal-guard-rejection.json'):
        if (source/name).exists():copy(source/name,name)
    copy(source/'software-verification.json','verification.json')
    if control:copy(source/'control-binding.json','control-binding.json')
    else:
        for name in ('execution-plan.json','preparation-result.json','cam-result.json','native-geometry.json','native-path.json',
                     'cam-job.FCStd','run-result.json','simulation-result.json','material-checkpoints.json'):
            copy(source/name,name)
    decoded_dir=case/'decoded';decoded_dir.mkdir()
    def geometry(path,name):
        dest=case/(name+'.gz');row=compressed(path,dest);row['path']=str(dest.relative_to(out))
        delivery[str((case/name).relative_to(out))]=row
    geometry(runs/parent/'target.mesh','target.mesh')
    actual=source/'actual.mesh'
    if not actual.exists():
        v,f,_=load_mesh(source/'simulated.stl');actual=decoded_dir/'actual.mesh';write_indexed(actual,v,f)
    geometry(actual,'actual.mesh');raw=compressed(source/'simulated.stl',case/'simulated.stl.gz')
    raw_bindings={'final':{'source_sha256':digest(source/'simulated.stl'),'archive_path':key+'/simulated.stl.gz',**raw}}
    frames=[]
    if not control:
        plan=LOAD(source/'execution-plan.json')
        for i,cp in enumerate(row['checkpoints'],1):
            folder='checkpoints/'+cp['id'];original=source/folder
            for name in ('program.nc','simulation.camotics','native-geometry.json','prefix-binding.json','simulation-result.json',
                         'metrics.json','observations.json','software-verification.json'):
                copy(original/name,folder+'/'+name)
            if (original/'setup-registers.nc').exists():copy(original/'setup-registers.nc',folder+'/setup-registers.nc')
            geometry(original/'actual.mesh',folder+'/actual.mesh')
            raw=compressed(original/'simulated.stl',case/folder/'simulated.stl.gz')
            raw_bindings[cp['id']]={'source_sha256':digest(original/'simulated.stl'),'archive_path':key+'/'+folder+'/simulated.stl.gz',**raw}
            fid=plan['operations'][i-1]['feature_id'];frames.append({'mesh':key+'/'+folder+'/actual.mesh','label':fid+' 完成 · 工步边界',
                'kind':'cumulative_native_prefix','time_seconds':None,'resolution_mm':.025,'fine_checkpoint_acceptance':True,
                'mesh_sha256':digest(original/'actual.mesh'),'source_stl_sha256':digest(original/'simulated.stl')})
    for path in sorted(source.rglob('*-resources.json')):copy(path,'resources/'+str(path.relative_to(source)))
    for path in sorted(source.rglob('*-capacity.json')):copy(path,'resources/'+str(path.relative_to(source)))
    for path in decoded_dir.iterdir():path.unlink()
    decoded_dir.rmdir()
    write(case/'archive-bindings.json',{'copied_sources':bindings,'raw_meshes':raw_bindings,'geometry_delivery':delivery,'frames':frames,
        'display_geometry_is_original_indexed_actual_mesh':True,'quantitative_geometry_modified':False})
    print(json.dumps({'archived':key,'frames':len(frames),'bytes':sum(p.stat().st_size for p in case.rglob('*') if p.is_file())}),flush=True)


def finish(out):
    suite=LOAD(out/'suite.json');delivery={};normals=[];controls=[];resources=[]
    for row in suite['normal_cases']+suite['controls']:
        key=row['id'];binding=LOAD(out/key/'archive-bindings.json');delivery.update(binding['geometry_delivery'])
        item={**row,'resolution_mm':.025,'frames':binding['frames']}
        (controls if 'parent_case' in row else normals).append(item)
        for path in (out/key/'resources').rglob('*-resources.json'):
            data=LOAD(path)
            if not data.get('success'):raise ValueError('Archive contains a failed qualified-case worker')
            resources.append({'case':key,'record':str(path.relative_to(out)),'seconds':data['seconds'],
                              'peak_rss_bytes':data['peak_observed_group_rss_bytes'],'cpu_affinity':data['cpu_affinity']})
    summary={**suite,'normal_cases':normals,'controls':controls,'stage_id':'s4','title':'型腔与孔组合',
        'ordered_milling_verified':True,'pocketed_sequence_verified':True,
        'new_features':['型腔与孔连续加工','腔底孔按剩余底板检验','保留卡片顺序与换刀']}
    if suite['schema']=='genlaw.work-coordinate-suite.v1':
        summary.update(title='工件零点与装夹位置',work_coordinates_verified=True,
            new_features=['卡片与坐标配置一致性检查','FreeCAD 原生工件坐标 NC','独立控制器配置与原 CAD 几何验收'])
    if suite['schema']=='genlaw.tool-length-suite.v1':
        cad_hashes={r['id']:LOAD(out/r['id']/'execution-plan.json')['cad_ref']['sha256'] for r in normals}
        summary.update(title='静态刀长补偿与换刀',tool_lengths_verified=True,
            original_cad_sha256_by_case=cad_hashes,distinct_original_cad_count=len(set(cad_hashes.values())),
            new_features=['实装刀长与补偿表分开绑定','FreeCAD 原生 G43 与换刀净空检查','最终及逐工步原 CAD 几何验收'])
    if suite['schema']=='genlaw.multi-pocket-suite.v1':
        cad_hashes={r['id']:LOAD(out/r['id']/'execution-plan.json')['cad_ref']['sha256'] for r in normals}
        summary.update(title='多型腔与腔底孔',multiple_pockets_verified=True,
            original_cad_sha256_by_case=cad_hashes,distinct_original_cad_count=len(set(cad_hashes.values())),
            new_features=['逐腔绑定底面与独立深度','多层剩料及腔底孔检验','卡片规定的交错工序与重复换刀'])
    write(out/'summary.json',summary);write(out/'geometry-delivery.json',delivery)
    write(out/'resources-summary.json',{'single_heavy_job':True,'wall_limit_seconds':60,'rss_limit_bytes':2*1024**3,
        'max_seconds':max(r['seconds'] for r in resources),'peak_rss_bytes':max(r['peak_rss_bytes'] for r in resources),
        'jobs':resources,'manufacturing_evidence':False})
    files={str(p.relative_to(out)):{'bytes':p.stat().st_size,'sha256':digest(p)} for p in sorted(out.rglob('*')) if p.is_file()}
    write(out/'manifest.json',{'schema':'genlaw.pocket-sequence-archive.v1','files':files,'raw_geometry_losslessly_preserved':True})
    print(json.dumps({'files':len(files)+1,'bytes':sum(v['bytes'] for v in files.values()),'transport_bytes':sum(v['bytes'] for v in delivery.values())}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('stage',choices=('initialize','case','finish'))
    p.add_argument('--inputs',type=Path);p.add_argument('--runs',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--case')
    a=p.parse_args();require_bounded_worker();out=a.output.resolve();out.relative_to(ROOT)
    if a.stage=='initialize':initialize(a.inputs.resolve(),a.runs.resolve(),out)
    elif a.stage=='case':pack_case(a.inputs.resolve(),a.runs.resolve(),out,a.case)
    else:finish(out)
