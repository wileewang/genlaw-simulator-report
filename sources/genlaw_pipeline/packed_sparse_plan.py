"""Budget-packed stock boundaries plus native-certified interior refinement.

The result is a cell-ownership plan only. Native surface execution stays blocked
until output triangle/file bounds and the new plan consumer are qualified.
No material cells, CAD, synthetic surface or removed-by-tool proof is invented.
"""
import argparse
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'web/tools')]
from mesh_metrics import MAX_TRIANGLES,require_bounded_worker
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.cell_windows import MAX_WINDOWS,MAX_CELLS,checked_file,file_binding,project_binding,expected_grid,window_budget
from genlaw_pipeline.material_blocks import engine_for_inspection,inspect
from genlaw_pipeline.sparse_material import shell_partition,split_box,validate_cover,checked_inspection
from genlaw_pipeline.rectangular_tiles import tiles
from genlaw_pipeline.certified_box_merge import proposals as merge_proposals
from genlaw_pipeline.run import write

LOAD=lambda p:json.loads(p.read_text())
SOURCE_PATHS=['genlaw_pipeline/packed_sparse_plan.py','genlaw_pipeline/rectangular_tiles.py','genlaw_pipeline/certified_box_merge.py',
    'genlaw_pipeline/sparse_material.py','genlaw_pipeline/material_blocks.py','genlaw_pipeline/cell_windows.py',
    'engines/camotics/source/src/camotics/contour/MarchingCubes.cpp','web/tools/mesh_metrics.py']


def sources(): return [file_binding(ROOT/name) for name in SOURCE_PATHS]


def initialize(project,engine,output):
    require_bounded_worker()
    project,engine=project.resolve(),engine.resolve(); config,binding=project_binding(project)
    grid=expected_grid(config); binary,_=engine_for_inspection(engine)
    binding.update(engine_manifest=file_binding(engine),engine_binary=file_binding(binary))
    boxes=shell_partition(grid['steps']); pending=[]; boundary_layouts=[]
    for index,box in enumerate(boxes):
        interior=len(boxes)==7 and index==6
        if interior:
            pending.append({'bounds':box,'role':'interior'})
        else:
            selected,packed=tiles(box,grid['steps'])
            boundary_layouts.append({'original_box':box,'layout':selected})
            pending.extend({'bounds':b,'role':'boundary'} for b in packed)
    if len(pending)>MAX_WINDOWS:
        raise ValueError('Packed boundaries exceed the unchanged global leaf budget')
    write(output,{'schema':'genlaw.packed-partition-state.v1','status':'inspection_pending',
        'binding':binding,'grid':grid,'sources':sources(),'boundary_layouts':boundary_layouts,
        'pending':pending,'leaves':[],'inspections':[],'refine_above_cells':500000,
        'native_material_jobs_started':0,'surface_computed':False,'target_cad_used':False,
        'native_execution_allowed':False,'production_approved':False})
    print(json.dumps({'initial_native_queries':len(pending),'boundary_tiles':sum(x['layout']['tiles'] for x in boundary_layouts)}),flush=True)


def state(path):
    value=LOAD(path)
    if (value.get('schema')!='genlaw.packed-partition-state.v1' or value.get('sources')!=sources()
            or value.get('status') not in ['inspection_pending','cell_partition_complete']
            or value.get('native_material_jobs_started')!=0 or value.get('surface_computed') is not False
            or value.get('target_cad_used') is not False or value.get('native_execution_allowed') is not False
            or value.get('production_approved') is not False or value.get('refine_above_cells')!=500000
            or not 1<=len(value['leaves'])+len(value['pending'])<=MAX_WINDOWS
            or len(value['inspections'])>2*MAX_WINDOWS):
        raise ValueError('Changed packed-partition scope, source or resource limits')
    binding=value['binding']; project=checked_file(binding['project']); config,current=project_binding(project)
    binary,_=engine_for_inspection(checked_file(binding['engine_manifest']))
    if (value['grid']!=expected_grid(config) or binding['nc_files']!=current['nc_files']
            or binding['engine_binary']!=file_binding(binary)):
        raise ValueError('Current stock, NC, engine or original lattice changed')
    for row in value['pending']:
        if row['role'] not in ['boundary','interior','certified_merge']: raise ValueError('Unknown packed partition role')
        budget=window_budget(row['bounds'],value['grid']['steps'])
        if row['role']=='boundary' and not budget['allowed']: raise ValueError('Boundary tile exceeds native memory budget')
        if row['role']=='certified_merge' and row.get('expected_state') not in ['uncut_solid','empty_outside_stock']:
            raise ValueError('A merge proposal requires a homogeneous native recheck')
    return value,project,binary


