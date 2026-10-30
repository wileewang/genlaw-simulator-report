"""Bind a quantitative cumulative-prefix run to pre-NC CAD and unchanged rules."""
import copy
import json
from pathlib import Path
import re
from .cell_windows import file_binding,checked_file
from .programming import checked_plan,verify_binding,simulation_project
from .sequence_nc import check_prefix
from .sequence_geometry import REFERENCE
from .profile_sequence_geometry import REFERENCE as PROFILED
from .facing_geometry import FACING
from .preparation import digest

ROOT=Path(__file__).resolve().parents[1]
LOAD=lambda p:json.loads(p.read_text())


def native_checkpoint(parent,key,policy_path):
    parent=parent.resolve();policy_path=policy_path.resolve()
    parent.relative_to(ROOT);policy_path.relative_to(ROOT)
    if not re.fullmatch(r'after-[0-9]{2}',key):raise ValueError('Invalid checkpoint identifier')
    request=LOAD(parent/'request.json');input_path=Path(request['input']).resolve()
    plan=checked_plan(input_path,parent/'preparation-result.json');cam=LOAD(parent/'cam-result.json')
    count=int(key[-2:]);source=parent/'checkpoints'/key
    if (plan['schema'] not in ('genlaw.execution-plan.v9','genlaw.execution-plan.v11') or not 1<=count<len(plan['operations'])
            or cam['status']!='generated_and_checked' or not plan['expected_states_ref']):
        raise ValueError('Need a native ordered job with independent pre-NC intermediate CAD')
    expected_ids=[f'after-{i:02d}' for i in range(1,len(plan['operations']))]
    if [r['id'] for r in cam['checkpoint_prefixes']]!=expected_ids:
        raise ValueError('Native checkpoint list is missing or reordered')
    verify_binding(source/'prefix-binding.json',cam['checkpoint_prefixes'][count-1]['prefix_binding_sha256'])
    proof=LOAD(source/'prefix-binding.json');ref=LOAD(source/'native-geometry.json')
    verify_binding(parent/'program.nc',cam['nc_sha256'])
    verify_binding(parent/'program.nc',proof['full_nc_sha256'])
    verify_binding(source/'program.nc',proof['prefix_nc_sha256'])
    verify_binding(source/'native-geometry.json',proof['expected_native_geometry_sha256'])
    observed=check_prefix((source/'program.nc').read_text(),(parent/'program.nc').read_text(),plan,count)
    if any(proof.get(k)!=v for k,v in observed.items()):raise ValueError('Native prefix proof differs')
    final_ref=LOAD(parent/'native-geometry.json')
    if cam['native_cad_binding']!=final_ref or final_ref['cad_sha256']!=plan['cad_ref']['sha256']:
        raise ValueError('Original final CAD differs from native job')
    oracles_path=input_path.parent/plan['expected_states_ref']['path']
    verify_binding(oracles_path,plan['expected_states_ref']['sha256']);oracles=LOAD(oracles_path)
    oracle=oracles['states'][count-1];features=[op['geometry'] for op in plan['operations'][:count]]
    version=plan['schema'].rsplit('.',1)[1]
    outline=next((f for f in features if f['kind']=='capsule_outline'),None)
    holes=[f for f in features if f['kind']=='through_hole']
    expected_class=PROFILED if outline else REFERENCE if holes else FACING
    if (oracles.get('created_before_nc') is not True or oracles['target_cad_sha256']!=final_ref['cad_sha256']
            or oracle['id']!=key or oracle['completed_features']!=[f['id'] for f in features]
            or ref['completed_features']!=oracle['completed_features']
            or ref['original_final_cad_sha256']!=final_ref['cad_sha256']
            or ref['schema']!='genlaw.native-binding.'+version or ref['reference_class']!=expected_class
            or ref.get('profile_feature')!=outline
            or ref['feature_bindings']!=features or ref['facing_feature']!=features[0]
            or ref['features']!=(features if count==1 else holes)
            or ref['bounds']!=oracle['bounds_mm'] or ref['incoming_stock_bounds']!=plan['stock']['bounds_mm']
            or ref['oracle_ref']!=oracle['cad'] or ref['cad_sha256']!=oracle['cad']['sha256']
            or not ref['valid_single_solid'] or ref['status']!='verified'
            or not 0<=ref['symmetric_difference_mm3']<=1e-8
            or abs(ref['volume_mm3']-oracle['volume_mm3'])>1e-8):
        raise ValueError('Checkpoint reference differs from the independent pre-NC CAD')
    oracle_path=input_path.parent/oracle['cad']['path'];verify_binding(oracle_path,oracle['cad']['sha256'])
    if policy_path!=input_path.parent/'software-policy.json':raise ValueError('Use the frozen original policy')
    manifest=LOAD(input_path.parent/'manifest.json')
    verify_binding(policy_path,manifest['files']['software-policy.json']['sha256'])
    policy=LOAD(policy_path)
    if (policy['schema']!='genlaw.software-geometry-policy.'+version or policy['cad_sha256']!=final_ref['cad_sha256']
            or policy['production_approved'] is not False
            or set(policy['features'])!={op['feature_id'] for op in plan['operations']}):
        raise ValueError('Original software policy does not cover the original CAD')
    return {'parent':parent,'source':source,'key':key,'count':count,'plan':plan,'cam':cam,
        'reference':ref,'full_policy':policy,'policy_path':policy_path,'input':input_path,
        'oracle_path':oracle_path,'oracles_path':oracles_path,'proof':proof}


