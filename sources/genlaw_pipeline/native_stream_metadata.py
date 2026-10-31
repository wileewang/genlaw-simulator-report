"""Metadata-only native stream receipt checks; payloads are hashed per chunk.

One execution owns one cell window even when it writes many files. This reader
does not invent legacy sparse-leaf receipts or relabel files as executions.
"""
import json
from pathlib import Path
import re

from .cell_windows import checked_file,file_binding,project_binding,expected_grid,window_budget
from .native_stream import checked_build,command
from .simulation_engine import workspace_path
from .sparse_material import checked_resources
from .sparse_stream_surface import header_count

ROOT=Path(__file__).resolve().parents[1]
LOAD=lambda p:json.loads(p.read_text())
PRODUCER=ROOT/'genlaw_pipeline/native_stream.py'


def context(project,build):
    config,inputs=project_binding(project)
    binary,_=checked_build(build)
    return {'inputs':inputs,'grid':expected_grid(config),'build':file_binding(build),
        'binary':file_binding(binary),'native_producer':file_binding(PRODUCER)}


def receipt(path,ctx):
    result=LOAD(path)
    if (result.get('schema')!='genlaw.native-stream-result.v1'
            or result.get('status')!='complete_native_window'
            or result.get('source_code')!=ctx['native_producer']
            or any(result.get(k) is not False for k in
                ['whole_stock_surface_verified','production_approved','whole_process_card_complete'])):
        raise ValueError('Unsupported native stream receipt or false manufacturing claim')
    request_path=checked_file(result['request']);request=LOAD(request_path);out=request_path.parent
    if (request.get('schema')!='genlaw.native-stream-request.v1'
            or request.get('inputs')!=ctx['inputs'] or request.get('build')!=ctx['build']
            or request.get('source_code')!=ctx['native_producer'] or request.get('grid')!=ctx['grid']
            or request.get('end_time_seconds') is not None or request.get('target_cad_used') is not False
            or request.get('production_approved') is not False):
        raise ValueError('Native request has different NC, stock, source, build or scope')
    window=request['window'];budget=window_budget(window,ctx['grid']['steps'])
    limit=request['facets_per_file']
    if not budget['allowed'] or budget!=request['budget'] or type(limit) is not int or not 1<=limit<=500000:
        raise ValueError('Native request exceeded the unchanged worker budget')
    native=checked_file(result['native']);data=LOAD(native)
    if native!=out/'native/native.json':raise ValueError('Foreign native output manifest')
    checked_resources(checked_file(result['resources']),command(workspace_path(ctx['binary']['path']),
        checked_file(ctx['inputs']['project']),window,native.parent,limit))
    expected={'schema':'genlaw.native-stream-window.v1','status':'complete_window','nc_executed':True,
        'complete_nc':True,'surface_computed':True,'target_cad_used':False,'reduction':False,'threads':1,
        'sampling':'global-integer-lattice-v1','units':'mm','cell_interval':'half-open',
        'cell_window':window,'maximum_triangles_per_file':limit,'whole_stock_surface_verified':False,
        'production_approved':False,**{k:ctx['grid'][k] for k in ['resolution_mm','offset_mm','steps']}}
    if any(data.get(k)!=v or type(data.get(k)) is not type(v) for k,v in expected.items()):
        raise ValueError('Native completion differs from its original execution request')
    for key,maximum in [('native_moves',200000),('visited_native_cells',budget['cell_count']),
                        ('triangle_count',5*budget['cell_count'])]:
        if type(data.get(key)) is not int or not 0<=data[key]<=maximum:
            raise ValueError('Invalid native output count')
    if data['triangle_count']>5*data['visited_native_cells']:
        raise ValueError('Native facet count exceeds its visited cells')
    rows=data.get('shards');shards=result.get('shards')
    if not isinstance(rows,list) or len(rows)>64 or not isinstance(shards,list) or len(shards)!=len(rows):
        raise ValueError('Missing or excessive native output chunks')
    chunks=[];total=0
    for i,(row,saved) in enumerate(zip(rows,shards)):
        filename=f'surface-{i:04d}.stl';count=row.get('triangles');mesh=native.parent/filename
        if (row.get('file')!=filename or type(row.get('index')) is not int or row['index']!=i
                or type(count) is not int or not 1<=count<=limit
                or i<len(rows)-1 and count!=limit or row.get('bytes')!=84+50*count
                or mesh.is_symlink() or not mesh.is_file() or header_count(mesh)!=count):
            raise ValueError('Incomplete, reordered, empty or malformed native chunk')
        binding=saved.get('surface',{})
        if (saved.get('index')!=i or saved.get('triangles')!=count
                or binding.get('path')!=str(mesh.relative_to(ROOT))
                or not isinstance(binding.get('sha256'),str) or not re.fullmatch('[0-9a-f]{64}',binding['sha256'])):
            raise ValueError('Native receipt lost its original chunk identity')
        chunks.append({'shard_index':i,'surface':binding,'triangles':count});total+=count
    if (total!=data['triangle_count'] or result.get('triangle_count')!=total
            or {p.name for p in native.parent.iterdir()}!={'native.json',*(r['file'] for r in rows)}):
        raise ValueError('Missing, added or unfinished native output')
    return {'result':file_binding(path),'request':result['request'],'native':result['native'],
        'window':window,'triangles':total,'chunks':chunks}
