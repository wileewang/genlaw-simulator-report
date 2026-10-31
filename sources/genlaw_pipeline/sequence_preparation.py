"""Prepare an explicitly ordered, bounded face-and-hole process card.

There is one original CAD binding, one stock and one coordinate system. Each
operation keeps its own tool and parameters; material states are expectations,
not replacement stock and not measurements.
"""
import math
from .preparation import object_at, list_at, vector, number, load_json, digest, InputError
from .milling_features import CONTRACTS, validate_feature, required_nominals
from .sequence_geometry import CAPABILITY, REFERENCE, KINDS, MAX_HOLES
from .profile_sequence_geometry import CAPABILITY as PROFILED, REFERENCE as PROFILED_REFERENCE, feature_nominals, validate_hole_retention
from .pocket_sequence_geometry import CAPABILITY as POCKETED, REFERENCE as POCKETED_REFERENCE, KINDS as POCKET_KINDS, contract_for_feature, validate_bound_feature, hole_locations
from .multi_pocket_geometry import CAPABILITY as MULTI_POCKET, REFERENCE as MULTI_POCKET_REFERENCE, MAX_POCKETS, validate_combination


def prepare_sequence(self, manifest):
    profiled=manifest.get('schema')=='genlaw.preparation-input.v11'
    multi_pocket=manifest.get('schema')=='genlaw.preparation-input.v13'
    pocketed=multi_pocket or manifest.get('schema')=='genlaw.preparation-input.v12'
    version='v13' if multi_pocket else 'v12' if pocketed else 'v11' if profiled else 'v9'
    capability=MULTI_POCKET if multi_pocket else POCKETED if pocketed else PROFILED if profiled else CAPABILITY
    reference=MULTI_POCKET_REFERENCE if multi_pocket else POCKETED_REFERENCE if pocketed else PROFILED_REFERENCE if profiled else REFERENCE
    extra=MAX_POCKETS if multi_pocket else int(profiled or pocketed)
    def nominals(feature):return feature_nominals(feature) if profiled else required_nominals(contracts[feature['id']],feature)
    self.result.update(adapter='genlaw.preparation.ordered-milling.v1',input_id=manifest.get('id'),input_manifest_sha256=digest(self.path))
    def reject(code,field,message):self.issue(code,field,message)
    def basis(value,field):
        if not isinstance(value,str) or not value.strip():reject('missing_information',field,'需要明确的文字依据。')
    if manifest.get('capability')!=capability:raise InputError('unsupported_capability','capability','该组合能力尚未实现。')
    if not isinstance(manifest.get('id'),str) or not manifest['id'].strip():raise InputError('invalid_input','id','需要输入编号。')
    if manifest.get('mode')!='software_geometry_reference':reject('unsupported_capability','mode','当前仅验证软件几何。')
    refs=object_at(manifest.get('files'),'files');needed=['process_plan','cad','geometry_observation','requirements','resources']
    if pocketed and 'expected_states' not in refs:raise InputError('missing_information','expected_states','型腔组合需提供 NC 前冻结的逐工步预期 CAD。')
    if any(k not in refs for k in needed):raise InputError('missing_information','files','缺少卡 / CAD / 要求 / 资源。')
    paths={k:self.bind(k,v) for k,v in refs.items()};self.documents=object_at(manifest.get('source_documents'),'source_documents')
    if not self.documents or any(k not in paths for k in self.documents.values()):reject('missing_information','source_documents','需要原始工艺卡。')
    values={k:object_at(load_json(paths[k]),k) for k in needed if k!='cad'}
    card,geo,requirements,resources=(values[k] for k in ['process_plan','geometry_observation','requirements','resources'])
    schemas={'process_plan':'genlaw.process-plan.v1','geometry_observation':'genlaw.cad-observation.'+version,'requirements':'genlaw.requirements.v1','resources':'genlaw.preparation-resources.v1'}
    for key,value in values.items():
        if value.get('schema')!=schemas[key]:reject('unsupported_capability',key+'/schema','资料版本不支持。')
        if key!='resources' and value.get('cad_sha256')!=self.files['cad']['sha256']:reject('conflicting_information',key+'/cad_sha256','CAD 版本不一致。')
        if key!='process_plan' and value.get('units')!='mm':reject('unsupported_capability',key+'/units','需要毫米单位。')
    if object_at(card.get('transcription'),'transcription').get('status')!='checked_against_source':reject('missing_information','transcription','结构化卡须与来源核对。')
    for key in list_at(card.get('external_documents'),'external_documents'):
        if key not in self.documents:reject('missing_information','external_documents','卡片引用的外部资料未提供。')
    if geo.get('reference_class')!=reference:reject('unsupported_capability','reference_class','原 CAD 几何类型与显式能力版本不一致。')
    features=list_at(geo.get('features'),'features');bounds=object_at(geo.get('bounds_mm'),'bounds')
    if not (3 if multi_pocket else 2+extra)<=len(features)<=MAX_HOLES+1+extra:raise InputError('unsupported_capability','features','所选组合版本的特征数量超出范围。')
    feature_map={};contracts={}
    for f in features:
        f=object_at(f,'feature');fid=f.get('id');kind=f.get('kind')
        if not isinstance(fid,str) or not fid or fid in feature_map:raise InputError('invalid_input','feature/id','特征编号缺失或重复。')
        if kind not in (POCKET_KINDS if pocketed else KINDS) or (not profiled and kind=='capsule_outline'):raise InputError('unsupported_capability','feature/kind','未实现的组合特征。')
        contract=contract_for_feature(f) if pocketed else CONTRACTS[KINDS[kind]]
        lo,hi,_=validate_bound_feature(f,bounds) if pocketed else validate_feature(contract,f,bounds)
        feature_map[fid]=f;contracts[fid]=contract
    tops=[f for f in features if f['kind']=='planar_top'];holes=[f for f in features if f['kind']=='through_hole']
    if len(tops)!=1:raise InputError('unsupported_capability','features','只允许一个完整顶面。')
    outlines=[f for f in features if f['kind']=='capsule_outline']
    pockets=[f for f in features if f['kind']=='rounded_rectangular_pocket']
    if (not (0 if multi_pocket else 1)<=len(holes)<=MAX_HOLES or len(outlines)!=int(profiled)
        or (not 2<=len(pockets)<=MAX_POCKETS if multi_pocket else len(pockets)!=int(pocketed))):
        raise InputError('unsupported_capability','features','特征组成不符合声明版本。')
    if pocketed:
        try:validate_combination(pockets,holes) if multi_pocket else hole_locations(pockets[0],holes)
        except ValueError as error:raise InputError('unsupported_capability','features',str(error))
    if profiled:
        try:validate_hole_retention(outlines[0],holes)
        except ValueError as error:raise InputError('unsupported_capability','features',str(error))
    for i,f in enumerate(holes):
        for other in holes[i+1:]:
            if math.dist(f['center_xy_mm'],other['center_xy_mm'])<=(f['diameter_mm']+other['diameter_mm'])/2:
                raise InputError('unsupported_capability','features','孔相交 / 相切组合尚未实现。')
    provenance=object_at(geo.get('provenance'),'provenance');key=provenance.get('file_key')
    if geo.get('observation_method')!='prior_native_cad_observation' or key not in paths or provenance.get('sha256')!=self.files[key]['sha256']:
        reject('missing_information','provenance','原生 CAD 观察未绑定。')
    else:
        prior=load_json(paths[key])
        if any(prior.get(k)!=v for k,v in {'sha256':self.files['cad']['sha256'],'valid_single_solid':True,'reference_class':reference,'bounds':bounds,'features':features}.items()):
            reject('conflicting_information','provenance','特征与原生观察不一致。')
    stock=object_at(resources.get('stock'),'stock');material=stock.get('material_label');basis(material,'stock/material_label')
    sb=object_at(stock.get('bounds_mm'),'stock/bounds_mm');sl,sh=[vector(sb.get(k),'stock/'+k) for k in ['min','max']]
    if stock.get('shape')!='box' or sl!=lo or sh[:2]!=hi[:2] or sh[2]<=hi[2]:
        raise InputError('unsupported_capability','stock','须单独提供与原 CAD 同底面及 XY 外形、顶面有面铣余量的矩形来料。')
    scope=object_at(manifest.get('scope'),'scope');selected=list_at(scope.get('selected_steps'),'selected_steps')
    if len(selected)!=len(features) or any(not isinstance(s,str) or not s for s in selected) or len(set(selected))!=len(selected):
        raise InputError('unsupported_capability','selected_steps','每个目标特征须对应唯一加工工步。')
    if scope.get('whole_process_card') is not False:reject('unsupported_capability','scope','当前仅验证选定几何工步。')
    exclusions=list_at(scope.get('excluded_checks'),'excluded_checks')
    from .fixture_setup import enabled as fixtures, EXCLUSIONS as FIXTURE_EXCLUSIONS
    modeled_fixtures = fixtures({'setup': resources.get('setup', {})})
    required_exclusions = FIXTURE_EXCLUSIONS if modeled_fixtures else ['fixture_collision','holder_collision','material_physics','surface_roughness']
    if any(k not in exclusions for k in required_exclusions):reject('unsupported_capability','excluded_checks','须明确未验证项目。')
    if modeled_fixtures and any(k in exclusions for k in ['fixture_collision', 'holder_collision']):
        reject('conflicting_information','excluded_checks','已要求夹具包络检查，不能同时把所有夹具 / 刀柄检查整体排除。')
    steps={};oids=set()
    if card.get('conditions') not in (None,[]):reject('unsupported_capability','conditions','不能隐式选择条件分支。')
    for op in list_at(card.get('operations'),'operations'):
        op=object_at(op,'operation');oid=op.get('id')
        if not isinstance(oid,str) or not oid or oid in oids:raise InputError('invalid_input','operation/id','工序编号缺失或重复。')
        oids.add(oid)
        for step in list_at(op.get('steps'),'steps'):
            sid=step.get('id')
            if not isinstance(sid,str) or not sid or sid in steps:raise InputError('invalid_input','step/id','工步编号缺失或重复。')
            self.source(step.get('source'),'step/source');basis(step.get('text'),sid+'/text');steps[sid]=(op,step)
            self.coverage.append({'operation_id':oid,'step_id':sid,'type':step.get('type'),'status':'selected_for_preparation' if sid in selected else 'not_verified'})
    if [sid for sid in steps if sid in selected]!=selected:raise InputError('conflicting_information','selected_steps','工步须存在且保持原卡顺序，不能自行重排。')
    declarations=object_at(scope.get('declared_preconditions'),'declared_preconditions');order=list(steps)
    machine=object_at(resources.get('machine'),'machine')
    if machine.get('kind')!='generic_XYZ_reference' or machine.get('axes')!=['X','Y','Z']:reject('unsupported_capability','machine','当前为 XYZ 参考配置。')
    setup=object_at(resources.get('setup'),'setup');basis(setup.get('basis'),'setup/basis')
    from .coordinate_setup import enabled as explicit_setup, validate_setup, setup_requirements
    framed=explicit_setup({'setup':setup})
    expected_setup={'origin_mm':[0,0,0],'rotation_degrees':[0,0,0],'tool_length_offset_mm':0,'work_offset':'G54','fixture_mode':'unmodelled_explicitly_excluded'}
    if framed:
        try:startup_z=validate_setup(setup,sb)
        except ValueError as error:raise InputError('conflicting_information','setup',str(error))
        dependencies=list_at(steps[selected[0]][1].get('depends_on'),'setup/depends_on')
        setup_steps=[steps[sid][1] for sid in dependencies if isinstance(sid,str) and sid in steps and steps[sid][1].get('type')=='setup']
        if len(setup_steps)!=1:raise InputError('missing_information','setup','显式坐标配置需要唯一的工艺卡装夹前置工步。')
        constraints=object_at(setup_steps[0].get('constraints'),'setup/constraints')
        if set(constraints)!={'coordinate_setup'} or constraints['coordinate_setup']!=setup_requirements(setup):
            raise InputError('conflicting_information','setup','工艺卡装夹约束与设备资源的显式坐标配置不一致或缺失。')
    else:
        startup_z=legacy_setup(self,setup,expected_setup,sh,reject,basis)
        if startup_z is None:return None
    controller=object_at(resources.get('controller'),'controller')
    if controller.get('profile')!='reference_NC_subset' or controller.get('arc_centers')!='incremental_IJK':reject('unsupported_capability','controller','需显式参考控制器及增量圆心语义。')
    tools=list_at(resources.get('tools'),'tools');tool_map={};numbers=set()
    if not 1<=len(tools)<=MAX_HOLES+1+extra:raise InputError('unsupported_capability','tools','刀具数量超出当前范围。')
    for tool in tools:
        tool=object_at(tool,'tool');tid=tool.get('id');n=tool.get('number')
        if not isinstance(tid,str) or not tid or tid in tool_map or isinstance(n,bool) or not isinstance(n,int) or not 1<=n<=99 or n in numbers:
            raise InputError('invalid_input','tool/id','刀具 ID 和 NC 刀号须分别唯一且明确，刀号为 1 至 99。')
        tool_map[tid]=tool;numbers.add(n);basis(tool.get('basis'),'tool/basis')
        if set(tool)-{'id','number','kind','center_cutting','diameter_mm','cutting_length_mm','length_mm','basis'}:reject('unsupported_capability','tool','额外刀具形状参数未建模。')
        for key in ['diameter_mm','cutting_length_mm','length_mm']:number(tool.get(key),'tool/'+key,True)
        if tool.get('kind')!='flat_endmill' or tool.get('center_cutting') is not True:reject('unsupported_capability','tool','当前要求可中心切削平底铣刀。')
        if tool['length_mm']<tool['cutting_length_mm']:reject('conflicting_information','tool/length_mm','刀具总长不得小于切长。')
    presets=list_at(resources.get('cutting_presets'),'cutting_presets');operations=[];completed=[]
    material_states=[{'id':'raw','bounds_mm':sb,'completed_features':[]}];used_tools=set();used_presets=set();tc_parameters={}
    for index,sid in enumerate(selected):
        op,step=steps[sid];fid=step.get('feature_id')
        if fid not in feature_map or fid in completed:raise InputError('conflicting_information',sid,'目标特征不存在或重复加工。')
        f=feature_map[fid];contract=contracts[fid];facing=f['kind']=='planar_top';external=f['kind']=='capsule_outline';operation=contract['operation']
        pocket=f['kind']=='rounded_rectangular_pocket'
        if (index==0)!=facing or step.get('type')!=operation:raise InputError('unsupported_capability',sid,'必须先面铣，再按原卡顺序执行声明的几何特征；不能重排。')
        if any(v.get('conditions') not in (None,[]) for v in [op,step]):reject('unsupported_capability',sid+'/conditions','条件分支未实现。')
        deps=list_at(step.get('depends_on'),'depends_on')
        if index and selected[index-1] not in deps:reject('missing_information',sid+'/depends_on','需显式依赖前一加工工步，防止丢失剩料与顺序。')
        if not index and not deps:reject('missing_information',sid+'/depends_on','需明确来料 / 装夹前提。')
        for dep in deps:
            if not isinstance(dep,str) or dep not in steps or order.index(dep)>=order.index(sid):reject('conflicting_information',sid+'/depends_on','依赖不存在或次序错误。');continue
            if dep not in selected:basis(declarations.get(dep),'declared_preconditions/'+dep)
            if any(v.get('conditions') not in (None,[]) for v in steps[dep]):reject('unsupported_capability','depends_on','前置条件分支未实现。')
        keys=contract.get('parameter_keys',['spindle_rpm','feed_mm_min','plunge_mm_min','step_down_mm','step_over_percent','safe_height_mm','clearance_height_mm','path_tolerance_mm'])+contract['extra_parameters']
        constraints=object_at(step.get('constraints'),sid+'/constraints');surface=contract['surface_constraint']
        if set(constraints)-set(keys)-set(contract['dimensions'])-{'tool_id',surface}:reject('unsupported_capability',sid+'/constraints','未知硬约束不能忽略。')
        for key in contract['dimensions']:
            supplied=self.require(constraints,key,sid+'/constraints/'+key)
            if supplied is not None and number(supplied,key,True)!=f[key]:reject('conflicting_information',sid+'/constraints/'+key,'卡片尺寸与原 CAD 不一致。')
        if constraints.get(surface)!=contract.get('surface_value','flat'):reject('conflicting_information',sid+'/'+surface,'目标表面或终止条件不一致。')
        tid=self.require(constraints,'tool_id',sid+'/tool_id')
        if tid not in tool_map:raise InputError('missing_information',sid+'/tool_id','明确刀具缺失，不能猜测。')
        tool=tool_map[tid];used_tools.add(tid);diameter=tool['diameter_mm'];depth=sh[2]-hi[2] if facing else f['depth_mm'] if pocket else hi[2]-lo[2]
        matching=[(i,p) for i,p in enumerate(presets) if p.get('operation')==operation and p.get('tool_id')==tid and p.get('material_label')==material]
        if len(matching)!=1:raise InputError('missing_information' if not matching else 'conflicting_information',sid+'/cutting_presets','需要唯一、明确的参数组合。')
        pi,preset=matching[0];used_presets.add(pi)
        strategies={'clearing_pattern':'Offset','cut_mode':'Climb','start_at':'Center','entry':'vertical_center_cutting','keep_tool_down':False,'radial_allowance_mm':0,'axial_allowance_mm':0,**contract.get('strategy_defaults',{}),**contract['extra_strategy']}
        if set(preset)-set(keys)-set(strategies)-{'id','operation','tool_id','material_label','feed_unit','speed_unit'}:reject('unsupported_capability',sid+'/cutting_presets','额外策略不能忽略。')
        if preset.get('feed_unit')!='mm/min' or preset.get('speed_unit')!='rpm':reject('unsupported_capability',sid+'/units','需要 mm/min 与 rpm。')
        for key,wanted in strategies.items():
            if preset.get(key)!=wanted:reject('unsupported_capability',sid+'/'+key,'策略须显式给定且属于验证范围。')
        p={k:self.parameter(preset,k,sid+'/cutting_presets') for k in keys}
        if any(v is None for v in p.values()):return None
        if any(p[k]<=0 for k in ['spindle_rpm','feed_mm_min','plunge_mm_min','step_down_mm']) or ('step_over_percent' in p and (not 0<p['step_over_percent']<=50 or not p['step_over_percent'].is_integer())) or p['step_down_mm']>depth:
            reject('conflicting_information',sid+'/parameters','进给 / 分层 / 整数步距不满足当前约束。')
        if not 0<p['path_tolerance_mm']<=.001:reject('unsupported_capability',sid+'/path_tolerance_mm','曲线精度须不大于 0.001 mm。')
        if not p['clearance_height_mm']>=p['safe_height_mm']>sh[2]:reject('conflicting_information',sid+'/heights','安全高度须高于原始来料。')
        if p['clearance_height_mm']!=startup_z:reject('unsupported_capability',sid+'/clearance_height_mm','当前各步清空平面须与显式装刀前高度一致。')
        tc_values={k:p[k] for k in ['spindle_rpm','feed_mm_min','plunge_mm_min']}
        if tid in tc_parameters and tc_parameters[tid]!=tc_values:reject('unsupported_capability',sid+'/parameters','当前同一刀具控制器须保持转速和进给；工步参数变更需要独立控制器支持。')
        tc_parameters[tid]=tc_values
        final=hi[2] if facing else hi[2]-depth if pocket else lo[2]-p['breakthrough_mm'];start=sh[2] if facing else hi[2] if external or pocket else hi[2]+p['entry_clearance_mm']
        if pocket and diameter>2*f['corner_radius_mm']:reject('conflicting_information',sid+'/tool','刀径不能大于型腔圆角直径。')
        if external and not 0<p['breakthrough_mm']<=diameter:reject('conflicting_information',sid+'/breakthrough_mm','外形贯穿余量须明确且不超过刀径。')
        if external and diameter<f['width_mm']/2*(math.sqrt(2)-1)+2*p['path_tolerance_mm']:
            reject('unsupported_capability',sid+'/profile_passes','单圈扫掠无法清除全部外侧余料；须另行实现多径向走刀。')
        if not facing and not external and not pocket:
            if diameter>=f['diameter_mm']:reject('conflicting_information',sid+'/tool','螺旋铣孔刀径必须小于孔径。')
            if not 0<p['entry_clearance_mm']<p['safe_height_mm']-hi[2] or not 0<p['breakthrough_mm']<=diameter:
                reject('conflicting_information',sid+'/heights','需明确入刀间隙和不超过刀径的贯穿余量。')
            if not any(i['severity']=='blocking' for i in self.issues):
                from .helix_geometry import clearing_radii
                clearing_radii(f['diameter_mm'],diameter,p['step_over_percent'])
        if tool['cutting_length_mm']<(depth if facing else hi[2]-final):reject('conflicting_information',sid+'/tool','切长不足以覆盖加工深度及贯穿余量。')
        for key in keys:
            if key in constraints and number(constraints[key],key)!=p[key]:reject('conflicting_information',sid+'/'+key,'卡片硬约束与资源冲突。')
        completed.append(fid);before=material_states[-1]['id'];after=f'after-{index+1:02d}'
        material_states.append({'id':after,'bounds_mm':bounds,'completed_features':list(completed)})
        operations.append({'operation_id':op['id'],'step_id':sid,'type':operation,'feature_id':fid,'geometry':f,
            'tool_id':tid,'tool_number':tool['number'],'parameters':p,'strategy':strategies,
            'parameter_provenance':{k:{'kind':'resource_preset','basis':preset[k]['basis']} for k in keys},
            'start_z_mm':start,'final_z_mm':final,'material_before_id':before,'material_after_id':after,
            **({'removal_depth_mm':depth} if facing or pocket else {} if external else {'hole_center_xy_mm':f['center_xy_mm'],'diameter_mm':f['diameter_mm'],'through':True})})
    if used_tools!=set(tool_map) or used_presets!=set(range(len(presets))):reject('unsupported_capability','resources','本入口要求只列所选加工工步使用的刀具及参数，不忽略多余项。')
    from .tool_length_setup import validate_resources as validate_tool_lengths
    try:validate_tool_lengths(setup,stock,tools,operations)
    except ValueError as error:raise InputError('conflicting_information','tool_length_compensation',str(error))
    ids=set();observed=set()
    for req in list_at(requirements.get('items'),'requirements/items'):
        rid=req.get('id');fid=req.get('feature_id');kind=req.get('kind')
        if not isinstance(rid,str) or not rid or rid in ids or (fid,kind) in observed:raise InputError('invalid_input','requirements/id','要求编号或特征检查重复。')
        ids.add(rid);observed.add((fid,kind))
        if fid not in feature_map:reject('conflicting_information','requirements/feature_id','要求未绑定目标特征。');continue
        expected=nominals(feature_map[fid])
        if kind in expected and req.get('nominal')!=expected[kind]:reject('conflicting_information',rid+'/nominal','要求名义值与原 CAD 不一致。')
        self.result['requirement_readiness'].append({'id':rid,'acceptance_defined':req.get('acceptance') is not None,'verification_status':'not_verified'})
        if req.get('acceptance') is None:self.issue('missing_information',rid+'/acceptance','制造验收依据待补。',severity='acceptance')
    if any((fid,k) not in observed for fid,f in feature_map.items() for k in nominals(f)):
        reject('missing_information','requirements','需列出全部选定特征的几何检查；制造判据可显式待定。')
    simulation=object_at(resources.get('simulation'),'simulation');resolution=number(simulation.get('resolution_mm'),'resolution_mm',True)
    if set(simulation)-{'resolution_mm','basis'} or not .01<=resolution<=.05:reject('unsupported_capability','simulation','精度范围或额外配置不支持。')
    basis(simulation.get('basis'),'simulation/basis')
    oracle_ref=None
    if 'expected_states' in paths:
        oracle=load_json(paths['expected_states']);states=oracle.get('states');oracle_ref=dict(self.files['expected_states'])
        if (oracle.get('schema')!='genlaw.independent-state-oracles.v1' or oracle.get('created_before_nc') is not True
            or oracle.get('target_cad_sha256')!=self.files['cad']['sha256'] or not isinstance(states,list) or len(states)!=len(operations)):
            raise InputError('conflicting_information','expected_states','独立预期形状的版本 / 数量不一致。')
        for actual,expected in zip(states,material_states[1:]):
            if any(actual.get(k)!=v for k,v in expected.items()) or actual.get('cad') not in self.files.values():
                reject('conflicting_information','expected_states','预期形状与声明工序、CAD 或绑定文件不一致。')
    self.issue('unverified_scope','review','顺序铣削软件参考；未完成整卡、工艺或生产批准。',severity='notice',owner='process_engineer')
    return {'schema':'genlaw.execution-plan.'+version,'capability':capability,'input_id':manifest['id'],'mode':manifest['mode'],
        'source_bindings':dict(self.files),'cad_ref':dict(self.files['cad']),'cad_observation_ref':dict(self.files['geometry_observation']),
        'requirements_ref':dict(self.files['requirements']),'stock':stock,'target_bounds_mm':bounds,'tools':tools,
        'setup':setup,'controller':controller,'operations':operations,'material_states':material_states,
        'expected_states_ref':oracle_ref,'simulation':simulation,'scope':scope,
        'production_approved':False,'whole_process_card_complete':False}


