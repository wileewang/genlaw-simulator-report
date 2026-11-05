"""Explicit same-setup rough/finish intent, with separate stage oracles.

This adapter preserves the original process card and final CAD. It neither
invents manufacturing parameters nor treats the expected rough solid as stock.
"""
import copy
from pathlib import Path

from .preparation import Preparation, InputError, load_json, digest, object_at, list_at, number, vector
from .milling_features import CONTRACTS, validate_feature, required_nominals
from .pocket_geometry import POCKET

CAPABILITY = 'rounded_pocket_rough_finish_xy'
PARAMETERS = ('spindle_rpm','feed_mm_min','plunge_mm_min','step_down_mm','step_over_percent',
              'safe_height_mm','clearance_height_mm','path_tolerance_mm')
STRATEGY = {'clearing_pattern':'Offset','cut_mode':'Climb','start_at':'Center',
            'entry':'vertical_center_cutting','keep_tool_down':False}


def stage_feature(feature, allowance):
    a, b = allowance['radial'], allowance['axial']
    return {**feature,'width_mm':feature['width_mm']-2*a,'height_mm':feature['height_mm']-2*a,
            'corner_radius_mm':feature['corner_radius_mm']-a,'depth_mm':feature['depth_mm']-b}


def guard_plan(plan, stage):
    """Explicit geometric projection for the existing single-pocket NC guard."""
    op = {**stage, 'geometry':stage['expected_feature']}
    return {'schema':'genlaw.execution-plan.v4','capability':'rounded_pocket_xy','stock':plan['stock'],
            'tool':plan['tool'],'setup':plan['setup'],'controller':plan['controller'],
            'parameters':stage['parameters'],'strategy':{**STRATEGY,'radial_allowance_mm':0,'axial_allowance_mm':0},
            'simulation':plan['simulation'],'operations':[op]}


