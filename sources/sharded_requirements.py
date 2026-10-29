"""Apply source-bound manufacturing criteria to existing actual-mesh observations."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from genlaw_pipeline.cell_windows import file_binding,checked_file
from genlaw_pipeline.programming import checked_plan,check_nc,verify_binding
from genlaw_pipeline.sequence_verification import evaluate
from genlaw_pipeline.profile_sequence_geometry import feature_nominals
from genlaw_pipeline.sharded_verification import checked as checked_observation_plan
from genlaw_pipeline.run import write

LOAD=lambda p:json.loads(p.read_text())


def evaluate_requirements(requirements,observations):
    """Keep unknown criteria and unsupported measurements as explicit results."""
    rows=[];seen=set()
    for item in requirements['items']:
        fid,kind=item['feature_id'],item['kind']
        if fid not in observations or (fid,kind) in seen:
            raise ValueError('Missing or duplicated manufacturing feature observation')
        seen.add((fid,kind));rule=item.get('acceptance')
        if rule is not None and (not isinstance(rule,dict) or rule.get('scope')!='engineering_requirement'):
            result={'status':'unsupported_criterion','reason':'Software criteria cannot be used as manufacturing requirements.'}
        else:result=evaluate(kind,observations[fid],rule)
        rows.append({k:item[k] for k in ('id','feature_id','kind','nominal')}|result|
            {'source_review':item.get('source_review')})
    return rows


def verify(run,observation_path,output):
    from mesh_metrics import require_bounded_worker
    require_bounded_worker()
    run,observation_path,output=[p.resolve() for p in (run,observation_path,output)]
    for p in (run,observation_path,output):p.relative_to(ROOT)
    request=LOAD(run/'request.json');input_path=Path(request['input'])
    plan=checked_plan(input_path,run/'preparation-result.json')
    if plan['schema']!='genlaw.execution-plan.v11':raise ValueError('This final requirement adapter is qualified for v11 only')
    report=LOAD(observation_path)
    if (report.get('schema')!='genlaw.sharded-feature-verification.v1' or report.get('status')!='measured'
            or report.get('test_only_guard_bypass')
            or report.get('source_code')!=file_binding(ROOT/'genlaw_pipeline/sharded_verification.py')):
        raise ValueError('Require current normal actual-mesh observations')
    observed_plan,surface,ref=checked_observation_plan(checked_file(report['plan']))
    if ('checkpoint_context' in observed_plan or 'test_control' in observed_plan
            or observed_plan['prepared_source']!=file_binding(run/'preparation-result.json')
            or observed_plan['native_cam']!=file_binding(run/'cam-result.json')
            or observed_plan['surface']!=file_binding(run/'surface.json')
            or observed_plan['reference']!=file_binding(run/'native-geometry.json')):
        raise ValueError('Manufacturing requirement report must use the bound complete normal NC')
    for binding in report['ray_parts']+report['section_parts']:checked_file(binding)
    cam=LOAD(run/'cam-result.json');verify_binding(run/'program.nc',cam['nc_sha256'])
    check_nc((run/'program.nc').read_text(),plan)
    requirements_path=input_path.parent/plan['requirements_ref']['path']
    verify_binding(requirements_path,plan['requirements_ref']['sha256'])
    requirements=LOAD(requirements_path)
    if requirements['cad_sha256']!=ref['cad_sha256']:raise ValueError('Manufacturing requirements refer to another CAD')
    byid={op['feature_id']:op['geometry'] for op in plan['operations']}
    observations=report['observations']['features']
    if set(byid)!=set(observations):raise ValueError('Final observed feature set differs from original CAD')
    for item in requirements['items']:
        values=feature_nominals(byid[item['feature_id']])
        if item['kind'] in values and item['nominal']!=values[item['kind']]:
            raise ValueError('Requirement nominal differs from original CAD')
    rows=evaluate_requirements(requirements,observations)
    defined=[row for row,item in zip(rows,requirements['items']) if item.get('acceptance') is not None]
    source_review=None
    if 'source_review' in plan['source_bindings']:
        binding=plan['source_bindings']['source_review'];path=input_path.parent/binding['path']
        verify_binding(path,binding['sha256']);source_review=file_binding(path)
    counts={status:sum(r['status']==status for r in rows) for status in sorted({r['status'] for r in rows})}
    result={'schema':'genlaw.sharded-requirement-verification.v1','status':'evaluated',
        'requirements':rows,'status_counts':counts,'defined_numerical_requirements':len(defined),
        'all_defined_numerical_requirements_passed':bool(defined) and all(r['status']=='numeric_pass' for r in defined),
        'all_requirements_passed':bool(rows) and all(r['status']=='numeric_pass' for r in rows)
            and not requirements.get('unmapped_source_annotations'),
        'unmapped_source_annotations':requirements.get('unmapped_source_annotations',[]),
        'input':file_binding(input_path),'requirements_source':file_binding(requirements_path),
        'actual_observations':file_binding(observation_path),'surface':observed_plan['surface'],
        'source_review':source_review,'nc':file_binding(run/'program.nc'),
        'resolution_mm':observed_plan['resolution_mm'],'registration_applied':False,
        'numerical_guard_mm':2*observed_plan['resolution_mm'],'source_code':file_binding(Path(__file__)),
        'manufacturing_acceptance':False,'production_approved':False,'whole_process_card_complete':False,
        'limits':['Checks use existing finite samples of the actual NC result, with the unchanged two-cell guard.',
            'Missing, unsupported and borderline criteria are not counted as passes.',
            'Card identity, revision, resources and pre/post-treatment applicability require separate review.']}
    write(output,result)
    print(json.dumps({'status':result['status'],'defined':len(defined),'counts':counts}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('run','observations','output'):p.add_argument('--'+name,required=True,type=Path)
    a=p.parse_args();verify(a.run,a.observations,a.output)
