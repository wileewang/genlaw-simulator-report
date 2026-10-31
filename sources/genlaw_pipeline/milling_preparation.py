"""Shared source, resource and operation checks for explicitly registered planar features."""
from .preparation import object_at,list_at,vector,number,load_json,digest,InputError
from .milling_features import contract_for,validate_feature,required_nominals


def prepare_milling(self,manifest):
    contract=contract_for(manifest);version=contract['version'];capability=manifest['capability'];reference=contract['reference'];operation=contract['operation']
    self.result.update(adapter=contract['adapter'],input_id=manifest.get('id'),input_manifest_sha256=digest(self.path))
    def reject(code,field,message):self.issue(code,field,message)
    def text_basis(value,field):
        if not isinstance(value,str) or not value.strip():reject('missing_information',field,'需要非空的文字依据。')
    if not isinstance(manifest.get('id'),str) or not manifest['id'].strip():raise InputError('invalid_input','id','需要非空输入编号。')
    if manifest.get('mode')!='software_geometry_reference':reject('unsupported_capability','mode','当前仅验证软件几何。')
    refs=object_at(manifest.get('files'),'files');needed=['process_plan','cad','geometry_observation','requirements','resources']
    if any(k not in refs for k in needed):raise InputError('missing_information','files','缺少卡 / CAD / 要求 / 资源。')
    paths={k:self.bind(k,v) for k,v in refs.items()}
    self.documents=object_at(manifest.get('source_documents'),'source_documents')
    if not self.documents or any(k not in paths for k in self.documents.values()):reject('missing_information','source_documents','需要可追溯的原始工艺卡。')
    values={k:object_at(load_json(paths[k]),k) for k in needed if k!='cad'}
    card,geo,requirements,resources=(values[k] for k in ['process_plan','geometry_observation','requirements','resources'])
    schemas={'process_plan':'genlaw.process-plan.v1','geometry_observation':'genlaw.cad-observation.'+version,'requirements':'genlaw.requirements.v1','resources':'genlaw.preparation-resources.v1'}
    for key,value in values.items():
        if value.get('schema')!=schemas[key]:reject('unsupported_capability',key+'/schema','资料版本不支持。')
        if key!='resources' and value.get('cad_sha256')!=self.files['cad']['sha256']:reject('conflicting_information',key+'/cad_sha256','CAD 版本不一致。')
        if key!='process_plan' and value.get('units')!='mm':reject('unsupported_capability',key+'/units','需要明确毫米单位。')
    if object_at(card.get('transcription'),'transcription').get('status')!='checked_against_source':reject('missing_information','transcription','结构化卡须与来源核对。')
    for name in list_at(card.get('external_documents'),'external_documents'):
        if name not in self.documents:reject('missing_information','external_documents','外部引用资料未提供。')
    if geo.get('reference_class')!=reference:reject('unsupported_capability','reference_class','该几何类型尚未实现。')
    features=list_at(geo.get('features'),'features')
    if len(features)!=1:raise InputError('unsupported_capability','features','此适配器先验证单个已声明的铣削特征。')
    f=object_at(features[0],'features/0');fid=f.get('id')
    if not isinstance(fid,str) or not fid:raise InputError('invalid_input','feature/id','需要稳定特征编号。')
    bounds=object_at(geo.get('bounds_mm'),'bounds');lo,hi,d=validate_feature(contract,f,bounds)
    provenance=object_at(geo.get('provenance'),'provenance');key=provenance.get('file_key')
    if geo.get('observation_method')!='prior_native_cad_observation' or key not in paths or provenance.get('sha256')!=self.files[key]['sha256']:
        reject('missing_information','provenance','原生 CAD 观察缺失或未绑定。')
    else:
        prior=load_json(paths[key])
        if any(prior.get(k)!=v for k,v in {'sha256':self.files['cad']['sha256'],'valid_single_solid':True,'reference_class':reference,'bounds':bounds,'features':features}.items()):reject('conflicting_information','provenance','特征包与原生观察不一致。')
    stock=object_at(resources.get('stock'),'stock');material=self.require(stock,'material_label','stock/material_label')
    text_basis(material,'stock/material_label')
    stock_bounds=object_at(stock.get('bounds_mm'),'stock/bounds_mm')
    stock_lo,stock_hi=[vector(stock_bounds.get(key),'stock/bounds_mm/'+key) for key in ['min','max']]
    facing=contract['kind']=='planar_top'
    helical=contract['kind']=='through_hole'
    external=contract['kind']=='capsule_outline'
    chamfer=contract['kind']=='top_perimeter_chamfer_45'
    if stock.get('shape')!='box' or any(a>=b for a,b in zip(stock_lo,stock_hi)):
        raise InputError('unsupported_capability','stock','来料须为尺寸为正的矩形毛坯。')
    if facing:
        if stock_lo!=lo or stock_hi[:2]!=hi[:2] or stock_hi[2]<=hi[2]:
            raise InputError('unsupported_capability','stock','本面铣入口要求 XY 外形及底面相同、来料顶面高于目标；不得从成品 CAD 推断来料。')
        d=stock_hi[2]-hi[2]
        final_z=hi[2]
    else:
        same_bounds=all(abs(stock_bounds[k][i]-bounds[k][i])<=1e-8 for k in ['min','max'] for i in range(3)) if external or chamfer else stock_bounds==bounds
        if not same_bounds:reject('unsupported_capability','stock','当前要求与目标外包框等大的显式矩形来料。')
        final_z=hi[2]-d
    scope=object_at(manifest.get('scope'),'scope');selected=list_at(scope.get('selected_steps'),'selected_steps')
    if len(selected)!=1 or not isinstance(selected[0],str):raise InputError('unsupported_capability','selected_steps','单个铣削特征须对应一个加工工步。')
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
    if step.get('type')!=operation or step.get('feature_id')!=fid:reject('conflicting_information',sid,'工步类型或目标特征不一致。')
    if any(x.get('conditions') not in (None,[]) for x in [card,op,step]):reject('unsupported_capability','conditions','不能隐式选择条件分支。')
    declarations=object_at(scope.get('declared_preconditions'),'declared_preconditions')
    order=list(steps)
    for dep in list_at(step.get('depends_on'),'depends_on'):
        if dep not in steps or order.index(dep)>=order.index(sid):reject('conflicting_information','depends_on','前置工步无效或次序错误。');continue
        if not isinstance(declarations.get(dep),str) or not declarations[dep].strip():reject('missing_information','declared_preconditions','需要明确来料 / 装夹前提。')
        if any(x.get('conditions') not in (None,[]) for x in steps[dep]):reject('unsupported_capability','depends_on','前置条件分支未实现。')
    keys=contract.get('parameter_keys',['spindle_rpm','feed_mm_min','plunge_mm_min','step_down_mm','step_over_percent','safe_height_mm','clearance_height_mm','path_tolerance_mm'])+contract['extra_parameters']
    constraints=object_at(step.get('constraints'),'constraints')
    surface_constraint=contract.get('surface_constraint','bottom_shape')
    if set(constraints)-set(keys)-set(contract['dimensions'])-{'tool_id',surface_constraint}:reject('unsupported_capability','constraints','未知硬约束不能忽略。')
    for key in contract['dimensions']:
        value=f[key]
        given=self.require(constraints,key,'constraints/'+key)
        if given is not None and (given if key=='side' else number(given,key,key!='shoulder_coordinate_mm'))!=value:reject('conflicting_information','constraints/'+key,'工艺卡名义尺寸与 CAD 不一致。')
    if constraints.get(surface_constraint)!=contract.get('surface_value','flat'):reject('conflicting_information',surface_constraint,'目标面的形状或孔的终止条件与该能力不符。')
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
    tool_fields={'id','kind','center_cutting','diameter_mm','cutting_length_mm','length_mm','basis'}|({'tip_diameter_mm','included_angle_degrees'} if chamfer else set())
    if set(tool)-tool_fields:reject('unsupported_capability','tool','额外刀具形状参数尚未建模。')
    length=number(tool.get('length_mm'),'tool/length_mm',True);flute=number(tool.get('cutting_length_mm'),'cutting_length_mm',True)
    if chamfer:
        tip=number(tool.get('tip_diameter_mm'),'tool/tip_diameter_mm',True)
        if (tool.get('kind')!='chamfer_frustum' or tool.get('center_cutting') is not False
            or number(tool.get('included_angle_degrees'),'tool/included_angle_degrees')!=90 or not tip<diameter
            or abs(flute-(diameter-tip)/2)>1e-8):
            reject('unsupported_capability','tool','倒角刀须显式声明 90° 截锥、非零平刃刀尖及一致的锥段高度。')
    elif tool.get('kind')!='flat_endmill' or tool.get('center_cutting') is not True:reject('unsupported_capability','tool','当前要求可中心切削的平底铣刀。')
    if (contract['kind']=='rounded_rectangular_pocket' and diameter/2>f['corner_radius_mm']) or flute<d or length<flute:reject('conflicting_information','tool','刀具不能覆盖轮廓 / 加工深度，或长度冲突。')
    if helical and diameter>=f['diameter_mm']:reject('conflicting_information','tool','螺旋铣孔必须使用小于孔径的铣刀。')
    presets=[p for p in list_at(resources.get('cutting_presets'),'cutting_presets') if p.get('operation')==operation and p.get('tool_id')==tool_id and p.get('material_label')==material]
    if len(presets)!=1:raise InputError('missing_information' if not presets else 'conflicting_information','cutting_presets','需要唯一有依据的参数组合。')
    preset=presets[0]
    if set(preset)-set(keys)-{'id','operation','tool_id','material_label','feed_unit','speed_unit','clearing_pattern','cut_mode','start_at','entry','keep_tool_down','radial_allowance_mm','axial_allowance_mm'}-set(contract['extra_strategy']):reject('unsupported_capability','cutting_presets','额外加工策略不能忽略。')
    if preset.get('feed_unit')!='mm/min' or preset.get('speed_unit')!='rpm':reject('unsupported_capability','cutting_presets/units','需要 mm/min 与 rpm。')
    for key,value in {'clearing_pattern':'Offset','cut_mode':'Climb','start_at':'Center','entry':'vertical_center_cutting','keep_tool_down':False,'radial_allowance_mm':0,'axial_allowance_mm':0,**contract.get('strategy_defaults',{}),**contract['extra_strategy']}.items():
        if preset.get(key)!=value:reject('unsupported_capability','cutting_presets/'+key,'该策略 / 余量须显式给定且属于当前验证范围。')
    params={k:self.parameter(preset,k,'cutting_presets') for k in keys}
    if any(v is None for v in params.values()):return None
    if 'step_over_percent' in params and not params['step_over_percent'].is_integer():reject('unsupported_capability','step_over_percent','当前原生 CAM 步距百分比要求整数，不自动取整。')
    if not 0<params['path_tolerance_mm']<=.001:reject('unsupported_capability','path_tolerance_mm','当前 CAM 曲线精度须显式给定且不大于 0.001 mm。')
    positive=['spindle_rpm','feed_mm_min','plunge_mm_min']+[k for k in ['step_down_mm','step_over_percent'] if k in params]
    if any(params[k]<=0 for k in positive) or params.get('step_over_percent',0)>50 or params.get('step_down_mm',0)>d:reject('conflicting_information','cutting_presets','进给 / 分层 / 步距不满足当前约束。')
    if not params['clearance_height_mm']>=params['safe_height_mm']>stock_hi[2]:reject('conflicting_information','heights','安全高度必须高于来料。')
    if helical:
        from .helix_geometry import clearing_radii
        if not 0<params['entry_clearance_mm']<params['safe_height_mm']-hi[2] or not 0<params['breakthrough_mm']<=diameter:
            reject('conflicting_information','helical_heights','需显式正入刀间隙及不大于刀径的贯穿余量。')
        final_z=lo[2]-params['breakthrough_mm']
        if flute<hi[2]-final_z:reject('conflicting_information','cutting_length_mm','有效刃长不足以覆盖贯穿余量。')
        if any(i['severity']=='blocking' for i in self.issues):return None
        clearing_radii(f['diameter_mm'],diameter,params['step_over_percent'])
    if external:
        import math
        if not 0<params['breakthrough_mm']<=diameter:
            reject('conflicting_information','breakthrough_mm','外轮廓贯穿余量须显式为正且不大于刀径。')
        final_z=lo[2]-params['breakthrough_mm']
        if flute<stock_hi[2]-final_z:reject('conflicting_information','cutting_length_mm','有效刃长必须覆盖来料及贯穿余量。')
        # One outside pass must remove every corner, not merely detach the part.
        if diameter<f['width_mm']/2*(math.sqrt(2)-1)+2*params['path_tolerance_mm']:
            reject('unsupported_capability','profile_passes','单圈扫掠不能清除全部外侧余料，需要另行实现粗加工或多径向走刀。')
    if external or chamfer:
        startup=object_at(setup.get('startup'),'setup/startup');text_basis(startup.get('basis'),'setup/startup/basis')
        expected={'position_mm':[0,0,0],'tool':'unloaded','spindle':'stopped','retract_before_tool_selection_z_mm':params['clearance_height_mm']}
        if set(startup)!=set(expected)|{'basis'} or any(startup.get(k)!=v for k,v in expected.items()):
            reject('unsupported_capability','setup/startup','须明确原点空刀停转启动，并先空刀移到清空高度再选刀。')
    if chamfer:
        from .freecad_deburr import checked_formal_backend
        try:backend=checked_formal_backend(resources.get('cam_backend'))
        except ValueError as error:reject('conflicting_information','cam_backend',str(error));backend=None
        extra=params['extra_depth_mm'];final_z=hi[2]-d-extra
        if extra<0 or d+extra+params['path_tolerance_mm']>=flute:
            reject('conflicting_information','extra_depth_mm','锥段必须覆盖整段倒角；刀杆起点须高于毛坯顶面。')
    if contract['kind']=='edge_step' and not diameter/2<=params['edge_extension_mm']<=2*diameter:reject('unsupported_capability','edge_extension_mm','开口延伸须显式给出，当前范围为半刀径至两刀径。')
    if contract['kind']=='planar_floor':
        extension=params['edge_extension_mm']
        if (f['open_edge_groups'] and not diameter/2<=extension<=2*diameter) or (not f['open_edge_groups'] and extension!=0):
            reject('conflicting_information','edge_extension_mm','封闭区域须显式零延伸；开口边组须指定半刀径至两刀径的延伸。')
    for key in keys:
        if key in constraints and number(constraints[key],key)!=params[key]:reject('conflicting_information','constraints/'+key,'卡片硬约束与资源参数冲突。')
    reqids=set()
    expected=required_nominals(contract,f)
    for req in list_at(requirements.get('items'),'requirements/items'):
        rid=req.get('id')
        if not isinstance(rid,str) or not rid or rid in reqids:raise InputError('invalid_input','requirements/id','要求编号缺失或重复。')
        reqids.add(rid)
        if req.get('feature_id')!=fid:reject('conflicting_information','requirements/feature_id','要求未绑定到目标特征。')
        if req.get('kind') in expected and req.get('nominal')!=expected[req['kind']]:reject('conflicting_information','requirements/nominal','要求名义值与 CAD 不一致。')
        self.result['requirement_readiness'].append({'id':rid,'acceptance_defined':req.get('acceptance') is not None,'verification_status':'not_verified'})
        if req.get('acceptance') is None:self.issue('missing_information','requirements/'+rid+'/acceptance','制造验收依据待补。',severity='acceptance')
    simulation=object_at(resources.get('simulation'),'simulation');resolution=number(simulation.get('resolution_mm'),'resolution_mm',True)
    if set(simulation)-{'resolution_mm','basis'} or not .01<=resolution<=.05:reject('unsupported_capability','simulation','精度范围或额外配置尚未支持。')
    text_basis(simulation.get('basis'),'simulation/basis')
    if chamfer and (d<8*resolution or min(hi[i]-lo[i]-2*d for i in range(2))<=8*resolution):
        reject('unsupported_capability','resolution_mm','当前截面测量要求倒角宽度及保留顶面至少覆盖八个网格单元。')
    self.issue('unverified_scope','review','软件参考；整卡 / 工艺 / 生产批准均未完成。',severity='notice',owner='process_engineer')
    if (external or chamfer) and any(i['severity']=='blocking' for i in self.issues):return None
    return {'schema':'genlaw.execution-plan.'+version,'capability':capability,'input_id':manifest['id'],'mode':manifest['mode'],
      'source_bindings':dict(self.files),'cad_ref':dict(self.files['cad']),'cad_observation_ref':dict(self.files['geometry_observation']),
      'operations':[{'operation_id':op['id'],'step_id':sid,'type':operation,'feature_id':fid,'geometry':f,'final_z_mm':final_z,
                     **({'removal_depth_mm':d} if facing else {}),
                     **({'hole_center_xy_mm':f['center_xy_mm'],'diameter_mm':f['diameter_mm'],'through':True,
                         'start_z_mm':hi[2]+params['entry_clearance_mm']} if helical else {})}],
      **({'target_bounds_mm':bounds} if facing or external or chamfer else {}),
      **({'cam_backend':backend} if chamfer else {}),
      'stock':stock,'tool':tool,'setup':setup,'controller':controller,'parameters':params,'strategy':{k:preset[k] for k in ['clearing_pattern','cut_mode','start_at','entry','keep_tool_down','radial_allowance_mm','axial_allowance_mm']+list(contract['extra_strategy'])},
      'parameter_provenance':{k:{'kind':'resource_preset','basis':preset[k]['basis']} for k in keys},'simulation':simulation,
      'scope':scope,'requirements_ref':dict(self.files['requirements']),'production_approved':False,'whole_process_card_complete':False}
