"""Archive the first sequential milling cards, geometry and resources before CAM."""
import argparse, copy, json, re, shutil, sys, zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.preparation import digest
from genlaw_pipeline.sequence_geometry import CAPABILITY, REFERENCE, KINDS
from genlaw_pipeline.milling_features import CONTRACTS, required_nominals
from genlaw_pipeline.run import bounded, write, FREECAD, workspace_environment
from tools.build_feature_inputs import policy
from genlaw_pipeline.profile_sequence_geometry import CAPABILITY as PROFILED,REFERENCE as PROFILED_REFERENCE,feature_nominals
from genlaw_pipeline.pocket_sequence_geometry import CAPABILITY as POCKETED, REFERENCE as POCKETED_REFERENCE, contract_for_feature as pocket_contract, feature_nominals as pocket_nominals


def specifications():
    cases=[]
    for key,name,centers,order,shift,two in [
        ('baseline','同刀：面铣后铣孔',[(3,2)],['P1','H1'],0,False),
        ('two-holes','按卡片顺序：先 H2 后 H1',[(2,2),(5.5,4)],['P1','H2','H1'],0,False),
        ('tool-change','换刀：φ2 面铣 → φ1 铣孔',[(3,2)],['P1','H1'],0,True),
        ('shifted-z','保留 CAD 的非零 Z 坐标',[(3,2)],['P1','H1'],1,False)]:
        cases.append({'id':key,'name':name,'stock_bounds_mm':{'min':[0,0,-1.5+shift],'max':[8,6,shift]},
            'target_bounds_mm':{'min':[0,0,-1.5+shift],'max':[8,6,-.5+shift]},'resolution_mm':.025,
            'holes':[{'id':f'H{i+1}','kind':'through_hole','axis':[0,0,1],'center_xy_mm':list(xy),'diameter_mm':2}
                     for i,xy in enumerate(centers)],'order':order,'two_tools':two})
    return {'schema':'genlaw.sequence-fixtures.v1','stage':'s4-sequence','cases':cases,
            'selection':'One original stock, ordered native facing and helical milling; same-tool, two-hole order, tool change and CAD-Z variants.',
            'production_approved':False,'not_yet_supported':['nonzero_work_offsets','nonzero_tool_length_offsets','fixtures','multiple_setups','five_axes']}


