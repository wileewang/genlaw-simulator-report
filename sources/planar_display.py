"""Compact actual mesh displays by retriangulating exact axis-aligned planar patches.

Curved/nonplanar triangles are retained. Planar patch coverage, every shard
boundary edge and oriented volume are checked; no target CAD generates output.
The original quantitative CAMotics STL shards remain untouched.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'web/tools'))
from mesh_metrics import np,STL_DTYPE,require_bounded_worker,write_indexed
from shapely import constrained_delaunay_triangles
from shapely.geometry import Polygon
from shapely.ops import polygonize_full,unary_union
from genlaw_pipeline.cell_windows import file_binding,checked_file
from genlaw_pipeline.sharded_surface import triangles,checked_result

MAX_DISPLAY_TRIANGLES=1_500_000
LOAD=lambda p:json.loads(p.read_text())


def write(path,data):
    with path.open('x') as stream:json.dump(data,stream,indent=2);stream.write('\n')


def edge_census(points):
    flat=np.ascontiguousarray(points.reshape(-1,3))
    _,first,inverse=np.unique(flat.view('V12').reshape(-1),return_index=True,return_inverse=True)
    vertices=flat[first];faces=inverse.reshape(-1,3).astype('<u4')
    edges=np.concatenate((faces[:,[0,1]],faces[:,[1,2]],faces[:,[2,0]]))
    signs=np.where(edges[:,0]<edges[:,1],1,-1);edges.sort(axis=1)
    _,first_edge,ids,counts=np.unique(np.ascontiguousarray(edges).view('V8').reshape(-1),return_index=True,return_inverse=True,return_counts=True)
    winding=np.bincount(ids,weights=signs)
    if np.any(counts>2) or np.any(winding[counts==2]):raise ValueError('Planar display is nonmanifold or has inconsistent winding')
    edge_points=vertices[edges[first_edge[counts==1]]]
    edge_signs=winding[counts==1]
    records=[]
    for pair,sign in zip(edge_points,edge_signs):
        a,b=pair[0].tobytes(),pair[1].tobytes()
        if b<a:a,b,sign=b,a,-sign
        records.append(a+b+struct.pack('<i',int(sign)))
    return edge_points,sorted(records)


def planar_patch(points,axis,sign):
    edges,before=edge_census(points)
    other=[i for i in range(3) if i!=axis]
    polygons,cuts,dangles,invalid=polygonize_full(edges[:,:,other].astype(float))
    if not cuts.is_empty or not dangles.is_empty or not invalid.is_empty:raise ValueError('Planar patch boundary is incomplete')
    pieces=list(polygons.geoms);rings=[Polygon(piece.exterior) for piece in pieces]
    region=unary_union([piece for piece in pieces if sum(ring.contains(piece.representative_point()) for ring in rings)%2==1])
    if region.is_empty or not region.is_valid:raise ValueError('Invalid original planar patch')
    triangulated=constrained_delaunay_triangles(region)
    faces=list(triangulated.geoms)
    if len(faces)>=len(points):return points,{'retained_original':True,'triangles':len(points)}
    projected=np.array([np.asarray(p.exterior.coords)[:3] for p in faces],dtype='<f4')
    compact=np.empty((len(faces),3,3),dtype='<f4');compact[:,:,axis]=points[0,0,axis]
    compact[:,:,other]=projected
    normals=np.cross(compact[:,1].astype(float)-compact[:,0],compact[:,2].astype(float)-compact[:,0])
    if np.any(normals[:,axis]==0):raise ValueError('Degenerate constrained planar triangulation')
    flip=np.sign(normals[:,axis])!=sign
    compact[flip]=compact[flip][:,[0,2,1]]
    _,after=edge_census(compact)
    if before!=after:raise ValueError('Planar retessellation changed an original boundary edge')
    union=unary_union(faces)
    delta=union.symmetric_difference(region).area
    overlap=abs(math.fsum(p.area for p in faces)-union.area)
    if delta>1e-10 or overlap>1e-10:raise ValueError('Planar display changed polygon coverage')
    return compact,{'retained_original':False,'input_triangles':len(points),'output_triangles':len(compact),
        'axis':axis,'plane_mm':float(points[0,0,axis]),'signed_normal':int(sign),
        'boundary_edges_identical':True,'symmetric_difference_mm2':float(delta),'overlap_area_mm2':float(overlap)}


def compact_points(points):
    if not len(points):return points,[]
    remaining=np.ones(len(points),dtype=bool);pieces=[];records=[]
    for axis in range(3):
        mask=(np.ptp(points[:,:,axis],axis=1)==0)&remaining
        if not np.any(mask):continue
        ids=np.flatnonzero(mask);normal=np.cross(points[ids,1].astype(float)-points[ids,0],points[ids,2].astype(float)-points[ids,0])[:,axis]
        if np.any(normal==0):raise ValueError('Degenerate input triangle')
        for level in np.unique(points[ids,0,axis]):
            for sign in (-1,1):
                selected=ids[(points[ids,0,axis]==level)&(np.sign(normal)==sign)]
                if not len(selected):continue
                patch,record=planar_patch(points[selected],axis,sign);pieces.append(patch);records.append(record)
        remaining[ids]=False
    pieces.append(points[remaining])
    result=np.concatenate(pieces)
    if len(result)>len(points):raise ValueError('Compacted display grew beyond original triangle budget')
    return result,records


def signed_volume(points):
    tri=points.astype(float)
    return math.fsum((np.einsum('ij,ij->i',tri[:,0],np.cross(tri[:,1],tri[:,2]))/6).tolist())


def compact_window(plan_path,result_path,audit_path,output):
    require_bounded_worker()
    record,mesh=checked_result(plan_path,result_path);audit=LOAD(audit_path)
    if audit['result']!=file_binding(result_path) or audit['surface']!=record['surface']:raise ValueError('Display input audit does not match actual simulation')
    checked_file(audit['source_code']);boundary=checked_file(audit['boundary_file']).read_bytes()
    old_edges=sorted(boundary[i:i+28] for i in range(0,len(boundary),28))
    original=triangles(mesh);compact,patches=compact_points(original)
    if len(compact):
        _,new_edges=edge_census(compact)
        norms=np.cross(compact[:,1].astype(float)-compact[:,0],compact[:,2].astype(float)-compact[:,0])
        if np.any(np.linalg.norm(norms,axis=1)<=1e-16):raise ValueError('Display has a degenerate triangle')
        volume=signed_volume(compact)
    else:new_edges=[];norms=np.empty((0,3));volume=0.
    if new_edges!=old_edges:raise ValueError('Display changed a shard seam')
    error=abs(volume-audit['signed_volume_mm3'])
    if error>1e-6:raise ValueError('Planar display changed oriented volume')
    surface_path=output.with_suffix('.stl');data=np.zeros(len(compact),dtype=STL_DTYPE)
    data['points']=compact
    if len(norms):data['normal']=norms/np.linalg.norm(norms,axis=1)[:,None]
    with surface_path.open('xb') as stream:
        stream.write(b'GenLaw actual CAMotics surface; planar display retessellation'.ljust(80,b'\0'))
        stream.write(struct.pack('<I',len(compact)));stream.write(data.tobytes())
    write(output,{'schema':'genlaw.planar-display-window.v1','source_result':file_binding(result_path),
        'source_audit':file_binding(audit_path),'display_surface':file_binding(surface_path),
        'original_triangles':len(original),'display_triangles':len(compact),'patches':patches,
        'nonplanar_triangles_retained':True,'original_boundary_edges_preserved':True,
        'signed_volume_mm3':volume,'absolute_signed_volume_difference_mm3':error,
        'source_code':file_binding(Path(__file__)),'quantitative_mesh_modified':False})
    print(json.dumps({'original_triangles':len(original),'display_triangles':len(compact),'volume_difference_mm3':error}),flush=True)


def combine(surface_path,parts,output,report):
    require_bounded_worker();surface=LOAD(surface_path);checked_file(surface['source_code'])
    expected={row['result']['sha256'] for row in surface['windows']}
    sources={};total=0;pieces=[];volumes=[]
    for path in parts:
        row=LOAD(path);checked_file(row['source_code']);checked_file(row['source_result']);checked_file(row['source_audit'])
        key=row['source_result']['sha256']
        if key in sources:raise ValueError('Duplicate display shard')
        sources[key]=file_binding(path);points=triangles(checked_file(row['display_surface']))
        if len(points)!=row['display_triangles']:raise ValueError('Display triangle count changed')
        total+=len(points)
        if total>MAX_DISPLAY_TRIANGLES:raise ValueError('Combined display exceeds browser mesh budget')
        pieces.append(points);volumes.append(row['signed_volume_mm3'])
    if set(sources)!=expected:raise ValueError('Display does not cover the complete actual surface')
    points=np.concatenate(pieces).reshape(-1,3)
    _,first,inverse=np.unique(np.ascontiguousarray(points).view('V12').reshape(-1),return_index=True,return_inverse=True)
    vertices=points[first];faces=inverse.reshape(-1,3).astype('<u4')
    if 16+vertices.nbytes+faces.nbytes>64*1024**2:raise ValueError('Display file exceeds unchanged output limit')
    write_indexed(output,vertices,faces)
    write(report,{'schema':'genlaw.planar-display.v1','actual_surface':file_binding(surface_path),
        'parts':list(sources.values()),'display':file_binding(output),'vertices':len(vertices),'triangles':len(faces),
        'original_triangles':surface['triangles'],'quantitative_mesh_modified':False,
        'method':'Retessellate exact axis-aligned planar patches; preserve nonplanar triangles, original seam edges and polygon coverage.',
        'cad_used_to_generate_output':False,'summed_signed_volume_mm3':math.fsum(volumes),
        'source_code':file_binding(Path(__file__))})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    one=sub.add_parser('window')
    for name in ('plan','result','audit','output'):one.add_argument('--'+name,required=True,type=Path)
    all_=sub.add_parser('combine')
    for name in ('surface','directory','output','report'):all_.add_argument('--'+name,required=True,type=Path)
    a=p.parse_args()
    if a.action=='window':compact_window(a.plan,a.result,a.audit,a.output)
    else:combine(a.surface,sorted(a.directory.glob('window-*.json')),a.output,a.report)
