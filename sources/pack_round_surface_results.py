"""Lossless public review bundle for the qualified synthetic round coupons."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'web/tools')]
from genlaw_pipeline.cell_windows import checked_file,file_binding
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.run import write
from mesh_metrics import require_bounded_worker,load_mesh,write_indexed
from tools.pack_pocket_sequence import compressed

CP=ROOT/'docs/checkpoints/2026-10-31/round-surface-01'
RUNS=ROOT/'validation/runs/S4-ROUND-SURFACE-20261031-r2'
INPUTS=ROOT/'validation/feature-inputs/S4-ROUND-SURFACE-20261031-r2'
NORMALS=['baseline','larger-radius','longer','shifted-datum']
CONTROLS=['insufficient-boundary','omitted-round']
READ=lambda p:json.loads(Path(p).read_text())


def initialize(out):
    complete=READ(CP/'completion.json');suite=READ(checked_file(complete['suite']))
    if suite['status']!='four_normal_two_geometry_controls_qualified' or not suite['feature_cohort_qualified']:
        raise ValueError('Require completed cohort')
    capture=READ(CP/'replay-01/capture.json')
    if capture['status']!='two_native_time_samples_ready' or capture['resolution_mm']!=.025:
        raise ValueError('Missing true full-resolution intermediate surfaces')
    for binding in READ(checked_file(complete['input_archive']))['files']:checked_file(binding)
    out.mkdir(parents=True,exist_ok=False)
    for source,name in [(RUNS/'summary.json','suite.json'),(CP/'completion.json','qualification.json'),
            (INPUTS/'manifest.json','inputs-manifest.json'),(INPUTS/'specifications.json','specifications.json'),
            (CP/'input-review-r2.json','paper-review.json'),(CP/'input-check-01/result.json','input-checks.json'),
            (CP/'feed-adapter-check.json','feed-checks.json'),(CP/'partition-qualification.json','partition-qualification.json'),
            (CP/'replay-01/capture.json','replay-capture.json')]:shutil.copy2(source,out/name)
    shutil.copytree(CP/'qualified-source',out/'sources')
    shutil.copy2(Path(__file__),out/'sources'/Path(__file__).name)
    shutil.copy2(CP/'replay-01/replay-source.py',out/'sources/replay-source.py')
    with zipfile.ZipFile(out/'input-cards-and-cad.zip','x',compression=zipfile.ZIP_DEFLATED) as archive:
        for key in NORMALS:
            for source in sorted((INPUTS/key).iterdir()):archive.write(source,str(source.relative_to(INPUTS)))
    write(out/'archive-scope.json',{'customer_original_documents_included':False,
        'input_kind':'Synthetic PDF card plus manually structured attachment and independent native CAD',
        'new_simulations_for_export':0,'whole_real_card_complete':False,'production_approved':False,
        'material_physics_verified':False,'physical_machine_or_fixture_qualified':False})


def pack_case(out,key):
    suite=READ(out/'suite.json');row=next(r for r in suite['normal_cases']+suite['controls'] if r['id']==key)
    case=out/key;case.mkdir();normal=key in NORMALS
    if normal:run=RUNS/key;inputs=INPUTS/key
    elif key=='insufficient-boundary':
        run=ROOT/'validation/runs/S4-ROUND-SURFACE-PROBE-20261031-r6/baseline'
        inputs=ROOT/'validation/feature-inputs/S4-ROUND-SURFACE-20261031-r1/baseline'
    else:run=CP/'geometry-controls-01/omitted-round';inputs=INPUTS/'baseline'
    shutil.copytree(inputs,case/'input')
    records={};delivery={};equivalence={}
    def copy(source,name):
        dest=case/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
        records[str(source.relative_to(ROOT))]={'path':str(dest.relative_to(out)),'sha256':digest(source)}
    metric_path=checked_file(row['metrics']);metric=READ(metric_path);inspection_path=checked_file(metric['inspection'])
    inspection=READ(inspection_path);mesh=checked_file(inspection['mesh'])
    if metric['geometry_pass']!=normal or not metric['numerical_stability_pass']:
        raise ValueError('Changed measured decision')
    copy(metric_path,'run/metrics.json');copy(inspection_path,'run/inspection.json')
    for part in metric['parts']:copy(checked_file(part),'run/measurement/'+Path(part['path']).name)
    copy(run/'program.nc','program.nc');copy(run/'simulation.camotics','simulation.camotics')
    if key=='omitted-round':copy(run/'control-request.json','run/control-request.json')
    else:
        for name in ['preparation-result.json','native-geometry.json','freecad-resources.json','request.json','cam-job.FCStd']:
            copy(run/name,'run/'+name)
        cam=READ(run/'cam-result.json')
        for name in ['cam-result.json','native-path.json','prepared-path.json']:
            compressed(run/name,case/'run'/(name+'.gz'))
        write(case/'run/nc-check.json',{'source':file_binding(run/'cam-result.json'),
            'note':'Motion trace retained in the lossless complete CAM record.',
            'nc_check':{k:v for k,v in cam['nc_check'].items() if k!='motion_trace'}})
    native_root=run/('material-partitioned' if normal else 'material')
    for source in sorted(native_root.rglob('*')):
        if source.is_file() and source.suffix in ['.json','.log','.py']:
            copy(source,'run/material/'+str(source.relative_to(native_root)))
    def geometry(source,name):
        vertices,faces,report=load_mesh(source)
        if report['exact_duplicate_faces_removed']:raise ValueError('Do not discard original faces')
        temp=case/(name+'.mesh');write_indexed(temp,vertices,faces)
        record=compressed(temp,case/(name+'.mesh.gz'));record['path']=key+'/'+name+'.mesh.gz'
        delivery[key+'/'+name+'.mesh']=record
        equivalence[name]={'source':file_binding(source),'inspection':report,
            'decoded_display_sha256':digest(temp),'vertices_moved':False,'faces_discarded':False}
        temp.unlink()
    target=run/'target-display.stl' if key!='omitted-round' else RUNS/'baseline/target-display.stl'
    geometry(target,'target');geometry(mesh,'actual');raw=compressed(mesh,case/'actual.stl.gz')
    frames=[]
    if key=='baseline':
        capture=READ(CP/'replay-01/capture.json')
        for binding in capture['frames']:
            frame=READ(checked_file(binding));name=frame['id'];source=checked_file(frame['surface'])
            geometry(source,name);compressed(source,case/(name+'.stl.gz'))
            copy(checked_file(binding),'run/replay/'+name+'.json')
            frames.append({'mesh':key+'/'+name+'.mesh','label':frame['label'],
                'time_seconds':frame['time_seconds'],'kind':'native_time_cutoff','resolution_mm':.025})
    spec=READ(inputs/'input.json')['specification']
    summary={'id':key,'name':spec['name'] if normal else {'insufficient-boundary':'刀路覆盖不足','omitted-round':'漏加工圆角'}[key],
        'case_kind':'normal' if normal else 'negative_control','iou_percent':metric['iou_percent'],
        'geometry_pass':normal,'numerical_stability_pass':True,'stock':spec['stock'],
        'radius_mm':spec['radius_mm'],'resolution_mm':.025,'frames':frames,
        'profile_pass':inspection['feature_observations']['profile_pass'],
        'radius_pass':inspection['feature_observations']['radius_pass'],
        'retained_planes_pass':inspection['feature_observations']['retained_planes_pass'],
        'maximum_profile_deviation_mm':inspection['feature_observations']['maximum_boundary_deviation_mm'],
        'maximum_radius_deviation_mm':inspection['feature_observations']['maximum_radius_deviation_mm'],
        'normal_NC_guard_rejected':key=='omitted-round'}
    write(case/'archive-bindings.json',{'copied_sources':records,'raw_geometry':raw,'display_equivalence':equivalence,
        'geometry_delivery':delivery,'summary':summary})
    print(json.dumps({'packed':key,'iou_percent':metric['iou_percent']}),flush=True)


def assemble(out):
    rows=[];delivery={}
    for key in NORMALS+CONTROLS:
        binding=READ(out/key/'archive-bindings.json');rows.append(binding['summary']);delivery.update(binding['geometry_delivery'])
    write(out/'summary.json',{'schema':'genlaw.round-surface-review-summary.v1','normal_cases':rows[:4],
        'controls':rows[4:],'qualified_scope':'Two X-parallel upper exterior rounds on four synthetic box coupons',
        'software_acceptance':READ(INPUTS/'specifications.json')['cases'][0]['software_acceptance'],
        'input_rejections':23,'feed_and_NC_rejections':8,'whole_real_card_complete':False,
        'production_approved':False,'new_machine_models':0,'new_material_physics':0})
    write(out/'geometry-delivery.json',delivery)


def finish(out,qa):
    (out/'thumbnails').mkdir();captures=[]
    for key in NORMALS+CONTROLS:
        image=qa/'thumbnails'/(key+'.png');resource=qa/('thumbnail-'+key+'-resources.json');r=READ(resource)
        if not r['ok'] or r['wall_limit_seconds']!=35 or r['rss_limit_bytes']!=2*1024**3:
            raise ValueError('Missing bounded actual browser capture')
        shutil.copy2(image,out/'thumbnails'/image.name);captures.append({'image':file_binding(image),'resource':file_binding(resource)})
    write(out/'thumbnail-provenance.json',{'actual_CAD_and_material':True,'captures':captures})
    files={str(p.relative_to(out)):{'bytes':p.stat().st_size,'sha256':digest(p)} for p in sorted(out.rglob('*')) if p.is_file()}
    total=sum(r['bytes'] for r in files.values())
    if total>=45_000_000:raise ValueError('Immutable review archive exceeds 45 MB CDN budget')
    write(out/'manifest.json',{'schema':'genlaw.round-surface-review-archive.v1','files':files,'bytes':total})
    print(json.dumps({'files':len(files),'bytes':total}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['initialize','case','assemble','finish'])
    p.add_argument('--output',type=Path,required=True);p.add_argument('--case');p.add_argument('--qa',type=Path)
    a=p.parse_args();require_bounded_worker();out=a.output.resolve();out.relative_to(ROOT)
    if a.mode=='initialize':initialize(out)
    elif a.mode=='case':pack_case(out,a.case)
    elif a.mode=='assemble':assemble(out)
    else:finish(out,a.qa.resolve())
