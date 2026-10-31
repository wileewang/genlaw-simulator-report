"""Complete native material assembled logically from bounded output files.

Cell ownership, native executions and output chunks have distinct identities.
All facets and oriented edges are audited globally without joining the mesh.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'web/tools')]
from mesh_metrics import np,require_bounded_worker
from genlaw_pipeline.cell_windows import checked_file,file_binding,MAX_WINDOWS
from genlaw_pipeline.simulation_engine import workspace_path
from genlaw_pipeline.native_stream_metadata import context,receipt
from genlaw_pipeline.packed_sparse_plan import checked as checked_partition
from genlaw_pipeline.sparse_material import validate_cover
from genlaw_pipeline.sharded_surface import triangles
from genlaw_pipeline.surface_records import audit_points,bucket_count,write_buckets,check_bucket,DTYPES,LIMITS,bucket_ids
from genlaw_pipeline.span_histogram import build as height_histogram,checked as checked_histogram
from genlaw_pipeline.run import write

LOAD=lambda p:json.loads(p.read_text())
SOURCES=['genlaw_pipeline/native_material.py','genlaw_pipeline/native_stream_metadata.py',
    'genlaw_pipeline/native_stream.py','genlaw_pipeline/span_histogram.py',
    'genlaw_pipeline/surface_records.py','genlaw_pipeline/sharded_surface.py',
    'genlaw_pipeline/packed_sparse_plan.py','genlaw_pipeline/sparse_material.py',
    'genlaw_pipeline/material_blocks.py','genlaw_pipeline/cell_windows.py',
    'genlaw_pipeline/sparse_stream_surface.py','genlaw_pipeline/sparse_sections.py',
    'genlaw_pipeline/native_material_entry.py','web/tools/mesh_metrics.py']


def definition(project,build,results,partition=None):
    require_bounded_worker();ctx=context(project,build)
    if partition is None:
        leaves=[{'index':0,'state':'requires_native_cells','bounds':[0,0,0,*ctx['grid']['steps']]}]
    else:
        proof=checked_partition(partition);release=LOAD(build)
        if (proof['binding']['project']!=ctx['inputs']['project']
                or proof['binding']['nc_files']!=ctx['inputs']['nc_files']
                or proof['binding']['engine_manifest']!=release['reference_manifest']
                or proof['grid']!=ctx['grid']):
            raise ValueError('Cell certificates use a different native engine, stock, NC or grid')
        leaves=proof['leaves']
    coverage=validate_cover(leaves,ctx['grid'])
    if not 1<=len(leaves)<=MAX_WINDOWS:raise ValueError('Original native window budget exceeded')
    expected={tuple(r['bounds']):r['index'] for r in leaves if r['state']=='requires_native_cells'}
    if len(results)!=len(expected):raise ValueError('Missing or added native executions')
    rows={}
    for path in results:
        row=receipt(path,ctx);key=tuple(row['window'])
        if key not in expected or key in rows:raise ValueError('Duplicate, foreign or partial native window')
        rows[key]={'index':expected[key],**row}
    windows=sorted(rows.values(),key=lambda r:r['index']);chunks=[]
    for window in windows:
        for chunk in window['chunks']:
            chunks.append({'index':len(chunks),'window_index':window['index'],
                'native_result':window['result'],**chunk})
    total=sum(r['triangles'] for r in chunks)
    if not chunks or len(chunks)>64*MAX_WINDOWS or not total:
        raise ValueError('Empty complete material or invalid native artifact count')
    return {'schema':'genlaw.native-material-plan.v1','context':ctx,'inputs':ctx['inputs'],
        'grid':ctx['grid'],'partition':file_binding(partition) if partition else None,
        'partition_role':'cell_ownership_certificates_only' if partition else 'one_complete_global_grid',
        'native_windows':windows,'chunks':chunks,'triangles':total,'coverage':coverage,
        'bucket_counts':{'facets':bucket_count(total,'facets'),'edges':bucket_count(3*total,'edges')},
        'sources':[file_binding(ROOT/p) for p in SOURCES],'record_limits':LIMITS,
        'maximum_native_windows':MAX_WINDOWS,'maximum_files_per_window':64,
        'maximum_bucket_source_files_per_worker':MAX_WINDOWS,
        'whole_mesh_assembled':False,'payload_validation':'each_original_chunk_worker',
        'production_approved':False,'whole_process_card_complete':False}


def prepare(project,build,results,output,partition=None):
    write(output,definition(project,build,results,partition))


def checked_plan(path):
    plan=LOAD(path)
    if plan.get('schema')!='genlaw.native-material-plan.v1':raise ValueError('Unsupported native material plan')
    ctx=plan['context'];partition=checked_file(plan['partition']) if plan.get('partition') else None
    expected=definition(checked_file(ctx['inputs']['project']),checked_file(ctx['build']),
        [checked_file(w['result']) for w in plan['native_windows']],partition)
    if plan!=expected:raise ValueError('Native material ownership, file membership or source changed')
    return plan


def audit_chunk(plan_path,index,out):
    require_bounded_worker();plan=checked_plan(plan_path)
    if type(index) is not int or not 0<=index<len(plan['chunks']):raise ValueError('Invalid native chunk')
    _audit_chunk(plan_path,plan,plan['chunks'][index],out)


def audit_chunks(plan_path,indices,out):
    require_bounded_worker();plan=checked_plan(plan_path)
    if (not 1<=len(indices)<=64 or indices!=sorted(set(indices))
            or any(type(i) is not int or not 0<=i<len(plan['chunks']) for i in indices)
            or sum(plan['chunks'][i]['triangles'] for i in indices)>333333):
        raise ValueError('Chunk batch exceeds unchanged total triangle/edge worker limits')
    for i in indices:_audit_chunk(plan_path,plan,plan['chunks'][i],out/f'chunk-{i:05d}')


def _audit_chunk(plan_path,plan,entry,out):
    mesh=checked_file(entry['surface']);points=triangles(mesh)
    if len(points)!=entry['triangles']:raise ValueError('Native chunk count changed')
    out=out.resolve();out.relative_to(ROOT);out.mkdir(parents=True,exist_ok=False)
    stats,facets,edges=audit_points(points)
    records={kind:write_buckets(data,plan['bucket_counts'][kind],kind,out)
        for kind,data in [('facets',facets),('edges',edges)]}
    del facets,edges
    side=points[np.ptp(points[:,:,2],axis=1)>0]
    heights=np.column_stack((side[:,:,2].min(axis=1),side[:,:,2].max(axis=1)))
    for name,array in [('sections',side),('heights',heights)]:
        with (out/(name+'.npy')).open('xb') as f:np.save(f,array,allow_pickle=False)
    identity={'material_plan':file_binding(plan_path),'chunk':entry,'source_code':file_binding(Path(__file__))}
    cache={'schema':'genlaw.native-chunk-cache.v1',**identity,'triangles':len(side),
        'omitted_exactly_horizontal_triangles':len(points)-len(side),'geometry_modified':False,
        'array':file_binding(out/'sections.npy'),'heights':file_binding(out/'heights.npy'),
        'height_histogram':height_histogram(heights)}
    write(out/'cache.json',cache)
    write(out/'audit.json',{'schema':'genlaw.native-chunk-audit.v1',**identity,
        'statistics':stats,'records':records,'cache':file_binding(out/'cache.json')})


def checked_cache(binding,plan_binding,entry):
    cache=LOAD(checked_file(binding));n=cache.get('triangles')
    if (cache.get('schema')!='genlaw.native-chunk-cache.v1' or cache.get('material_plan')!=plan_binding
            or cache.get('chunk')!=entry or cache.get('source_code')!=file_binding(Path(__file__))
            or cache.get('geometry_modified') is not False or type(n) is not int
            or not 0<=n<=entry['triangles']<=500000
            or type(cache.get('omitted_exactly_horizontal_triangles')) is not int
            or n+cache['omitted_exactly_horizontal_triangles']!=entry['triangles']
            or cache['height_histogram']['triangles']!=n):
        raise ValueError('Changed native cache provenance or count')
    checked_histogram(cache['height_histogram'])
    return cache


def checked_audit(path,plan_path,plan,entry):
    row=LOAD(path);n=entry['triangles']
    if (row.get('schema')!='genlaw.native-chunk-audit.v1' or row.get('material_plan')!=file_binding(plan_path)
            or row.get('chunk')!=entry or row.get('source_code')!=file_binding(Path(__file__))):
        raise ValueError('Foreign native chunk audit')
    stats=row['statistics'];levels=stats['z_levels_mm'];bounds=stats['bounds']
    if (stats['triangles']!=n or not math.isfinite(stats['signed_volume_mm3'])
            or type(stats['boundary_edges']) is not int or not 0<=stats['boundary_edges']<=3*n
            or not 1<=len(levels)<=4096 or levels!=sorted(set(levels))
            or not all(math.isfinite(z) for z in levels)
            or not isinstance(bounds,dict) or any(len(bounds[k])!=3 for k in ['min','max'])
            or not all(math.isfinite(v) for k in ['min','max'] for v in bounds[k])
            or any(a>b for a,b in zip(bounds['min'],bounds['max']))
            or [bounds['min'][2],bounds['max'][2]]!=[levels[0],levels[-1]]):
        raise ValueError('Invalid native chunk statistics')
    if set(row['records'])!=set(DTYPES):raise ValueError('Missing facet or edge records')
    counts={'facets':n,'edges':(3*n+stats['boundary_edges'])//2}
    for kind,part in row['records'].items():
        if part['records']!=counts[kind] or part['records']>LIMITS[kind]:raise ValueError('Invalid record count')
        offset=0;previous=-1
        for bucket in part['buckets']:
            if (type(bucket['index']) is not int or not previous<bucket['index']<plan['bucket_counts'][kind]
                    or bucket['offset']!=offset or type(bucket['records']) is not int or bucket['records']<=0):
                raise ValueError('Missing or overlapping native record range')
            offset+=bucket['records']*DTYPES[kind].itemsize;previous=bucket['index']
        if offset!=part['records']*DTYPES[kind].itemsize:raise ValueError('Dropped native records')
    checked_cache(row['cache'],file_binding(plan_path),entry)
    return row


def catalog(plan_path,audits,out):
    require_bounded_worker();plan=checked_plan(plan_path)
    if len(audits)!=len(plan['chunks']):raise ValueError('Missing original chunk audit')
    out=out.resolve();out.relative_to(ROOT);out.mkdir(parents=True,exist_ok=False)
    requests={k:[[] for _ in range(v)] for k,v in plan['bucket_counts'].items()}
    stats=[];chunks=[];totals=dict.fromkeys(DTYPES,0)
    for entry,path in zip(plan['chunks'],audits):
        row=checked_audit(path,plan_path,plan,entry);binding=file_binding(path);stats.append(row['statistics'])
        chunks.append({'index':entry['index'],'audit':binding,'cache':row['cache'],
            'files':{k:v['file'] for k,v in row['records'].items()}})
        for kind,part in row['records'].items():
            totals[kind]+=part['records']
            for bucket in part['buckets']:
                requests[kind][bucket['index']].append({**bucket,'file':part['file'],
                    'chunk_index':entry['index'],'audit':binding})
    levels=sorted({z for s in stats for z in s['z_levels_mm']});volume=math.fsum(s['signed_volume_mm3'] for s in stats)
    if not 2<=len(levels)<=4096 or not math.isfinite(volume) or volume<=0:
        raise ValueError('Invalid complete native material statistics')
    bounds={k:[fn(s['bounds'][k][i] for s in stats) for i in range(3)] for k,fn in [('min',min),('max',max)]}
    bound={}
    for kind,groups in requests.items():
        bound[kind]=[]
        for index,files in enumerate(groups):
            count=sum(r['records'] for r in files)
            if count>LIMITS[kind]:raise ValueError('Global hash bucket exceeds unchanged record budget')
            path=out/f'{kind}-{index:04d}-request.json'
            write(path,{'schema':'genlaw.native-bucket-request.v1','material_plan':file_binding(plan_path),
                'kind':kind,'index':index,'files':files,'records':count,
                'source_batches':[[i,min(i+MAX_WINDOWS,len(files))] for i in range(0,len(files),MAX_WINDOWS)]})
            bound[kind].append(file_binding(path))
    write(out/'catalog.json',{'schema':'genlaw.native-material-catalog.v1','material_plan':file_binding(plan_path),
        'chunks':chunks,'requests':bound,'record_totals':totals,
        'statistics':{'triangles':plan['triangles'],'volume_mm3':volume,'bounds':bounds,'z_levels_mm':levels},
        'source_code':file_binding(Path(__file__))})


def checked_catalog(path):
    data=LOAD(path)
    if data.get('schema')!='genlaw.native-material-catalog.v1' or data.get('source_code')!=file_binding(Path(__file__)):
        raise ValueError('Unsupported native material catalog')
    plan_path=checked_file(data['material_plan']);plan=checked_plan(plan_path)
    if ([r['index'] for r in data['chunks']]!=list(range(len(plan['chunks'])))
            or set(data['requests'])!=set(DTYPES)
            or any(len(data['requests'][k])!=plan['bucket_counts'][k] for k in DTYPES)
            or data['record_totals']['facets']!=plan['triangles']):
        raise ValueError('Incomplete original native material catalog')
    statistics=[];totals=dict.fromkeys(DTYPES,0)
    requests={k:[[] for _ in range(v)] for k,v in plan['bucket_counts'].items()}
    for entry,chunk in zip(plan['chunks'],data['chunks']):
        audit=checked_audit(checked_file(chunk['audit']),plan_path,plan,entry)
        if (chunk['cache']!=audit['cache']
                or chunk['files']!={k:v['file'] for k,v in audit['records'].items()}):
            raise ValueError('Catalog cache or file identity differs from original chunk audit')
        statistics.append(audit['statistics'])
        for kind,part in audit['records'].items():
            totals[kind]+=part['records']
            for bucket in part['buckets']:
                requests[kind][bucket['index']].append({**bucket,'file':part['file'],
                    'chunk_index':entry['index'],'audit':chunk['audit']})
    expected_stats={'triangles':plan['triangles'],
        'volume_mm3':math.fsum(r['signed_volume_mm3'] for r in statistics),
        'bounds':{k:[fn(r['bounds'][k][i] for r in statistics) for i in range(3)] for k,fn in [('min',min),('max',max)]},
        'z_levels_mm':sorted({z for r in statistics for z in r['z_levels_mm']})}
    if (data['statistics']!=expected_stats or data['record_totals']!=totals
            or expected_stats['volume_mm3']<=0 or not 2<=len(expected_stats['z_levels_mm'])<=4096):
        raise ValueError('Catalog statistics differ from complete original chunk audits')
    for kind,groups in requests.items():
        for index,files in enumerate(groups):
            expected={'schema':'genlaw.native-bucket-request.v1','material_plan':data['material_plan'],
                'kind':kind,'index':index,'files':files,'records':sum(r['records'] for r in files),
                'source_batches':[[i,min(i+MAX_WINDOWS,len(files))] for i in range(0,len(files),MAX_WINDOWS)]}
            if LOAD(checked_file(data['requests'][kind][index]))!=expected:
                raise ValueError('Catalog dropped or changed original bucket contributors')
    return data,plan


def bucket_request(catalog_path,kind,index):
    data,plan=checked_catalog(catalog_path)
    if kind not in DTYPES or type(index) is not int or not 0<=index<plan['bucket_counts'][kind]:
        raise ValueError('Invalid native bucket selection')
    path=checked_file(data['requests'][kind][index]);request=LOAD(path);indices=[]
    if (request.get('schema')!='genlaw.native-bucket-request.v1' or request.get('kind')!=kind
            or request.get('index')!=index or request.get('material_plan')!=data['material_plan']):
        raise ValueError('Foreign native bucket request')
    for row in request['files']:
        i=row.get('chunk_index')
        if type(i) is not int or not 0<=i<len(data['chunks']):raise ValueError('Foreign bucket chunk')
        chunk=data['chunks'][i];indices.append(i)
        audit=checked_audit(checked_file(chunk['audit']),checked_file(data['material_plan']),plan,plan['chunks'][i])
        matches=[r for r in audit['records'][kind]['buckets'] if r['index']==index]
        if (len(matches)!=1 or row!={**matches[0],'file':audit['records'][kind]['file'],
                'chunk_index':i,'audit':chunk['audit']} or chunk['files'][kind]!=row['file']):
            raise ValueError('Native bucket source range changed')
    n=len(indices)
    if (indices!=sorted(set(indices)) or sum(r['records'] for r in request['files'])!=request['records']
            or request['source_batches']!=[[i,min(i+MAX_WINDOWS,n)] for i in range(0,n,MAX_WINDOWS)]
            or request['records']>LIMITS[kind]):raise ValueError('Duplicate or incomplete bucket sources')
    return data,plan,path,request


def concatenate_sources(files,count,index,kind,output):
    """Only concatenate a bounded source batch; partial output cannot pass topology."""
    require_bounded_worker()
    if not 1<=len(files)<=MAX_WINDOWS or sum(r['records'] for r in files)>LIMITS[kind]:
        raise ValueError('Bucket batch exceeds the unchanged source/record limits')
    if len({r['file']['path'] for r in files})!=len(files):raise ValueError('Duplicate record files')
    total=0
    with output.open('xb') as out:
        for row in files:
            path=workspace_path(row['file']['path']);size=row['records']*DTYPES[kind].itemsize;offset=row['offset']
            if (type(offset) is not int or offset<0 or offset%DTYPES[kind].itemsize
                    or type(row['records']) is not int or row['records']<=0 or row['index']!=index
                    or offset+size>path.stat().st_size or path.stat().st_size>LIMITS[kind]*DTYPES[kind].itemsize):
                raise ValueError('Invalid bucket source range')
            with path.open('rb') as f:f.seek(offset);payload=f.read(size)
            if len(payload)!=size or hashlib.sha256(payload).hexdigest()!=row['sha256']:
                raise ValueError('Changed native bucket bytes')
            if np.any(bucket_ids(np.frombuffer(payload,dtype=DTYPES[kind]),count)!=index):
                raise ValueError('Foreign native bucket records')
            out.write(payload);total+=row['records']
    return total


def bucket_part(catalog_path,kind,index,part,output):
    require_bounded_worker();_,plan,request_path,request=bucket_request(catalog_path,kind,index)
    if len(request['source_batches'])<=1 or type(part) is not int or not 0<=part<len(request['source_batches']):
        raise ValueError('Unnecessary or invalid bounded bucket batch')
    start,end=request['source_batches'][part];payload=output.with_suffix('.bin')
    count=concatenate_sources(request['files'][start:end],plan['bucket_counts'][kind],index,kind,payload)
    write(output,{'schema':'genlaw.native-bucket-part.v1','request':file_binding(request_path),'part':part,
        'source_interval':[start,end],'records':count,'file':file_binding(payload),'partial_only':True,
        'source_code':file_binding(Path(__file__))})


def audit_bucket(catalog_path,kind,index,output,parts=()):
    require_bounded_worker();_,plan,request_path,request=bucket_request(catalog_path,kind,index)
    if len(request['source_batches'])<=1:
        if parts:raise ValueError('Unexpected partial bucket artifacts')
        files=request['files']
    else:
        if len(parts)!=len(request['source_batches']):raise ValueError('Missing bounded source batch')
        files=[]
        for i,(path,interval) in enumerate(zip(parts,request['source_batches'])):
            row=LOAD(path);start,end=interval
            if (row.get('schema')!='genlaw.native-bucket-part.v1' or row.get('request')!=file_binding(request_path)
                    or row.get('part')!=i or row.get('source_interval')!=interval or row.get('partial_only') is not True
                    or row.get('records')!=sum(r['records'] for r in request['files'][start:end])
                    or row.get('source_code')!=file_binding(Path(__file__))):raise ValueError('Foreign or incomplete source batch')
            checked_file(row['file'])
            files.append({'file':row['file'],'records':row['records'],'index':index,'offset':0,
                'sha256':row['file']['sha256']})
    result=check_bucket(files,plan['bucket_counts'][kind],index,kind)
    write(output,{'schema':'genlaw.native-bucket-audit.v1','catalog':file_binding(catalog_path),
        'request':file_binding(request_path),'kind':kind,'index':index,**result,
        'parts':[file_binding(p) for p in parts],'source_code':file_binding(Path(__file__))})


def accepted_buckets(catalog_path,data,plan,paths):
    expected={(k,i) for k in DTYPES for i in range(plan['bucket_counts'][k])};seen={}
    totals=dict.fromkeys(DTYPES,0);unique=dict.fromkeys(DTYPES,0)
    for path in paths:
        row=LOAD(path);key=(row.get('kind'),row.get('index'))
        if (row.get('schema')!='genlaw.native-bucket-audit.v1' or key not in expected or key in seen
                or row.get('catalog')!=file_binding(catalog_path) or row.get('source_code')!=file_binding(Path(__file__))
                or row.get('request')!=data['requests'][key[0]][key[1]]):raise ValueError('Missing or foreign global bucket audit')
        request=LOAD(checked_file(row['request']))
        if (row['records']!=request['records'] or type(row['unique_keys']) is not int
                or not 0<=row['unique_keys']<=row['records']):raise ValueError('Global bucket lost records')
        seen[key]=True;totals[key[0]]+=row['records'];unique[key[0]]+=row['unique_keys']
    if (set(seen)!=expected or totals!=data['record_totals'] or unique['facets']!=plan['triangles']
            or 2*unique['edges']!=3*unique['facets']):raise ValueError('Incomplete, duplicated or open complete material surface')
    return unique


def finish(catalog_path,results,output):
    require_bounded_worker();data,plan=checked_catalog(catalog_path)
    unique=accepted_buckets(catalog_path,data,plan,results)
    write(output,{'schema':'genlaw.native-material-surface.v1','status':'closed_oriented_surface',
        'catalog':file_binding(catalog_path),'material_plan':data['material_plan'],
        'bucket_audits':[file_binding(p) for p in results],**data['statistics'],
        'unique_edges':unique['edges'],'coverage':plan['coverage'],
        'caches':[r['cache'] for r in data['chunks']],'source_code':file_binding(Path(__file__)),
        'native_execution_windows':len(plan['native_windows']),'output_files':len(plan['chunks']),
        'all_global_cells_covered_once':True,'whole_mesh_assembled':False,'geometry_modified':False,
        'artificial_caps':False,'synthetic_empty_stls':0,'production_approved':False,'whole_process_card_complete':False})


def checked_surface(path):
    require_bounded_worker();surface=LOAD(path)
    if (surface.get('schema')!='genlaw.native-material-surface.v1' or surface.get('status')!='closed_oriented_surface'
            or surface.get('source_code')!=file_binding(Path(__file__))
            or surface.get('all_global_cells_covered_once') is not True
            or any(surface.get(k) is not False for k in ['whole_mesh_assembled','geometry_modified','artificial_caps',
                'production_approved','whole_process_card_complete']) or surface.get('synthetic_empty_stls')!=0):
        raise ValueError('A complete native material surface is required')
    catalog_path=checked_file(surface['catalog']);data,plan=checked_catalog(catalog_path)
    if (surface.get('material_plan')!=data['material_plan'] or surface.get('coverage')!=plan['coverage']
            or surface.get('caches')!=[r['cache'] for r in data['chunks']]
            or surface.get('native_execution_windows')!=len(plan['native_windows'])
            or surface.get('output_files')!=len(plan['chunks'])
            or any(surface.get(k)!=v for k,v in data['statistics'].items())):
        raise ValueError('Native material coverage, cache or statistics changed')
    unique=accepted_buckets(catalog_path,data,plan,[checked_file(b) for b in surface['bucket_audits']])
    if surface['unique_edges']!=unique['edges']:raise ValueError('Changed native edge count')
    for entry,chunk in zip(plan['chunks'],data['chunks']):
        row=checked_audit(checked_file(chunk['audit']),checked_file(data['material_plan']),plan,entry)
        if chunk['cache']!=row['cache']:raise ValueError('Unbound native section cache')
    return surface,plan,data


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','chunk','chunks','catalog','bucket-part','bucket','finish'])
    for name in ['project','build','partition','plan','catalog','output']:p.add_argument('--'+name,type=Path)
    p.add_argument('--audit-index',type=Path);p.add_argument('--result-index',type=Path)
    for name in ['results','audits','parts']:p.add_argument('--'+name,nargs='*',type=Path,default=[])
    p.add_argument('--index',type=int);p.add_argument('--indices',nargs='+',type=int);p.add_argument('--part',type=int);p.add_argument('--kind',choices=sorted(DTYPES));a=p.parse_args()
    if a.audit_index:a.audits=[checked_file(b) for b in LOAD(a.audit_index)['audits']]
    if a.result_index:a.results=[checked_file(b) for b in LOAD(a.result_index)['results']]
    if a.action=='prepare':prepare(a.project,a.build,a.results,a.output,a.partition)
    elif a.action=='chunk':audit_chunk(a.plan,a.index,a.output)
    elif a.action=='chunks':audit_chunks(a.plan,a.indices,a.output)
    elif a.action=='catalog':catalog(a.plan,a.audits,a.output)
    elif a.action=='bucket-part':bucket_part(a.catalog,a.kind,a.index,a.part,a.output)
    elif a.action=='bucket':audit_bucket(a.catalog,a.kind,a.index,a.output,a.parts)
    else:finish(a.catalog,a.results,a.output)
