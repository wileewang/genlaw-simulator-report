"""Single-job native multi-file chamfer measurement, independent of regression fixtures.

The public measure() entry accepts only ordinary checked NC. Frozen rejected
NC has a separate measure_control() entry and an explicitly test-only result.
Neither entry starts CAM/native simulation or approves a complete process card.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.cell_windows import checked_file,file_binding
from genlaw_pipeline.run import write

WORKER=ROOT/'genlaw_pipeline/planar_chamfer_measurement.py'
LOAD=lambda p:json.loads(p.read_text())


def initial_command(surface,reference,policy,output,control=None):
    command=[sys.executable,WORKER,'plan-control' if control is not None else 'plan',
        '--surface',surface,'--reference',reference,'--policy',policy,'--output',output]
    return command+['--control',control] if control is not None else command


def jobs(plan_path):
    """Derive every worker and its expected artifact from the current plan."""
    plan=LOAD(plan_path); out=plan_path.parent
    groups={'part':plan['schedule']['groups'],'section':plan['sampling']['heights'],
            'rays':plan['sampling']['ray_groups']}
    paths={mode:[out/f'{mode}-{i:03d}.json' for i in range(len(rows))] for mode,rows in groups.items()}
    for mode in groups:
        if mode=='section':
            if len(paths[mode])>16:raise ValueError('Feature batch exceeds original interval count bound')
            yield 'sections',[sys.executable,WORKER,'sections','--plan',plan_path,'--indices',*map(str,range(len(paths[mode]))),'--output',out],out/'sections-batch.json'
            continue
        for index,path in enumerate(paths[mode]):
            yield path.stem,[sys.executable,WORKER,mode,'--plan',plan_path,'--index',str(index),'--output',path],path
    yield 'finish',[sys.executable,WORKER,'finish','--plan',plan_path,'--parts',*paths['part'],
        '--output',out/'metrics.json'],out/'metrics.json'
    yield 'verify',[sys.executable,WORKER,'verify','--plan',plan_path,'--sections',*paths['section'],
        '--rays',*paths['rays'],'--output',out/'verification.json'],out/'verification.json'


def complete(plan_path,output):
    from genlaw_pipeline.planar_chamfer_measurement import require_bounded_worker,checked,source_bindings,result_status
    from genlaw_pipeline.sparse_material import checked_resources
    require_bounded_worker(); plan,*_=checked(plan_path); out=plan_path.parent; inputs=plan['inputs']
    control=checked_file(inputs['test_control']) if inputs['test_only_guard_bypass'] else None
    first=initial_command(checked_file(plan['surface']),checked_file(plan['reference']),
        checked_file(plan['policy']),plan_path,control)
    resources=[file_binding(out/'plan-resources.json')]
    checked_resources(out/'plan-resources.json',first)
    for label,command,path in jobs(plan_path):
        checked_resources(out/(label+'-resources.json'),command)
        resources.append(file_binding(out/(label+'-resources.json')))
        if not path.is_file(): raise ValueError('A planned measurement artifact is missing')
    if control is None:
        status=result_status(plan_path,out/'metrics.json',out/'verification.json')
    else:
        report=LOAD(out/'verification.json')
        if (report['plan']!=file_binding(plan_path) or report['test_only_guard_bypass'] is not True
                or report['test_control']!=inputs['test_control']
                or report['normal_guard_rejection']!=inputs['normal_guard_rejection']):
            raise ValueError('Missing explicit test-control evidence')
        status={'status':'test_control_measured','normal_acceptance':False,
            'all_software_geometry_checks_passed':report['all_software_geometry_checks_passed'],
            'test_control':inputs['test_control'],'normal_guard_rejection':inputs['normal_guard_rejection']}
    write(output,{'schema':'genlaw.planar-chamfer-measurement.v1',**status,
        'plan':file_binding(plan_path),'metrics':file_binding(out/'metrics.json'),
        'verification':file_binding(out/'verification.json'),'resources':resources,'sources':source_bindings(),
        'test_only_guard_bypass':control is not None,'historical_measurement_required':False,
        'new_native_jobs':0,'full_tolerance_acceptance':False,'production_approved':False,
        'whole_process_card_complete':False})
    print(json.dumps({'native_measurement':status['status'],
        'iou_percent':LOAD(out/'metrics.json')['iou_percent'],'output':str(output)}),flush=True)


def _run(out,surface,reference,policy,control):
    out=out.resolve(); out.mkdir(parents=True,exist_ok=False)
    surface,reference,policy=(p.resolve() for p in [surface,reference,policy])
    if control is not None: control=control.resolve()
    sources=[file_binding(Path(__file__)),file_binding(WORKER)]
    plan=out/'plan.json'
    admitted_bounded(out,'plan',initial_command(surface,reference,policy,plan,control))
    for label,command,_ in jobs(plan):
        admitted_bounded(out,label,command)
        print(json.dumps({'measurement_worker_completed':label,'directory':str(out)}),flush=True)
    admitted_bounded(out,'complete',[sys.executable,Path(__file__).resolve(),'complete',
        '--plan',plan,'--output',out/'result.json'])
    for binding in sources: checked_file(binding)
    return LOAD(out/'result.json')


def measure(out,surface,reference,policy):
    return _run(out,surface,reference,policy,None)


def measure_control(out,surface,reference,policy,control):
    return _run(out,surface,reference,policy,control)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); sub=parser.add_subparsers(dest='action',required=True)
    for mode in ['measure','measure-control']:
        p=sub.add_parser(mode)
        for name in ['surface','reference','policy','output']: p.add_argument('--'+name,type=Path,required=True)
        if mode=='measure-control': p.add_argument('--control',type=Path,required=True)
    p=sub.add_parser('complete'); p.add_argument('--plan',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.action=='complete': complete(args.plan,args.output)
    elif args.action=='measure': measure(args.output,args.surface,args.reference,args.policy)
    else: measure_control(args.output,args.surface,args.reference,args.policy,args.control)
