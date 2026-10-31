"""Complete exact-planar material with bounded record-index pages.

Native execution, original chunk audits and geometric kernels are reused.
The catalog never builds the Cartesian graph of every bucket/contributor.
"""
import argparse
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'web/tools')]
from mesh_metrics import np,require_bounded_worker
from genlaw_pipeline.cell_windows import checked_file,file_binding,MAX_WINDOWS
from genlaw_pipeline.planar_material import checked_plan,checked_audit
from genlaw_pipeline.native_material import concatenate_sources
from genlaw_pipeline.bucket_directory import write_page,BucketPage,MAX_SOURCES
from genlaw_pipeline.surface_records import DTYPES,LIMITS,check_bucket
from genlaw_pipeline.run import write

LOAD=lambda p:json.loads(p.read_text())
SOURCES=['genlaw_pipeline/planar_directory.py','genlaw_pipeline/planar_directory_entry.py',
    'genlaw_pipeline/bucket_directory.py','genlaw_pipeline/planar_material.py','genlaw_pipeline/native_planar.py','genlaw_pipeline/native_material.py',
    'genlaw_pipeline/surface_records.py','genlaw_pipeline/native_stream_metadata.py']


def source_bindings():return [file_binding(ROOT/p) for p in SOURCES]


def ranges(plan):
    return [[i,min(i+MAX_SOURCES,len(plan['chunks']))] for i in range(0,len(plan['chunks']),MAX_SOURCES)]


def summarize(plan_path,plan,interval,audits):
    start,end=interval
    if len(audits)!=end-start:raise ValueError('Incomplete original chunk audit page')
    chunks=[];stats=[];totals=dict.fromkeys(DTYPES,0)
    for entry,binding in zip(plan['chunks'][start:end],audits):
        row=checked_audit(checked_file(binding),plan_path,plan,entry);stats.append({**row['statistics'],'z_levels_mm':row['original_z_levels_mm']})
        chunks.append({'index':entry['index'],'audit':binding,'cache':row['cache'],
            'files':{k:v['file'] for k,v in row['records'].items()}})
        for k in DTYPES:totals[k]+=row['records'][k]['records']
    return {'chunks':chunks,'record_totals':totals,
        'signed_chunk_volumes_mm3':[r['signed_volume_mm3'] for r in stats],
        'bounds':{k:[fn(r['bounds'][k][i] for r in stats) for i in range(3)] for k,fn in [('min',min),('max',max)]},
        'z_levels_mm':sorted({z for r in stats for z in r['z_levels_mm']})}


def build_page(plan_path,audit_index,index,output):
    require_bounded_worker();plan=checked_plan(plan_path);intervals=ranges(plan)
    audits=LOAD(audit_index)['audits']
    if len(audits)!=len(plan['chunks']) or type(index) is not int or not 0<=index<len(intervals):
        raise ValueError('Missing audits or invalid native material page')
    interval=intervals[index];start,end=interval
    summary=summarize(plan_path,plan,interval,audits[start:end])
    output=output.resolve();output.relative_to(ROOT);output.mkdir(parents=True,exist_ok=False)
    entries=[{'source_id':i,'audit':audits[i]} for i in range(start,end)];indexes={}
    for kind,count in plan['bucket_counts'].items():
        path=output/(kind+'.json');write_page(entries,kind,count,path);indexes[kind]=file_binding(path)
    write(output/'page.json',{'schema':'genlaw.planar-material-directory-page.v1',
        'material_plan':file_binding(plan_path),'index':index,'interval':interval,**summary,
        'indexes':indexes,'sources':source_bindings(),'geometry_acceptance':False})


