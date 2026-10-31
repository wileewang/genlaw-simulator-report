"""Exact planar retessellation candidates for quantitative native material.

Reuse the existing constrained planar-display kernel, then independently check
the oriented boundary of every exact plane, every untouched nonplanar facet,
source vertex membership and complete chunk seams. Original NC/STLs remain
bound and unmodified. This artifact alone is not a complete-material verdict.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'web/tools')]
from mesh_metrics import np,STL_DTYPE,MAX_TRIANGLES,require_bounded_worker
from genlaw_pipeline.cell_windows import checked_file,file_binding
from genlaw_pipeline.native_stream_metadata import context,receipt
from genlaw_pipeline.sharded_surface import triangles,canonical_facets
from genlaw_pipeline.surface_records import audit_points
from genlaw_pipeline.planar_display import compact_points,edge_census
from genlaw_pipeline.run import write

LOAD=lambda p:json.loads(p.read_text())
SOURCES=['genlaw_pipeline/native_planar.py','genlaw_pipeline/planar_display.py',
    'genlaw_pipeline/native_stream_metadata.py','genlaw_pipeline/surface_records.py',
    'genlaw_pipeline/sharded_surface.py','web/tools/mesh_metrics.py']


def sources():return [file_binding(ROOT/p) for p in SOURCES]


def plane_groups(points):
    groups={};remaining=np.ones(len(points),dtype=bool)
    normals=np.cross(points[:,1].astype('<f8')-points[:,0],points[:,2].astype('<f8')-points[:,0])
    if np.any(np.linalg.norm(normals,axis=1)<=1e-16):raise ValueError('Degenerate native or candidate triangle')
    for axis in range(3):
        ids=np.flatnonzero(np.all(points[:,:,axis]==points[:,:1,axis],axis=1)&remaining)
        for level in np.unique(points[ids,0,axis]):
            for sign in (-1,1):
                selected=ids[(points[ids,0,axis]==level)&(np.sign(normals[ids,axis])==sign)]
                if len(selected):groups[(axis,float(level),sign)]=selected
        remaining[ids]=False
    return groups,remaining


def boundary_digest(records):return hashlib.sha256(b''.join(records)).hexdigest()


def prove_equivalence(original,candidate):
    """Positive coplanar chains with identical directed boundaries are equal.

