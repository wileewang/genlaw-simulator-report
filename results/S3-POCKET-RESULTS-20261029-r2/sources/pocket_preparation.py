"""V4 feature adapter: source-bound single-pocket preparation, no native CAD execution."""
from .preparation import object_at,list_at,vector,number,load_json,digest,InputError
from .pocket_geometry import POCKET,CAPABILITY


def prepare_pocket(self,manifest):
    self.result.update(adapter='genlaw.preparation.rounded-pocket.v1',input_id=manifest.get('id'),input_manifest_sha256=digest(self.path))
    def reject(code,field,message):self.issue(code,field,message)
    def text_basis(value,field):
        if not isinstance(value,str) or not value.strip():reject('missing_information',field,'需要非空的文字依据。')
    if not isinstance(manifest.get('id'),str) or not manifest['id'].strip():raise InputError('invalid_input','id','需要非空输入编号。')
    if manifest.get('capability')!=CAPABILITY:raise InputError('unsupported_capability','capability','该特征加工适配器尚未实现。')
    if manifest.get('mode')!='software_geometry_reference':reject('unsupported_capability','mode','当前仅验证软件几何。')
    refs=object_at(manifest.get('files'),'files');needed=['process_plan','cad','geometry_observation','requirements','resources']
    if any(k not in refs for k in needed):raise InputError('missing_information','files','缺少卡 / CAD / 要求 / 资源。')
    paths={k:self.bind(k,v) for k,v in refs.items()}
    self.documents=object_at(manifest.get('source_documents'),'source_documents')
    if not self.documents or any(k not in paths for k in self.documents.values()):reject('missing_information','source_documents','需要可追溯的原始工艺卡。')
    values={k:object_at(load_json(paths[k]),k) for k in needed if k!='cad'}
    card,geo,requirements,resources=(values[k] for k in ['process_plan','geometry_observation','requirements','resources'])
    schemas={'process_plan':'genlaw.process-plan.v1','geometry_observation':'genlaw.cad-observation.v4','requirements':'genlaw.requirements.v1','resources':'genlaw.preparation-resources.v1'}
    for key,value in values.items():
        if value.get('schema')!=schemas[key]:reject('unsupported_capability',key+'/schema','资料版本不支持。')
        if key!='resources' and value.get('cad_sha256')!=self.files['cad']['sha256']:reject('conflicting_information',key+'/cad_sha256','CAD 版本不一致。')
        if key!='process_plan' and value.get('units')!='mm':reject('unsupported_capability',key+'/units','需要明确毫米单位。')
    if object_at(card.get('transcription'),'transcription').get('status')!='checked_against_source':reject('missing_information','transcription','结构化卡须与来源核对。')
    for name in list_at(card.get('external_documents'),'external_documents'):
        if name not in self.documents:reject('missing_information','external_documents','外部引用资料未提供。')
    if geo.get('reference_class')!=POCKET:reject('unsupported_capability','reference_class','该几何类型尚未实现。')
    features=list_at(geo.get('features'),'features')
    if len(features)!=1:raise InputError('unsupported_capability','features','此适配器先验证单个圆角矩形槽。')
    f=object_at(features[0],'features/0');fid=f.get('id')
    if not isinstance(fid,str) or not fid:raise InputError('invalid_input','feature/id','需要稳定特征编号。')
    allowed={'id','kind','axis','center_xy_mm','width_mm','height_mm','corner_radius_mm','depth_mm'}
    if set(f)!=allowed or f.get('kind')!='rounded_rectangular_pocket' or vector(f.get('axis'),'axis')!=[0,0,1]:
        raise InputError('unsupported_capability','feature','未知轮廓、方向或几何字段不能忽略。')
    xy=vector(f['center_xy_mm'],'center_xy_mm',2)
    w,h,r,d=[number(f[k],k,True) for k in ['width_mm','height_mm','corner_radius_mm','depth_mm']]
    if 2*r>=min(w,h):reject('unsupported_capability','corner_radius_mm','当前圆角矩形要求内角半径小于半短边；胶囊形另行验收。')
    bounds=object_at(geo.get('bounds_mm'),'bounds');lo=vector(bounds.get('min'),'bounds/min');hi=vector(bounds.get('max'),'bounds/max')
    if any(a>=b for a,b in zip(lo,hi)) or d>=hi[2]-lo[2]:reject('conflicting_information','depth_mm','槽必须保留底部。')
    if not all(lo[i]<xy[i]-size/2 and xy[i]+size/2<hi[i] for i,size in enumerate([w,h])):reject('unsupported_capability','feature','开口槽与越界轮廓尚待扩展。')
    provenance=object_at(geo.get('provenance'),'provenance');key=provenance.get('file_key')
    if geo.get('observation_method')!='prior_native_cad_observation' or key not in paths or provenance.get('sha256')!=self.files[key]['sha256']:
        reject('missing_information','provenance','原生 CAD 观察缺失或未绑定。')
    else:
        prior=load_json(paths[key])
        if any(prior.get(k)!=v for k,v in {'sha256':self.files['cad']['sha256'],'valid_single_solid':True,'reference_class':POCKET,'bounds':bounds,'features':features}.items()):reject('conflicting_information','provenance','特征包与原生观察不一致。')
    stock=object_at(resources.get('stock'),'stock');material=self.require(stock,'material_label','stock/material_label')
    text_basis(material,'stock/material_label')
    stock_bounds=object_at(stock.get('bounds_mm'),'stock/bounds_mm')
    for key in ['min','max']:vector(stock_bounds.get(key),'stock/bounds_mm/'+key)
    if stock.get('shape')!='box' or stock.get('bounds_mm')!=bounds:reject('unsupported_capability','stock','当前要求已具备外形的等大矩形来料。')
    scope=object_at(manifest.get('scope'),'scope');selected=list_at(scope.get('selected_steps'),'selected_steps')
    if len(selected)!=1 or not isinstance(selected[0],str):raise InputError('unsupported_capability','selected_steps','单槽须对应一个加工工步。')
    if scope.get('whole_process_card') is not False:reject('unsupported_capability','scope','当前仅选定几何工步。')
    exclusions=list_at(scope.get('excluded_checks'),'excluded_checks')
    if any(x not in exclusions for x in ['fixture_collision','material_physics','surface_roughness']):reject('unsupported_capability','excluded_checks','须明确当前未验证项目。')
    steps={};oids=set()
    for op in list_at(card.get('operations'),'operations'):
        oid=op.get('id')
        if not isinstance(oid,str) or not oid or oid in oids:raise InputError('invalid_input','operation/id','工序编号缺失或重复。')
        oids.add(oid)
        for s in list_at(op.get('steps'),'steps'):
            sid=s.get('id')
            if not isinstance(sid,str) or not sid or sid in steps:raise InputError('invalid_input','step/id','工步编号缺失或重复。')
            self.source(s.get('source'),'step/source')
            if not s.get('text'):reject('missing_information',sid,'须保留工步原意。')
            steps[sid]=(op,s);self.coverage.append({'operation_id':oid,'step_id':sid,'type':s.get('type'),'status':'selected_for_preparation' if sid in selected else 'not_verified'})
    sid=selected[0]
    if sid not in steps:raise InputError('missing_information','selected_steps','选定工步不存在。')
    op,step=steps[sid]
    if step.get('type')!='pocket_milling' or step.get('feature_id')!=fid:reject('conflicting_information',sid,'工步类型或目标特征不一致。')
    if any(x.get('conditions') not in (None,[]) for x in [card,op,step]):reject('unsupported_capability','conditions','不能隐式选择条件分支。')
    declarations=object_at(scope.get('declared_preconditions'),'declared_preconditions')
    order=list(steps)
    for dep in list_at(step.get('depends_on'),'depends_on'):
        if dep not in steps or order.index(dep)>=order.index(sid):reject('conflicting_information','depends_on','前置工步无效或次序错误。');continue
        if not isinstance(declarations.get(dep),str) or not declarations[dep].strip():reject('missing_information','declared_preconditions','需要明确来料 / 装夹前提。')
        if any(x.get('conditions') not in (None,[]) for x in steps[dep]):reject('unsupported_capability','depends_on','前置条件分支未实现。')
    keys=['spindle_rpm','feed_mm_min','plunge_mm_min','step_down_mm','step_over_percent','safe_height_mm','clearance_height_mm','path_tolerance_mm']
    constraints=object_at(step.get('constraints'),'constraints')
    if set(constraints)-set(keys)-{'width_mm','height_mm','corner_radius_mm','depth_mm','tool_id','bottom_shape'}:reject('unsupported_capability','constraints','未知硬约束不能忽略。')
    for key,value in [('width_mm',w),('height_mm',h),('corner_radius_mm',r),('depth_mm',d)]:
        given=self.require(constraints,key,'constraints/'+key)
        if given is not None and number(given,key,True)!=value:reject('conflicting_information','constraints/'+key,'工艺卡名义尺寸与 CAD 不一致。')
    if constraints.get('bottom_shape')!='flat':reject('conflicting_information','bottom_shape','该槽须明确平底。')
    machine=object_at(resources.get('machine'),'machine')
    if machine.get('kind')!='generic_XYZ_reference' or machine.get('axes')!=['X','Y','Z']:reject('unsupported_capability','machine','当前为 XYZ 参考配置。')
    setup=object_at(resources.get('setup'),'setup');text_basis(setup.get('basis'),'setup/basis')
    for key,value in {'origin_mm':[0,0,0],'rotation_degrees':[0,0,0],'tool_length_offset_mm':0,'work_offset':'G54','fixture_mode':'unmodelled_explicitly_excluded'}.items():
        supplied=self.require(setup,key,'setup/'+key)
        if supplied is not None and key in ('origin_mm','rotation_degrees'):vector(supplied,'setup/'+key)
        if supplied is not None and key=='tool_length_offset_mm':number(supplied,'setup/'+key)
        if supplied is not None and supplied!=value:reject('unsupported_capability','setup/'+key,'该装夹 / 偏置语义尚未实现。')
    controller=object_at(resources.get('controller'),'controller')
    if controller.get('profile')!='reference_NC_subset' or controller.get('arc_centers')!='incremental_IJK':reject('unsupported_capability','controller','须明确参考 NC 与增量圆心语义。')
    tools=list_at(resources.get('tools'),'tools');ids=[t.get('id') for t in tools]
    if len(set(ids))!=len(ids) or any(not isinstance(x,str) or not x for x in ids):raise InputError('invalid_input','tool/id','刀具编号缺失或重复。')
    tool_id=self.require(constraints,'tool_id','constraints/tool_id');candidates=[t for t in tools if t.get('id')==tool_id]
    if len(candidates)!=1:raise InputError('missing_information','tool_id','须明确唯一刀具，不能猜测。')
    tool=candidates[0];text_basis(tool.get('basis'),'tool/basis');diameter=number(tool.get('diameter_mm'),'tool/diameter_mm',True)
    if set(tool)-{'id','kind','center_cutting','diameter_mm','cutting_length_mm','length_mm','basis'}:reject('unsupported_capability','tool','额外刀具形状参数尚未建模。')
    length=number(tool.get('length_mm'),'tool/length_mm',True);flute=number(tool.get('cutting_length_mm'),'cutting_length_mm',True)
    if tool.get('kind')!='flat_endmill' or tool.get('center_cutting') is not True:reject('unsupported_capability','tool','当前要求可中心切削的平底铣刀。')
    if diameter/2>r or flute<d or length<flute:reject('conflicting_information','tool','刀具不能覆盖内角 / 槽深，或长度冲突。')
    presets=[p for p in list_at(resources.get('cutting_presets'),'cutting_presets') if p.get('operation')=='pocket_milling' and p.get('tool_id')==tool_id and p.get('material_label')==material]
    if len(presets)!=1:raise InputError('missing_information' if not presets else 'conflicting_information','cutting_presets','需要唯一有依据的参数组合。')
    preset=presets[0]
    if set(preset)-set(keys)-{'id','operation','tool_id','material_label','feed_unit','speed_unit','clearing_pattern','cut_mode','start_at','entry','keep_tool_down','radial_allowance_mm','axial_allowance_mm'}:reject('unsupported_capability','cutting_presets','额外加工策略不能忽略。')
    if preset.get('feed_unit')!='mm/min' or preset.get('speed_unit')!='rpm':reject('unsupported_capability','cutting_presets/units','需要 mm/min 与 rpm。')
    for key,value in {'clearing_pattern':'Offset','cut_mode':'Climb','start_at':'Center','entry':'vertical_center_cutting','keep_tool_down':False,'radial_allowance_mm':0,'axial_allowance_mm':0}.items():
        if preset.get(key)!=value:reject('unsupported_capability','cutting_presets/'+key,'该策略 / 余量须显式给定且属于当前验证范围。')
    params={k:self.parameter(preset,k,'cutting_presets') for k in keys}
    if any(v is None for v in params.values()):return None
    if not params['step_over_percent'].is_integer():reject('unsupported_capability','step_over_percent','当前原生 CAM 步距百分比要求整数，不自动取整。')
    if not 0<params['path_tolerance_mm']<=.001:reject('unsupported_capability','path_tolerance_mm','当前 CAM 曲线精度须显式给定且不大于 0.001 mm。')
    if any(params[k]<=0 for k in keys[:5]) or params['step_over_percent']>50 or params['step_down_mm']>d:reject('conflicting_information','cutting_presets','进给 / 分层 / 步距不满足当前约束。')
    if not params['clearance_height_mm']>=params['safe_height_mm']>hi[2]:reject('conflicting_information','heights','安全高度必须高于来料。')
    for key in keys:
        if key in constraints and number(constraints[key],key)!=params[key]:reject('conflicting_information','constraints/'+key,'卡片硬约束与资源参数冲突。')
    reqids=set()
    expected={'profile_xy':{k:f[k] for k in ['center_xy_mm','width_mm','height_mm','corner_radius_mm']},'pocket_depth':d,'floor_geometry':'flat'}
    for req in list_at(requirements.get('items'),'requirements/items'):
        rid=req.get('id')
        if not isinstance(rid,str) or not rid or rid in reqids:raise InputError('invalid_input','requirements/id','要求编号缺失或重复。')
        reqids.add(rid)
        if req.get('feature_id')!=fid:reject('conflicting_information','requirements/feature_id','要求未绑定到目标槽。')
        if req.get('kind') in expected and req.get('nominal')!=expected[req['kind']]:reject('conflicting_information','requirements/nominal','要求名义值与 CAD 不一致。')
        self.result['requirement_readiness'].append({'id':rid,'acceptance_defined':req.get('acceptance') is not None,'verification_status':'not_verified'})
        if req.get('acceptance') is None:self.issue('missing_information','requirements/'+rid+'/acceptance','制造验收依据待补。',severity='acceptance')
    simulation=object_at(resources.get('simulation'),'simulation');resolution=number(simulation.get('resolution_mm'),'resolution_mm',True)
    if set(simulation)-{'resolution_mm','basis'} or not .01<=resolution<=.05:reject('unsupported_capability','simulation','精度范围或额外配置尚未支持。')
    text_basis(simulation.get('basis'),'simulation/basis')
    self.issue('unverified_scope','review','软件参考；整卡 / 工艺 / 生产批准均未完成。',severity='notice',owner='process_engineer')
    return {'schema':'genlaw.execution-plan.v4','capability':CAPABILITY,'input_id':manifest['id'],'mode':manifest['mode'],
      'source_bindings':dict(self.files),'cad_ref':dict(self.files['cad']),'cad_observation_ref':dict(self.files['geometry_observation']),
      'operations':[{'operation_id':op['id'],'step_id':sid,'type':'pocket_milling','feature_id':fid,'geometry':f,'final_z_mm':hi[2]-d}],
      'stock':stock,'tool':tool,'setup':setup,'controller':controller,'parameters':params,'strategy':{k:preset[k] for k in ['clearing_pattern','cut_mode','start_at','entry','keep_tool_down','radial_allowance_mm','axial_allowance_mm']},
      'parameter_provenance':{k:{'kind':'resource_preset','basis':preset[k]['basis']} for k in keys},'simulation':simulation,
      'scope':scope,'requirements_ref':dict(self.files['requirements']),'production_approved':False,'whole_process_card_complete':False}
