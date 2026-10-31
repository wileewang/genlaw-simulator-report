"""Complete small material from eight native global-lattice windows.

Original triangle records are concatenated without repair or simplification.
The output must still pass the independent closed/oriented mesh observer.
"""
import argparse
import itertools
import json
import os
from pathlib import Path
import shutil
import struct
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.cell_windows import checked_file,file_binding,project_binding,expected_grid,validate_partition
from genlaw_pipeline.native_stream import run as native_run,checked_result,checked_build
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.planar_resume import process_identity
from genlaw_pipeline.run import write
from step2gcode_demo.validation.local_resource_limits import require_bounded_worker
READ=lambda p:json.loads(Path(p).read_text())


def windows(steps):
    cuts=[[0,n//2,n] for n in steps]
    result=[[cuts[i][part[i]] for i in range(3)]+[cuts[i][part[i]+1] for i in range(3)]
        for part in itertools.product(range(2),repeat=3)]
    validate_partition(result,steps);return result


def context(out):
    request=READ(out/'request.json')
    if request['schema']!='genlaw.small-partition-material-request.v1' or request['source_code']!=file_binding(Path(__file__)):
        raise ValueError('Unknown or changed small-material request')
    project=checked_file(request['inputs']['project']);config,binding=project_binding(project)
    grid=expected_grid(config)
    if binding!=request['inputs'] or grid!=request['grid'] or windows(grid['steps'])!=request['windows']:
        raise ValueError('Original stock, NC, grid or complete partition changed')
    checked_build(checked_file(request['build']));rows=[]
    for i,window in enumerate(request['windows']):
        result_path=out/f'window-{i:02d}'/'result.json';result,r=checked_result(result_path)
        if (r['inputs']!=binding or r['grid']!=grid or r['window']!=window or r['build']!=request['build']
                or r['end_time_seconds'] is not None):raise ValueError('Foreign or partial native window')
        rows.append({'result':file_binding(result_path),'window':window,'shards':result['shards'],
                     'triangle_count':result['triangle_count']})
    total=sum(r['triangle_count'] for r in rows)
    if not 0<total<=500000:raise ValueError('Complete small material exceeds the unchanged mesh limit')
    return request,rows,total


def join(out):
    require_bounded_worker();request,rows,total=context(out);mesh=out/'surface.stl'
    with mesh.open('xb') as dest:
        dest.write(b'GenLaw exact native window records'.ljust(80,b'\0'));dest.write(struct.pack('<I',total))
        for row in rows:
            for shard in row['shards']:
                with checked_file(shard['surface']).open('rb') as source:
                    source.seek(84);shutil.copyfileobj(source,dest,1024*1024)
    if mesh.stat().st_size!=84+50*total:raise ValueError('Incomplete native triangle concatenation')
    write(out/'result.json',{'schema':'genlaw.small-complete-native-material.v1','request':file_binding(out/'request.json'),
        'inputs':request['inputs'],'grid':request['grid'],'native_windows':rows,'triangle_count':total,
        'surface':file_binding(mesh),'all_global_cells_covered_once':True,'native_triangle_records_unchanged':True,
        'artificial_caps':False,'repair_or_simplification':False,'target_CAD_used':False,
        'mesh_topology_not_yet_verified':True,'whole_process_card_complete':False,'production_approved':False,
        'source_code':file_binding(Path(__file__))})


def checked(path):
    require_bounded_worker();result=READ(path);out=path.parent;request,rows,total=context(out)
    if (result['schema']!='genlaw.small-complete-native-material.v1' or result['request']!=file_binding(out/'request.json')
            or result['source_code']!=file_binding(Path(__file__)) or result['inputs']!=request['inputs']
            or result['grid']!=request['grid'] or result['native_windows']!=rows or result['triangle_count']!=total
            or result['all_global_cells_covered_once'] is not True or result['native_triangle_records_unchanged'] is not True
            or any(result[k] is not False for k in ['artificial_caps','repair_or_simplification','target_CAD_used',
                                                  'whole_process_card_complete','production_approved'])):
        raise ValueError('Complete material provenance changed')
    mesh=checked_file(result['surface'])
    with mesh.open('rb') as source:
        source.seek(84)
        for row in rows:
            for shard in row['shards']:
                with checked_file(shard['surface']).open('rb') as native:
                    native.seek(84)
                    while block:=native.read(1024*1024):
                        if source.read(len(block))!=block:raise ValueError('Joined native triangle record changed')
        if source.read(1):raise ValueError('Added native triangles')
    return result,mesh


def run(project,build,out):
    project=project.resolve();build=build.resolve();out=out.resolve();out.relative_to(ROOT)
    config,binding=project_binding(project);grid=expected_grid(config);parts=windows(grid['steps'])
    checked_build(build);out.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(Path(__file__),out/'material-source.py')
    write(out/'request.json',{'schema':'genlaw.small-partition-material-request.v1','inputs':binding,
        'build':file_binding(build),'grid':grid,'windows':parts,'source_code':file_binding(Path(__file__)),
        'preserved_source':file_binding(out/'material-source.py'),'target_CAD_used':False,'limits_increased':False})
    write(out/'owner.json',{'process':process_identity(os.getpid()),'request':file_binding(out/'request.json')})
    for i,window in enumerate(parts):
        native_run(project,build,window,out/f'window-{i:02d}',500000)
        print(json.dumps({'complete_native_window':i+1,'windows':len(parts)}),flush=True)
    admitted_bounded(out,'join',[sys.executable,Path(__file__),'join','--output',out])
    print(json.dumps({'complete_original_material_written':True,'geometry_acceptance_pending':True}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['run','join']);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--project',type=Path);p.add_argument('--build',type=Path);a=p.parse_args()
    if a.mode=='run':run(a.project,a.build,a.output)
    else:join(a.output.resolve())
