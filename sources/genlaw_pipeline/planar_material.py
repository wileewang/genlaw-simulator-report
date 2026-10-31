"""Typed exact planar material; original native receipts remain authoritative.

The complete native plan proves cell ownership. Every candidate is tied to one
original chunk and its geometry proof is replayed before records/caches exist.
Candidate facets are never presented as original native facet bytes.
"""
import argparse
import json
import math
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'web/tools')]
from mesh_metrics import np,require_bounded_worker
from genlaw_pipeline.cell_windows import checked_file,file_binding
from genlaw_pipeline.native_material import checked_plan as original_plan,SOURCES as ORIGINAL_SOURCES
from genlaw_pipeline.native_planar import sources as proof_sources,prove_equivalence
from genlaw_pipeline.sharded_surface import triangles
from genlaw_pipeline.surface_records import audit_points,bucket_count,write_buckets,DTYPES,LIMITS
from genlaw_pipeline.span_histogram import build as height_histogram,checked as checked_histogram
from genlaw_pipeline.run import write
LOAD=lambda p:json.loads(p.read_text())


def source_bindings():
    names=['genlaw_pipeline/planar_material.py',*ORIGINAL_SOURCES]
    return [file_binding(ROOT/name) for name in names]+proof_sources()


def checked_candidate(path,entry):
    row=LOAD(path);n=row.get('candidate_statistics',{}).get('triangles')
    native={k:entry[k] for k in ['shard_index','surface','triangles']}
    if (row.get('schema')!='genlaw.native-planar-chunk.v1' or row.get('status')!='exact_chunk_geometry_verified'
            or row.get('sources')!=proof_sources() or row.get('native_result')!=entry['native_result']
            or row.get('native_chunk')!=native or type(row.get('index')) is not int or row['index']!=native['shard_index']
            or row.get('original_statistics',{}).get('triangles')!=native['triangles']
            or type(n) is not int or not 0<n<=native['triangles']<=500000
            or row.get('coordinate_displacement_mm')!=0 or row.get('only_original_vertices') is not True
            or row.get('original_chunk_seams_preserved') is not True
            or any(row.get(k) is not False for k in ['fitted_planes','holes_filled','target_cad_used',
                'original_native_modified','whole_material_verified','production_approved','whole_process_card_complete'])):
        raise ValueError('Candidate has foreign native provenance or overstated geometry')
    if any(p.get('symmetric_difference_mm2',0)!=0 for p in row['patches']):
        raise ValueError('Candidate lacks exact planar coverage')
    return row


def definition(native_plan,candidates):
    require_bounded_worker();original=original_plan(native_plan)
    if len(candidates)!=len(original['chunks']) or len(set(candidates))!=len(candidates):
        raise ValueError('Missing or duplicate compacted native chunks')
    chunks=[]
    for entry,path in zip(original['chunks'],candidates):
        proof=checked_candidate(path,entry)
        chunks.append({'index':entry['index'],'window_index':entry['window_index'],
            'native_result':entry['native_result'],'native_chunk':entry,'candidate':file_binding(path),
            'surface':proof['surface'],'triangles':proof['candidate_statistics']['triangles'],
            'original_triangles':entry['triangles']})
    total=sum(c['triangles'] for c in chunks)
    return {**original,'schema':'genlaw.planar-material-plan.v1','original_plan':file_binding(native_plan),
        'candidate_results':[file_binding(p) for p in candidates],'original_triangles':original['triangles'],
        'chunks':chunks,'triangles':total,
        'bucket_counts':{'facets':bucket_count(total,'facets'),'edges':bucket_count(3*total,'edges')},
        'sources':source_bindings(),'representation':'exact_native_planar',
        'payload_validation':'replay_exact_native_candidate_proof_in_each_chunk_worker'}


def prepare(native_plan,candidates,output):write(output,definition(native_plan,candidates))


def checked_plan(path):
    value=LOAD(path)
    if value.get('schema')!='genlaw.planar-material-plan.v1':raise ValueError('Typed planar material required')
    expected=definition(checked_file(value['original_plan']),[checked_file(p) for p in value['candidate_results']])
    if value!=expected:raise ValueError('Planar material ownership, provenance, count or source changed')
    return value