def checked_page(path,plan_path,plan,index):
    page=LOAD(path);intervals=ranges(plan)
    if (page.get('schema')!='genlaw.planar-material-directory-page.v1' or page.get('sources')!=source_bindings()
            or page.get('material_plan')!=file_binding(plan_path) or page.get('index')!=index
            or not 0<=index<len(intervals) or page.get('interval')!=intervals[index]
            or page.get('geometry_acceptance') is not False or set(page.get('indexes',{}))!=set(DTYPES)):
        raise ValueError('Foreign or incomplete directory material page')
    start,end=intervals[index]
    if [r['index'] for r in page['chunks']]!=list(range(start,end)):
        raise ValueError('Page omitted, duplicated or reordered native chunks')
    expected=summarize(plan_path,plan,[start,end],[r['audit'] for r in page['chunks']])
    if any(page.get(k)!=v for k,v in expected.items()):raise ValueError('Page statistics or cache identities changed')
    maps={};counts={};contributors={}
    for kind,count in plan['bucket_counts'].items():
        view=BucketPage(checked_file(page['indexes'][kind]));v=view.value
        expected_sources=[{'source_id':r['index'],'audit':r['audit'],'file':r['files'][kind]} for r in page['chunks']]
        if (v['kind']!=kind or v['bucket_count']!=count
                or [{k:s[k] for k in ['source_id','audit','file']} for s in v['sources']]!=expected_sources
                or v['record_count']!=page['record_totals'][kind]):
            raise ValueError('Index page does not represent every original audit source exactly once')
        totals=np.zeros(count,dtype='<i8');sources=np.zeros(count,dtype='<i8')
        np.add.at(totals,view.rows['bucket'],view.rows['records']);np.add.at(sources,view.rows['bucket'],1)
        maps[kind]=view;counts[kind]=totals;contributors[kind]=sources
    return page,maps,counts,contributors


def aggregate(plan_path,plan,pages,selection=None):
    if len(pages)!=len(ranges(plan)):raise ValueError('Missing native material directory pages')
    records={k:np.zeros(n,dtype='<i8') for k,n in plan['bucket_counts'].items()}
    contributors={k:np.zeros(n,dtype='<i8') for k,n in plan['bucket_counts'].items()}
    chunks=[];volumes=[];levels=set();bounds=None;selected=[];totals=dict.fromkeys(DTYPES,0)
    for index,binding in enumerate(pages):
        page,maps,counts,sources=checked_page(checked_file(binding),plan_path,plan,index)
        chunks.extend(page['chunks']);volumes.extend(page['signed_chunk_volumes_mm3']);levels.update(page['z_levels_mm'])
        if bounds is None:bounds=page['bounds']
        else:bounds={k:[fn(a,b) for a,b in zip(bounds[k],page['bounds'][k])] for k,fn in [('min',min),('max',max)]}
        for kind in DTYPES:
            records[kind]+=counts[kind];contributors[kind]+=sources[kind];totals[kind]+=page['record_totals'][kind]
        if selection is not None:selected.extend(maps[selection[0]].sources(selection[1]))
        # No view of another page is retained. The only accumulated geometry
        # metadata is one small identity per chunk and bounded height levels.
        del maps
    volume=math.fsum(volumes)
    if ([r['index'] for r in chunks]!=list(range(len(plan['chunks'])))
            or totals['facets']!=plan['triangles'] or not math.isfinite(volume) or volume<=0
            or not 2<=len(levels)<=4096
            or any(np.any(records[k]>LIMITS[k]) for k in DTYPES)):
        raise ValueError('Incomplete complete-material coverage or over-budget global records')
    expected={'statistics':{'triangles':plan['triangles'],'volume_mm3':volume,'bounds':bounds,'z_levels_mm':sorted(levels)},
        'record_totals':totals,'bucket_records':{k:v.tolist() for k,v in records.items()},
        'bucket_sources':{k:v.tolist() for k,v in contributors.items()}}
    if selection is not None:
        kind,index=selection
        if (len(selected)!=int(contributors[kind][index])
                or sum(r['records'] for r in selected)!=int(records[kind][index])
                or [r['chunk_index'] for r in selected]!=sorted({r['chunk_index'] for r in selected})):
            raise ValueError('Missing or duplicated selected bucket contributors')
    return expected,chunks,selected


def catalog(plan_path,pages,output):
    require_bounded_worker();plan=checked_plan(plan_path);bindings=[file_binding(p) for p in pages]
    expected,_,_=aggregate(plan_path,plan,bindings)
    write(output,{'schema':'genlaw.planar-native-catalog.v1','material_plan':file_binding(plan_path),
        'pages':bindings,**expected,'sources':source_bindings(),'all_bucket_contributors_materialized':False,
        'geometry_acceptance':False})


