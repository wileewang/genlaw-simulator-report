"""Collect every original native window with exact candidates and local archives.

A completed window is not a closed complete material. Original cell ownership,
native receipts and candidate proofs keep distinct identities. Bulk payloads
are retained locally; the small manifests can be versioned independently.
"""
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from genlaw_pipeline.cell_windows import checked_file,file_binding,window_budget
from genlaw_pipeline.packed_sparse_plan import checked as checked_partition
from genlaw_pipeline.native_stream_metadata import context,receipt
from genlaw_pipeline.native_planar import batch as compact_batch
from genlaw_pipeline.planar_material import checked_candidate
from genlaw_pipeline.sparse_material import checked_resources
from genlaw_pipeline.sparse_stream_surface import header_count
from genlaw_pipeline.run import write
from step2gcode_demo.validation.local_resource_limits import require_bounded_worker

LOAD=lambda p:json.loads(Path(p).read_text())
MAX_FILE=64*1024**2
DISK_FLOOR=math.ceil(4.5*1024**3)
SOURCE_NAMES=['genlaw_pipeline/native_collection.py','genlaw_pipeline/native_collection_entry.py',
    'genlaw_pipeline/native_stream.py','genlaw_pipeline/native_stream_metadata.py',
    'genlaw_pipeline/native_planar.py','genlaw_pipeline/planar_material.py',
    'genlaw_pipeline/packed_sparse_plan.py','genlaw_pipeline/planar_resume.py']


def sources():return [file_binding(ROOT/p) for p in SOURCE_NAMES]


def allocation(budget):
    if budget['allowed'] is not True:raise ValueError('Original per-window compute budget exceeded')
    if type(budget['cell_count']) is not int or not 0<budget['cell_count']<=5000000:
        raise ValueError('Invalid original cell count')
    triangles=5*budget['cell_count'];chunks=math.ceil(triangles/500000)
    if chunks>64:raise ValueError('Original native file-count limit exceeded')
    raw=50*triangles+84*chunks
    # Native and no-larger candidate payloads, conservative archive space,
    # candidate proof / worker log file maxima, and fixed metadata reserve.
    workers=chunks+4
    return {'source_bound_triangles':triangles,'source_bound_native_files':chunks,
        'source_bound_native_stl_bytes':raw,
        'required_free_bytes_before_window':DISK_FLOOR+6*raw+(chunks+workers)*MAX_FILE+512*1024**2,
        'disk_floor_bytes':DISK_FLOOR,'reserve_is_not_a_whole_stock_size_prediction':True}


def groups(chunks):
    result=[];current=[];count=0
    for i,row in enumerate(chunks):
        n=row['triangles']
        if type(row['shard_index']) is not int or row['shard_index']!=i or type(n) is not int or not 0<n<=500000:
            raise ValueError('Malformed original chunk sequence')
        if current and (count+n>500000 or len(current)==64):result.append(current);current=[];count=0
        current.append(i);count+=n
    if current:result.append(current)
    return result


def definition(partition,build):
    require_bounded_worker();original=checked_partition(partition)
    ctx=context(checked_file(original['binding']['project']),build);release=LOAD(build)
    if (original['binding']['nc_files']!=ctx['inputs']['nc_files'] or original['grid']!=ctx['grid']
            or original['binding']['engine_manifest']!=release['reference_manifest']):
        raise ValueError('Collection NC, stock, grid or native build differs from the original partition')
    windows=[]
    for leaf in original['leaves']:
        if leaf['state']!='requires_native_cells':continue
        budget=window_budget(leaf['bounds'],original['grid']['steps'])
        windows.append({'index':leaf['index'],'bounds':leaf['bounds'],'budget':budget,'storage':allocation(budget)})
    return {'schema':'genlaw.native-collection-plan.v1','partition':file_binding(partition),
        'build':file_binding(build),'context':ctx,'coverage':original['coverage'],'windows':windows,
        'execution_order':[r['index'] for r in sorted(windows,key=lambda r:(-r['storage']['source_bound_native_stl_bytes'],r['index']))],
        'certified_homogeneous_leaves':sum(r['state']!='requires_native_cells' for r in original['leaves']),
        'sources':sources(),'native_facets_per_file':500000,'max_group_original_triangles':500000,
        'bulk_archive_storage':'local_workspace','original_partition_modified':False,
        'global_geometry_verified':False,'whole_process_card_complete':False,'production_approved':False}