def paper(path,spec,card,resources):
    profiled=any(f['kind']=='capsule_outline' for f in spec['features'])
    pocket=spec.get('pocket')
    framed=resources['setup'].get('schema')=='genlaw.setup.xyz-translation.v1'
    setup=resources['setup']
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.pagesizes import A4
    pdfmetrics.registerFont(TTFont('SeqLatin','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
    pdfmetrics.registerFont(TTFont('SeqCJK','/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf'))
    c=canvas.Canvas(str(path),pagesize=A4);c.setTitle(card['id']+' / 顺序铣削软件验证工艺卡')
    def font(ch):return 'SeqLatin' if ord(ch) in pdfmetrics.getFont('SeqLatin').face.charToGlyph else 'SeqCJK'
    def width(s,size):return sum(pdfmetrics.stringWidth(ch,font(ch),size) for ch in s)
    def text(x,y,s,size=10):
        for ch in s:c.setFont(font(ch),size);c.drawString(x,y,ch);x+=pdfmetrics.stringWidth(ch,font(ch),size)
    def lines(x,y,s,w=470,size=10):
        line=''
        for token in re.findall(r'[A-Za-z0-9_.+°/×()%-]+|.',s):
            if line and width(line+token,size)>w and token not in '，。、；：':text(x,y,line,size);y-=16;line=''
            line+=token
        if line:text(x,y,line,size);y-=16
        return y
    lo,hi=[spec['target_bounds_mm'][k] for k in ['min','max']]; raw=spec['stock_bounds_mm']['max'][2]
    text(42,792,'GenLaw 顺序铣削软件验证工艺卡',18);text(42,765,card['id'],10)
    text(42,739,spec['name'],12);text(42,714,spec.get('card_origin_note','合成验证件 · 工艺师傅审阅 / 生产批准：待定'))
    stock_dims=[b-a for a,b in zip(spec['stock_bounds_mm']['min'],spec['stock_bounds_mm']['max'])]
    text(42,682,'材料标签：'+resources['stock']['material_label']+'；来料 '+' × '.join(f'{v:g}' for v in stock_dims)+f' mm；成品厚 {hi[2]-lo[2]:g} mm')
    text(42,658,f'原 CAD：底面 Z{lo[2]:g}；来料顶面 Z{raw:g} → 成品顶面 Z{hi[2]:g}')
    ox,oy=70,435;scale=min(260/(hi[0]-lo[0]),170/(hi[1]-lo[1]));c.setStrokeColorRGB(.2,.35,.5)
    if profiled:c.roundRect(ox,oy,(hi[0]-lo[0])*scale,(hi[1]-lo[1])*scale,spec['width_mm']/2*scale)
    else:c.rect(ox,oy,(hi[0]-lo[0])*scale,(hi[1]-lo[1])*scale)
    if pocket:
        px,py=pocket['center_xy_mm'];pw,ph=pocket['width_mm'],pocket['height_mm']
        c.roundRect(ox+(px-pw/2-lo[0])*scale,oy+(py-ph/2-lo[1])*scale,pw*scale,ph*scale,pocket['corner_radius_mm']*scale)
        text(ox+(px-pw/2-lo[0])*scale+5,oy+(py+ph/2-lo[1])*scale-14,'K1')
    for f in spec['holes']:
        x,y=f['center_xy_mm'];c.circle(ox+(x-lo[0])*scale,oy+(y-lo[1])*scale,f['diameter_mm']/2*scale)
        text(ox+(x-lo[0])*scale+4,oy+(y-lo[1])*scale+4,f['id'])
    text(345,578,'孔径 / 孔心（mm）');y=550
    for f in spec['holes']:
        text(345,y,f"{f['id']}: φ{f['diameter_mm']:g}");y-=17
        text(345,y,f"({f['center_xy_mm'][0]:g}, {f['center_xy_mm'][1]:g})");y-=24
    text(70,405,'原 CAD 顶视示意，按页面缩放；标注单位 mm。',9)
    if pocket:text(70,384,f"K1：{pocket['width_mm']:g} × {pocket['height_mm']:g}，R{pocket['corner_radius_mm']:g}；深 {pocket['depth_mm']:g}，底厚 {hi[2]-lo[2]-pocket['depth_mm']:g} mm。",9)
    y=lines(42,361,'加工顺序：'+' → '.join(spec['order'])+'。P1 面铣厚度；'+('C1 铣圆头外形；' if profiled else 'K1 铣型腔；' if pocket else '')+'H 为螺旋铣孔。各工步共用原 CAD 坐标及同一块剩料。',470,11)-14
    coordinate_note=(f"{setup['work_offset']}：工件零点在原 CAD 中为 {setup['work_origin_cad_mm']}；材料原点在机床中为 {setup['material_origin_machine_mm']}；控制器零点为 {setup['work_offset_origin_machine_mm']} mm。程序坐标 = 原 CAD 坐标 − 工件零点；尺寸及高度标注仍用原 CAD。XYZ 无旋转、无刀长补偿；夹具、刀柄及材料物理不在本次仿真内。" if framed else 'G54 零变换，刀长补偿为零；Z 轴向上。非零 CAD 高度不代表已支持工件偏置。通用 XYZ 参考控制器；夹具、刀柄、材料物理不在本次仿真内。')
    y=lines(42,y,coordinate_note)-14
    lines(42,y,'目标 CAD、工艺卡、刀具表及逐工步预期形状均在 NC 前冻结。逐工步预期 CAD 只作比对，不用作仿真来料。')
    text(42,58,'第 1 / 3 页 · 软件开发输入，不作现场加工批准',9);c.showPage()
    text(42,792,'工序 / 工步内容',18);text(42,765,card['id'],10);y=727
    for op in card['operations']:
        text(42,y,op['id']+'  '+op['name'],11);y-=24
        for step in op['steps']:
            text(46,y,step['id'],9);y=lines(112,y,step['text'],390,9)-10
    text(42,58,'第 2 / 3 页 · 各工步不自动等于整卡通过',9);c.showPage()
    text(42,792,'软件执行附件 / 资源与验收',18);y=750
    for tool in resources['tools']:
        y=lines(42,y,f"{tool['id']} / NC T{tool['number']}：φ{tool['diameter_mm']:g} 可中心切削平底铣刀，有效刃长 5 mm，总长 20 mm。")-10
    for line in [
        '面铣：FreeCAD MillFace；Boundbox，清除边缘，不排除凸起；Offset / Climb / Center。',
        '铣孔：FreeCAD Helix；Inside / Climb，从内向外清除，中心不留芯；StartRadius 为零。',
        *(['型腔：FreeCAD PocketShape；Offset / Climb / Center。按原底面外轮廓清除全部型腔，未来孔口不作为岛保留。腔底孔只在剩余底板内形成孔壁。'] if pocket else []),
        *(['外形：FreeCAD Profile；Outside / CW / Round，CAM 刀径补偿；每层闭合后直接向下进给，层间不退刀；排除内孔。'] if profiled else []),
        'S3000 rpm；切削 F120、入刀 F60 mm/min；每层 / 每转下降不大于 0.25 mm。面铣 / 铣孔径向步距 50%；外形每层单圈。' if profiled else 'S3000 rpm；切削 F120、入刀 F60 mm/min；每层 / 每转下降不大于 0.25 mm，径向步距为刀径的 50%。',
        ('原 CAD 中安全高度 Z2、清空高度 Z3；FreeCAD 按工件零点转换程序高度。铣孔由成品顶面上方 0.1 mm 进入，越过底面 0.1 mm 后完成整周。' if framed else '安全高度 Z2，清空高度 Z3；铣孔从成品顶面上方 0.1 mm 进入，越过底面 0.1 mm 后完成整周精加工。'),
        (f"启动：机床 (0,0,0)、未装刀、停转；原生 NC 用 G53 先移至机床 Z{setup['startup']['retract_before_tool_selection_z_mm']:g}，再装 T1。控制器寄存器由独立配置文件设置，零件 NC 不修改。" if framed else '启动状态：参考位置 (0,0,0)、未装刀、主轴停止。原生 NC 先空刀移至 Z3，再装 T1；不模拟主轴头 / 刀柄。'),
        'G17，增量 I、J 圆心；余量为零，工步间清空退刀；仅在已证明清空的孔内快速移动。',
        spec.get('software_criteria_note',f"CAM 曲线精度 0.001 mm；最终仿真网格 {spec['resolution_mm']:g} mm；软件尺寸容差 {4*spec['resolution_mm']:g} mm，数值保护带 {2*spec['resolution_mm']:g} mm。"),
        '验收：各工步实际剩料与独立预期 CAD 比较；最终核对高度 / 厚度 / 顶面、'+('圆头外形及外侧无残料、' if profiled else '矩形外缘、型腔轮廓 / 深度 / 保留底面、' if pocket else '矩形外缘、')+'逐孔孔径 / 孔位 / 贯穿；另验顺序及用刀。',
        spec.get('manufacturing_note','制造公差 / 粗糙度依据尚空；软件有限采样不代替制造验收。设备、装夹及工艺参数仍待工艺师傅审核。')]:
        y=lines(42,y,line)-15
    text(42,58,'第 3 / 3 页 · 资源参数仅为显式软件参考',9);c.save()


def build(out,cad_from=None,specification_file=None,case_transform=None):
    out=out.resolve()
    if not out.is_relative_to(ROOT):raise ValueError('Keep all work in project')
    initial=json.loads(specification_file.read_text()) if specification_file else specifications()
    profiled=initial.get('capability')==PROFILED;pocketed=initial.get('capability')==POCKETED
    version='v12' if pocketed else 'v11' if profiled else 'v9'
    capability=POCKETED if pocketed else PROFILED if profiled else CAPABILITY
    reference=POCKETED_REFERENCE if pocketed else PROFILED_REFERENCE if profiled else REFERENCE
    prefix='S4-POCKET-SEQUENCE-' if pocketed else 'S4-PROFILE-SEQUENCE-' if profiled else 'S4-SEQUENCE-'
    if pocketed and cad_from:raise ValueError('Pocket sequence archive reuse is not implemented; preserve original frozen inputs')
    out.mkdir(parents=True,exist_ok=False);write(out/'specifications.json',initial);rev=out.name.rsplit('-',1)[-1]
    if cad_from:
        previous=json.loads((cad_from/'specifications.json').read_text());nominal=copy.deepcopy(previous)
        for spec in nominal['cases']:
            spec.pop('features')
            for hole in spec['holes']:hole.pop('cylinder_face')
        # These three strings are used only by the PDF writer. Geometry,
        # materials, resource values and operation order must remain identical.
        paper_notes=('card_origin_note','software_criteria_note','manufacturing_note')
        requested=copy.deepcopy(initial)
        for data in (nominal,requested):
            for spec in data['cases']:
                for key in paper_notes:spec.pop(key,None)
        if nominal!=requested:raise ValueError('Only identical original CAD specifications can be reused')
        for old,new in zip(previous['cases'],initial['cases']):
            for key in paper_notes:
                if key in new:old[key]=new[key]
                else:old.pop(key,None)
        manifest=json.loads((cad_from/'manifest.json').read_text())['files']
        for spec in previous['cases']:
            (out/spec['id']).mkdir()
            names=['target.brep','target.step','target.FCStd','reference.json','expected-states.json']+[f'expected-state-{i+1:02d}.brep' for i in range(len(spec['order']))]
            for name in names:
                key=spec['id']+'/'+name
                if digest(cad_from/key)!=manifest[key]['sha256']:raise ValueError('Frozen CAD changed')
                shutil.copy2(cad_from/key,out/key)
        (out/'specifications.json').write_text(json.dumps(previous,ensure_ascii=False,indent=2)+'\n')
        write(out/'cad-generation.json',{'status':'reused_identical_original_and_intermediate_cad','source':str(cad_from),'source_manifest_sha256':digest(cad_from/'manifest.json'),'target_created_before_nc':True})
    else:
        env=workspace_environment();env.update(QT_QPA_PLATFORM='offscreen',GENLAW_SEQUENCE_FIXTURES=str(out))
        bounded(out,'generate-cad',[FREECAD,'freecadcmd',ROOT/('tools/build_pocket_sequence_cad.py' if pocketed else 'tools/build_sequence_cad.py')],env)
    specs=json.loads((out/'specifications.json').read_text())
    base=ROOT/'validation/feature-inputs/S3-HELIX-20261029-r2/baseline'
    facebase=ROOT/'validation/feature-inputs/S3-FACING-20261029-r1/baseline'
    # Both resource profiles are already explicit, source-bound software references.
    facing_files=list((ROOT/'validation/feature-inputs').glob('S3-FACING-*/baseline/resources-software-reference.json'))
    if len(facing_files)!=1:raise ValueError('Select the unique frozen facing profile explicitly')
    facebase=facing_files[0].parent
    for spec in specs['cases']:
        case=out/spec['id'];ref=json.loads((case/'reference.json').read_text());cad=ref['sha256'];features=spec['features'];byid={f['id']:f for f in features}
        card=json.loads((base/'process-plan.json').read_text());card.update(id=prefix+spec['id']+'-CARD-'+rev,cad_sha256=cad)
        dims=[b-a for a,b in zip(spec['stock_bounds_mm']['min'],spec['stock_bounds_mm']['max'])]
        material=spec.get('material_label','6061-T6')
        card['operations'][0]['steps'][0]['text']='核对 '+' × '.join(f'{v:g}' for v in dims)+' mm 完整矩形来料、'+material+' 材料标签、独立原 CAD 和版本。'
        selected=[];previous=None;steps=card['operations'][1]['steps'][:1]
        for i,fid in enumerate(spec['order'],2):
            sid=f'OP10.{i}';selected.append(sid);f=byid[fid];facing=fid=='P1';external=f['kind']=='capsule_outline'
            pocket=f['kind']=='rounded_rectangular_pocket'
            tid='T2' if spec['two_tools'] and not facing and not external and not pocket else 'T1'
            contract=pocket_contract(f) if pocketed else CONTRACTS[KINDS[f['kind']]]
            constraints={**{key:f[key] for key in contract['dimensions']},contract['surface_constraint']:contract.get('surface_value','flat'),'tool_id':tid}
            content=(f"用 {tid} 面铣 P1：由来料顶面 Z{spec['stock_bounds_mm']['max'][2]:g} 铣至 Z{f['top_z_mm']:g}，厚度 {f['thickness_mm']:g}；保持底面及 XY 外形。" if facing
                else f"用 {tid} 铣 {fid} 圆头外形：长 {f['length_mm']:g}、宽 {f['width_mm']:g}、端圆 R{f['width_mm']/2:g}，长轴 {f['major_axis']}，贯穿原底面并清除外侧余料。层内闭合后直接下层；孔不得被轮廓操作加工。" if external
                else f"用 {tid} 铣 {fid}：中心 ({f['center_xy_mm'][0]:g}, {f['center_xy_mm'][1]:g})，长 {f['width_mm']:g} × 宽 {f['height_mm']:g}，R{f['corner_radius_mm']:g}，从成品顶面向下 {f['depth_mm']:g}，平底。按原底面外轮廓清空整个型腔；孔口不留岛，不得贯穿底板。" if pocket
                else f"用 {tid} 在当前剩料上螺旋铣 {fid}：孔心 ({f['center_xy_mm'][0]:g}, {f['center_xy_mm'][1]:g})，φ{f['diameter_mm']:g} 贯穿；中心不得留芯，沿用原 CAD 坐标。")
            steps.append({'id':sid,'type':contract['operation'],'text':content,
                'source':{'document_id':'CARD','page':2,'location':'工序 / 工步 '+sid},'feature_id':fid,
                'depends_on':[previous] if previous else ['OP05.1','OP10.1'],'constraints':constraints});previous=sid
        card['operations'][1]['steps']=steps
        card['operations'][3]['steps'][0]['text']='检查面铣后的实际剩料，以及最终厚度、顶面、外缘和各孔；工步顺序、用刀亦须符合本卡。制造验收依据待确认。'
        write(case/'geometry-observation.json',{'schema':'genlaw.cad-observation.'+version,'cad_sha256':cad,'units':'mm','reference_class':reference,
            'bounds_mm':ref['bounds'],'observation_method':'prior_native_cad_observation','provenance':{'file_key':'native_reference','sha256':digest(case/'reference.json')},'features':features})
        items=[];rules={};limit=4*spec['resolution_mm'];common={'schema':'genlaw.numeric-acceptance.v1','units':'mm','scope':'software_regression',
            'basis':'Frozen software regression: four-grid-cell dimensional allowance, two-cell numerical guard; finite probes, not manufacturing acceptance.'}
        for f in features:
            values={**(pocket_nominals(f) if pocketed else feature_nominals(f) if profiled else required_nominals(CONTRACTS[KINDS[f['kind']]],f)),'surface_roughness':None}
            items.extend({'id':f['id']+'-'+k,'feature_id':f['id'],'kind':k,'nominal':v,'acceptance':None} for k,v in values.items())
            if f['kind']=='planar_top':
                z=f['top_z_mm'];t=f['thickness_mm'];rules[f['id']]={
                    'top_height':{**common,'method':'sampled_top_height_and_mesh_max','min_mm':z-limit,'max_mm':z+limit},
                    'target_thickness':{**common,'method':'sampled_remaining_thickness','min_mm':t-limit,'max_mm':t+limit},
                    'top_geometry':{**common,'method':'sampled_top_plane','max_flatness_mm':limit},
                    'outline_xy':{**common,'method':'sampled_original_box_boundary','max_deviation_mm':limit}}
                if profiled:rules[f['id']].pop('outline_xy')
            elif f['kind']=='capsule_outline':
                rules[f['id']]={
                    'outer_profile_xy':{**common,'method':'sectioned_original_capsule_boundary','max_deviation_mm':limit},
                    'retained_thickness':{**common,'method':'sampled_original_thickness','min_mm':f['thickness_mm']-limit,'max_mm':f['thickness_mm']+limit},
                    'retained_planes':{**common,'method':'sampled_original_top_bottom','max_deviation_mm':limit},
                    'exterior_clearance':{**common,'method':'sampled_full_depth_exterior_clearance','must_be_clear':True}}
            elif f['kind']=='rounded_rectangular_pocket':
                rules[f['id']]={
                    'profile_xy':{**common,'method':'sampled_boundary_distance','max_deviation_mm':limit},
                    'pocket_depth':{**common,'method':'sampled_floor_depth','min_mm':f['depth_mm']-limit,'max_mm':f['depth_mm']+limit},
                    'floor_geometry':{**common,'method':'sampled_flat_floor','max_flatness_mm':limit,'min_remaining_floor_mm':.1}}
            else:
                r=policy(cad,f['diameter_mm'])['rules'];r['diameter'].update(min_mm=f['diameter_mm']-limit,max_mm=f['diameter_mm']+limit)
                r['position_xy']['max_deviation_mm']=limit
                for rule in r.values():rule['basis']=common['basis']
                rules[f['id']]=r
        requirements={'schema':'genlaw.requirements.v1','cad_sha256':cad,'units':'mm','items':items}
        software_policy={'schema':'genlaw.software-geometry-policy.'+version,'cad_sha256':cad,'production_approved':False,'features':rules}
        resources=json.loads((base/'resources-software-reference.json').read_text());resources['id']=prefix+spec['id']+'-RESOURCES-'+rev
        resources['stock']['bounds_mm']=spec['stock_bounds_mm'];resources['tools'][0]['number']=1
        resources['tools'][0]['diameter_mm']=spec.get('tool_diameter_mm',1)
        resources['setup']['startup']={'position_mm':[0,0,0],'tool':'unloaded','spindle':'stopped','retract_before_tool_selection_z_mm':3,
            'basis':'Explicit CAMotics reference initial state: no selected tool. Native post moves to declared clearance before M6; no spindle-head or holder geometry is represented.'}
        if spec['two_tools']:
            second=copy.deepcopy(resources['tools'][0]);second.update(id='T2',number=2)
            if pocketed:second['diameter_mm']=spec['second_tool_diameter_mm']
            else:resources['tools'][0]['diameter_mm']=2
            resources['tools'].append(second)
        face=json.loads((facebase/'resources-software-reference.json').read_text())['cutting_presets'][0];face['id']='FACE-REFERENCE'
        helix=resources['cutting_presets'][0];helix.update(id='HELIX-REFERENCE',tool_id='T2' if spec['two_tools'] else 'T1')
        resources['cutting_presets']=[face,helix];resources['simulation']['resolution_mm']=spec['resolution_mm']
        if pocketed:
            pocket_preset=copy.deepcopy(face)
            for key in ('boundary_shape','clear_edges','exclude_raised_areas'):pocket_preset.pop(key)
            pocket_preset.update(id='POCKET-REFERENCE',operation='pocket_milling',floor_outline='clear_entire_outer_wire')
            resources['cutting_presets'].append(pocket_preset)
        if profiled:
            external=json.loads((ROOT/'validation/feature-inputs/S4-PROFILE-20261029-r2/baseline/resources-software-reference.json').read_text())['cutting_presets'][0]
            external.update(id='PROFILE-REFERENCE',tool_id='T1');resources['cutting_presets'].append(external)
        if initial.get('cell_window_reference'):
            resources['simulation']['basis']='Explicit 0.025 mm grid frozen before CAM at original physical dimensions. Experimental global-cell execution: at most 5,000,000 cells and 100,000 XY samples per job; stock-wide legacy execution remains rejected.'
        if 'material_label' in spec:
            resources['stock']['material_label']=material
            for preset in resources['cutting_presets']:preset['material_label']=material
        extra_refs=case_transform(case,spec,card,requirements,software_policy,resources) if case_transform else {}
        write(case/'process-plan.json',card);write(case/'requirements.json',requirements)
        write(case/'software-policy.json',software_policy)
        write(case/'resources-software-reference.json',resources);paper(case/'process-card.pdf',spec,card,resources)
        entry=json.loads((base/'input.json').read_text());entry.update(schema='genlaw.preparation-input.'+version,capability=capability,id=prefix+spec['id']+'-'+rev)
        entry['scope']['selected_steps']=selected;entry['scope']['declared_preconditions']['OP05.1']='显式完整矩形来料；各工步连续去料，不从预期成品重新创建来料。'
        entry['files']['expected_states']={'path':'expected-states.json'}
        entry['files'].update(extra_refs or {})
        for state in json.loads((case/'expected-states.json').read_text())['states']:entry['files'][state['id'].replace('-','_')]=state['cad']
        for value in entry['files'].values():value['sha256']=digest(case/value['path'])
        write(case/'input.json',entry)
        write(case/'manifest.json',{'id':entry['id'],'target_created_before_nc':True,'production_approved':False,
            'files':{p.name:{'bytes':p.stat().st_size,'sha256':digest(p)} for p in sorted(case.iterdir())}})
    write(out/'manifest.json',{'id':out.name,'files':{str(p.relative_to(out)):{'bytes':p.stat().st_size,'sha256':digest(p)} for p in sorted(out.rglob('*')) if p.is_file()}})
    with zipfile.ZipFile(out.with_suffix('.zip'),'x',compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(out.rglob('*')):
            if p.is_file():z.write(p,str(p.relative_to(out)))
    print(json.dumps({'status':'archived_before_nc','cases':len(specs['cases']),'output':str(out)}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--cad-from',type=Path)
    p.add_argument('--specifications',type=Path);a=p.parse_args();build(a.output,a.cad_from,a.specifications)
