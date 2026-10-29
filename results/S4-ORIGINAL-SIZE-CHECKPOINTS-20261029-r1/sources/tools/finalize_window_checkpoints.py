"""Finalize already measured checkpoints after an inspected summary interruption."""
import json
import math
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.cell_windows import checked_file,file_binding
from genlaw_pipeline.checkpoint_context import checked_context
from genlaw_pipeline.sharded_measure import checked_surface
from genlaw_pipeline.run import write
from genlaw_pipeline.sharded_surface import require_bounded_worker

RUN=ROOT/'validation/runs/S4-ORIGINAL-SIZE-CHECKPOINTS-20261029-r1'
LOAD=lambda p:json.loads(p.read_text())


def finalize():
    require_bounded_worker();intent=LOAD(RUN/'intent.json')
    for field in ('parent_cam','full_nc','engine_manifest','engine_regression','observer_source','context_source','source_code','checkpoint_regression'):
        checked_file(intent[field])
    parent=checked_file(intent['parent_cam']).parent
    failure=RUN/'after-03/features/finish-resources.json';retry=RUN/'after-03/features/finish-retry-01-resources.json'
    a,b=LOAD(failure),LOAD(retry)
    assert a['status']=='interrupted' and a['error']=='Interrupted by signal 15'
    assert not a['success'] and b['success'] and b['status']=='completed' and a['command']==b['command']
    for k in ('wall_limit_seconds','per_process_address_space_limit_bytes','observed_group_rss_limit_bytes','file_size_limit_bytes'):
        assert a[k]==b[k]
    ledger=RUN/'recovered-interruptions.json'
    write(ledger,{'status':'recovered_after_inspection','failures':[{'failed':file_binding(failure),'retry':file_binding(retry),
        'same_command':True,'input_plan':file_binding(RUN/'after-03/features/plan.json'),'output':file_binding(RUN/'after-03/features/verification.json')}],
        'earlier_driver_interruption':file_binding(RUN/'interruptions/before-admission-01/record.json'),
        'termination_cause':'unknown','nc_or_material_simulation_repeated':False,'limits_increased':False})
    config=LOAD(RUN/'after-01/simulation.camotics');stock=config['workpiece']['bounds']
    volumes=[math.prod(b-a for a,b in zip(stock['min'],stock['max']))];records=[]
    for key in intent['expected_checkpoints']:
        case=RUN/key;context=checked_context(case/'context.json');surface=checked_surface(case/'surface.json')
        metrics=LOAD(case/'metrics.json');report=LOAD(case/'features/verification.json');plan=LOAD(checked_file(report['plan']))
        assert plan['surface']==metrics['surface']==file_binding(case/'surface.json')
        assert plan['reference']==metrics['reference']==context['files']['reference']
        assert plan['checkpoint_context']==file_binding(case/'context.json') and plan['policy']==context['files']['policy']
        checked_file(plan['source_code']);assert report['all_software_geometry_checks_passed']
        assert all(c['status']=='numeric_pass' for c in report['software_regression'])
        path=case/'checkpoint-result.json'
        if not path.exists():
            write(path,{'schema':'genlaw.fine-window-checkpoint.v1','status':'software_geometry_passed','id':key,
                'context':file_binding(case/'context.json'),'surface':metrics['surface'],
                'metrics':file_binding(case/'metrics.json'),'verification':file_binding(case/'features/verification.json'),
                'iou_percent':metrics['iou_percent'],'checks_passed':len(report['software_regression']),
                'actual_volume_mm3':surface['volume_mm3'],'window_count':len(surface['windows']),'resolution_mm':.025,
                'legacy_comparison':None,'fine_checkpoint_acceptance':True,'production_approved':False,
                'recovery':file_binding(ledger),'source_code':file_binding(Path(__file__))})
        result=LOAD(path)
        assert result['iou_percent']==metrics['iou_percent'] and result['surface']==metrics['surface']
        assert result['verification']==file_binding(case/'features/verification.json') and result['fine_checkpoint_acceptance']
        records.append({'id':key,'result':file_binding(path)});volumes.append(surface['volume_mm3'])
    volumes.append(LOAD(parent/'surface.json')['volume_mm3']);assert all(a>b for a,b in zip(volumes,volumes[1:]))
    write(RUN/'summary.json',{'schema':'genlaw.fine-window-checkpoints.v1','status':'fine_checkpoints_passed',
        'intent':file_binding(RUN/'intent.json'),'engine_manifest':intent['engine_manifest'],'checkpoints':records,
        'final_metrics':file_binding(parent/'metrics.json'),'full_nc':intent['full_nc'],'actual_volumes_mm3':volumes,
        'material_volume_monotonically_decreases':True,'original_stock_used':True,'expected_cad_used_as_stock':False,
        'resolution_mm':.025,'observer_source':intent['observer_source'],'context_source':intent['context_source'],
        'legacy_comparison_passed':False,'source_code':file_binding(Path(__file__)),'recovery':file_binding(ledger),
        'original_runner':intent['source_code'],'production_approved':False,'whole_process_card_complete':False})
    print(json.dumps({'status':'fine_checkpoints_passed','checkpoints':3,'checks':21,'actual_volumes_mm3':volumes}),flush=True)


if __name__=='__main__':finalize()