class RoughFinishPreparation(Preparation):
    def _prepare(self):
        m=object_at(load_json(self.path),'input')
        def require(condition, field, message, code='conflicting_information'):
            if not condition: raise InputError(code,field,message)
        require(m.get('schema')=='genlaw.rough-finish-input.v1' and m.get('capability')==CAPABILITY,
                'schema','Unsupported rough/finish input contract','unsupported_capability')
        require(isinstance(m.get('id'),str) and bool(m['id'].strip()),'id','Missing input ID','invalid_input')
        require(m.get('mode')=='software_geometry_reference','mode','Only explicit software reference is supported','unsupported_capability')
        self.result.update(adapter='genlaw.preparation.rough-finish.v1',input_id=m['id'],input_manifest_sha256=digest(self.path))
        refs=object_at(m.get('files'),'files')
        required={'process_plan','cad','native_reference','rough_cad','rough_reference','requirements','resources','paper_card','stage_policy'}
        require(required<=set(refs),'files','Missing card/CAD/resource/stage evidence','missing_information')
        paths={k:self.bind(k,v) for k,v in refs.items()}
        self.documents=object_at(m.get('source_documents'),'source_documents')
        require(bool(self.documents) and all(v in paths for v in self.documents.values()),'source_documents','Missing original source documents','missing_information')
        card=object_at(load_json(paths['process_plan']),'process_plan');ref=load_json(paths['native_reference']);rough_ref=load_json(paths['rough_reference'])
        resources=object_at(load_json(paths['resources']),'resources');requirements=load_json(paths['requirements']);policy=load_json(paths['stage_policy'])
        require(card.get('schema')=='genlaw.process-plan.v1','process_plan/schema','Unsupported process card','unsupported_capability')
        require(card.get('cad_sha256')==self.files['cad']['sha256'],'process_plan/cad_sha256','Card does not bind the original final CAD')
        transcription=object_at(card.get('transcription'),'transcription')
        checked=transcription.get('status')=='checked_against_source'
        generated=(card.get('origin')=='developer_authored_synthetic_paper_card' and transcription.get('status')=='generated_from_same_authoring_source')
        require(checked or generated,'transcription','Real cards require source-checked transcription','missing_information')
        for name in list_at(card.get('external_documents'),'external_documents'):
            require(name in self.documents,'external_documents','Referenced source document missing','missing_information')
        for value,key in [(ref,'cad'),(rough_ref,'rough_cad')]:
            require(value.get('sha256')==self.files[key]['sha256'] and value.get('valid_single_solid') is True
                    and value.get('reference_class')==POCKET,'native_reference','Original native observations disagree')
        require(len(list_at(ref.get('features'),'features'))==1,'features','First contract supports one pocket','unsupported_capability')
        f=ref['features'][0];lo,hi,depth=validate_feature(CONTRACTS['rounded_pocket_xy'],f,ref['bounds'])
        require(isinstance(f.get('id'),str) and bool(f['id']),'feature/id','Missing feature ID','invalid_input')
        require(resources.get('schema')=='genlaw.preparation-resources.v1' and resources.get('units')=='mm','resources/schema','Explicit metric resource contract required')
        stock=object_at(resources.get('stock'),'stock')
        require(stock.get('shape')=='box' and stock.get('bounds_mm')==ref['bounds'],'stock','Stock must be the explicitly declared full rectangular blank')
        require(isinstance(stock.get('material_label'),str) and bool(stock['material_label'].strip()),'stock/material_label','Missing material label','missing_information')
        machine=object_at(resources.get('machine'),'machine')
        require(machine.get('kind')=='generic_XYZ_reference' and machine.get('axes')==['X','Y','Z'],'machine','Current scope is XYZ','unsupported_capability')
        setup=object_at(resources.get('setup'),'setup')
        for k,v in {'origin_mm':[0,0,0],'rotation_degrees':[0,0,0],'tool_length_offset_mm':0,'work_offset':'G54','fixture_mode':'unmodelled_explicitly_excluded'}.items():
            require(setup.get(k)==v,'setup/'+k,'Undeclared setup semantics','unsupported_capability')
        require(isinstance(setup.get('basis'),str) and bool(setup['basis'].strip()),'setup/basis','Missing setup basis','missing_information')
        controller=object_at(resources.get('controller'),'controller')
        require(controller.get('profile')=='reference_NC_subset' and controller.get('arc_centers')=='incremental_IJK','controller','Unsupported controller dialect','unsupported_capability')
        tools=list_at(resources.get('tools'),'tools')
        require(len(tools)==1,'tools','First contract uses one explicit tool','unsupported_capability');tool=tools[0]
        require(set(tool)=={'id','kind','center_cutting','diameter_mm','cutting_length_mm','length_mm','basis'},'tool','Unknown tool geometry must not be ignored','unsupported_capability')
        require(tool.get('kind')=='flat_endmill' and tool.get('center_cutting') is True,'tool','Center-cutting flat endmill required')
        diameter=number(tool['diameter_mm'],'tool/diameter_mm',True);flute=number(tool['cutting_length_mm'],'tool/cutting_length_mm',True)
        require(number(tool['length_mm'],'tool/length_mm',True)>=flute>=depth,'tool','Insufficient tool reach')
        require(bool(tool['basis']) and isinstance(tool['id'],str) and bool(tool['id']),'tool/basis','Missing tool ID/basis','missing_information')
        scope=object_at(m.get('scope'),'scope');selected=list_at(scope.get('selected_steps'),'selected_steps')
        require(len(selected)==2 and len(set(selected))==2,'selected_steps','Two distinct rough and finish steps are required')
        require(scope.get('whole_process_card') is False,'scope','Selected steps do not establish whole-card completion')
        require(set(['fixture_collision','holder_collision','material_physics','surface_roughness','whole_process_completion'])<=set(list_at(scope.get('excluded_checks'),'excluded_checks')),
                'excluded_checks','Unverified manufacturing scope must remain explicit')
        steps={}; op_ids=set(); sequence=[]
        require(card.get('conditions') in (None,[]),'conditions','Conditional branches not supported','unsupported_capability')
        for op in list_at(card.get('operations'),'operations'):
            oid=op.get('id');require(isinstance(oid,str) and bool(oid) and oid not in op_ids,'operation/id','Duplicate/missing operation ID','invalid_input');op_ids.add(oid)
            require(op.get('conditions') in (None,[]),'conditions','Conditional operation not supported','unsupported_capability')
            for step in list_at(op.get('steps'),'steps'):
                sid=step.get('id');require(isinstance(sid,str) and bool(sid) and sid not in steps,'step/id','Duplicate/missing step ID','invalid_input')
                self.source(step.get('source'),sid+'/source')
                require(isinstance(step.get('text'),str) and bool(step['text'].strip()),sid+'/text','Original step intent required','missing_information')
                steps[sid]=(oid,step);sequence.append(sid)
                self.coverage.append({'operation_id':oid,'step_id':sid,'type':step.get('type'),'status':'selected_for_preparation' if sid in selected else 'not_verified'})
        require(all(s in steps for s in selected) and [s for s in sequence if s in selected]==selected,'selected_steps','Selected step order differs from original card')
        declarations=object_at(scope.get('declared_preconditions'),'declared_preconditions')
        presets=list_at(resources.get('cutting_presets'),'cutting_presets');preset_ids=[p.get('id') for p in presets]
        require(len(preset_ids)==len(set(preset_ids)) and all(isinstance(i,str) and i for i in preset_ids),'cutting_presets','Preset IDs missing or duplicated')
        simulation=object_at(resources.get('simulation'),'simulation');resolution=number(simulation.get('resolution_mm'),'simulation/resolution_mm',True)
        require(set(simulation)=={'resolution_mm','basis'} and .01<=resolution<=.05 and bool(simulation['basis']),'simulation','Explicit bounded resolution required')
        operations=[]
        for index,sid in enumerate(selected):
            oid,step=steps[sid];stage=('rough','finish')[index]
            require(step.get('stage')==stage and step.get('type')=='pocket_milling' and step.get('feature_id')==f['id'],sid,'Declared rough/finish role, feature or order differs')
            require(step.get('conditions') in (None,[]),sid+'/conditions','Conditional step not supported','unsupported_capability')
            deps=list_at(step.get('depends_on'),'depends_on')
            require(index==0 or selected[0] in deps,'depends_on','Finish must explicitly depend on roughing')
            for dep in deps:
                require(dep in steps and sequence.index(dep)<sequence.index(sid),'depends_on','Invalid prerequisite or reversed dependency')
                if dep not in selected:require(isinstance(declarations.get(dep),str) and bool(declarations[dep].strip()),'declared_preconditions','Missing incoming/setup prerequisite','missing_information')
            constraints=object_at(step.get('constraints'),'constraints')
            wanted={k:f[k] for k in ('width_mm','height_mm','corner_radius_mm','depth_mm')};wanted.update(bottom_shape='flat',tool_id=tool['id'])
            require(constraints==wanted,'constraints','Original final dimensions/tool must agree; unknown hard constraints cannot be discarded')
            allowance=object_at(step.get('allowance_mm'),'allowance_mm')
            require(set(allowance)=={'radial','axial'},'allowance_mm','Both allowances must be explicit','missing_information')
            allowance={k:number(v,'allowance/'+k) for k,v in allowance.items()}
            require(all(v>=0 for v in allowance.values()),'allowance_mm','Negative allowance would overcut')
            require((any(v>0 for v in allowance.values()) if index==0 else all(v==0 for v in allowance.values())),'allowance_mm','Roughing must leave material; finish must reach zero allowance')
            expected=stage_feature(f,allowance);validate_feature(CONTRACTS['rounded_pocket_xy'],expected,ref['bounds'])
            require(expected['corner_radius_mm']>=diameter/2,'allowance_mm','Tool cannot reach the reduced rough pocket')
            choices=[p for p in presets if p['id']==step.get('preset_id')]
            require(len(choices)==1,'preset_id','Explicit preset missing','missing_information');preset=choices[0]
            preset_fields=set(PARAMETERS)|set(STRATEGY)|{'id','operation','tool_id','material_label','feed_unit','speed_unit','radial_allowance_mm','axial_allowance_mm'}
            require(not set(preset)-preset_fields,'cutting_presets','Unknown preset strategy must not be ignored','unsupported_capability')
            require(not preset_fields-set(preset),'cutting_presets','Required cutting parameters are missing','missing_information')
            require(all(preset.get(k)==v for k,v in STRATEGY.items()),'cutting_presets/strategy','Unsupported native strategy','unsupported_capability')
            require(preset.get('operation')=='pocket_milling' and preset.get('tool_id')==tool['id'] and preset.get('material_label')==stock['material_label'] and preset.get('feed_unit')=='mm/min' and preset.get('speed_unit')=='rpm','cutting_presets','Preset operation/tool/material/units mismatch')
            require(preset.get('radial_allowance_mm')==allowance['radial'] and preset.get('axial_allowance_mm')==allowance['axial'],'allowance_mm','Card and preset allowances conflict')
            params={k:self.parameter(preset,k,'cutting_presets') for k in PARAMETERS}
            if any(v is None for v in params.values()):return None
            require(all(params[k]>0 for k in PARAMETERS) and params['step_over_percent'].is_integer() and params['step_over_percent']<=50 and params['step_down_mm']<=expected['depth_mm'] and params['path_tolerance_mm']<=.001,'parameters','Parameters outside declared range')
            require(params['clearance_height_mm']>=params['safe_height_mm']>hi[2],'heights','Safety planes must clear original stock')
            operations.append({'operation_id':oid,'step_id':sid,'stage':stage,'type':'pocket_milling','feature_id':f['id'],
                'geometry':f,'expected_feature':expected,'allowance_mm':allowance,'parameters':params,
                'final_z_mm':hi[2]-expected['depth_mm'],'preset_id':preset['id']})
        require(rough_ref.get('bounds')==ref['bounds'] and rough_ref.get('features')==[operations[0]['expected_feature']],'rough_reference','Pre-NC rough oracle differs from declared allowance')
        require(requirements.get('schema')=='genlaw.requirements.v1' and requirements.get('units')=='mm' and requirements.get('cad_sha256')==self.files['cad']['sha256'],'requirements','Requirements not bound to original final CAD')
        expected_nominals=required_nominals(CONTRACTS['rounded_pocket_xy'],f);seen=set();kinds=set()
        for req in list_at(requirements.get('items'),'requirements/items'):
            rid=req.get('id');require(isinstance(rid,str) and bool(rid) and rid not in seen,'requirements/id','Invalid requirement ID');seen.add(rid)
            require(req.get('feature_id')==f['id'],'requirements/feature_id','Unbound requirement')
            kind=req.get('kind');kinds.add(kind)
            if kind in expected_nominals:require(req.get('nominal')==expected_nominals[kind],'requirements/nominal','Requirement differs from original CAD')
            self.result['requirement_readiness'].append({'id':rid,'acceptance_defined':req.get('acceptance') is not None,'verification_status':'not_verified'})
            self.issue('missing_information','requirements/'+rid,'Manufacturing acceptance remains separate from software stage criteria.',severity='acceptance')
        require(set(expected_nominals)<=kinds,'requirements','Missing final geometry requirements','missing_information')
        require(policy.get('schema')=='genlaw.rough-finish-policy.v1' and policy.get('order')==selected,'stage_policy','Independent stage/order criteria missing')
        for stage,key in [('rough','rough_cad'),('finish','cad')]:
            criteria=object_at(policy.get('stages',{}).get(stage),'stage_policy/'+stage)
            require(criteria.get('cad_sha256')==self.files[key]['sha256'],'stage_policy/cad','Criterion target differs from archived stage CAD')
            require(set(criteria)=={'cad_sha256','profile_xy','pocket_depth','floor_geometry'},'stage_policy','Incomplete or unsupported stage criteria')
        self.issue('unsupported_capability','scope','Same-tool XYZ rough/finish software verification; not whole-card or production approval.',severity='scope')
        return {'schema':'genlaw.execution-plan.rough-finish.v1','capability':CAPABILITY,'input_id':m['id'],'mode':m['mode'],
            'source_bindings':dict(self.files),'cad_ref':dict(self.files['cad']),'rough_cad_ref':dict(self.files['rough_cad']),
            'operations':operations,'stock':stock,'tool':tool,'setup':setup,'controller':controller,'simulation':simulation,
            'requirements_ref':dict(self.files['requirements']),'scope':scope,'production_approved':False,'whole_process_card_complete':False}


def prepare(path):
    return RoughFinishPreparation(path).run()


def checked_plan(path, result):
    from .preparation import canonical
    current=prepare(path);saved=load_json(Path(result))
    if current['status']!='ready_for_declared_scope':raise ValueError('Rough/finish preparation blocked: '+current['status'])
    for key in ('input_manifest_sha256','execution_plan_sha256','execution_plan','input_bindings'):
        if canonical(current.get(key))!=canonical(saved.get(key)):raise ValueError('Rough/finish binding changed: '+key)
    return current['execution_plan']