def advance(state_path,inspection_path,output):
    require_bounded_worker(); value,project,binary=state(state_path)
    if not value['pending'] or value['status']!='inspection_pending': raise ValueError('No uninspected cells remain')
    report=checked_inspection(inspection_path,value['binding'],value['grid'],project,binary)
    if [r['bounds'] for r in report['blocks']]!=[r['bounds'] for r in value['pending']]:
        raise ValueError('Native inspection does not cover the exact pending cells')
    proof=file_binding(inspection_path); leaves=list(value['leaves']); uncertain=[]
    for index,(node,row) in enumerate(zip(value['pending'],report['blocks'])):
        budget=window_budget(row['bounds'],value['grid']['steps'])
        if node['role']=='certified_merge' and row['state']!=node['expected_state']:
            raise ValueError('Merged region failed its new homogeneous native certificate')
        unknown=row['state']=='requires_native_cells'
        refine=unknown and (not budget['allowed'] or node['role']=='interior' and budget['cell_count']>value['refine_above_cells'])
        leaf={'bounds':row['bounds'],'state':row['state'],'inspection':proof,'query_index':index,'role':node['role']}
        if refine: uncertain.append((node,row,budget,leaf))
        else: leaves.append(leaf)
    merges=merge_proposals(leaves); consumed={i for row in merges for i in row['members']}
    pending=[{'bounds':row['bounds'],'role':'certified_merge','expected_state':row['state'],
        'original_leaves':[leaves[i] for i in row['members']]} for row in merges]
    leaves=[row for i,row in enumerate(leaves) if i not in consumed]
    for index,(node,row,budget,leaf) in enumerate(uncertain):
        refine=True
        if len(leaves)+len(pending)+len(uncertain)-index+1>MAX_WINDOWS:
            if not budget['allowed']: raise ValueError('Native-certified interior still exceeds the unchanged leaf budget')
            refine=False
        if refine:
            pending.extend({'bounds':b,'role':node['role']} for b in split_box(row['bounds']))
        else:
            leaves.append(leaf)
    for index,leaf in enumerate(leaves): leaf['index']=index
    value.update(parent=file_binding(state_path),leaves=leaves,pending=pending,
        inspections=value['inspections']+[proof],status='inspection_pending' if pending else 'cell_partition_complete')
    write(output,value)
    print(json.dumps({'accepted_leaves':len(leaves),'pending_native_queries':len(pending)}),flush=True)


def output_preflight(leaves,grid):
    """Conservative code-level bounds; these are not measured native facet counts."""
    source=ROOT/'engines/camotics/source/src/camotics/contour/MarchingCubes.cpp'
    if 'for (int j = 0; j < 5; j++)' not in source.read_text():
        raise ValueError('Native source no longer supports the five-triangles-per-cell bound')
    rows=[]
    for leaf in leaves:
        if leaf['state']!='requires_native_cells': continue
        budget=window_budget(leaf['bounds'],grid['steps']); maximum_triangles=5*budget['cell_count']
        maximum_bytes=84+50*maximum_triangles
        rows.append({'index':leaf['index'],'cell_count':budget['cell_count'],'xy_samples':budget['xy_sample_points'],
            'code_level_maximum_triangles':maximum_triangles,'code_level_maximum_binary_stl_bytes':maximum_bytes,
            'triangle_budget_proven_by_cell_bound':maximum_triangles<=MAX_TRIANGLES,
            'file_budget_proven_by_cell_bound':maximum_bytes<=64*1024**2})
    return {'schema':'genlaw.packed-output-preflight.v1','method':'conservative five-facets-per-cell source bound',
        'marching_cubes_source':file_binding(source),'maximum_triangles_per_audit':MAX_TRIANGLES,
        'maximum_file_bytes':64*1024**2,'active_leaves':rows,
        'leaves_with_triangle_budget_unproven':sum(not r['triangle_budget_proven_by_cell_bound'] for r in rows),
        'leaves_with_file_budget_unproven':sum(not r['file_budget_proven_by_cell_bound'] for r in rows),
        'actual_native_triangle_counts_measured':False,'bounds_exceeded_does_not_prove_actual_overflow':True,
        'native_surface_execution_qualified':False}