def subset_policy(context):
    return {'schema':context['full_policy']['schema'],
        'cad_sha256':context['reference']['cad_sha256'],'production_approved':False,
        'features':{fid:copy.deepcopy(context['full_policy']['features'][fid])
                    for fid in context['reference']['completed_features']}}


def make_context(parent,key,policy_path,case,output):
    c=native_checkpoint(parent,key,policy_path)
    if LOAD(case/'simulation.camotics')!=simulation_project(c['plan']):
        raise ValueError('Fine checkpoint must preserve original stock, tools and resolution')
    for name in ('program.nc','native-geometry.json','prefix-binding.json'):
        if digest(case/name)!=digest(c['source']/name):raise ValueError('Checkpoint source was altered')
    if LOAD(case/'software-policy.json')!=subset_policy(c):raise ValueError('Checkpoint rules changed')
    bindings={
        'parent_request':parent/'request.json','parent_preparation':parent/'preparation-result.json',
        'parent_cam':parent/'cam-result.json','parent_reference':parent/'native-geometry.json',
        'parent_nc':parent/'program.nc','parent_policy':policy_path,
        'source_prefix':c['source']/'program.nc','source_reference':c['source']/'native-geometry.json',
        'source_proof':c['source']/'prefix-binding.json','input':c['input'],
        'input_manifest':c['input'].parent/'manifest.json','oracles':c['oracles_path'],'oracle_cad':c['oracle_path'],
        'nc':case/'program.nc','reference':case/'native-geometry.json','project':case/'simulation.camotics',
        'policy':case/'software-policy.json','prefix_binding':case/'prefix-binding.json'}
    record={'schema':'genlaw.window-checkpoint-context.v1','id':key,'completed_operations':c['count'],
        'completed_features':c['reference']['completed_features'],'files':{k:file_binding(v) for k,v in bindings.items()},
        'resolution_mm':simulation_project(c['plan'])['resolution'],'original_stock_used':True,
        'independent_pre_nc_expected_cad':True,'expected_cad_used_as_stock':False,'criteria_modified':False,
        'source_code':file_binding(Path(__file__)),'production_approved':False}
    with output.open('x') as f:json.dump(record,f,indent=2);f.write('\n')
    return record


def checked_context(path):
    record=LOAD(path)
    if record.get('schema')!='genlaw.window-checkpoint-context.v1':raise ValueError('Invalid checkpoint context')
    checked_file(record['source_code'])
    paths={k:checked_file(v) for k,v in record['files'].items()}
    if (digest(paths['nc'])!=digest(paths['source_prefix']) or digest(paths['reference'])!=digest(paths['source_reference'])
            or digest(paths['prefix_binding'])!=digest(paths['source_proof'])):
        raise ValueError('Checkpoint context copies differ from native source')
    reference=LOAD(paths['reference']);full_policy=LOAD(paths['parent_policy'])
    version=reference['schema'].rsplit('.',1)[1]
    if version not in ('v9','v11') or full_policy['schema']!='genlaw.software-geometry-policy.'+version:
        raise ValueError('Checkpoint policy version differs from native reference')
    expected={'schema':full_policy['schema'],'cad_sha256':reference['cad_sha256'],
        'production_approved':False,'features':{fid:full_policy['features'][fid] for fid in reference['completed_features']}}
    if LOAD(paths['policy'])!=expected:raise ValueError('Checkpoint policy differs from original rules')
    return record
