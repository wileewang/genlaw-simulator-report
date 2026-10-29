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
    for name in ('build_pocket_sequence_cad.py','build_pocket_sequence_inputs.py','build_sequence_inputs.py','run_pocket_sequence_suite.py','pack_pocket_sequence.py'):
        shutil.copy2(ROOT/'tools'/name,out/'sources'/name)
    shutil.copy2(inputs/'manifest.json',out/'inputs-manifest.json')
    shutil.copy2(inputs/'specifications.json',out/'specifications.json')
    from genlaw_pipeline.simulation_engine import selected_engine
    engine=selected_engine();shutil.copy2(engine['manifest_path'],out/'engine-build.json')
    shutil.copy2(ROOT/'engines/camotics/selected-engine.json',out/'engine-selection.json')
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
        for name,value in LOAD(inputs/key/'manifest.json')['files'].items():verify_binding(inputs/key/name,value['sha256'])
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