def audit_chunk(plan_path,index,out):
    require_bounded_worker();plan=checked_plan(plan_path)
    if type(index) is not int or not 0<=index<len(plan['chunks']):raise ValueError('Invalid native chunk')
    _audit_chunk(plan_path,plan,plan['chunks'][index],out)

def audit_chunks(plan_path,indices,out):
    require_bounded_worker();plan=checked_plan(plan_path)
    if (not 1<=len(indices)<=64 or indices!=sorted(set(indices))
            or any(type(i) is not int or not 0<=i<len(plan['chunks']) for i in indices)
            or sum(plan['chunks'][i]['original_triangles'] for i in indices)>333333):
        raise ValueError('Chunk batch exceeds unchanged total triangle/edge worker limits')
    for i in indices:_audit_chunk(plan_path,plan,plan['chunks'][i],out/f'chunk-{i:05d}')

def _audit_chunk(plan_path,plan,entry,out):
    mesh=checked_file(entry['surface']);points=triangles(mesh)
    if len(points)!=entry['triangles']:raise ValueError('Native chunk count changed')
    out=out.resolve();out.relative_to(ROOT);out.mkdir(parents=True,exist_ok=False)
    proof=checked_candidate(checked_file(entry['candidate']),entry['native_chunk'])
    actual=prove_equivalence(triangles(checked_file(entry['native_chunk']['surface'])),points)
    if any(proof.get(k)!=v for k,v in actual.items()):raise ValueError('Exact native candidate proof does not replay')
    stats,facets,edges=audit_points(points)
    records={kind:write_buckets(data,plan['bucket_counts'][kind],kind,out)
        for kind,data in [('facets',facets),('edges',edges)]}
    del facets,edges
    side=points[np.ptp(points[:,:,2],axis=1)>0]
    heights=np.column_stack((side[:,:,2].min(axis=1),side[:,:,2].max(axis=1)))
    for name,array in [('sections',side),('heights',heights)]:
        with (out/(name+'.npy')).open('xb') as f:np.save(f,array,allow_pickle=False)
    identity={'material_plan':file_binding(plan_path),'chunk':entry,'source_code':file_binding(Path(__file__))}
    cache={'schema':'genlaw.planar-chunk-cache.v1',**identity,'triangles':len(side),
        'omitted_exactly_horizontal_triangles':len(points)-len(side),'geometry_modified':False,
        'array':file_binding(out/'sections.npy'),'heights':file_binding(out/'heights.npy'),
        'height_histogram':height_histogram(heights)}
    write(out/'cache.json',cache)
    write(out/'audit.json',{'schema':'genlaw.planar-chunk-audit.v1',**identity,
        'statistics':stats,'records':records,'cache':file_binding(out/'cache.json'),
        'original_z_levels_mm':actual['original_statistics']['z_levels_mm'],
        'exact_geometry_rechecked':True,'candidate_proof':entry['candidate']})

def checked_cache(binding,plan_binding,entry):
    cache=LOAD(checked_file(binding));n=cache.get('triangles')
    if (cache.get('schema')!='genlaw.planar-chunk-cache.v1' or cache.get('material_plan')!=plan_binding
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
    if (row.get('schema')!='genlaw.planar-chunk-audit.v1' or row.get('material_plan')!=file_binding(plan_path)
            or row.get('chunk')!=entry or row.get('source_code')!=file_binding(Path(__file__))):
        raise ValueError('Foreign native chunk audit')
    proof=checked_candidate(checked_file(entry['candidate']),entry['native_chunk'])
    if (row.get('exact_geometry_rechecked') is not True or row.get('candidate_proof')!=entry['candidate']
            or row.get('original_z_levels_mm')!=proof['original_statistics']['z_levels_mm']
            or row.get('statistics')!=proof['candidate_statistics']):raise ValueError('Missing replayed native geometry or original levels')
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

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','chunk','chunks'])
    for name in ['native-plan','candidates','plan','output']:p.add_argument('--'+name,type=Path)
    p.add_argument('--index',type=int);p.add_argument('--indices',type=int,nargs='+');a=p.parse_args()
    if a.action=='prepare':prepare(a.native_plan,[checked_file(b) for b in LOAD(a.candidates)['candidates']],a.output)
    elif a.action=='chunk':audit_chunk(a.plan,a.index,a.output)
    else:audit_chunks(a.plan,a.indices,a.output)
