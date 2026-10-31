"""Prepare human-review data without changing any measured material coordinates."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import zipfile
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'web/tools')]
from genlaw_pipeline.cell_windows import checked_file,file_binding
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.run import write
from mesh_metrics import require_bounded_worker,load_mesh,write_indexed,np
from tools.pack_pocket_sequence import compressed

READ=lambda p:json.loads(Path(p).read_text())
PREP=ROOT/'docs/checkpoints/2026-10-31/batch-review-preparation-01/result.json'
NORMALS=['baseline','unequal-products','rotated-layout','shifted-datum']
CONTROLS=['not-through','omitted-second-profile']
NAMES={'baseline':'一坯两件','unequal-products':'两件尺寸不同','rotated-layout':'轮廓沿 Y 排列',
    'shifted-datum':'CAD 使用非零坐标','not-through':'未切透：底部仍相连','omitted-second-profile':'漏加工第二件'}


def initialize(out):
    prep=READ(PREP);suite=READ(checked_file(prep['suite']));integrity=READ(checked_file(prep['integrity']))
    if (prep['status']!='batch_integrity_archives_and_review_geometry_prepared'
            or suite['status']!='four_batch_normals_and_two_native_fault_controls_verified'
            or len(suite['cases'])!=6 or integrity['status']!='seven_material_source_controls_rejected'):
        raise ValueError('Complete six-case geometry and integrity qualification required')
    out.mkdir(parents=True,exist_ok=False)
    for binding,name in [(file_binding(PREP),'preparation.json'),(prep['suite'],'suite.json'),
        (prep['integrity'],'source-integrity.json'),(prep['display_CAD'],'display-cad.json'),
        (prep['actual_replay_prefix'],'replay-prefix-proof.json')]:shutil.copyfile(checked_file(binding),out/name)
    for name in ['engine_qualification','simulation_precision']:
        shutil.copyfile(checked_file(suite[name]),out/(name+'.json'))
    with zipfile.ZipFile(out/'input-cards-and-cad.zip','x',compression=zipfile.ZIP_DEFLATED) as z:
        for ident in NORMALS:
            folder=ROOT/'validation/feature-inputs/S4-BATCH-PROFILE-20261031-r1'/ident
            for path in sorted(folder.iterdir()):
                if path.is_file():z.write(path,str(Path(ident)/path.name))
    (out/'sources').mkdir()
    for source in [Path(__file__),ROOT/'tools/export_batch_review_cad.py',ROOT/'tools/check_batch_replay_prefix.py']:
        shutil.copyfile(source,out/'sources'/source.name)
    write(out/'archive-scope.json',{'development_dimension':'part_features_and_operation_composition',
        'capability':'One original blank, two planned products and the entire retained remainder',
        'whole_real_card_complete':False,'production_approved':False,'new_machine_models':0,'new_material_physics':0,
        'customer_original_documents_included':False,'synthetic_cards_archived_before_NC':True,
        'physical_clamping_or_detached_part_motion_verified':False,'new_simulations_for_display':0})


def pack(out,key):
    prep=READ(out/'preparation.json');suite=READ(out/'suite.json');normal=key in NORMALS
    row=next(r for r in suite['cases'] if r['id']==key and r['kind']=='normal') if normal else next(r for r in suite['cases'] if r['kind']==key)
    parent=row['id'];request=READ(checked_file(row['request']));metrics=READ(checked_file(row['metrics']))
    inspection=READ(checked_file(metrics['inspection']));plan=inspection['plan']
    if (metrics['geometry_pass']!=normal or not metrics['regression_expectation_met']
            or not metrics['numerical_stability_pass'] or not metrics['all_original_native_faces_accounted']):
        raise ValueError('Batch outcome or complete material accounting changed')
    case=out/key;case.mkdir();bindings={};delivery={};equivalence={}
    def copy(source,name):
        source=Path(source);dest=case/name;dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists():raise ValueError('Duplicate review file')
        shutil.copyfile(source,dest);bindings[str(source.relative_to(ROOT))]={'path':str(dest.relative_to(out)),'sha256':digest(source)}
    def geometry(name,vertices,faces,proof):
        dest=case/(name+'.mesh');write_indexed(dest,vertices,faces)
        transport=compressed(dest,case/(name+'.mesh.gz'));transport['path']=key+'/'+name+'.mesh.gz'
        delivery[key+'/'+name+'.mesh']=transport;equivalence[name]={'triangles':len(faces),
            'vertices':len(vertices),'vertices_moved':False,'faces_discarded_within_selected_role':False,**proof}
        dest.unlink() # Only the newly encoded temporary; lossless gzip is decoded/hash checked above.
    inputs=ROOT/'validation/feature-inputs/S4-BATCH-PROFILE-20261031-r1'/parent
    for path in inputs.iterdir():
        if path.is_file():copy(path,'input/'+path.name)
    run=Path(request['case'])
    for name in ['preparation-result.json','native-CAD-check.json','cam-result.json','native-paths.json','cam-job.FCStd']:
        copy(run/name,'run/'+name)
    copy(checked_file(request['executed_NC']),'program.nc');copy(checked_file(request['executed_project']),'simulation.camotics')
    if not normal:copy(checked_file(request['test_control']),'run/control.json')
    for b,name in [(row['request'],'request.json'),(row['metrics'],'metrics.json'),(row['inspection'],'inspection.json'),(row['material'],'material.json')]:
        copy(checked_file(b),'run/'+name)
    for b in metrics['parts']:copy(checked_file(b),'run/'+Path(b['path']).name)
    display=next(r for r in READ(out/'display-cad.json')['cases'] if r['id']==parent)
    roles=['whole','P01','P02','R01'] if normal else ['whole']
    vertices=np.load(checked_file(inspection['arrays']['vertices']),allow_pickle=False)
    faces=np.load(checked_file(inspection['arrays']['faces']),allow_pickle=False)
    labels=np.load(checked_file(inspection['arrays']['labels']),allow_pickle=False)
    material=READ(checked_file(row['material']));raw=checked_file(material['surface'])
    if len(faces)!=material['triangle_count'] or len(labels)!=len(faces):raise ValueError('Display lost native face membership')
    for role in roles:
        reference=display['roles'][role];tv,tf,ti=load_mesh(checked_file(reference['display']))
        if ti['exact_duplicate_faces_removed']:raise ValueError('Original CAD display contains duplicate faces')
        geometry('target-'+role,tv,tf,{'original_CAD':reference['original_CAD'],'display':reference['display'],'display_only_CAD_triangulation':True})
        selected=faces if role=='whole' else faces[labels==inspection['assignments'][role]]
        if role=='whole':av,af=vertices,selected
        else:
            index,inverse=np.unique(selected.reshape(-1),return_inverse=True);av=vertices[index];af=inverse.reshape(-1,3).astype('<u4')
        # Exact original float32 coordinates, winding and face order for the selected entire shell.
        if not np.array_equal(av[af],vertices[selected]):raise ValueError('Display changed original face coordinates')
        geometry('actual-'+role,av,af,{'source':file_binding(raw),'role':role,
            'entire_connected_native_shell':role!='whole','whole_native_material':role=='whole',
            'role_component':None if role=='whole' else inspection['assignments'][role],'bbox_cropping':False})
    archive_row=next(r for r in prep['lossless_archives'] if r['cohort']=='fine' and r['id']==parent and r['kind']==row['kind'])
    archive=READ(checked_file(archive_row['archive']))
    if archive['original_complete_surface']!=material['surface']:raise ValueError('Wrong lossless complete material payload')
    copy(checked_file(archive['payload']),'actual.stl.gz');copy(checked_file(archive_row['archive']),'run/lossless-archive.json')
    summary={'id':key,'name':NAMES[key],'case_kind':'normal' if normal else 'negative_control','parent_case':parent,
        'geometry_pass':metrics['geometry_pass'],'numerical_stability_pass':metrics['numerical_stability_pass'],
        'iou_percent':metrics['whole_material_iou_percent'],'by_role_iou_percent':row['by_role_iou_percent'],
        'role_assignment_complete':metrics['role_assignment_complete'],'native_shells':len(inspection['components']),
        'native_faces':material['triangle_count'],'all_native_faces_accounted':True,'stock':plan['stock']['bounds_mm'],
        'roles':roles,'resolution_mm':plan['grid']['resolution_mm'],'arc_precision_mm':.0005,
        'CAD':key+'/input/nominal-whole-material.step','paper_card':key+'/input/process-card.pdf',
        'metrics':key+'/run/metrics.json','inspection':key+'/run/inspection.json',
        'software_acceptance':plan['acceptance'],'new_machine_models':0,'new_material_physics':0}
    write(case/'archive-bindings.json',{'summary':summary,'copied_sources':bindings,'display_equivalence':equivalence,'geometry_delivery':delivery})
    print(json.dumps({'packed':key,'roles':roles,'native_faces':len(faces)}),flush=True)


def assemble(out):
    rows=[];delivery={}
    for key in NORMALS+CONTROLS:
        v=READ(out/key/'archive-bindings.json');rows.append(v['summary']);delivery.update(v['geometry_delivery'])
    proof=READ(out/'replay-prefix-proof.json')
    if proof['status']!='exact_native_first_operation_prefix_verified':raise ValueError('Replay prefix equivalence unproven')
    write(out/'summary.json',{'schema':'genlaw.batch-review-summary.v1','normal_cases':rows[:4],'controls':rows[4:],
        'source_integrity_rejections':7,'representative_id':'baseline','representative_actual_states_including_stock':3,
        'replay':{'intermediate_mesh':'omitted-second-profile/actual-whole.mesh','label':'第一件外轮廓完成',
            'native_prefix_proof':'replay-prefix-proof.json','final_geometry_acceptance_for_intermediate':False},
        'whole_real_card_complete':False,'production_approved':False,'new_machine_models':0,'new_material_physics':0,
        'physical_clamping_or_detached_part_motion_verified':False})
    write(out/'geometry-delivery.json',delivery)
    total=sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
    if total>=43_000_000:raise ValueError('Archive leaves insufficient room for images within 45 MB budget')
    print(json.dumps({'archive_bytes_before_images':total}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['initialize','case','assemble']);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--case',choices=NORMALS+CONTROLS);a=p.parse_args();require_bounded_worker();out=a.output.resolve();out.relative_to(ROOT)
    if a.mode=='initialize':initialize(out)
    elif a.mode=='case':pack(out,a.case)
    else:assemble(out)
