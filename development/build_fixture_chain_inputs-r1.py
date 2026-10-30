"""Archive paper cards, fixture appendices and original CAD before new NC runs."""
import copy
import json
import math
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from genlaw_pipeline.preparation import prepare, digest
from genlaw_pipeline.run import write
from genlaw_pipeline.cell_windows import file_binding
from genlaw_pipeline.fixture_setup import SCHEMA, MODE, FILE_KEY, EXCLUSIONS, setup_requirements
from tools.build_sequence_inputs import paper

SOURCE = ROOT/'validation/feature-inputs/S4-TOOL-LENGTH-20261030-r1'
MODELS = ROOT/'validation/feature-inputs/FIXTURE-OBSERVER-20261030-r1'
OUTPUT = ROOT/'validation/feature-inputs/S4-FIXTURE-CLEARANCE-20261030-r1'
load = lambda p: json.loads(p.read_text())
TITLES = {'clear-jaws': '夹爪避让', 'cutting-clamp': '压块挡刀反例',
          'holder-overhang': '刀柄碰到支架反例', 'rapid-crossing': '快速移动穿柱反例',
          'near-clearance': '净空不足待判定反例'}


def appendix(path, key, model, setup):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    font = 'STSong-Light'; pdfmetrics.registerFont(UnicodeCIDFont(font))
    style = ParagraphStyle('body', fontName=font, fontSize=9, leading=14)
    title = ParagraphStyle('title', parent=style, fontSize=17, leading=23)
    small = ParagraphStyle('small', parent=style, fontSize=8, leading=12)
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=34, rightMargin=34, topMargin=30, bottomMargin=30)
    rows = [Paragraph('夹具与装刀检验附页 · '+TITLES[key], title), Spacer(1, 10),
            Paragraph('用途：软件验证工艺卡的 OP10.1 装夹附件。尺寸单位 mm，所有夹具坐标直接使用原始 CAD / 材料坐标；原 CAD 和毛坯不移动。此页资源均为构造测试配置，待工艺师傅审阅。', style),
            Spacer(1, 8), Paragraph('机床中的材料原点 P = '+str(setup['material_origin_machine_mm'])+'；原 CAD 中的工件零点 D = '+str(setup['work_origin_cad_mm'])+'；'+setup['work_offset']+' 寄存器 O = P + D = '+str(setup['work_offset_origin_machine_mm'])+'。', style),
            Spacer(1, 10), Paragraph('静态夹具实体（轴对齐长方体，不是任意形状的包围盒）', style)]
    data = [['标识', '最小坐标 [X, Y, Z]', '最大坐标 [X, Y, Z]']]
    for fixture in model['fixtures']:
        data.append([fixture['id'], str(fixture['bounds_mm']['min']), str(fixture['bounds_mm']['max'])])
    def table(data, widths):
        formatted = [[Paragraph(str(v), small) for v in row] for row in data]
        t = Table(formatted, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eaf0f6')),
                              ('GRID',(0,0),(-1,-1),.4,colors.HexColor('#b8c4ce')),
                              ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),6),
                              ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
        return t
    rows += [table(data,[140,190,190]), Spacer(1,12), Paragraph('装刀外形（固定 Z 轴，轴向距离从刀尖向上量至量规点）',style)]
    data = [['刀号 / 实装长度', '部位', '轴向起止 [mm]', '半径 [mm]']]
    names = {'cutting':'切削段','shank':'刀杆','holder':'刀柄'}
    for tool in model['tools']:
        for segment in tool['segments']:
            data.append([f'T{tool["tool_number"]} / {tool["gauge_to_tip_mm"]:g}', names[segment['role']],
                         f'{segment["z_min_mm"]:g} ～ {segment["z_max_mm"]:g}', f'{segment["radius_mm"]:g}'])
    rows += [table(data,[140,100,180,100]),Spacer(1,12),
             Paragraph(f'验收：每段原生 NC 运动中，全部已建模工具外形与夹具需达到 {model["minimum_clearance_mm"]:g} mm 净空。检测到干涉或保守误差带内无法判定时，停止后续去料仿真。',style),Spacer(1,8),
             Paragraph('范围：参考 XYZ 控制器、静态 Z 刀补；检查进给、快速移动和换刀前后的实际装刀位置。未验证夹紧力、换刀机构运动、主轴 / 机床本体、刀柄与加工中材料的干涉、材料物理或生产批准。',style),Spacer(1,8),
             Paragraph('绑定文件：fixture-model.json；工艺卡和资源文件均保存其 SHA-256。CAD、制造要求和各工步目标沿用已存档的面铣 / 型腔 / 双孔试件。本附页在新的 NC 生成之前冻结。',small)]
    doc.build(rows)


