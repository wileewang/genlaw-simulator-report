"""Opt-in bounded native window output, with explicit multiple-file provenance.

This is not yet a complete stock assembler or a manufacturing acceptance entry.
The original NC and stock are bound independently of the output partition.
"""
import argparse
import json
import math
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.cell_windows import (candidate_engine, checked_file, file_binding,
    project_binding, expected_grid, window_budget)
from genlaw_pipeline.run import write
from genlaw_pipeline.sparse_material import checked_resources
from step2gcode_demo.validation.local_resource_limits import require_bounded_worker

LOAD = lambda p: json.loads(Path(p).read_text())
MAX_TRIANGLES = 500000
MAX_SHARDS = 64


def checked_build(path):
    build = LOAD(path)
    if (build.get('schema') != 'genlaw.native-stream-build.v1'
            or build.get('status') != 'linked_source_bound'
            or build.get('reference_relink_byte_identical') is not True
            or build.get('maximum_cells_per_window') != 5000000
            or build.get('maximum_xy_samples') != 100000
            or build.get('maximum_triangles_per_file') != MAX_TRIANGLES
            or build.get('maximum_shards_per_window') != MAX_SHARDS
            or build.get('threads') != 1 or build.get('target_cad_used') is not False
            or build.get('default_changed') is not False):
        raise ValueError('Unqualified native stream build configuration')
    reference_binary, env, reference = candidate_engine(checked_file(build['reference_manifest']))
    if (file_binding(checked_file(build['reference_relink']))['sha256'] != reference['binary_sha256']
            or build['linked_libraries'] != reference['linked_libraries']
            or build['library_directories'] != reference['library_directories']):
        raise ValueError('Reused native objects or runtime differ from frozen reference')
    for item in build['reused_objects'] + build['source_code']:
        checked_file(item)
    if len(build['resources']) != 5:
        raise ValueError('Missing compiler or linker evidence')
    for binding in build['resources']:
        p=checked_file(binding); checked_resources(p, LOAD(p)['command'])
    return checked_file(build['binary']), env


def command(binary, project, window, directory, limit):
    return [binary,'--cell-window',','.join(map(str,window)),
        '--facets-per-file',str(limit),project,directory]


def checked_request(path):
    request=LOAD(path)
    if (request.get('schema') != 'genlaw.native-stream-request.v1'
            or request.get('source_code') != file_binding(Path(__file__))
            or request.get('end_time_seconds') is not None
            or request.get('target_cad_used') is not False
            or request.get('production_approved') is not False):
        raise ValueError('Foreign or changed stream request')
    project=checked_file(request['inputs']['project'])
    config,binding=project_binding(project)
    grid=expected_grid(config); window=request['window']
    if binding != request['inputs'] or grid != request['grid']:
        raise ValueError('Original project, NC or grid changed')
    budget=window_budget(window,grid['steps'])
    if not budget['allowed'] or budget != request['budget']:
        raise ValueError('Native window exceeds unchanged compute limits')
    limit=request['facets_per_file']
    if type(limit) is not int or not 1 <= limit <= MAX_TRIANGLES:
        raise ValueError('Invalid native output facet limit')
    binary,env=checked_build(checked_file(request['build']))
    return request,project,binary,env


def validate_native(report, grid, window, limit, directory):
    """Check exact output membership and framing; does not assert watertightness."""
    require_bounded_worker()
    expected={'schema':'genlaw.native-stream-window.v1','status':'complete_window',
        'nc_executed':True,'complete_nc':True,'surface_computed':True,'target_cad_used':False,
        'reduction':False,'threads':1,'sampling':'global-integer-lattice-v1','units':'mm',
        'cell_interval':'half-open','cell_window':window,'maximum_triangles_per_file':limit,
        'whole_stock_surface_verified':False,'production_approved':False,
        **{k:grid[k] for k in ['resolution_mm','offset_mm','steps']}}
    if any(report.get(k) != v or type(report.get(k)) is not type(v) for k,v in expected.items()):
        raise ValueError('Native output differs from requested original lattice or execution scope')
    budget=window_budget(window,grid['steps'])
    if not budget['allowed'] or type(limit) is not int or not 1 <= limit <= MAX_TRIANGLES:
        raise ValueError('Over-budget native window or shard limit')
    for key,maximum in [('native_moves',200000),('visited_native_cells',budget['cell_count']),
                        ('triangle_count',5*budget['cell_count'])]:
        if type(report.get(key)) is not int or not 0 <= report[key] <= maximum:
            raise ValueError('Invalid native count: '+key)
    if report['triangle_count'] > 5*report['visited_native_cells']:
        raise ValueError('Native facets exceed the visited marching-cubes cells')
    rows=report.get('shards')
    if not isinstance(rows,list) or len(rows)>MAX_SHARDS:
        raise ValueError('Invalid native shard list')
    result=[]; total=0
    for i,row in enumerate(rows):
        filename=f'surface-{i:04d}.stl'; count=row.get('triangles')
        if (row.get('file') != filename or type(row.get('index')) is not int or row['index'] != i
                or type(count) is not int or not 1 <= count <= limit
                or (i<len(rows)-1 and count != limit)
                or type(row.get('bytes')) is not int or row['bytes'] != 84+50*count):
            raise ValueError('Missing, reordered, empty or over-budget native shard')
        p=directory/filename
        if p.is_symlink() or not p.is_file() or p.stat().st_size != row['bytes']:
            raise ValueError('Missing or incorrectly sized native STL')
        with p.open('rb') as stream:
            header=stream.read(84)
        if len(header)!=84 or struct.unpack_from('<I',header,80)[0] != count:
            raise ValueError('Native STL facet count disagrees with its manifest')
        result.append({'index':i,'triangles':count,'surface':file_binding(p)})
        total+=count
    if total != report['triangle_count']:
        raise ValueError('Native output omitted or added facets')
    if {p.name for p in directory.iterdir()} != {'native.json',*(r['file'] for r in rows)}:
        raise ValueError('Unfinished or unlisted native output files')
    return result


