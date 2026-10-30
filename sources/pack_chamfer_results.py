"""Losslessly archive qualified chamfer inputs, native outputs and actual replay."""
import argparse,json,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'web/tools')]
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.programming import verify_binding
from genlaw_pipeline.run import write
from mesh_metrics import require_bounded_worker,load_mesh,write_indexed
from tools.pack_pocket_sequence import compressed
LOAD=lambda path:json.loads(path.read_text())
CP=ROOT/'docs/checkpoints/2026-10-30/chamfer-formal-01'
INPUTS=ROOT/'validation/feature-inputs/S4-CHAMFER-20261030-r1'
RUNS=CP/'native-suite-01'


def initialize(out):
    suite=LOAD(RUNS/'summary.json');qualified=LOAD(CP/'qualification.json')
    if not suite['all_regression_expectations_met'] or qualified['status']!='formal_synthetic_scope_qualified':raise ValueError('Qualification incomplete')
    for name,value in LOAD(INPUTS/'manifest.json')['files'].items():verify_binding(INPUTS/name,value['sha256'])
    for row in suite['normal_cases']:
        for name,sha in LOAD(RUNS/row['id']/'run-result.json')['source_code_sha256'].items():verify_binding(ROOT/'genlaw_pipeline'/name,sha)
    out.mkdir(parents=True,exist_ok=False);write(out/'suite.json',suite)
    for source,name in [(CP/'qualification.json','qualification.json'),(INPUTS/'manifest.json','inputs-manifest.json'),
        (INPUTS/'specifications.json','specifications.json'),(INPUTS.with_suffix('.zip'),'input-cards-and-cad.zip')]:shutil.copy2(source,out/name)
    (out/'sources').mkdir()
    for path in sorted((ROOT/'genlaw_pipeline').glob('*.py')):shutil.copy2(path,out/'sources'/path.name)
    for name in ['build_chamfer_cad.py','build_chamfer_inputs.py','build_chamfer_replay.py','run_chamfer_suite.py','finalize_chamfer_qualification.py','pack_chamfer_results.py']:
        shutil.copy2(ROOT/'tools'/name,out/'sources'/name)
    from genlaw_pipeline.simulation_engine import selected_engine
    engine=selected_engine();shutil.copy2(engine['manifest_path'],out/'engine-build.json')
    shutil.copy2(ROOT/'engines/camotics/selected-engine.json',out/'engine-selection.json')
    for name in ['deburr-linear-height-r1','deburr-linear-height-r2']:shutil.copytree(ROOT/'engines/freecad'/name,out/'freecad'/name)
    write(out/'archive-scope.json',{'source_checkpoint':str(CP.relative_to(ROOT)),'source_inputs':str(INPUTS.relative_to(ROOT)),
        'source_summary_sha256':digest(RUNS/'summary.json'),'raw_actual_meshes_retained_losslessly':True,
        'no_registration_or_discarded_material':True,'replay_resolution_mm':.025,'new_simulations_for_publication':0,
        'source_pdf_kind':'Developer-authored frozen synthetic reference cards; no customer PDFs.',
        'production_approved':False,'whole_process_card_complete':False})