def initialize(partition,build,output):write(output,definition(partition,build))


def checked(plan_path):
    value=LOAD(plan_path)
    if value.get('schema')!='genlaw.native-collection-plan.v1':raise ValueError('Unsupported collection plan')
    expected=definition(checked_file(value['partition']),checked_file(value['build']))
    if value!=expected:raise ValueError('Collection changed original ownership, execution order, resources or sources')
    return value


def window(plan,index):
    if type(index) is not int:raise ValueError('Invalid collection window index')
    rows=[r for r in plan['windows'] if r['index']==index]
    if len(rows)!=1:raise ValueError('Window is not a required original native region')
    return rows[0]


def folder(plan_path,index):return plan_path.parent/'windows'/f'window-{index:04d}'


def group_command(plan_path,index,number):
    return [sys.executable,Path(__file__).resolve(),'group','--plan',plan_path,'--index',str(index),'--group',str(number)]


def seal_command(plan_path,index):
    return [sys.executable,Path(__file__).resolve(),'seal','--plan',plan_path,'--index',str(index)]


def restored_archive(row):
    original=checked_file(row['original']);archive=checked_file(row['archive']);n=original.stat().st_size
    if (n!=row['bytes'] or not 0<n<=MAX_FILE or archive.stat().st_size!=row['archive_bytes']
            or not 0<row['archive_bytes']<=MAX_FILE):
        raise ValueError('Archive framing or original size changed')
    digest=hashlib.sha256();count=0
    with gzip.open(archive,'rb') as stream:
        for data in iter(lambda:stream.read(1024**2),b''):
            count+=len(data)
            if count>n:raise ValueError('Archive expands beyond the original bounded file')
            digest.update(data)
    if count!=n or digest.hexdigest()!=row['original']['sha256']:raise ValueError('Archive does not restore exact original bytes')


def archive(binding,path):
    original=checked_file(binding);n=original.stat().st_size
    if not 0<n<=MAX_FILE:raise ValueError('Archive input exceeds unchanged per-file bound')
    with original.open('rb') as source,path.open('xb') as target:
        with gzip.GzipFile(fileobj=target,mode='wb',filename='',mtime=0,compresslevel=1) as compressed:
            for data in iter(lambda:source.read(1024**2),b''):compressed.write(data)
    row={'original':binding,'bytes':n,'archive':file_binding(path),'archive_bytes':path.stat().st_size,'restore_verified':True}
    if row['archive_bytes']>MAX_FILE or row['archive_bytes']>2*n:raise ValueError('Archive exceeds its reserved output budget')
    restored_archive(row);return row


def native_for(plan,spec,path):
    native=receipt(path,plan['context'])
    if native['window']!=spec['bounds']:raise ValueError('Native result belongs to a different original window')
    return native


def group(plan_path,index,number):
    require_bounded_worker();plan=checked(plan_path);spec=window(plan,index);out=folder(plan_path,index)
    native=native_for(plan,spec,out/'native/result.json');parts=groups(native['chunks'])
    if type(number) is not int or not 0<=number<len(parts):raise ValueError('Invalid original chunk group')
    indices=parts[number];target=out/'groups'/f'group-{number:04d}';target.mkdir(parents=True,exist_ok=False)
    compact_batch(out/'native/result.json',indices,out/'candidates')
    candidates=[];archives=[]
    for i in indices:
        path=out/'candidates'/f'chunk-{i:04d}/result.json';value=LOAD(path);candidates.append(file_binding(path))
        for kind,binding in [('native',native['chunks'][i]['surface']),('candidate',value['surface'])]:
            archives.append(archive(binding,target/f'{kind}-{i:04d}.stl.gz'))
    write(target/'result.json',{'schema':'genlaw.native-collection-group.v1','plan':file_binding(plan_path),
        'window_index':index,'group':number,'indices':indices,'native_result':native['result'],
        'candidates':candidates,'archives':archives,'source_code':file_binding(Path(__file__))})


