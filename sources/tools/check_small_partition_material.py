"""Compare every oriented float32 triangle with saved complete native output."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'web/tools')]
from mesh_metrics import require_bounded_worker,load_mesh,STL_DTYPE,np
from genlaw_pipeline.small_partition_material import checked
from genlaw_pipeline.native_stream import checked_result
from genlaw_pipeline.cell_windows import checked_file,file_binding
from genlaw_pipeline.run import write


def facets(path,count):
    raw=np.memmap(path,dtype=STL_DTYPE,mode='r',offset=84,shape=(count,))
    return np.sort(np.ascontiguousarray(raw['points']).reshape(count,9).view('V36').reshape(-1))


def check(full,partitioned,out):
    require_bounded_worker();old,req=checked_result(full);new,mesh=checked(partitioned)
    if (len(old['shards'])!=1 or req['window']!=[0,0,0,*req['grid']['steps']]
            or req['inputs']!=new['inputs'] or req['grid']!=new['grid']
            or old['triangle_count']!=new['triangle_count']):raise ValueError('Complete original inputs or facet counts differ')
    request=json.loads(checked_file(new['request']).read_text())
    if request['build']!=req['build']:raise ValueError('Different native engines')
    source=checked_file(old['shards'][0]['surface']);a=facets(source,old['triangle_count']);b=facets(mesh,new['triangle_count'])
    if not np.array_equal(a,b):raise ValueError('Native partition changed oriented float32 triangle records')
    _,_,inspection=load_mesh(mesh)
    if inspection['exact_duplicate_faces_removed']:raise ValueError('Partition duplicated surface facets')
    write(out,{'schema':'genlaw.small-partition-qualification.v1','status':'passed','build':req['build'],
        'saved_full_native':file_binding(full),'eight_window_result':file_binding(partitioned),
        'native_windows':len(new['native_windows']),'oriented_float32_triangle_count':len(a),
        'sorted_oriented_facets_sha256':hashlib.sha256(a.tobytes()).hexdigest(),
        'exact_oriented_facet_records_equal':True,'mesh_inspection':inspection,
        'source_code':[file_binding(ROOT/'genlaw_pipeline/small_partition_material.py'),file_binding(Path(__file__))],
        'reference_engine_and_resolution_unchanged':True,'whole_real_card_complete':False,
        'production_approved':False,'new_complete_reference_simulation':False})
    print(json.dumps({'partition_qualification':'passed','exact_oriented_facets':len(a)}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ['full','partitioned','output']:p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();check(a.full.resolve(),a.partitioned.resolve(),a.output.resolve())