def pack_case(out,key):
    suite=LOAD(out/'suite.json');row=next(r for r in suite['normal_cases']+suite['controls'] if r['id']==key)
    parent=row.get('parent_case',key);control='parent_case' in row;source=RUNS/key;case=out/key;case.mkdir()
    report=LOAD(source/'software-verification.json')
    for name,field in [('simulated.stl','mesh_sha256'),('program.nc','nc_sha256'),('simulation.camotics','project_sha256')]:verify_binding(source/name,report[field])
    bindings={};delivery={};raws={}
    def copy(path,name):
        dest=case/name;dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists():raise ValueError('Duplicate archive destination')
        shutil.copy2(path,dest);bindings[str(path.relative_to(ROOT))]={'path':str(dest.relative_to(out)),'sha256':digest(path)}
    if not control:
        for path in sorted((INPUTS/key).iterdir()):
            if path.is_file():copy(path,'input-manifest.json' if path.name=='manifest.json' else path.name)
    else:
        for name in ['target.step','target.brep']:copy(INPUTS/parent/name,name)
    for name in ['program.nc','simulation.camotics','metrics.json']:copy(source/name,name)
    copy(source/'software-verification.json','verification.json')
    if control:copy(source/'control-binding.json','control-binding.json')
    else:
        for name in ['execution-plan.json','preparation-result.json','cam-result.json','native-geometry.json','native-path.json','cam-job.FCStd','run-result.json','simulation-result.json']:
            copy(source/name,name)
    def geometry(path,name):
        dest=case/(name+'.gz');record=compressed(path,dest);record['path']=str(dest.relative_to(out))
        delivery[key+'/'+name]=record
    geometry(RUNS/parent/'target.mesh','target.mesh')
    actual=source/'actual.mesh';temporary=case/'actual-indexed.mesh'
    if not actual.exists():
        v,f,_=load_mesh(source/'simulated.stl');write_indexed(temporary,v,f);actual=temporary
    geometry(actual,'actual.mesh')
    if temporary.exists():temporary.unlink()
    raws['final']=compressed(source/'simulated.stl',case/'simulated.stl.gz')
    frames=[]
    if key=='baseline':
        replay=CP/'replay-01/baseline';capture=LOAD(replay/'frames.json')
        if capture['final_mesh_sha256']!=digest(source/'simulated.stl'):raise ValueError('Replay bound to another final output')
        for frame in capture['frames']:
            verify_binding(replay/frame['mesh'],frame['mesh_sha256']);verify_binding(replay/frame['stl'],frame['stl_sha256'])
            geometry(replay/frame['mesh'],'replay/'+frame['mesh'])
            raws[frame['stl']]=compressed(replay/frame['stl'],case/'replay'/(frame['stl']+'.gz'))
            frames.append({k:frame[k] for k in ['kind','time_seconds','label','resolution_mm','mesh_sha256','stl_sha256']})
            frames[-1]['mesh']=key+'/replay/'+frame['mesh']
        for name in ['frames.json','capture.json','program.nc','simulation.camotics']:copy(replay/name,'replay/'+name)
        for path in replay.glob('*-resources.json'):copy(path,'replay/resources/'+path.name)
    for path in sorted(source.glob('*-resources.json')):copy(path,'resources/'+path.name)
    write(case/'archive-bindings.json',{'copied_sources':bindings,'raw_meshes':raws,'geometry_delivery':delivery,'frames':frames,
        'display_geometry_is_original_indexed_actual_mesh':True,'quantitative_geometry_modified':False})
    print(json.dumps({'archived':key,'frames':len(frames),'bytes':sum(p.stat().st_size for p in case.rglob('*') if p.is_file())}),flush=True)


def finish(out):
    suite=LOAD(out/'suite.json');delivery={};normals=[];controls=[]
    for row in suite['normal_cases']+suite['controls']:
        binding=LOAD(out/row['id']/'archive-bindings.json');delivery.update(binding['geometry_delivery'])
        item={**row,'frames':binding['frames'],'resolution_mm':.025}
        (controls if 'parent_case' in row else normals).append(item)
    write(out/'summary.json',{**suite,'normal_cases':normals,'controls':controls,'stage_id':'s4','title':'四边 45° 倒角',
        'rectangular_chamfer_verified':True,'representative_replay_case':'baseline','new_simulations_for_publication':0})
    write(out/'geometry-delivery.json',delivery)
    files={str(p.relative_to(out)):{'bytes':p.stat().st_size,'sha256':digest(p)} for p in sorted(out.rglob('*')) if p.is_file()}
    write(out/'manifest.json',{'schema':'genlaw.chamfer-results-archive.v1','files':files,'raw_geometry_losslessly_preserved':True})
    print(json.dumps({'files':len(files)+1,'bytes':sum(v['bytes'] for v in files.values()),'transport_bytes':sum(v['bytes'] for v in delivery.values())}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['initialize','case','finish']);p.add_argument('--output',type=Path,required=True);p.add_argument('--case')
    a=p.parse_args();require_bounded_worker();out=a.output.resolve();out.relative_to(ROOT)
    if a.stage=='initialize':initialize(out)
    elif a.stage=='case':pack_case(out,a.case)
    else:finish(out)
