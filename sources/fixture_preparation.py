"""Require a paper-bound, complete fixture declaration before native CAM."""
import copy

from .preparation import InputError, load_json
from .fixture_setup import FILE_KEY, enabled
from .fixture_clearance import validate_model
from .tool_length_setup import actual_lengths


def attach(preparation, manifest, plan):
    if plan is None or any(i['severity'] == 'blocking' for i in preparation.issues):
        return plan
    if not enabled(plan) or plan['schema'] != 'genlaw.execution-plan.v12':
        raise InputError('unsupported_capability', FILE_KEY, '夹具检查目前仅接入显式静态刀补的单型腔 / 面铣 / 孔组合；附件不能被忽略。')
    if FILE_KEY not in preparation.files:
        raise InputError('missing_information', FILE_KEY, '新装夹配置要求提供完整夹具与装刀外形文件。')
    ref = plan['setup'][FILE_KEY]; binding = preparation.files[FILE_KEY]
    if ref['sha256'] != binding['sha256']:
        raise InputError('conflicting_information', FILE_KEY, '工艺卡 / 资源所绑定的夹具版本与实际附件不同。')
    preparation.source(ref['source'], FILE_KEY+'/source')
    document_id = ref['source']['document_id']
    source_key = preparation.documents.get(document_id)
    if source_key not in preparation.files:
        raise InputError('missing_information', FILE_KEY+'/source', '夹具几何须对应可下载审阅的来源文档。')
    model = load_json(preparation.root/binding['path'])
    try:
        tools = validate_model(model)
        actual = actual_lengths(plan['setup']['tool_length_compensation']['actual_lengths_mm'])
        if {n: t['gauge_to_tip_mm'] for n, t in tools.items()} != actual:
            raise ValueError('实际装刀长度和夹具检查的工具外形不一致。')
        for tool in plan['tools']:
            cutting = tools[tool['number']]['segments'][0]
            if cutting['radius_mm']*2 != tool['diameter_mm'] or cutting['z_max_mm'] != tool['cutting_length_mm']:
                raise ValueError('夹具检查不能改变实际 CAM / 去料刀具的切削半径或长度。')
        stock = plan['stock']['bounds_mm']
        for fixture in model['fixtures']:
            b = fixture['bounds_mm']
            if all(min(stock['max'][i], b['max'][i]) > max(stock['min'][i], b['min'][i]) for i in range(3)):
                raise ValueError('夹具实体与原始来料内部重叠；需修正装夹配置。')
    except (ValueError, KeyError, TypeError) as error:
        raise InputError('conflicting_information', FILE_KEY, str(error)) from error
    plan['fixture_inspection'] = {
        'schema': 'genlaw.fixture-inspection-plan.v1', 'model_ref': dict(binding),
        'source_document_ref': copy.deepcopy(preparation.files[source_key]), 'scope': model['scope'],
        'minimum_clearance_mm': model['minimum_clearance_mm'],
        'stage': 'after_native_nc_before_material_removal',
        'collision_or_indeterminate_blocks_material_simulation': True,
        'workholding_or_production_approved': False,
    }
    preparation.result['requirement_readiness'].append({
        'id': 'modeled-fixture-clearance', 'acceptance_defined': True, 'verification_status': 'not_verified',
        'inspection': 'native_tool_assembly_vs_declared_static_fixtures'})
    return plan