def main():
    from local_resource_limits import require_bounded_worker
    require_bounded_worker(); OUTPUT.mkdir(parents=True, exist_ok=False)
    source = SOURCE/'shorter-tool'; old_manifest = load(SOURCE/'manifest.json')
    old_spec = next(s for s in load(SOURCE/'specifications.json')['cases'] if s['id'] == 'shorter-tool')
    specifications = []; models = load(MODELS/'manifest.json')
    for key in TITLES:
        dest = OUTPUT/key; dest.mkdir(); entry = load(source/'input.json')
        for item in entry['files'].values():
            name = item['path']
            if digest(source/name) != item['sha256'] or old_manifest['files']['shorter-tool/'+name]['sha256'] != item['sha256']:
                raise ValueError('Original frozen tool-length input changed')
            shutil.copy2(source/name, dest/name)
        for name in ['target.FCStd','software-policy.json']:
            if digest(source/name) != old_manifest['files']['shorter-tool/'+name]['sha256']: raise ValueError('Original geometry or criteria changed')
            shutil.copy2(source/name,dest/name)
        if key in models['cases']:
            model_path = MODELS/key/'fixture-model.json'
            if digest(model_path) != models['files'][key+'/fixture-model.json']: raise ValueError('Qualified fixture model changed')
            model = load(model_path); expected = load(MODELS/key/'expectation.json')['status']
        else:
            model = load(MODELS/'clear-jaws/fixture-model.json')
            # Independent arithmetic from original first XY rapid, before this
            # new NC: its midpoint is offset 0.55 mm along the planar normal.
            dx,dy=15.1,22.9;length=math.hypot(dx,dy)
            x=-10+dx/3-.55*dy/length; y=-20+dy/3+.55*dx/length
            model['fixtures'].append({'id':'clearance-post','shape':'box','bounds_mm':{
                'min':[round(x-.005,8),round(y-.005,8),3.2],
                'max':[round(x+.005,8),round(y+.005,8),3.8]}})
            model['basis']='Synthetic minimum-clearance control: nominal 0.55 mm normal offset from original approach line, 0.01 mm post width; no geometric contact is presumed.'
            expected='indeterminate'
        write(dest/'fixture-model.json',model)
        res=load(source/'resources-software-reference.json');card=load(source/'process-plan.json')
        res['id']='S4-FIXTURE-'+key+'-RESOURCES-r1';card['id']='S4-FIXTURE-'+key+'-CARD-r1'
        setup=res['setup'];setup.update(schema=SCHEMA,fixture_mode=MODE)
        setup[FILE_KEY]={'schema':'genlaw.fixture-geometry-ref.v1','file_key':FILE_KEY,
                         'sha256':digest(dest/'fixture-model.json'),
                         'source':{'document_id':'FIXTURE','page':1,'location':'静态夹具实体、装刀外形与验收规则表'}}
        setup['basis']='Explicit original material frame, static mounted lengths and separately frozen fixture/tool envelope appendix.'
        step=card['operations'][1]['steps'][0]
        step['constraints']={'coordinate_setup':setup_requirements(setup)}
        step['text']='按原 CAD / 机床零点与实装刀长、独立 H 表配置；按夹具与装刀检验附页布置夹具。先生成原生 NC 并检查工具外形对夹具的连续净空，干涉或无法判定时停止后续去料。夹紧力和真实设备仍待审阅。'
        spec=copy.deepcopy(old_spec);spec.update(id=key,name=TITLES[key],coordinate_setup=copy.deepcopy(setup),
            card_origin_note='原 CAD / 工艺顺序不变 · 新增夹具资源检验 · 工艺师傅审核待定',
            expected_fixture_status=expected,original_geometry_case='shorter-tool')
        write(dest/'process-plan.json',card);write(dest/'resources-software-reference.json',res)
        paper(dest/'process-card.pdf',spec,card,res);appendix(dest/'fixture-inspection.pdf',key,model,setup)
        entry.update(id='S4-FIXTURE-'+key+'-r1')
        entry['source_documents']['FIXTURE']='fixture_paper'
        entry['scope']['excluded_checks']=EXCLUSIONS+['whole_process_completion']
        entry['scope']['declared_preconditions']['OP10.1']='按已绑定附页布置静态夹具及装刀外形；原 CAD 坐标、实际刀长和 H 表显式一致。夹紧力未验证。'
        entry['files'][FILE_KEY]={'path':'fixture-model.json','sha256':digest(dest/'fixture-model.json')}
        entry['files']['fixture_paper']={'path':'fixture-inspection.pdf','sha256':digest(dest/'fixture-inspection.pdf')}
        for item in entry['files'].values(): item['sha256']=digest(dest/item['path'])
        write(dest/'input.json',entry)
        result=prepare(dest/'input.json')
        write(dest/'preparation-preview.json',result)
        if result['status']!='ready_for_declared_scope': raise ValueError(result['issues'])
        specifications.append(spec)
    write(OUTPUT/'specifications.json',{'cases':specifications,'new_geometries':0,'existing_original_geometry_count':1,
        'resource_configurations':len(specifications),'frozen_before_new_nc':True,'manufacturing_approved':False})
    write(OUTPUT/'manifest.json',{'id':OUTPUT.name,'builder':file_binding(Path(__file__)),
        'prior_input_manifest':file_binding(SOURCE/'manifest.json'),'prior_observer_models':file_binding(MODELS/'manifest.json'),
        'files':{str(p.relative_to(OUTPUT)):{'sha256':digest(p),'bytes':p.stat().st_size} for p in sorted(OUTPUT.rglob('*')) if p.is_file()},
        'frozen_before_nc':True,'new_part_geometries':0,'process_card_approved_by_manufacturing':False})
    print(json.dumps({'status':'archived_before_nc','configurations':len(specifications),'output':str(OUTPUT)}))


if __name__=='__main__': main()