The finite oriented 2-chain difference has zero boundary and zero winding at
infinity. Equal plane/normal groups therefore retain their full winding sets;
nonplanar triangles are compared byte-for-byte with orientation preserved.
No fitted planes, coordinate tolerance, surface repair or target CAD is used.
"""
    require_bounded_worker()
    for points in [original,candidate]:
        if (points.dtype!=np.dtype('<f4') or points.shape!=(len(points),3,3)
                or not 0<len(points)<=MAX_TRIANGLES or not np.isfinite(points).all()):
            raise ValueError('Invalid or over-budget planar material')
    if len(candidate)>len(original):raise ValueError('Candidate exceeds original triangle count')
    old_vertices=np.unique(np.ascontiguousarray(original.reshape(-1,3)).view('V12').reshape(-1))
    new_vertices=np.unique(np.ascontiguousarray(candidate.reshape(-1,3)).view('V12').reshape(-1))
    if not np.isin(new_vertices,old_vertices).all():raise ValueError('Candidate introduced or displaced a native vertex')
    before,old_other=plane_groups(original);after,new_other=plane_groups(candidate)
    if set(before)!=set(after):raise ValueError('Candidate omitted or introduced a native oriented plane')
    a=canonical_facets(original[old_other]);b=canonical_facets(candidate[new_other])
    if not np.array_equal(a,b):raise ValueError('Candidate changed nonplanar native facets')
    planes=[]
    for key,ids in sorted(before.items()):
        _,left=edge_census(original[ids]);_,right=edge_census(candidate[after[key]])
        if not left or left!=right:raise ValueError('Candidate changed an oriented native plane boundary')
        planes.append({'axis':key[0],'coordinate_mm':key[1],'normal_sign':key[2],
            'original_triangles':len(ids),'candidate_triangles':len(after[key]),
            'directed_boundary_edges':len(left),'boundary_sha256':boundary_digest(left)})
    _,left=edge_census(original);_,right=edge_census(candidate)
    if left!=right:raise ValueError('Candidate changed original chunk seams')
    old_stats,old_faces,old_edges=audit_points(original)
    del old_faces,old_edges
    new_stats,new_faces,new_edges=audit_points(candidate)
    del new_faces,new_edges
    if old_stats['bounds']!=new_stats['bounds']:raise ValueError('Candidate changed original material bounds')
    return {'planes':planes,'nonplanar_triangles':int(old_other.sum()),
        'unchanged_nonplanar_facets_sha256':hashlib.sha256(a.tobytes()).hexdigest(),
        'chunk_boundary_sha256':boundary_digest(left),'chunk_boundary_edges':len(left),
        'original_statistics':old_stats,'candidate_statistics':new_stats,
        'signed_volume_float64_difference_mm3':new_stats['signed_volume_mm3']-old_stats['signed_volume_mm3'],
        'equivalence_basis':'exact oriented planar boundary chains plus unchanged nonplanar facets',
        'only_original_vertices':True,'coordinate_displacement_mm':0,'fitted_planes':False,
        'original_chunk_seams_preserved':True,'holes_filled':False,'target_cad_used':False}


def native_context(path):
    result=LOAD(path);request=LOAD(checked_file(result['request']))
    ctx=context(checked_file(request['inputs']['project']),checked_file(request['build']))
    return receipt(path,ctx)


def compact_chunk(result_path,native,index,out):
    if type(index) is not int or not 0<=index<len(native['chunks']):raise ValueError('Invalid native chunk')
    entry=native['chunks'][index];original=triangles(checked_file(entry['surface']))
    candidate,patches=compact_points(original)
    if any(p.get('symmetric_difference_mm2',0)!=0 for p in patches):
        raise ValueError('Quantitative candidate requires zero planar coverage difference')
    proof=prove_equivalence(original,candidate)
    out.mkdir(parents=True,exist_ok=False)
    raw=np.zeros(len(candidate),dtype=STL_DTYPE);raw['points']=candidate
    normal=np.cross(candidate[:,1].astype('<f8')-candidate[:,0],candidate[:,2].astype('<f8')-candidate[:,0])
    raw['normal']=normal/np.linalg.norm(normal,axis=1)[:,None]
    surface=out/'surface.stl'
    with surface.open('xb') as stream:
        stream.write(b'GenLaw exact native planar retessellation candidate'.ljust(80,b'\0'))
        stream.write(struct.pack('<I',len(candidate)));stream.write(raw.tobytes())
    if not np.array_equal(triangles(surface),candidate):raise ValueError('Candidate STL serialization changed coordinates')
    write(out/'result.json',{'schema':'genlaw.native-planar-chunk.v1','status':'exact_chunk_geometry_verified',
        'native_result':file_binding(result_path),'native_chunk':entry,'index':index,
        'surface':file_binding(surface),'patches':patches,**proof,'sources':sources(),
        'triangle_partition_changed':len(candidate)!=len(original),'original_native_modified':False,
        'quantitative_consumer_qualified':False,'whole_material_verified':False,
        'whole_process_card_complete':False,'production_approved':False})
    print(json.dumps({'chunk':index,'original':len(original),'candidate':len(candidate),
        'float64_volume_difference':proof['signed_volume_float64_difference_mm3']}),flush=True)


def batch(result_path,indices,out):
    require_bounded_worker();native=native_context(result_path)
    if (not indices or indices!=sorted(set(indices)) or len(indices)>64
            or any(type(i) is not int or not 0<=i<len(native['chunks']) for i in indices)
            or sum(native['chunks'][i]['triangles'] for i in indices)>MAX_TRIANGLES):
        raise ValueError('Planar batch exceeds the original triangle/file bounds')
    for i in indices:compact_chunk(result_path,native,i,out/f'chunk-{i:04d}')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--result',type=Path,required=True);p.add_argument('--indices',type=int,nargs='+',required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    batch(a.result,a.indices,a.output)
