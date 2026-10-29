"""Execute cumulative native prefixes from the original stock and measure them."""
import json
import math
import sys
from .preparation import digest
from .programming import verify_binding,simulation_project
from .sequence_nc import check_prefix
from .measurement import measure_bounded


def run_checkpoints(out,plan,cam,engine):
    from .run import bounded,write,CAMOTICS,simulation_env,ROOT
    records=[];volumes=[math.prod(b-a for a,b in zip(plan['stock']['bounds_mm']['min'],plan['stock']['bounds_mm']['max']))]
    for i,source in enumerate(cam['checkpoint_prefixes'],1):
        case=out/'checkpoints'/source['id'];verify_binding(case/'prefix-binding.json',source['prefix_binding_sha256'])
        proof=json.loads((case/'prefix-binding.json').read_text());verify_binding(case/'program.nc',proof['prefix_nc_sha256'])
        check_prefix((case/'program.nc').read_text(),(out/'program.nc').read_text(),plan,i)
        config=simulation_project(plan);write(case/'simulation.camotics',config);project_hash=digest(case/'simulation.camotics')
        bounded(case,'camotics',[CAMOTICS,'--threads','1','--reduce=false','--binary',case/'simulation.camotics',case/'simulated.stl'],simulation_env(),case/'simulation.camotics')
        verify_binding(case/'program.nc',proof['prefix_nc_sha256']);verify_binding(case/'simulation.camotics',project_hash)
        verify_binding(CAMOTICS,engine['manifest']['binary_sha256'])
        write(case/'simulation-result.json',{'schema':'genlaw.simulation-result.v1','status':'executed',
            'nc_sha256_before':proof['prefix_nc_sha256'],'nc_sha256_after':digest(case/'program.nc'),'nc_text_modified':False,
            'project_sha256':project_hash,'mesh_sha256':digest(case/'simulated.stl'),'resolution_mm':config['resolution'],
            'engine_binary_sha256':digest(CAMOTICS),'engine_manifest_sha256':engine['manifest_sha256'],
            'engine_build_id':engine['manifest']['id'],'cumulative_prefix':True,'original_stock_used':True,'initial_machine_state':plan['setup']['startup'],
            'fixture_collision_verified':False,'material_physics_verified':False})
        measure_bounded(case,'measure',case,case/'simulated.stl',case/'metrics.json',display=True)
        observer='genlaw_pipeline.pocket_sequence_verification' if plan['schema']=='genlaw.execution-plan.v12' else 'genlaw_pipeline.sequence_verification'
        bounded(case,'observe',[sys.executable,'-m',observer,'--parent',out,'--case',case,'--output',case/'observations.json'])
        metrics=json.loads((case/'metrics.json').read_text());volumes.append(metrics['actual_mesh']['volume_mm3'])
        records.append({'id':source['id'],'completed_operations':i,'iou_percent':metrics['iou_percent'],
            'prefix_binding_sha256':digest(case/'prefix-binding.json'),'simulation_result_sha256':digest(case/'simulation-result.json'),
            'mesh_sha256':digest(case/'simulated.stl'),'native_geometry_sha256':digest(case/'native-geometry.json'),
            'metrics_sha256':digest(case/'metrics.json'),'observations_sha256':digest(case/'observations.json')})
    volumes.append(json.loads((out/'metrics.json').read_text())['actual_mesh']['volume_mm3'])
    if not all(a>b for a,b in zip(volumes,volumes[1:])):raise ValueError('Ordered actual material volumes do not strictly decrease')
    result={'schema':'genlaw.material-checkpoints.v1','status':'measured','full_nc_sha256':digest(out/'program.nc'),
        'full_mesh_sha256':digest(out/'simulated.stl'),'checkpoints':records,'actual_volumes_mm3':volumes,
        'material_volume_monotonically_decreases':True,'separate_cumulative_prefix_runs_from_original_stock':True,
        'main_final_simulation_is_one_unchanged_nc_execution':True,'manufacturing_acceptance':False}
    write(out/'material-checkpoints.json',result)
    return result


def verify_checkpoint_bindings(out,cam):
    """Verify recorded checkpoint evidence without repeating native simulations."""
    result=json.loads((out/'material-checkpoints.json').read_text())
    verify_binding(out/'program.nc',result['full_nc_sha256']);verify_binding(out/'simulated.stl',result['full_mesh_sha256'])
    if [r['id'] for r in result['checkpoints']]!=[r['id'] for r in cam['checkpoint_prefixes']]:raise ValueError('Checkpoint set differs from native job')
    for row,source in zip(result['checkpoints'],cam['checkpoint_prefixes']):
        case=out/'checkpoints'/row['id']
        if row['prefix_binding_sha256']!=source['prefix_binding_sha256']:raise ValueError('Native checkpoint binding differs')
        for name,field in [('prefix-binding.json','prefix_binding_sha256'),('simulation-result.json','simulation_result_sha256'),
            ('simulated.stl','mesh_sha256'),('native-geometry.json','native_geometry_sha256'),('metrics.json','metrics_sha256'),('observations.json','observations_sha256')]:
            verify_binding(case/name,row[field])
        proof=json.loads((case/'prefix-binding.json').read_text());verify_binding(case/'program.nc',proof['prefix_nc_sha256'])
        sim=json.loads((case/'simulation-result.json').read_text());verify_binding(case/'simulation.camotics',sim['project_sha256'])
    return result
