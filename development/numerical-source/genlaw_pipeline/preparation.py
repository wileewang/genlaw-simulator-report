"""Source-bound preparation of axial through-holes, without running CAD/CAM.

The first adapter consumes a manually checked transcription and a prior native
CAD observation. It does not OCR PDFs or independently re-read CAD geometry.
Ready means ready for the declared software-reference scope, never production.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
from .drilling_geometry import SINGLE, MULTI, DRILLED, MAX_HOLES, blind_levels

VERSION = 'genlaw.preparation.single-hole.v1'
JSON_LIMIT = 2 * 1024 * 1024
ARTIFACT_LIMIT = 64 * 1024 * 1024


class InputError(ValueError):
    def __init__(self, code, field, message):
        super().__init__(message)
        self.code, self.field = code, field


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(path):
    if path.stat().st_size > ARTIFACT_LIMIT:
        raise InputError('unsupported_capability', str(path.name), '文件超过首版读取预算。')
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def load_json(path):
    if path.stat().st_size > JSON_LIMIT:
        raise InputError('invalid_input', path.name, '结构化输入超过 2 MiB。')

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise InputError('invalid_input', key, 'JSON 存在重复字段。')
            result[key] = value
        return result

    def constant(value):
        raise InputError('invalid_input', value, '数值必须有限。')
    def floating(value):
        result = float(value)
        return result if math.isfinite(result) else constant(value)
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs, parse_constant=constant, parse_float=floating)


def object_at(value, field):
    if not isinstance(value, dict):
        raise InputError('invalid_input', field, '应为对象。')
    return value


def list_at(value, field):
    if not isinstance(value, list):
        raise InputError('invalid_input', field, '应为列表。')
    return value


def number(value, field, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InputError('invalid_input', field, '需要明确的有限数值。')
    if positive and value <= 0:
        raise InputError('invalid_input', field, '数值必须大于零。')
    return float(value)


def vector(value, field, length=3):
    value = list_at(value, field)
    if len(value) != length:
        raise InputError('invalid_input', field, f'需要 {length} 个坐标分量。')
    return [number(x, f'{field}/{i}') for i, x in enumerate(value)]


class Preparation:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.root = self.path.parent
        self.issues = []
        self.files = {}
        self.documents = {}
        self.coverage = []
        self.result = {
            'schema': 'genlaw.preparation-result.v1', 'adapter': VERSION,
            'status': 'invalid_input', 'execution_plan': None,
            'issues': self.issues, 'coverage': self.coverage,
            'requirement_readiness': [],
            'input_bindings': self.files, 'source_pdf_automatically_recognized': False,
            'cad_native_geometry_reparsed_this_run': False,
            'production_approved': False, 'whole_process_card_complete': False,
        }

    def issue(self, code, field, message, *, severity='blocking', owner='input_maintenance'):
        self.issues.append({
            'code': code, 'field': field, 'message': message, 'severity': severity,
            'blocked_stage': 'requirement_acceptance' if severity=='acceptance' else 'nc_generation' if severity=='blocking' else None,
            'owner': owner,
        })

    def require(self, obj, key, field):
        value = obj.get(key)
        if value is None or value == '':
            self.issue('missing_information', field, '需要补齐该字段，未使用默认值。')
            return None
        return value

    def bind(self, key, ref):
        ref = object_at(ref, f'files/{key}')
        name, expected = ref.get('path'), ref.get('sha256')
        if not isinstance(name, str) or not isinstance(expected, str) or len(expected) != 64:
            raise InputError('invalid_input', f'files/{key}', '文件引用须包含相对路径及 SHA-256。')
        path = (self.root / name).resolve()
        if Path(name).is_absolute() or not path.is_relative_to(self.root):
            raise InputError('invalid_input', f'files/{key}/path', '引用须位于当前输入包内。')
        if not path.is_file():
            raise InputError('missing_information', f'files/{key}', '引用文件不存在。')
        actual = digest(path)
        if actual != expected:
            raise InputError('conflicting_information', f'files/{key}/sha256', '文件内容与声明版本不一致。')
        self.files[key] = {'path': name, 'sha256': actual}
        return path

    def source(self, source, field):
        source = object_at(source, field)
        if source.get('document_id') not in self.documents:
            self.issue('missing_information', field, '来源文档未提供，无法追溯。')
        page = source.get('page')
        if isinstance(page, bool) or not isinstance(page, int) or page < 1 or not source.get('location'):
            self.issue('invalid_input', field, '需要来源页码和字段/工步位置。')

    def parameter(self, obj, key, field):
        item = self.require(obj, key, field+'/'+key)
        if item is None:
            return None
        item = object_at(item, field+'/'+key)
        if not isinstance(item.get('basis'), str) or not item['basis'].strip():
            self.issue('missing_information', field+'/'+key+'/basis', '执行参数必须记录依据。')
        return number(item.get('value'), field+'/'+key+'/value')

    def run(self):
        try:
            plan = self._prepare()
            manifest = load_json(self.path)
            if 'inspection' in manifest or 'dimension_requirements' in manifest.get('files', {}):
                from .declared_dimensions import attach
                plan = attach(self, manifest, plan)
            from .fixture_setup import enabled as fixtures
            if 'fixture_geometry' in manifest.get('files', {}) or (plan is not None and fixtures(plan)):
                from .fixture_preparation import attach as attach_fixtures
                plan = attach_fixtures(self, manifest, plan)
            blockers = [i for i in self.issues if i['severity']=='blocking']
            if blockers:
                priority = ['invalid_input', 'conflicting_information', 'missing_information', 'unsupported_capability']
                self.result['status'] = next((x for x in priority if any(i['code']==x for i in blockers)), 'invalid_input')
            else:
                self.result['status'] = 'ready_for_declared_scope'
                self.result['execution_plan'] = plan
                self.result['execution_plan_sha256'] = hashlib.sha256(canonical(plan).encode()).hexdigest()
        except InputError as error:
            self.issue(error.code, error.field, str(error))
            self.result['status'] = error.code
        except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
            self.issue('invalid_input', 'input', f'输入结构无法处理：{type(error).__name__}。')
            self.result['status'] = 'invalid_input'
        return self.result

    def _prepare(self):
        manifest = object_at(load_json(self.path), 'input')
        if manifest.get('schema') in ('genlaw.preparation-input.v9','genlaw.preparation-input.v11','genlaw.preparation-input.v12','genlaw.preparation-input.v13'):
            from .sequence_preparation import prepare_sequence
            return prepare_sequence(self,manifest)
        if manifest.get('schema')=='genlaw.preparation-input.v4':
            from .pocket_preparation import prepare_pocket
            return prepare_pocket(self,manifest)
        if manifest.get('schema') in ('genlaw.preparation-input.v5','genlaw.preparation-input.v6','genlaw.preparation-input.v7','genlaw.preparation-input.v8','genlaw.preparation-input.v10'):
            from .milling_preparation import prepare_milling
            return prepare_milling(self,manifest)
        if manifest.get('schema') not in ('genlaw.preparation-input.v1', 'genlaw.preparation-input.v2', 'genlaw.preparation-input.v3'):
            raise InputError('unsupported_capability', 'schema', '此入口接受 preparation-input.v1/v2/v3；旧固定参数卡须显式迁移。')
        drilled = manifest['schema'] == 'genlaw.preparation-input.v3'
        multiple = manifest['schema'] != 'genlaw.preparation-input.v1'
        if multiple:
            self.result['adapter'] = 'genlaw.preparation.drilled-holes.v3' if drilled else 'genlaw.preparation.hole-pattern.v2'
        self.result['input_id'] = manifest.get('id')
        if not isinstance(manifest.get('id'), str) or not manifest['id'].strip():
            raise InputError('invalid_input', 'id', '输入包需要非空 ID。')
        self.result['input_manifest_sha256'] = digest(self.path)
        if manifest.get('mode') != 'software_geometry_reference':
            self.issue('unsupported_capability', 'mode', '首版仅用于声明范围内的软件几何参考，不能用于生产放行。')
        refs = object_at(manifest.get('files'), 'files')
        needed = ['process_plan', 'cad', 'geometry_observation', 'requirements', 'resources']
        for key in needed:
            if key not in refs:
                self.issue('missing_information', 'files/'+key, '缺少必要的输入资料。')
        if self.issues:
            return None
        paths = {k: self.bind(k, v) for k,v in refs.items()}
        self.documents = object_at(manifest.get('source_documents'), 'source_documents')
        for doc, file_key in self.documents.items():
            if file_key not in paths:
                self.issue('missing_information', 'source_documents/'+doc, '原始工艺资料未包含在输入包内。')
        if not self.documents:
            self.issue('missing_information', 'source_documents', '必须保留原工艺卡资料。')
        values = {k: object_at(load_json(paths[k]), k) for k in needed if k!='cad'}
        card, geo, requirements, resources = (values[k] for k in ['process_plan','geometry_observation','requirements','resources'])
        transcription = object_at(card.get('transcription'), 'process_plan/transcription')
        if transcription.get('status') != 'checked_against_source':
            self.issue('missing_information', 'process_plan/transcription', '结构化工艺须先与原卡核对；模型候选内容不能自动视作已确认。')
        schemas = {'process_plan':'genlaw.process-plan.v1', 'geometry_observation':'genlaw.cad-observation.v1',
                   'requirements':'genlaw.requirements.v1', 'resources':'genlaw.preparation-resources.v1'}
        if multiple:
            schemas['geometry_observation'] = 'genlaw.cad-observation.v3' if drilled else 'genlaw.cad-observation.v2'
        for key, schema in schemas.items():
            if values[key].get('schema') != schema:
                self.issue('unsupported_capability', key+'/schema', '尚不支持该结构化资料版本。')
        for key in ['process_plan','geometry_observation','requirements']:
            if values[key].get('cad_sha256') != self.files['cad']['sha256']:
                self.issue('conflicting_information', key+'/cad_sha256', '资料未绑定同一版本的 CAD。')
        for key in ['geometry_observation','requirements','resources']:
            if values[key].get('units') != 'mm':
                self.issue('unsupported_capability', key+'/units', '首版只接受明确的毫米单位。')
        if geo.get('reference_class') != (DRILLED if drilled else MULTI if multiple else SINGLE):
            self.issue('unsupported_capability', 'geometry_observation/reference_class', '当前仅支持该版本声明的矩形体及不相交 Z 向钻孔。')
        if not geo.get('provenance') or geo.get('observation_method') != 'prior_native_cad_observation':
            self.issue('missing_information', 'geometry_observation/provenance', '需要可追溯的原生 CAD 观察记录，不能仅提供自由填写的孔位。')
        features = list_at(geo.get('features'), 'geometry_observation/features')
        if not 1 <= len(features) <= (MAX_HOLES if multiple else 1):
            self.issue('unsupported_capability', 'geometry_observation/features', '孔数量超过当前版本的计算范围。')
            return None
        feature_map = {}
        for index, feature in enumerate(features):
            field = f'geometry_observation/features/{index}'
            feature = object_at(feature, field)
            if not isinstance(feature.get('id'),str) or not feature['id'].strip() or feature['id'] in feature_map:
                raise InputError('invalid_input',field+'/id','需要唯一、稳定的目标特征 ID。')
            vector(feature.get('center_xy_mm'), field+'/center_xy_mm', 2)
            number(feature.get('diameter_mm'), field+'/diameter_mm', True)
            kinds=('through_hole','blind_hole') if drilled else ('through_hole',)
            if feature.get('kind') not in kinds or vector(feature.get('axis'),field+'/axis') != [0,0,1]:
                self.issue('unsupported_capability', field, '该输入版本不支持此孔型或轴向。')
            if drilled:
                allowed={'id','kind','center_xy_mm','diameter_mm','axis'}
                if feature.get('kind')=='blind_hole':
                    allowed|={'cylindrical_depth_mm','bottom_angle_degrees'}
                    number(feature.get('cylindrical_depth_mm'),field+'/cylindrical_depth_mm',True)
                    angle=number(feature.get('bottom_angle_degrees'),field+'/bottom_angle_degrees',True)
                    if not 30<=angle<180:
                        self.issue('unsupported_capability',field+'/bottom_angle_degrees','盲孔须声明 30–180 度以内的锥底夹角；平底需其他工艺。')
                if set(feature)-allowed:
                    self.issue('unsupported_capability',field,'未知几何字段或深度语义不能被忽略。')
            feature_map[feature['id']] = feature
        feature = features[0]
        center, diameter = feature['center_xy_mm'], feature['diameter_mm']
        if any(f['diameter_mm'] != diameter for f in features):
            self.issue('unsupported_capability','geometry_observation/features','当前孔系要求同径、同一钻头；换刀按后续路线开发。')
        bounds = object_at(geo.get('bounds_mm'), 'geometry_observation/bounds_mm')
        low, high = vector(bounds.get('min'), 'bounds/min'), vector(bounds.get('max'), 'bounds/max')
        if drilled:
            for f in features:
                if f['kind']=='blind_hole' and 30<=f['bottom_angle_degrees']<180:
                    if blind_levels(f,high[2])[1]<=low[2]:
                        self.issue('conflicting_information','geometry_observation/'+f['id'],'盲孔的钻尖已穿过毛坯底面；圆柱孔深与钻尖总深度必须区分。')
        provenance = object_at(geo.get('provenance'), 'geometry_observation/provenance')
        reference_key = provenance.get('file_key')
        if reference_key not in paths or provenance.get('sha256') != self.files[reference_key]['sha256']:
            self.issue('missing_information','geometry_observation/provenance','缺少绑定的原生观察记录。')
        else:
            original = object_at(load_json(paths[reference_key]), 'native_reference')
            if (original.get('sha256') != self.files['cad']['sha256'] or original.get('valid_single_solid') is not True
                or original.get('reference_class') != geo['reference_class'] or original.get('bounds') != bounds
                or (multiple and original.get('features') != features)
                or (not multiple and (original.get('hole_center_xy') != center
                    or number(original.get('hole_radius_mm'),'native_reference/hole_radius_mm',True)*2 != diameter))):
                self.issue('conflicting_information','geometry_observation/provenance','特征包与已保存的原生 CAD 观察不一致。')
        if any(a>=b for a,b in zip(low,high)) or not all(low[i]+f['diameter_mm']/2 < f['center_xy_mm'][i] < high[i]-f['diameter_mm']/2 for f in features for i in range(2)):
            self.issue('conflicting_information', 'geometry_observation', '孔与外形边界不满足完整内孔约束。')
        for i, f in enumerate(features):
            for other in features[i+1:]:
                if math.dist(f['center_xy_mm'],other['center_xy_mm']) <= (f['diameter_mm']+other['diameter_mm'])/2:
                    self.issue('unsupported_capability','geometry_observation/features','相交或相切孔需要其他特征及测量模型。')
        stock = object_at(resources.get('stock'), 'resources/stock')
        stock_bounds = object_at(stock.get('bounds_mm'),'resources/stock/bounds_mm')
        vector(stock_bounds.get('min'),'resources/stock/bounds_mm/min')
        vector(stock_bounds.get('max'),'resources/stock/bounds_mm/max')
        if stock.get('shape')!='box' or stock.get('bounds_mm') != bounds:
            self.issue('unsupported_capability', 'resources/stock', '当前单钻孔方案要求已具备目标外形的等大矩形来料；额外余量需要新增工序。')
        material = self.require(stock, 'material_label', 'resources/stock/material_label')
        scope = object_at(manifest.get('scope'), 'scope')
        selected = list_at(scope.get('selected_steps'), 'scope/selected_steps')
        if len(selected) != len(features) or not all(isinstance(s,str) for s in selected) or len(set(selected)) != len(selected):
            self.issue('unsupported_capability', 'scope/selected_steps', '每个目标孔须对应一个唯一的选定钻孔工步。')
            return None
        if scope.get('whole_process_card') is not False:
            self.issue('unsupported_capability', 'scope/whole_process_card', '首版必须声明局部工步范围。')
        exclusions = list_at(scope.get('excluded_checks'), 'scope/excluded_checks')
        for item in ['fixture_collision','material_physics','surface_roughness']:
            if item not in exclusions:
                self.issue('unsupported_capability', 'scope/excluded_checks', f'当前未实现 {item}，需明确未验证范围。')
        steps = {}
        operation_ids = set()
        for operation in list_at(card.get('operations'), 'process_plan/operations'):
            operation = object_at(operation, 'process_plan/operation')
            oid = operation.get('id')
            if not isinstance(oid,str) or oid in operation_ids:
                raise InputError('invalid_input', 'process_plan/operations/id', '工序 ID 缺失或重复。')
            operation_ids.add(oid)
            for step in list_at(operation.get('steps'), 'process_plan/'+oid+'/steps'):
                step = object_at(step, 'process_plan/'+oid+'/step')
                sid = step.get('id')
                if not isinstance(sid,str) or sid in steps:
                    raise InputError('invalid_input', 'process_plan/steps/id', '工步 ID 缺失或重复。')
                self.source(step.get('source'), 'process_plan/'+sid+'/source')
                if not step.get('text'):
                    self.issue('missing_information', 'process_plan/'+sid+'/text', '须保留工艺卡原意。')
                steps[sid] = (operation,step)
                self.coverage.append({'operation_id':oid,'step_id':sid,'type':step.get('type'),
                                      'status':'selected_for_preparation' if sid in selected else 'not_verified'})
        if any(s not in steps for s in selected):
            raise InputError('missing_information', 'scope/selected_steps', '选定工步不存在。')
        if selected != [s for s in steps if s in selected]:
            self.issue('conflicting_information','scope/selected_steps','执行顺序与工艺卡的工步顺序不一致。')
        declarations = object_at(scope.get('declared_preconditions'), 'scope/declared_preconditions')
        allowed_constraints={'through','diameter_mm','tool_id','spindle_rpm','feed_mm_min','breakthrough_mm','retract_plane_mm','safe_height_mm','clearance_height_mm'}
        if drilled:allowed_constraints|={'cylindrical_depth_mm','bottom_angle_degrees'}
        resolved_operations = []; selected_features = []
        for order, sid in enumerate(selected):
            operation, step = steps[sid]
            if step.get('type')!='drilling':
                self.issue('unsupported_capability', 'process_plan/'+sid+'/type', '当前尚未实现该加工类型。')
            feature = feature_map.get(step.get('feature_id'))
            if feature is None:
                self.issue('conflicting_information', 'process_plan/'+sid+'/feature_id', '目标特征与 CAD 观察不一致。')
                continue
            selected_features.append(feature['id'])
            if any(x.get('conditions') not in (None,[]) for x in [step,operation,card]):
                self.issue('unsupported_capability', 'process_plan/'+sid+'/conditions', '条件路线尚未实现，不能静默选择分支。')
            for dep in list_at(step.get('depends_on'), 'process_plan/'+sid+'/depends_on'):
                if dep == sid:
                    self.issue('invalid_input','process_plan/'+sid+'/depends_on','工步不能依赖自身。')
                if dep in selected:
                    if selected.index(dep) >= order:
                        self.issue('conflicting_information','process_plan/'+sid+'/depends_on','前置加工工步不能位于当前工步之后或形成依赖环。')
                    continue
                if dep in steps and any(x.get('conditions') not in (None,[]) for x in steps[dep]):
                    self.issue('unsupported_capability','process_plan/'+str(dep)+'/conditions','前置工步含未实现的条件分支。')
                if dep not in steps or not isinstance(declarations.get(dep),str) or not declarations[dep].strip():
                    self.issue('missing_information', 'scope/declared_preconditions/'+str(dep), '前置工步未执行，需要明确软件试验的来料/装夹前提。')
            constraints = object_at(step.get('constraints'), 'process_plan/'+sid+'/constraints')
            for key in constraints.keys()-allowed_constraints:
                self.issue('unsupported_capability','process_plan/'+sid+'/constraints/'+key,'尚未实现该硬约束，不能忽略后继续生成方案。')
            through=feature['kind']=='through_hole'
            if constraints.get('through') is not through:
                self.issue('conflicting_information', 'process_plan/'+sid+'/constraints/through', '工艺卡须明确通孔或盲孔意图，并与 CAD 一致。')
            if drilled:
                if through and any(k in constraints for k in ['cylindrical_depth_mm','bottom_angle_degrees']):
                    self.issue('conflicting_information','process_plan/'+sid+'/constraints','通孔不接受盲孔深度和锥底约束。')
                if not through:
                    if 'breakthrough_mm' in constraints:
                        self.issue('conflicting_information','process_plan/'+sid+'/constraints','盲孔不能应用额外贯穿量。')
                    for key in ['cylindrical_depth_mm','bottom_angle_degrees']:
                        value=self.require(constraints,key,'process_plan/'+sid+'/constraints/'+key)
                        if value is not None and number(value,'constraints/'+key,True)!=feature[key]:
                            self.issue('conflicting_information','process_plan/'+sid+'/constraints/'+key,'卡片孔深或底部形状与 CAD 不一致。',owner='process_engineer')
            if 'diameter_mm' in constraints and number(constraints['diameter_mm'], 'constraints/diameter_mm', True)!=feature['diameter_mm']:
                self.issue('conflicting_information', 'process_plan/'+sid+'/constraints/diameter_mm', '工艺指定孔径与 CAD 不一致。', owner='process_engineer')
            resolved_operations.append({'operation_id':operation['id'],'step_id':sid,'type':'drilling',
                                        'feature_id':feature['id'],'hole_center_xy_mm':feature['center_xy_mm'],
                                        'diameter_mm':feature['diameter_mm'],'through':through})
            if drilled and not through:
                resolved_operations[-1].update(cylindrical_depth_mm=feature['cylindrical_depth_mm'],bottom_angle_degrees=feature['bottom_angle_degrees'])
        if len(set(selected_features)) != len(features):
            self.issue('conflicting_information','scope/selected_steps','选定工步必须覆盖全部目标孔且每孔恰好一次。')
        if any(i['severity']=='blocking' for i in self.issues):
            return None
        external = list_at(card.get('external_documents'), 'process_plan/external_documents')
        for ref in external:
            if ref not in self.documents:
                self.issue('missing_information', 'process_plan/external_documents', f'缺少引用资料：{ref}。', owner='process_engineer')
        requirement_ids=set()
        for req in list_at(requirements.get('items'),'requirements/items'):
            req=object_at(req,'requirements/item')
            rid=req.get('id')
            if not isinstance(rid,str) or rid in requirement_ids:
                raise InputError('invalid_input','requirements/items/id','要求 ID 缺失或重复。')
            requirement_ids.add(rid)
            self.result['requirement_readiness'].append({'id':rid,'acceptance_defined':req.get('acceptance') is not None,'verification_status':'not_verified'})
            feature = feature_map.get(req.get('feature_id'))
            if feature is None:
                self.issue('conflicting_information','requirements/'+rid+'/feature_id','要求未关联到当前目标特征。')
                continue
            expected={'diameter':feature['diameter_mm'],'position_xy':feature['center_xy_mm'],'through':feature['kind']=='through_hole'}
            if drilled and feature['kind']=='blind_hole':
                expected.update(cylindrical_depth=feature['cylindrical_depth_mm'],conical_bottom=feature['bottom_angle_degrees'])
            if req.get('kind')=='position_xy':
                vector(req.get('nominal'),'requirements/'+rid+'/nominal',2)
            if req.get('kind')=='through' and req.get('nominal') is not expected['through']:
                self.issue('conflicting_information','requirements/'+rid+'/nominal','通盲孔要求需要与 CAD 一致的布尔值。')
            if req.get('kind') in expected and req.get('nominal') != expected[req['kind']]:
                self.issue('conflicting_information','requirements/'+rid+'/nominal','要求中的名义值与 CAD 特征不一致。',owner='process_engineer')
            if req.get('acceptance') is None:
                self.issue('missing_information','requirements/'+rid+'/acceptance','验收要求待确认；不能用整体 IoU 代替。',severity='acceptance',owner='process_engineer')
            elif req.get('kind') not in expected:
                self.issue('unsupported_capability','requirements/'+rid,'该要求尚无验证器，保留为未验证。',severity='acceptance',owner='verification')
        if not requirement_ids:
            self.issue('missing_information','requirements/items','未提供验收要求。',severity='acceptance',owner='process_engineer')
        machine = object_at(resources.get('machine'), 'resources/machine')
        if machine.get('kind')!='generic_XYZ_reference' or machine.get('axes')!=['X','Y','Z']:
            self.issue('unsupported_capability', 'resources/machine', '当前仅支持已声明的 XYZ 软件参考配置。')
        setup = self.require(resources, 'setup', 'resources/setup')
        if setup is not None:
            setup = object_at(setup, 'resources/setup')
            for key in ['work_offset','origin_mm','rotation_degrees','tool_length_offset_mm','fixture_mode','basis']:
                self.require(setup,key,'resources/setup/'+key)
            if setup.get('basis') is not None and not isinstance(setup['basis'],str):
                self.issue('invalid_input','resources/setup/basis','配置依据须为可阅读的来源说明。')
            for key in ['origin_mm','rotation_degrees']:
                if setup.get(key) is not None:
                    vector(setup[key], 'resources/setup/'+key)
            if setup.get('tool_length_offset_mm') is not None:
                number(setup['tool_length_offset_mm'],'resources/setup/tool_length_offset_mm')
            if setup.get('origin_mm') not in (None,[0,0,0]) or setup.get('rotation_degrees') not in (None,[0,0,0]) or setup.get('tool_length_offset_mm') not in (None,0):
                self.issue('unsupported_capability', 'resources/setup', '首版仅接受明确的零变换和零刀长偏置。')
            if setup.get('work_offset') not in (None,'G54') or setup.get('fixture_mode') not in (None,'unmodelled_explicitly_excluded'):
                self.issue('unsupported_capability', 'resources/setup', '此软件参考只实现 G54 和明确排除的夹具检查。')
        controller = object_at(resources.get('controller'), 'resources/controller')
        if controller.get('profile')!='reference_NC_subset' or controller.get('return_mode')!='initial_plane':
            self.issue('unsupported_capability', 'resources/controller', '控制器/返回平面语义尚未支持。')
        candidates = []
        tool_ids = set()
        required_tools = {steps[s][1]['constraints']['tool_id'] for s in selected if steps[s][1]['constraints'].get('tool_id')}
        if len(required_tools) > 1:
            self.issue('unsupported_capability','resources/tools','当前孔系只支持同一钻头；不同指定刀具须换刀适配。')
            return None
        for tool in list_at(resources.get('tools'), 'resources/tools'):
            tool = object_at(tool, 'resources/tools/item')
            if not isinstance(tool.get('id'),str) or tool['id'] in tool_ids:
                raise InputError('invalid_input','resources/tools/id','刀具 ID 缺失或重复。')
            tool_ids.add(tool['id'])
            if tool.get('kind')=='drill' and number(tool.get('diameter_mm'),'resources/tools/diameter_mm',True)==diameter:
                if not required_tools or tool.get('id') in required_tools:
                    candidates.append(tool)
        if len(candidates)!=1:
            self.issue('missing_information' if not candidates else 'conflicting_information', 'resources/tools', '需要唯一匹配的钻头；不自动猜测或覆盖工艺卡指定刀具。')
            return None
        tool = candidates[0]
        self.require(tool,'basis','resources/tools/'+tool['id']+'/basis')
        tip_angle = number(tool.get('tip_angle_degrees'),'resources/tools/tip_angle_degrees',True)
        if tip_angle >=180:
            raise InputError('invalid_input','resources/tools/tip_angle_degrees','钻尖角须小于 180 度。')
        length = number(tool.get('length_mm'),'resources/tools/length_mm',True)
        presets = [x for x in list_at(resources.get('cutting_presets'),'resources/cutting_presets')
                   if isinstance(x,dict) and x.get('operation')=='drilling' and x.get('tool_id')==tool.get('id') and x.get('material_label')==material]
        if len(presets)!=1:
            self.issue('missing_information' if not presets else 'conflicting_information','resources/cutting_presets','需要唯一且有依据的钻削参数配置；不会套用历史 S1 默认值。')
            return None
        preset = presets[0]
        if preset.get('feed_unit')!='mm/min' or preset.get('speed_unit')!='rpm':
            self.issue('unsupported_capability','resources/cutting_presets/units','首版只接受明确的 mm/min 与 rpm，不隐式转换每转进给。')
        keys=['spindle_rpm','feed_mm_min','breakthrough_mm','retract_plane_mm','safe_height_mm','clearance_height_mm']
        if drilled and not any(op['through'] for op in resolved_operations):keys.remove('breakthrough_mm')
        params={k:self.parameter(preset,k,'resources/cutting_presets') for k in keys}
        if any(v is None for v in params.values()):
            return None
        if params['spindle_rpm']<=0 or params['feed_mm_min']<=0 or params.get('breakthrough_mm',0)<0:
            self.issue('invalid_input','resources/cutting_presets','转速、进给须为正，额外贯穿量不能为负。')
        if not (params['clearance_height_mm']>=params['safe_height_mm']>=params['retract_plane_mm']>high[2]):
            self.issue('conflicting_information','resources/cutting_presets/heights','退刀/安全/清空平面与毛坯顶部不一致。')
        for sid in selected:
            constraints = steps[sid][1]['constraints']
            for key in keys:
                if key in constraints and number(constraints[key],'constraints/'+key)!=params[key]:
                    self.issue('conflicting_information','process_plan/'+sid+'/constraints/'+key,'资源参数与卡片硬约束冲突，须返回工艺师傅。',owner='process_engineer')
        tip_length=diameter/(2*math.tan(math.radians(tip_angle/2)))
        required_depth=high[2]-low[2]+tip_length+params.get('breakthrough_mm',0)
        if drilled:
            required_depth=max((required_depth if op['through'] else op['cylindrical_depth_mm']+tip_length) for op in resolved_operations)
            for op in resolved_operations:
                if not op['through'] and abs(op['bottom_angle_degrees']-tip_angle)>1e-8:
                    self.issue('conflicting_information','resources/tools/tip_angle_degrees','所选钻尖角与盲孔 CAD 锥底不一致。')
        if length<=required_depth:
            self.issue('conflicting_information','resources/tools/length_mm','声明刀具长度不足以覆盖该几何深度。')
        final_z=low[2]-params.get('breakthrough_mm',0)-tip_length
        self.issue('unverified_scope','review','工艺输入尚未作生产批准；当前只处理明确的软件参考范围。',severity='notice',owner='process_engineer')
        feature = feature_map[resolved_operations[0]['feature_id']]
        plan = {
            'schema':'genlaw.execution-plan.v1','input_id':manifest.get('id'),
            'mode':'software_geometry_reference','source_bindings':dict(self.files),
            'cad_ref':dict(self.files['cad']), 'cad_observation_ref':dict(self.files['geometry_observation']),
            'operation':resolved_operations[0],
            'stock':stock,'tool':tool,'setup':setup,'controller':controller,
            'parameters':{**params,'drill_tip_length_mm':tip_length,'cam_final_depth_mm':low[2]-params.get('breakthrough_mm',0),'final_tip_z_mm':final_z},
            'parameter_provenance':{
                'tool_selection':{'kind':'derived','rule':'unique-drill.v1: exact diameter and optional explicit card tool_id','inputs':['resources.tools','process_plan.constraints','geometry_observation.feature']},
                **{k:{'kind':'resource_preset','ref':'resources','field':'cutting_presets/'+k,'basis':preset[k]['basis']} for k in keys},
                'hole_center_xy_mm':{'kind':'cad_observation','ref':'geometry_observation','field':feature['id']},
                'drill_tip_length_mm':{'kind':'derived','rule':'cone-tip.v1: diameter / (2*tan(tip_angle/2))','inputs':['tool.diameter_mm','tool.tip_angle_degrees']},
                'final_tip_z_mm':{'kind':'derived','rule':'through-hole-tip.v1: stock_bottom - breakthrough - tip_length','inputs':['stock.bounds_mm.min[2]','breakthrough_mm','drill_tip_length_mm']},
                'cam_final_depth_mm':{'kind':'derived','rule':'through-depth.v1: stock_bottom - breakthrough','inputs':['stock.bounds_mm.min[2]','breakthrough_mm']},
            },
            'scope':scope,'requirements_ref':dict(self.files['requirements']),
            'required_downstream_checks':['Re-read original CAD and confirm feature binding in CAM adapter.','Preserve this configuration and final NC together for simulation.'],
            'production_approved':False,'whole_process_card_complete':False,
        }
        if multiple:
            plan['schema'] = 'genlaw.execution-plan.v3' if drilled else 'genlaw.execution-plan.v2'
            plan['operations'] = resolved_operations
            del plan['operation']
            plan['parameter_provenance']['hole_center_xy_mm'] = {
                'kind':'cad_observation','ref':'geometry_observation','features':selected_features}
            if 'simulation' in resources:
                simulation=object_at(resources['simulation'],'resources/simulation')
                if set(simulation)-{'resolution_mm','basis'}:
                    self.issue('unsupported_capability','resources/simulation','未知仿真参数不能被静默忽略。')
                resolution=number(simulation.get('resolution_mm'),'resources/simulation/resolution_mm',True)
                if not .01 <= resolution <= .05:
                    self.issue('unsupported_capability','resources/simulation/resolution_mm','当前数值配置仅接受 0.01–0.05 mm；实际任务仍须通过计算预算准入。')
                if not isinstance(simulation.get('basis'),str) or not simulation['basis'].strip():
                    self.issue('missing_information','resources/simulation/basis','改变数值精度须记录依据，不能静默降低精度。')
                plan['simulation']=dict(simulation)
        if drilled:
            for key in ['cam_final_depth_mm','final_tip_z_mm']:
                del plan['parameters'][key];del plan['parameter_provenance'][key]
            for op in plan['operations']:
                shoulder=low[2]-params['breakthrough_mm'] if op['through'] else high[2]-op['cylindrical_depth_mm']
                op['parameters']={'cam_final_depth_mm':shoulder,'final_tip_z_mm':shoulder-tip_length}
                op['depth_provenance']={'kind':'derived','reference':'stock bottom + breakthrough' if op['through'] else 'CAD and card cylindrical depth from stock top',
                    'drill_tip_compensation':'tool cone height added by FreeCAD Drill Tip; no breakthrough applied to a blind hole'}
        return plan


def prepare(path):
    return Preparation(path).run()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    result=prepare(args.input)
    # Never overwrite an earlier result or review artefact.
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False)
        stream.write('\n')
    print(json.dumps({'status':result['status'],'issues':len(result['issues']),'output':str(args.output)},ensure_ascii=False))
    return 0 if result['status']=='ready_for_declared_scope' else 2


if __name__=='__main__':
    raise SystemExit(main())