def checked_catalog(path,selection=None):
    require_bounded_worker();data=LOAD(path)
    if (data.get('schema')!='genlaw.planar-native-catalog.v1' or data.get('sources')!=source_bindings()
            or data.get('all_bucket_contributors_materialized') is not False or data.get('geometry_acceptance') is not False):
        raise ValueError('Unsupported or overstated indexed native catalog')
    plan_path=checked_file(data['material_plan']);plan=checked_plan(plan_path)
    if selection is not None:
        kind,index=selection
        if kind not in DTYPES or type(index) is not int or not 0<=index<plan['bucket_counts'][kind]:
            raise ValueError('Invalid global native bucket')
    expected,chunks,files=aggregate(plan_path,plan,data['pages'],selection)
    if any(data.get(k)!=v for k,v in expected.items()):raise ValueError('Catalog totals or statistics changed')
    return data,plan,chunks,files


def part_count(data,kind,index):return (data['bucket_sources'][kind][index]+MAX_WINDOWS-1)//MAX_WINDOWS


def bucket_part(catalog_path,kind,index,part,output):
    require_bounded_worker();data,plan,_,files=checked_catalog(catalog_path,(kind,index))
    count=part_count(data,kind,index)
    if count<=1 or type(part) is not int or not 0<=part<count:raise ValueError('Invalid bounded source batch')
    start=part*MAX_WINDOWS;end=min(start+MAX_WINDOWS,len(files));payload=output.with_suffix('.bin')
    n=concatenate_sources(files[start:end],plan['bucket_counts'][kind],index,kind,payload)
    write(output,{'schema':'genlaw.planar-native-bucket-part.v1','catalog':file_binding(catalog_path),
        'kind':kind,'index':index,'part':part,'source_interval':[start,end],
        'records':n,'file':file_binding(payload),'partial_only':True,'sources':source_bindings()})


def audit_bucket(catalog_path,kind,index,output,parts=()):
    require_bounded_worker();data,plan,_,files=checked_catalog(catalog_path,(kind,index));batches=part_count(data,kind,index)
    if batches<=1:
        if parts:raise ValueError('Unexpected partial bucket outputs')
        actual=files
    else:
        if len(parts)!=batches:raise ValueError('Missing bounded source batch')
        actual=[]
        for i,path in enumerate(parts):
            row=LOAD(path);start=i*MAX_WINDOWS;end=min(start+MAX_WINDOWS,len(files))
            if (row.get('schema')!='genlaw.planar-native-bucket-part.v1' or row.get('sources')!=source_bindings()
                    or row.get('catalog')!=file_binding(catalog_path) or row.get('kind')!=kind or row.get('index')!=index
                    or row.get('part')!=i or row.get('source_interval')!=[start,end] or row.get('partial_only') is not True
                    or row.get('records')!=sum(v['records'] for v in files[start:end])):
                raise ValueError('Foreign or incomplete compact bucket batch')
            checked_file(row['file'])
            actual.append({'file':row['file'],'records':row['records'],'index':index,'offset':0,'sha256':row['file']['sha256']})
    result=check_bucket(actual,plan['bucket_counts'][kind],index,kind)
    write(output,{'schema':'genlaw.planar-native-bucket-audit.v1','catalog':file_binding(catalog_path),
        'kind':kind,'index':index,**result,'source_count':len(files),
        'parts':[file_binding(p) for p in parts],'sources':source_bindings()})


def accepted(data,plan,catalog_path,paths):
    expected={(k,i) for k in DTYPES for i in range(plan['bucket_counts'][k])};seen=set()
    totals=dict.fromkeys(DTYPES,0);unique=dict.fromkeys(DTYPES,0)
    for path in paths:
        row=LOAD(path);key=(row.get('kind'),row.get('index'))
        if (row.get('schema')!='genlaw.planar-native-bucket-audit.v1' or row.get('sources')!=source_bindings()
                or row.get('catalog')!=file_binding(catalog_path) or key not in expected or key in seen):
            raise ValueError('Missing, duplicated or foreign global bucket audit')
        kind,index=key
        if (row.get('records')!=data['bucket_records'][kind][index]
                or row.get('source_count')!=data['bucket_sources'][kind][index]
                or type(row.get('unique_keys')) is not int or not 0<=row['unique_keys']<=row['records']):
            raise ValueError('Global bucket lost original records or sources')
        seen.add(key);totals[kind]+=row['records'];unique[kind]+=row['unique_keys']
    if (seen!=expected or totals!=data['record_totals'] or unique['facets']!=plan['triangles']
            or 2*unique['edges']!=3*unique['facets']):
        raise ValueError('Incomplete, duplicated or open complete native material')
    return unique