def validate_payloads(native,candidates,archives,full):
    if len(candidates)!=len(native['chunks']) or len(archives)!=2*len(candidates):
        raise ValueError('Missing candidate or exact archive for an original chunk')
    expected=[];total=0
    for binding,entry in zip(candidates,native['chunks']):
        proof=checked_candidate(checked_file(binding),{'native_result':native['result'],**entry})
        surface=ROOT/proof['surface']['path']
        if header_count(surface)!=proof['candidate_statistics']['triangles']:raise ValueError('Candidate STL count changed')
        if full:checked_file(entry['surface']);checked_file(proof['surface'])
        expected.extend([entry['surface'],proof['surface']]);total+=proof['candidate_statistics']['triangles']
    if [r['original'] for r in archives]!=expected:raise ValueError('Archives omitted, reordered or replaced original geometry')
    for row in archives:
        if row.get('restore_verified') is not True:raise ValueError('Missing exact archive restoration check')
        if full:checked_file(row['archive'])
        path=ROOT/row['archive']['path'];original=ROOT/row['original']['path']
        if (path.stat().st_size!=row['archive_bytes'] or original.stat().st_size!=row['bytes']
                or not 0<row['bytes']<=MAX_FILE or not 0<row['archive_bytes']<=MAX_FILE):
            raise ValueError('Local archive or original is missing, oversized or changed size')
    return {'native_triangles':native['triangles'],'candidate_triangles':total,
        'native_output_files':len(native['chunks']),'raw_and_candidate_bytes':sum(r['bytes'] for r in archives),
        'local_archive_bytes':sum(r['archive_bytes'] for r in archives)}


def write_window(plan_path,index,native,candidates,archives,import_input=None):
    stats=validate_payloads(native,candidates,archives,True)
    write(folder(plan_path,index)/'result.json',{'schema':'genlaw.native-collection-window.v1',
        'status':'original_window_collected','plan':file_binding(plan_path),'window_index':index,
        'window':native['window'],'native_result':native['result'],'candidates':candidates,'archives':archives,**stats,
        'import_input':file_binding(import_input) if import_input else None,'source_code':file_binding(Path(__file__)),
        'complete_original_window':True,'all_window_payloads_checked_at_completion':True,
        'global_geometry_verified':False,'whole_process_card_complete':False,'production_approved':False})


def seal(plan_path,index):
    require_bounded_worker();plan=checked(plan_path);spec=window(plan,index);out=folder(plan_path,index)
    native=native_for(plan,spec,out/'native/result.json');candidates=[];archives=[]
    for number,indices in enumerate(groups(native['chunks'])):
        label=f'group-{number:04d}';row=LOAD(out/'groups'/label/'result.json')
        checked_resources(out/(label+'-resources.json'),group_command(plan_path,index,number))
        if (row.get('schema')!='genlaw.native-collection-group.v1' or row.get('plan')!=file_binding(plan_path)
                or row.get('window_index')!=index or row.get('group')!=number or row.get('indices')!=indices
                or row.get('native_result')!=native['result'] or row.get('source_code')!=file_binding(Path(__file__))):
            raise ValueError('Unbound or incomplete native collection group')
        candidates.extend(row['candidates']);archives.extend(row['archives'])
    checked_resources(out/'native/validate-resources.json',
        [sys.executable,ROOT/'genlaw_pipeline/native_stream.py','finish','--out',out/'native'])
    write_window(plan_path,index,native,candidates,archives)