def legacy_setup(self,setup,expected_setup,sh,reject,basis):
    if set(setup)-set(expected_setup)-{'basis','startup'}:reject('unsupported_capability','setup','额外装夹 / 偏置字段尚未实现。')
    for key,wanted in expected_setup.items():
        supplied=self.require(setup,key,'setup/'+key)
        if key in ('origin_mm','rotation_degrees') and supplied is not None:vector(supplied,key)
        if key=='tool_length_offset_mm' and supplied is not None:number(supplied,key)
        if supplied is not None and supplied!=wanted:reject('unsupported_capability','setup/'+key,'当前不支持该装夹 / 偏置语义。')
    startup=self.require(setup,'startup','setup/startup')
    if startup is None:return None
    startup=object_at(startup,'setup/startup')
    if set(startup)!={'position_mm','tool','spindle','retract_before_tool_selection_z_mm','basis'}:reject('unsupported_capability','setup/startup','启动状态字段须明确且在当前范围内。')
    if vector(startup.get('position_mm'),'startup/position_mm')!=[0,0,0] or startup.get('tool')!='unloaded' or startup.get('spindle')!='stopped':
        reject('unsupported_capability','setup/startup','当前参考引擎从原点、无刀具及停转状态启动。')
    startup_z=number(startup.get('retract_before_tool_selection_z_mm'),'startup/retract_before_tool_selection_z_mm')
    basis(startup.get('basis'),'setup/startup/basis')
    if startup_z<=sh[2]:reject('conflicting_information','setup/startup','装刀前启动高度必须高于原始毛坯。')
    return startup_z