def finish(catalog_path,results,output):
    require_bounded_worker();data,plan,chunks,_=checked_catalog(catalog_path)
    unique=accepted(data,plan,catalog_path,results)
    write(output,{'schema':'genlaw.planar-native-surface.v1','status':'closed_oriented_surface',
        'catalog':file_binding(catalog_path),'material_plan':data['material_plan'],
        'bucket_audits':[file_binding(p) for p in results],**data['statistics'],
        'unique_edges':unique['edges'],'coverage':plan['coverage'],'caches':[r['cache'] for r in chunks],
        'sources':source_bindings(),'native_execution_windows':len(plan['native_windows']),
        'output_files':len(plan['chunks']),'representation':'exact_native_planar',
        'original_native_triangles':plan['original_triangles'],'original_native_plan':plan['original_plan'],
        'all_global_cells_covered_once':True,
        'whole_mesh_assembled':False,'all_bucket_contributors_materialized':False,'geometry_modified':False,
        'artificial_caps':False,'synthetic_empty_stls':0,'production_approved':False,'whole_process_card_complete':False})


def checked_surface(path):
    require_bounded_worker();surface=LOAD(path)
    if (surface.get('schema')!='genlaw.planar-native-surface.v1' or surface.get('status')!='closed_oriented_surface'
            or surface.get('sources')!=source_bindings() or surface.get('all_global_cells_covered_once') is not True
            or any(surface.get(k) is not False for k in ['whole_mesh_assembled','all_bucket_contributors_materialized',
                'geometry_modified','artificial_caps','production_approved','whole_process_card_complete'])
            or surface.get('synthetic_empty_stls')!=0):raise ValueError('Complete indexed native material is required')
    catalog_path=checked_file(surface['catalog']);data,plan,chunks,_=checked_catalog(catalog_path)
    if (surface.get('representation')!='exact_native_planar'
            or surface.get('original_native_triangles')!=plan['original_triangles']
            or surface.get('original_native_plan')!=plan['original_plan']
            or surface.get('material_plan')!=data['material_plan'] or surface.get('coverage')!=plan['coverage']
            or surface.get('caches')!=[r['cache'] for r in chunks]
            or surface.get('native_execution_windows')!=len(plan['native_windows'])
            or surface.get('output_files')!=len(plan['chunks'])
            or any(surface.get(k)!=v for k,v in data['statistics'].items())):
        raise ValueError('Indexed surface coverage, statistics or cache identity changed')
    unique=accepted(data,plan,catalog_path,[checked_file(b) for b in surface['bucket_audits']])
    if surface.get('unique_edges')!=unique['edges']:raise ValueError('Changed complete edge count')
    return surface,plan,{**data,'chunks':chunks}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['page','catalog','bucket-part','bucket','finish'])
    for name in ['plan','audit-index','page-index','catalog','result-index','output']:p.add_argument('--'+name,type=Path)
    p.add_argument('--index',type=int);p.add_argument('--part',type=int);p.add_argument('--kind',choices=sorted(DTYPES))
    p.add_argument('--parts',nargs='*',type=Path,default=[]);a=p.parse_args()
    if a.action=='page':build_page(a.plan,a.audit_index,a.index,a.output)
    elif a.action=='catalog':catalog(a.plan,[checked_file(b) for b in LOAD(a.page_index)['pages']],a.output)
    elif a.action=='bucket-part':bucket_part(a.catalog,a.kind,a.index,a.part,a.output)
    elif a.action=='bucket':audit_bucket(a.catalog,a.kind,a.index,a.output,a.parts)
    else:finish(a.catalog,[checked_file(b) for b in LOAD(a.result_index)['results']],a.output)
