"""Prove the native first-profile material is a prefix of the normal program."""
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.cell_windows import checked_file,file_binding
from genlaw_pipeline.sparse_material import checked_resources
from genlaw_pipeline.run import write
from step2gcode_demo.validation.local_resource_limits import require_bounded_worker
READ=lambda p:json.loads(Path(p).read_text())


def run(path):
    require_bounded_worker();req=READ(path);suite=READ(checked_file(req['suite']))
    normal=next(r for r in suite['cases'] if r['id']=='baseline' and r['kind']=='normal')
    control=next(r for r in suite['cases'] if r['kind']=='omitted-second-profile')
    normal_req=READ(checked_file(normal['request']));control_req=READ(checked_file(control['request']))
    if (normal_req['engine_build']!=control_req['engine_build'] or normal_req['simulation_precision']!=control_req['simulation_precision']):
        raise ValueError('Replay states came from different numerical configurations')
    build=READ(checked_file(normal_req['engine_build']));binary=checked_file(build['binary'])
    traces=[]
    for key,row in [('normal',normal),('first-profile',control)]:
        stage=checked_file(req[key]['stages']);material=READ(checked_file(row['material']))
        mr=READ(checked_file(material['request']))
        if file_binding(stage)!=mr['stages']:raise ValueError('Replay path is not the material stage request')
        trace_path=checked_file(req[key]['native_paths']);data=READ(trace_path)
        command=[str(binary),'--path-only',str(stage),str(trace_path.parent)]
        checked_resources(checked_file(req[key]['resources']),command)
        if (data['schema']!='genlaw.precision-native-paths.v1' or data['max_arc_error_mm']!=.0005
                or len(data['stages'])!=1 or data['surface_computed'] is not False):raise ValueError('Unbound native replay path')
        moves=data['stages'][0]['moves'];native=READ(checked_file(material['native_windows'][0]['native_manifest']))
        if len(moves)!=native['native_moves']:raise ValueError('Material and replay path counts differ')
        traces.append(moves)
    full,prefix=traces
    if not 0<len(prefix)<len(full) or prefix!=full[:len(prefix)]:raise ValueError('First-profile material is not an exact original native prefix')
    plan=READ(checked_file(READ(checked_file(normal['metrics']))['inspection']))['plan']
    text=checked_file(normal_req['executed_NC']).read_text().splitlines()
    prefix_end=prefix[-1]['line'];second_start=next(i for i,s in enumerate(text,1) if s.strip()=='(begin operation: Profile_P02)')
    first_end=next(i for i,s in enumerate(text,1) if s.strip()=='(finish operation: Profile_P01)')
    if not prefix_end<first_end<second_start or any(m['line']<second_start for m in full[len(prefix):]):
        raise ValueError('Prefix does not terminate between the two native operations')
    write(path.parent/'prefix-proof.json',{'status':'exact_native_first_operation_prefix_verified','request':file_binding(path),
        'full_native_moves':len(full),'first_operation_native_moves':len(prefix),'all_native_prefix_fields_identical':True,
        'native_line_before_second_operation':prefix_end,'first_profile_complete':True,
        'first_operation_material':control['material'],'final_material':normal['material'],
        'original_stock':plan['stock'],'native_precision_mm':.0005,'resolution_mm':plan['grid']['resolution_mm'],
        'first_operation_final_target_acceptance':False,'first_operation_is_intentionally_incomplete_final_product':True,
        'new_material_simulations':0,'continuous_time_animation':False,'source':file_binding(Path(__file__))})
    print(json.dumps({'native_first_operation_prefix_moves':len(prefix),'full_moves':len(full)}),flush=True)


if __name__=='__main__':run(Path(sys.argv[1]).resolve())