def finish(out):
    require_bounded_worker()
    request,project,binary,_=checked_request(out/'request.json')
    native=out/'native/native.json'
    checked_resources(out/'native-resources.json',command(binary,project,request['window'],
        out/'native',request['facets_per_file']))
    data=LOAD(native)
    shards=validate_native(data,request['grid'],request['window'],request['facets_per_file'],native.parent)
    result={'schema':'genlaw.native-stream-result.v1','status':'complete_native_window',
        'request':file_binding(out/'request.json'),'native':file_binding(native),
        'resources':file_binding(out/'native-resources.json'),'shards':shards,
        'triangle_count':data['triangle_count'],'source_code':file_binding(Path(__file__)),
        'whole_stock_surface_verified':False,'production_approved':False,'whole_process_card_complete':False}
    write(out/'result.json',result)
    return result


def checked_result(path):
    require_bounded_worker()
    result=LOAD(path)
    if (result.get('schema') != 'genlaw.native-stream-result.v1'
            or result.get('status') != 'complete_native_window'
            or result.get('source_code') != file_binding(Path(__file__))
            or any(result.get(k) is not False for k in
                ['whole_stock_surface_verified','production_approved','whole_process_card_complete'])):
        raise ValueError('Foreign stream result or unsupported manufacturing claim')
    request_path=checked_file(result['request']); out=request_path.parent
    request,project,binary,_=checked_request(request_path)
    native=checked_file(result['native']); data=LOAD(native)
    if native != out/'native/native.json': raise ValueError('Foreign native manifest')
    checked_resources(checked_file(result['resources']),command(binary,project,request['window'],
        native.parent,request['facets_per_file']))
    shards=validate_native(data,request['grid'],request['window'],request['facets_per_file'],native.parent)
    if shards != result['shards'] or data['triangle_count'] != result['triangle_count']:
        raise ValueError('Stream result does not describe all current native files')
    return result,request


def run(project,build,window,out,limit=MAX_TRIANGLES):
    project=project.resolve(); build=build.resolve(); out=out.resolve()
    out.relative_to(ROOT)
    config,binding=project_binding(project); grid=expected_grid(config)
    budget=window_budget(window,grid['steps'])
    if not budget['allowed'] or type(limit) is not int or not 1<=limit<=MAX_TRIANGLES:
        raise ValueError('Native request exceeds fixed cell, sample or file limits')
    binary,env=checked_build(build)
    out.mkdir(parents=True,exist_ok=False)
    write(out/'request.json',{'schema':'genlaw.native-stream-request.v1','inputs':binding,
        'build':file_binding(build),'grid':grid,'window':window,'budget':budget,
        'facets_per_file':limit,'end_time_seconds':None,'target_cad_used':False,
        'source_code':file_binding(Path(__file__)),'production_approved':False})
    admitted_bounded(out,'native',command(binary,project,window,out/'native',limit),env)
    admitted_bounded(out,'validate',[sys.executable,Path(__file__),'finish','--out',out])
    return LOAD(out/'result.json')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['run','finish'])
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--project',type=Path)
    parser.add_argument('--build',type=Path)
    parser.add_argument('--window',type=int,nargs=6)
    parser.add_argument('--facets-per-file',type=int,default=MAX_TRIANGLES)
    args=parser.parse_args()
    if args.action=='run': run(args.project,args.build,args.window,args.out,args.facets_per_file)
    else: finish(args.out.resolve())
