"""Archive fine operation checkpoints, sharing byte-identical raw STL payloads.

The earlier final-result archive stays immutable. Reused shards are selected by
their complete source SHA-256, never by apparent geometry or feature position.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.cell_windows import checked_file,file_binding
from genlaw_pipeline.checkpoint_context import checked_context
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.run import write
from genlaw_pipeline.admission_wait import admitted_bounded as bounded

RUN=ROOT/'validation/runs/S4-ORIGINAL-SIZE-CHECKPOINTS-20261029-r1'
CONTROL=ROOT/'validation/runs/FINE-WINDOW-CHECKPOINT-CONTROL-20261029-r1'
DISPLAYS=ROOT/'validation/runs/S4-ORIGINAL-SIZE-CHECKPOINT-DISPLAY-20261029-r1'
OLD=ROOT/'web/content/s4-original-size-results'
OUT=ROOT/'web/content/s4-original-size-checkpoints'
QA=ROOT/'web/qa/s4-fine-checkpoints-01'
ID='S4-ORIGINAL-SIZE-CHECKPOINTS-20261029-r1'
LOAD=lambda p:json.loads(p.read_text())


def admit():
    from genlaw_pipeline.sharded_surface import require_bounded_worker
    require_bounded_worker()


def initialize():
    admit();record=LOAD(RUN/'summary.json');old=LOAD(OLD/'summary.json')
    assert record['status']=='fine_checkpoints_passed' and len(record['checkpoints'])==3
    assert record['resolution_mm']==.025 and record['original_stock_used']
    assert not record['expected_cad_used_as_stock'] and not record['production_approved']
    oldbase='results/'+old['id']+'/'
    bindings={key:{'public_path':oldbase+entry['path'],'sha256':entry['sha256']}
              for key,entry in LOAD(OLD/'bindings.json')['files'].items()}
    OUT.mkdir(parents=True,exist_ok=False)

    def copy(source,path):
        source=source.resolve();key=str(source.relative_to(ROOT));sha=digest(source)
        dest=OUT/path;dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists():raise ValueError('Archive overwrite: '+str(dest))
        shutil.copy2(source,dest)
        bindings[key]={'public_path':'results/'+ID+'/'+path,'sha256':sha}

    for folder,category in [(RUN,'run'),(CONTROL,'control'),(DISPLAYS,'display')]:
        for source in sorted(folder.rglob('*')):
            if source.is_file() and source.suffix in ('.json','.nc','.camotics','.edges'):
                copy(source,'evidence/'+category+'/'+str(source.relative_to(folder)))
    # Freeze the new observer and all its unchanged Python helpers beside it.
    for source in sorted((ROOT/'genlaw_pipeline').glob('*.py')):
        copy(source,'sources/genlaw_pipeline/'+source.name)
    for source in [Path(__file__),ROOT/'tools/run_window_checkpoints.py',ROOT/'tools/resume_window_checkpoints.py',
                   ROOT/'tools/resume_window_checkpoints_wait.py',ROOT/'tools/build_window_display.py',
                   ROOT/'tools/build_fine_checkpoint_displays.py',
                   ROOT/'tools/resume_fine_checkpoint_displays.py',
                   ROOT/'tools/finalize_window_checkpoints.py',
                   ROOT/'web/tools/mesh_metrics.py']:
        copy(source,'sources/'+str(source.relative_to(ROOT)))
    # The old small control has different native inputs from the large example.
    for folder in (RUN,CONTROL):
        for context in sorted(folder.glob('after-*/context.json')):
            c=checked_context(context)
            for binding in c['files'].values():
                source=checked_file(binding)
                if bindings.get(binding['path'],{}).get('sha256')!=binding['sha256']:
                    copy(source,'external/'+binding['path'])
    for source in sorted(CONTROL.glob('after-*/legacy-comparison.json')):
        comparison=LOAD(source)
        for field in ('old_metrics','old_verification'):
            b=comparison[field]
            if bindings.get(b['path'],{}).get('sha256')!=b['sha256']:
                copy(checked_file(b),'external/'+b['path'])

    existing={row['source']['sha256']:{'public_path':oldbase+row['path'],
        'sha256':row['sha256'],'bytes':row['bytes'],'decoded_bytes':row['decoded_bytes']}
        for row in LOAD(OLD/'raw-shards.json')['files']}
    unique={};rows=[];checkpoints=[];triangles=old['display_triangles']
    for index,item in enumerate(record['checkpoints'],1):
        case=RUN/item['id'];result=LOAD(checked_file(item['result']))
        context=checked_context(checked_file(result['context']));surface=LOAD(checked_file(result['surface']))
        verification=LOAD(checked_file(result['verification']));display=LOAD(DISPLAYS/item['id']/'display.json')
        assert result['fine_checkpoint_acceptance'] and verification['all_software_geometry_checks_passed']
        assert display['actual_surface']==result['surface'] and not display['quantitative_mesh_modified']
        assert not display['cad_used_to_generate_output'] and display['triangles']<=800000
        triangles+=display['triangles']
        copy(checked_file(display['display']),item['id']+'/actual.mesh')
        for name in ('checkpoint-result.json','metrics.json','native-geometry.json','program.nc','software-policy.json','prefix-binding.json'):
            copy(case/name,item['id']+'/'+name)
        copy(checked_file(result['verification']),item['id']+'/verification.json')
        copy(DISPLAYS/item['id']/'display.json',item['id']+'/display.json')
        oracle=context['files']['oracle_cad'];copy(checked_file(oracle),item['id']+'/expected.brep')
        for entry in surface['windows']:
            native=LOAD(checked_file(entry['result']));source=native['surface'];raw=checked_file(source)
            key=source['sha256']
            if key not in existing and key not in unique:
                unique[key]={'source':source,'path':'raw-shards/'+key+'.stl.gz','decoded_bytes':raw.stat().st_size}
            rows.append({'checkpoint':item['id'],'index':native['index'],'window':native['window'],
                'source':source,'result':entry['result'],
                'payload':existing[key] if key in existing else {'public_path':'results/'+ID+'/'+unique[key]['path']}})
        label='面铣完成' if index==1 else f'H{index-1} 铣孔完成'
        checkpoints.append({'id':item['id'],'label':label,'kind':'cumulative_native_prefix','time_seconds':None,
            'completed_features':context['completed_features'],'iou_percent':result['iou_percent'],
            'checks_passed':result['checks_passed'],'resolution_mm':.025,'display_only':False,
            'fine_checkpoint_acceptance':True,'result':item['result'],
            'mesh':file_binding(checked_file(display['display'])),'mesh_path':item['id']+'/actual.mesh',
            'mesh_triangles':display['triangles'],'raw_manifest':item['id']+'/raw-shards.json'})
    # The final target mesh is tiny but is included in the same browser budget.
    import struct
    target=(OLD/'target.mesh').read_bytes();triangles+=struct.unpack_from('<I',target,12)[0]
    assert triangles<=2000000,triangles
    assert len(rows)==192 and len({(r['checkpoint'],r['index']) for r in rows})==192
    write(OUT/'compression-plan.json',{'unique':list(unique.values()),'rows':rows})
    write(OUT/'bindings.json',{'schema':'genlaw.public-source-bindings.v2','files':bindings})
    write(OUT/'summary-draft.json',{'schema':'genlaw.original-size-checkpoints.v1','id':ID,
        'status':'fine_checkpoints_passed','source':file_binding(RUN/'summary.json'),
        'previous_final_archive':old['id'],'checkpoints':checkpoints,'resolution_mm':.025,
        'intermediate_checks_passed':sum(c['checks_passed'] for c in checkpoints),
        'final_checks_passed':old['software_checks_passed'],'final_iou_percent':old['iou_percent'],
        'total_display_triangles':triangles,'fine_checkpoint_acceptance':True,
        'original_stock_used':True,'expected_cad_used_as_stock':False,
        'actual_volumes_mm3':record['actual_volumes_mm3'],'material_volume_monotonically_decreases':True,
        'legacy_checkpoint_regression':file_binding(CONTROL/'summary.json'),
        'all_original_checkpoint_raw_meshes_public':True,
        'production_approved':False,'real_process_card_tested':False,'s4_complete':False})


def compress(start):
    admit();plan=LOAD(OUT/'compression-plan.json');rows=[]
    for item in plan['unique'][start:start+8]:
        source=checked_file(item['source']);dest=OUT/item['path'];dest.parent.mkdir(parents=True,exist_ok=True)
        with source.open('rb') as raw,dest.open('xb') as output:
            with gzip.GzipFile(filename='',mode='wb',fileobj=output,compresslevel=6,mtime=0) as zipped:
                shutil.copyfileobj(raw,zipped,1024*1024)
        observed=hashlib.sha256();size=0
        with gzip.open(dest,'rb') as stream:
            for data in iter(lambda:stream.read(1024*1024),b''):observed.update(data);size+=len(data)
        assert observed.hexdigest()==item['source']['sha256'] and size==item['decoded_bytes']
        rows.append({'source_sha256':item['source']['sha256'],'public_path':'results/'+ID+'/'+item['path'],
            'sha256':digest(dest),'bytes':dest.stat().st_size,'decoded_bytes':size})
    write(OUT/f'raw-shards/part-{start:03d}.json',rows)


def finish():
    admit();plan=LOAD(OUT/'compression-plan.json')
    compressed={r['source_sha256']:r for path in sorted((OUT/'raw-shards').glob('part-*.json')) for r in LOAD(path)}
    assert set(compressed)=={r['source']['sha256'] for r in plan['unique']}
    for item in plan['rows']:
        if item['source']['sha256'] in compressed:
            item['payload']={k:v for k,v in compressed[item['source']['sha256']].items() if k!='source_sha256'}
    summary=LOAD(OUT/'summary-draft.json');resources=[]
    recoveries=[LOAD(folder/'recovered-interruptions.json') for folder in (RUN,DISPLAYS)]
    recovered={row['failed']['path']:row for recovery in recoveries for row in recovery['failures']};failed=[]
    for item in summary['checkpoints']:
        rows=[r for r in plan['rows'] if r['checkpoint']==item['id']]
        assert [r['index'] for r in rows]==list(range(64))
        write(OUT/item['raw_manifest'],{'schema':'genlaw.public-checkpoint-shards.v1','resolution_mm':.025,
            'closed_as_a_whole':True,'individual_shards_are_not_capped':True,'original_coordinates':True,'files':rows})
    for folder in (RUN,DISPLAYS):
        for path in sorted(folder.rglob('*-resources.json')):
            data=LOAD(path)
            if not data['success']:
                row=recovered[str(path.relative_to(ROOT))]
                assert file_binding(path)==row['failed'] and data['status']=='interrupted'
                if 'retry' in row:
                    retry=LOAD(checked_file(row['retry']));assert retry['success'] and retry['command']==data['command']
                else:
                    display=LOAD(checked_file(row['accepted_display']));assert not display['quantitative_mesh_modified']
                    assert row['all_parts_bound_to_same_actual_surface']
                    for part in row['completed_parts_reused']+row['remaining_original_batch_parts']:checked_file(part)
                failed.append(row);continue
            assert data['success'] and len(data['cpu_affinity'])==1
            resources.append({'source':file_binding(path),'seconds':data['seconds'],
                'peak_rss_mib':data['peak_observed_group_rss_bytes']/1024**2})
    write(OUT/'resources-summary.json',{'single_cpu':True,'memory_limit_mib':2048,'job_wall_limit_seconds':60,
        'maximum_job_seconds':max(r['seconds'] for r in resources),
        'maximum_job_rss_mib':max(r['peak_rss_mib'] for r in resources),'jobs':resources,
        'preserved_interrupted_attempts':failed})
    summary.update(raw_shard_instances=192,additional_unique_raw_shards=len(compressed),
        additional_raw_compressed_bytes=sum(r['bytes'] for r in compressed.values()))
    write(OUT/'summary.json',summary)
    files={str(p.relative_to(OUT)):{'sha256':digest(p),'bytes':p.stat().st_size}
        for p in sorted(OUT.rglob('*')) if p.is_file() and p!=OUT/'manifest.json'}
    write(OUT/'manifest.json',{'schema':'genlaw.result-archive.v1','files':files})
    print(json.dumps({'files':len(files),'bytes':sum(r['bytes'] for r in files.values()),
        'unique_raw_shards':len(compressed),'display_triangles':summary['total_display_triangles']}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--phase',choices=['initialize','compress','finish'])
    p.add_argument('--start',type=int);a=p.parse_args()
    if a.phase=='initialize':initialize()
    elif a.phase=='compress':compress(a.start)
    elif a.phase=='finish':finish()
    else:
        QA.mkdir(parents=True,exist_ok=True)
        bounded(QA,'pack-initialize',[sys.executable,Path(__file__),'--phase','initialize'])
        count=len(LOAD(OUT/'compression-plan.json')['unique'])
        for start in range(0,count,8):
            bounded(QA,f'pack-compress-{start:03d}',[sys.executable,Path(__file__),'--phase','compress','--start',str(start)])
            print(json.dumps({'compressed_unique_shards':min(start+8,count),'total':count}),flush=True)
        bounded(QA,'pack-finish',[sys.executable,Path(__file__),'--phase','finish'])
