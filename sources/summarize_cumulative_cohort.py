"""Assemble qualified cumulative results without extending their tested scope."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.cell_windows import checked_file,file_binding
from genlaw_pipeline.cumulative_execution import ACCEPTANCE
from genlaw_pipeline.planar_resume import process_identity
from genlaw_pipeline.sparse_material import checked_resources
from genlaw_pipeline.run import write
from step2gcode_demo.validation.local_resource_limits import require_bounded_worker

READ=lambda p:json.loads(Path(p).read_text())
NORMALS=['baseline','larger-round','wider-bottom','shifted-datum']
CONTROLS=['omitted-round','wrong-second-setup','omitted-chamfer']


def summarize(checkpoint,out):
    require_bounded_worker();cohort=READ(checkpoint/'result.json');owner=READ(checkpoint/'owner.json')['process']
    if (process_identity(owner['pid'])==owner or cohort['status']!='four_normal_and_three_controls_verified'):
        raise ValueError('Cohort is still live or incomplete')
    request=READ(checked_file(cohort['request']))
    for b in request['sources']:checked_file(b)
    normal=[];controls=[];archives=[];resources=[]
    for key in NORMALS+CONTROLS:
        group='normal_qualifications' if key in NORMALS else 'control_qualifications'
        qualified_path=checked_file(cohort[group][key]);qualified=READ(qualified_path);receipts=qualified_path.parent
        if qualified['status']!='qualified' or qualified['kind']!=('normal' if key in NORMALS else key):
            raise ValueError('Missing case qualification')
        qualifier=checked_file(qualified['qualifier'])
        if key=='omitted-chamfer':
            source=checked_file(qualified['stages'][0]['metrics'])
            command=[sys.executable,qualifier,'--reused-control',source,'--output',qualified_path]
        else:
            case_result_path=checked_file(qualified['result']);run=case_result_path.parent
            command=[sys.executable,qualifier,'--run',run,'--output',qualified_path]
            archive=run/'geometry-archive/manifest.json';a=READ(archive)
            if a['qualification']!=file_binding(qualified_path):raise ValueError('Geometry archive belongs to another result')
            for row in a['stages']:
                checked_file(row['payload'])
                if not row['every_original_shard_reconstruction_sha256_checked']:
                    raise ValueError('Missing lossless native shard preservation')
            archives.append(file_binding(archive))
            resources.append(checked_resources(receipts/('archive-'+key+'-resources.json'),
                [sys.executable,ROOT/'tools/archive_cumulative_material.py','--qualification',qualified_path,'--output',run/'geometry-archive']))
        resources.append(checked_resources(receipts/('qualify-'+key+'-resources.json'),command))
        stages=qualified['stages']
        for row in stages:
            checked_file(row['metrics']);checked_file(row['material']);checked_file(row['reference_CAD']);checked_file(row['surface'])
        if key in NORMALS:
            if [row['stage_id'] for row in stages]!=list('ABCD') or not all(row['geometry_pass'] for row in stages):
                raise ValueError('Normal case lacks four accepted cumulative materials')
            spec=next(r for r in READ(ROOT/'validation/feature-inputs/S4-CUMULATIVE-TARGET-20261031-r1/specifications.json')['cases'] if r['id']==key)
            case=Path(qualified['case']);cam=READ(case/'result.json')
            if cam['status']!='four_programs_generated_and_checked':raise ValueError('Missing original native CAM')
            normal.append({'id':key,'name':spec['name'],'specification':spec,'qualification':file_binding(qualified_path),
                'CAM':file_binding(case/'result.json'),'input':file_binding(ROOT/'validation/feature-inputs/S4-CUMULATIVE-CAM-20261031-r1'/key/'input.json'),
                'states':stages,'final_iou_percent':stages[-1]['iou_percent']})
        else:
            if len(stages)!=1 or stages[0]['stage_id']!='D' or stages[0]['geometry_pass']:
                raise ValueError('Negative control was not detected against final CAD')
            controls.append({'id':key,'qualification':file_binding(qualified_path),**stages[0],
                             'new_native_material':key!='omitted-chamfer'})
    guard_path=checked_file(cohort['input_and_NC_guards']);guards=READ(guard_path)
    if guards['status']!='passed' or len(guards['rejections'])!=20 or not all(r['rejected'] for r in guards['rejections']):
        raise ValueError('Missing input or executable-NC rejection evidence')
    review=checkpoint.parent/'execution-paper-review.json';paper=READ(review)
    if paper['status']!='reviewed' or len(paper['inspected_images'])!=12 or paper['blocking_issues']:
        raise ValueError('Execution cards still need visual review')
    for b in paper['inspected_images']:checked_file(b)
    result={'schema':'genlaw.cumulative-CAM-suite.v1','status':'four_normal_three_controls_qualified',
        'cohort':file_binding(checkpoint/'result.json'),'normal_cases':normal,'controls':controls,
        'programs_per_case':4,'static_setups_per_case':2,'software_acceptance':ACCEPTANCE,
        'input_and_NC_rejections':20,'input_checks':file_binding(guard_path),'paper_review':file_binding(review),
        'geometry_archives':archives,'native_qualification':request['native_qualification'],
        'empty_group_correction_normal_regression':cohort['v2_normal_regression'],
        'empty_group_correction_positive_comparison':cohort['v2_positive_comparison'],
        'ball_qualification':request['ball_qualification'],'NC_generated_by_FreeCAD':True,'NC_text_modified':False,
        'CAM_uses_complete_predecessor_CAD':True,'CAM_uses_complete_cumulative_target_CAD':True,
        'same_original_material_for_all_programs':True,'every_prefix_geometry_gates_its_successor':True,
        'target_CAD_supplied_to_material_engine':False,'geometry_registered_repaired_or_simplified':False,
        'complete_normal_material_states':16,'complete_control_material_measurements':3,
        'new_native_material_collections':18,'native_windows_per_collection':8,
        'continuous_five_axis_supported':False,'physical_fixture_or_holder_collision_verified':False,
        'material_physics_verified':False,'whole_real_card_complete':False,'production_approved':False,
        'maximum_observed_worker_seconds':max([r['seconds'] for r in resources]+[s['max_worker_seconds'] for q in normal for s in q['states']]+[s['max_worker_seconds'] for s in controls]),
        'source':file_binding(Path(__file__))}
    write(out,result);print(json.dumps({'normal_cases':4,'complete_material_states':16,'detected_controls':3,
        'final_iou_percent':{r['id']:r['final_iou_percent'] for r in normal}}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();summarize(a.checkpoint.resolve(),a.output.resolve())