def import_window(plan_path,input_path):
    require_bounded_worker();plan=checked(plan_path);request=LOAD(input_path)
    native=receipt(checked_file(request['native_result']),plan['context'])
    rows=[r for r in plan['windows'] if r['bounds']==native['window']]
    if len(rows)!=1:raise ValueError('Imported native material does not match an original required window')
    index=rows[0]['index'];out=folder(plan_path,index);out.mkdir(parents=True,exist_ok=False)
    lookup={}
    for binding in request['archive_indexes']:
        for row in LOAD(checked_file(binding))['files']:
            key=row['original']['path']
            if key in lookup:raise ValueError('Ambiguous original archive source')
            lookup[key]=(row,binding)
    expected=[]
    if len(request['candidates'])!=len(native['chunks']):raise ValueError('Imported candidate count differs')
    for binding,entry in zip(request['candidates'],native['chunks']):
        proof=checked_candidate(checked_file(binding),{'native_result':native['result'],**entry})
        expected.extend([entry['surface'],proof['surface']])
    archives=[]
    for binding in expected:
        old,source=lookup[binding['path']]
        if old['original']!=binding:raise ValueError('Old archive belongs to different raw geometry')
        row={k:old[k] for k in ['original','bytes','archive','archive_bytes']}
        restored_archive(row);archives.append({**row,'restore_verified':True,'source_archive_index':source})
    write_window(plan_path,index,native,request['candidates'],archives,input_path)


def window_state(plan_path,plan,index):
    """Recheck receipts/metadata; full payload replay is the material consumer's job."""
    path=folder(plan_path,index)/'result.json';row=LOAD(path);spec=window(plan,index)
    if (row.get('schema')!='genlaw.native-collection-window.v1' or row.get('status')!='original_window_collected'
            or row.get('plan')!=file_binding(plan_path) or row.get('window_index')!=index or row.get('window')!=spec['bounds']
            or row.get('source_code')!=file_binding(Path(__file__)) or row.get('complete_original_window') is not True
            or row.get('all_window_payloads_checked_at_completion') is not True
            or any(row.get(k) is not False for k in ['global_geometry_verified','whole_process_card_complete','production_approved'])):
        raise ValueError('Incomplete or overstated collection window')
    native=native_for(plan,spec,checked_file(row['native_result']))
    stats=validate_payloads(native,row['candidates'],row['archives'],False)
    if any(row.get(k)!=v for k,v in stats.items()):raise ValueError('Collection statistics differ from bound original receipts')
    if row['import_input']:
        source=checked_file(row['import_input']);resource=plan_path.parent/f'import-{index:04d}-resources.json'
        command=[sys.executable,Path(__file__).resolve(),'import','--plan',plan_path,'--input',source]
    else:resource=folder(plan_path,index)/'seal-resources.json';command=seal_command(plan_path,index)
    checked_resources(resource,command)
    return row,{'window_index':index,'result':file_binding(path),'completion_resources':file_binding(resource),**stats}


def snapshot(plan_path,output):
    require_bounded_worker();plan=checked(plan_path);rows=[];pending=[];imports=0
    for spec in plan['windows']:
        i=spec['index'];path=folder(plan_path,i)/'result.json'
        if not path.exists():pending.append(i);continue
        row,summary=window_state(plan_path,plan,i);rows.append(summary);imports+=row['import_input'] is not None
    write(output,{'schema':'genlaw.native-collection-progress.v1','plan':file_binding(plan_path),
        'status':'all_native_windows_collected' if not pending else 'partial_native_collection',
        'required_windows':len(plan['windows']),'completed_windows':rows,'completed_window_count':len(rows),
        'imported_windows':imports,'new_native_windows':len(rows)-imports,'pending_window_indices':pending,
        'statistics':{k:sum(r[k] for r in rows) for k in ['native_triangles','candidate_triangles','native_output_files','raw_and_candidate_bytes','local_archive_bytes']},
        'metadata_revalidated':True,'all_payload_bytes_revalidated_in_this_snapshot':False,
        'bulk_archives_stored_locally':True,'global_geometry_verified':False,'whole_process_card_complete':False,
        'production_approved':False,'source_code':file_binding(Path(__file__))})
    print(json.dumps({'collected':len(rows),'required':len(plan['windows']),'imported':imports,'new_native':len(rows)-imports}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['initialize','group','seal','import','snapshot'])
    for name in ['partition','build','plan','input','output']:p.add_argument('--'+name,type=Path)
    p.add_argument('--index',type=int);p.add_argument('--group',type=int);a=p.parse_args()
    if a.action=='initialize':initialize(a.partition,a.build,a.output)
    elif a.action=='group':group(a.plan,a.index,a.group)
    elif a.action=='seal':seal(a.plan,a.index)
    elif a.action=='import':import_window(a.plan,a.input)
    else:snapshot(a.plan,a.output)