def checked(path):
    plan=LOAD(path)
    if (plan.get('schema')!='genlaw.packed-sparse-plan.v1' or plan.get('status')!='complete_cells_output_unqualified'
            or plan.get('native_execution_allowed') is not False or plan.get('surface_computed') is not False
            or plan.get('target_cad_used') is not False or plan.get('native_material_jobs_started')!=0
            or plan.get('sources')!=sources() or plan.get('production_approved') is not False
            or plan.get('whole_process_card_complete') is not False):
        raise ValueError('Packed cell plan cannot authorize material execution or manufacturing acceptance')
    last,project,binary=state(checked_file(plan['final_state']))
    if last['pending'] or last['status']!='cell_partition_complete': raise ValueError('Uninspected cells remain')
    if any(plan[k]!=last[k] for k in ['binding','grid','leaves','inspections','boundary_layouts']):
        raise ValueError('Packed plan differs from its bound final native-inspection state')
    reports={}
    for binding in plan['inspections']:
        key=binding['path'],binding['sha256']
        if key in reports: raise ValueError('Duplicate native material inspection')
        reports[key]=checked_inspection(checked_file(binding),plan['binding'],plan['grid'],project,binary)
    for leaf in plan['leaves']:
        binding=leaf['inspection']; report=reports.get((binding['path'],binding['sha256'])); i=leaf['query_index']
        if report is None or type(i) is not int or not 0<=i<len(report['blocks']): raise ValueError('Missing native leaf certificate')
        row=report['blocks'][i]
        if leaf['bounds']!=row['bounds'] or leaf['state']!=row['state']: raise ValueError('Leaf differs from its native certificate')
    coverage=validate_cover(plan['leaves'],plan['grid'])
    if coverage!=plan['coverage'] or plan['output_preflight']!=output_preflight(plan['leaves'],plan['grid']):
        raise ValueError('Complete cell coverage or conservative output preflight changed')
    return plan


def finish(state_path,output):
    require_bounded_worker(); value,_,_=state(state_path)
    if value['pending'] or value['status']!='cell_partition_complete': raise ValueError('Cannot finish with uninspected cells')
    coverage=validate_cover(value['leaves'],value['grid'])
    plan={'schema':'genlaw.packed-sparse-plan.v1','status':'complete_cells_output_unqualified',
        **{k:value[k] for k in ['binding','grid','leaves','inspections','boundary_layouts']},
        'sources':sources(),'final_state':file_binding(state_path),'coverage':coverage,
        'output_preflight':output_preflight(value['leaves'],value['grid']),
        'native_execution_allowed':False,'native_material_jobs_started':0,'target_cad_used':False,
        'surface_computed':False,'production_approved':False,'whole_process_card_complete':False}
    write(output,plan); checked(output)
    print(json.dumps({'leaves':len(plan['leaves']),'coverage':coverage,
        'triangle_budget_unproven':plan['output_preflight']['leaves_with_triangle_budget_unproven'],
        'material_execution_allowed':False}),flush=True)


def create_plan(project,engine,out):
    project,engine,out=[p.resolve() for p in [project,engine,out]]
    for path in [project,engine,out]: path.relative_to(ROOT)
    out.mkdir(parents=True,exist_ok=False); current=out/'state-000.json'; worker=Path(__file__).resolve()
    admitted_bounded(out,'initialize',[sys.executable,worker,'initialize','--project',project,'--engine',engine,'--output',current])
    for index in range(2*MAX_WINDOWS):
        value=LOAD(current)
        if not value['pending']: break
        inspection=out/f'inspection-{index:03d}'
        inspect(project,engine,[r['bounds'] for r in value['pending']],inspection)
        following=out/f'state-{index+1:03d}.json'
        admitted_bounded(out,f'advance-{index:03d}',[sys.executable,worker,'advance','--state',current,
            '--inspection',inspection/'result.json','--output',following])
        current=following
        print(json.dumps({'inspection_batches_completed':index+1,'pending':len(LOAD(current)['pending'])}),flush=True)
    else: raise ValueError('Native inspection refinement budget exhausted')
    admitted_bounded(out,'finish',[sys.executable,worker,'finish','--state',current,'--output',out/'plan.json'])
    return out/'plan.json'


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); sub=parser.add_subparsers(dest='action',required=True)
    for mode in ['plan','initialize']:
        p=sub.add_parser(mode)
        for name in ['project','engine','output']: p.add_argument('--'+name,type=Path,required=True)
    for mode in ['advance','finish']:
        p=sub.add_parser(mode); p.add_argument('--state',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
        if mode=='advance': p.add_argument('--inspection',type=Path,required=True)
    args=parser.parse_args()
    if args.action=='plan': create_plan(args.project,args.engine,args.output)
    elif args.action=='initialize': initialize(args.project,args.engine,args.output)
    elif args.action=='advance': advance(args.state,args.inspection,args.output)
    else: finish(args.state,args.output)
