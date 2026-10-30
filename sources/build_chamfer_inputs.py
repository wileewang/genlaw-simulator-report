"""Archive paper cards, original CAD, explicit frustum resources and frozen criteria."""
import argparse,json,re,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.preparation import prepare,digest
from genlaw_pipeline.chamfer_geometry import CAPABILITY,REFERENCE,nominals
from genlaw_pipeline.freecad_deburr import FORMAL,checked_formal_backend
from genlaw_pipeline.run import write,FREECAD,workspace_environment
from genlaw_pipeline.admission_wait import admitted_bounded


def specifications():
    cases=[]
    for key,name,c,lo,tip in [('baseline','四边等宽倒角',.4,[0,0,-1.5],.4),
        ('wider','倒角加宽',.5,[0,0,-1.5],.4),('translated','原 CAD 坐标平移',.4,[1,-2,-.5],.4),
        ('wider-tip','更换平刃刀尖尺寸',.4,[0,0,-1.5],.8)]:
        cases.append({'id':key,'name':name,'chamfer_width_mm':c,'stock_bounds_mm':{'min':lo,'max':[lo[i]+[8,6,1.5][i] for i in range(3)]},
            'tip_diameter_mm':tip,'tool_diameter_mm':tip+1.6,'cone_height_mm':.8,'extra_depth_mm':.2,'resolution_mm':.025})
    return {'schema':'genlaw.chamfer-fixtures.v1','stage':'s4-chamfer','cases':cases,
        'selection':'One synthetic four-edge top chamfer with independent width, original CAD translation and finite tip changes. Not a scaled real-card part, new WCS support or whole-card test.',
        'production_approved':False,'real_process_card_tested':False}


def paper(path,spec,card):
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.pagesizes import A4
    pdfmetrics.registerFont(TTFont('ChamferLatin','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
    pdfmetrics.registerFont(TTFont('ChamferCJK','/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf'))
    c=canvas.Canvas(str(path),pagesize=A4);c.setTitle(card['id']+' / 四边倒角工艺卡')
    def font(ch):return 'ChamferLatin' if ord(ch) in pdfmetrics.getFont('ChamferLatin').face.charToGlyph else 'ChamferCJK'
    def width(text,size):return sum(pdfmetrics.stringWidth(ch,font(ch),size) for ch in text)
    def text(x,y,value,size=10):
        for ch in value:c.setFont(font(ch),size);c.drawString(x,y,ch);x+=pdfmetrics.stringWidth(ch,font(ch),size)
    def lines(x,y,value,w=470,size=10):
        line=''
        for token in re.findall(r'[A-Za-z0-9_.+°/×()%-]+|.',value):
            if line and width(line+token,size)>w and token not in '，。、；：':text(x,y,line,size);y-=16;line=''
            line+=token
        if line:text(x,y,line,size);y-=16
        if y<70:raise ValueError('Paper card content overflow')
        return y
    f=spec['features'][0];lo,hi=[spec['stock_bounds_mm'][k] for k in ['min','max']];cw=f['width_mm']
    text(42,792,'GenLaw 四边倒角软件验证工艺卡',18);text(42,764,card['id']+' / '+spec['name'])
    text(42,738,'合成验证件 · 工艺师傅审阅 / 生产批准：待定')
    text(42,706,f'矩形来料 8 × 6 × 1.5 mm；材料标签 6061-T6；上口四边 C{cw:g} × 45°')
    scale=31;x,y=92,462
    c.setStrokeColorRGB(.2,.35,.55);c.setFillColorRGB(.7,.8,.92);c.rect(x,y,8*scale,6*scale,fill=1)
    c.setFillColorRGB(.48,.65,.84);c.rect(x+cw*scale,y+cw*scale,(8-2*cw)*scale,(6-2*cw)*scale,fill=1)
    for px,py,sx,sy in [(0,0,1,1),(8,0,-1,1),(0,6,1,-1),(8,6,-1,-1)]:c.line(x+px*scale,y+py*scale,x+(px+sx*cw)*scale,y+(py+sy*cw)*scale)
    c.setFillColorRGB(0,0,0);text(92,440,'顶视示意：浅色为四周倒角面；深色为保留顶面',9)
    yy=408
    for row in [f'原 CAD 坐标：X {lo[0]:g} 至 {hi[0]:g}，Y {lo[1]:g} 至 {hi[1]:g}，Z {lo[2]:g} 至 {hi[2]:g} mm。',
        f'只加工上口四边 C1：四个倒角面的水平宽度和垂直高度均为 {cw:g} mm；保留其余侧壁、底面和中央顶面。',
        '参考实际工艺卡的工序 / 工步写法，资源与软件验收条件另列附件。结构化工步由开发者对照本 PDF 录入，不代表自动识别 PDF 已完成。',
        '本件为软件开发独立构造的几何，不是本地生产图纸的缩小版；上板 / 固定板的实际 C3、翻面装夹与整卡尚未验收。',
        '制造公差、角度公差和粗糙度需另行确认。本文的 0.1 mm 为软件回归阈值，不是现场加工公差。']:
        yy=lines(42,yy,row)-14
    text(42,58,'第 1 / 3 页 · 输入 CAD 与卡片在 NC 前存档',9);c.showPage()
    text(42,792,'工序 / 工步内容',18);text(42,765,card['id']);yy=728
    for op in card['operations']:
        text(42,yy,op['id']+'  '+op['name'],11);yy-=24
        for step in op['steps']:text(46,yy,step['id'],9);yy=lines(112,yy,step['text'],390,9)-11
    text(42,58,'第 2 / 3 页 · 仅 OP10.2 进入本次软件几何验证',9);c.showPage()
    text(42,792,'软件执行附件 / 资源与验收',18);yy=752
    for row in [f"T1：90° 截锥倒角刀，大端 φ{spec['tool_diameter_mm']:g}、平刃刀尖 φ{spec['tip_diameter_mm']:g}，锥段高 0.8 mm，总长 20 mm；非中心切削。",
        'FreeCAD 原生 Deburr；绑定原 CAD 的四个倒角面。启用明确版本的直边高度判断修正 deburr-linear-height-r2，安装目录保持原样。',
        f"倒角宽 {cw:g} mm；额外下刀 0.2 mm；刀尖 Z{hi[2]-cw-.2:g}，中心线在矩形边界外偏 {spec['tip_diameter_mm']/2+.2:g} mm。沿外周顺时针单圈加工，四角圆弧连接。",
        'S3000 rpm；切削 F120、下刀 F60 mm/min；径向和轴向余量零。参数为软件参考，材料切削性能未验证。',
        '安全高度 Z2，清空高度 Z3。初始 (0,0,0)、空刀、主轴停止；先空刀升至 Z3 再选刀。G54 零变换、无刀长补偿。',
        'G17 / G90 / G40；I、J 为增量圆心。原生 NC 原文交给完整 CAMotics，截锥使用 snubnose，平刃刀尖显式保留。',
        'CAM 曲线精度 0.001 mm；仿真网格 0.025 mm。完整原 CAD 比较包括所有剩余材料；不移动结果、不裁除多余实体。',
        '软件检查：11 层倒角全轮廓 + 3 层保留侧壁；四边中线估计倒角宽度；25 个中央取点检查原厚度与顶底面。',
        '软件截面、宽度与平面偏差上限 0.1 mm；厚度 1.5 ±0.1 mm，采用 0.05 mm 数值保护带。角度只报告有限截面估计，不验收 ±1° 等制造要求。',
        '夹具、刀杆刀柄、真实机床适配、粗糙度、材料物理、整卡工序和生产批准均未验证。']:
        yy=lines(42,yy,row)-12
    text(42,58,'第 3 / 3 页 · 制造验收条件仍待工艺师傅确认',9);c.save()


def build(out):
    out=out.resolve()
    if not out.is_relative_to(ROOT):raise ValueError('Keep input archive in project')
    checked_formal_backend(FORMAL);out.mkdir(parents=True,exist_ok=False)
    write(out/'specifications.json',specifications());env=workspace_environment()
    env.update(QT_QPA_PLATFORM='offscreen',GENLAW_CHAMFER_FIXTURES=str(out))
    admitted_bounded(out,'generate-cad',[FREECAD,'freecadcmd',ROOT/'tools/build_chamfer_cad.py'],env)
    specs=json.loads((out/'specifications.json').read_text());base=ROOT/'validation/feature-inputs/S4-PROFILE-20261029-r2/baseline'
    for spec in specs['cases']:
        case=out/spec['id'];f=spec['features'][0];ref=json.loads((case/'reference.json').read_text());cad=ref['sha256'];lo,hi=[spec['stock_bounds_mm'][k] for k in ['min','max']]
        card=json.loads((base/'process-plan.json').read_text());card.update(id=out.name+'-'+spec['id']+'-CARD',cad_sha256=cad)
        card['operations'][0]['steps'][0]['text']='核对 8 × 6 × 1.5 mm 矩形来料、6061-T6 标签、CAD 版本和原坐标；原顶底厚度已具备。'
        card['operations'][1]['steps'][1].update(type='chamfer_milling',feature_id='C1',
            constraints={'width_mm':f['width_mm'],'angle_degrees':45,'edge_selection':'all_four_top_edges','tool_id':'T1'},
            text=f"用 T1 对上口四边 C1 倒角 C{f['width_mm']:g} × 45°；从外侧垂直入刀，顺时针一圈闭合后抬刀。保留其余侧壁、底面和中央顶面，禁止添加额外切削线。")
        card['operations'][2]['steps'][0]['text']='检查倒角边缘毛刺；是否需人工去毛刺按实际工艺确认，不追加未经定义的倒角。'
        card['operations'][3]['steps'][0]['text']='检验四边倒角宽度、角度、中央保留厚度与顶底面；制造公差、角度公差及粗糙度依据待确认。'
        write(case/'process-plan.json',card)
        write(case/'geometry-observation.json',{'schema':'genlaw.cad-observation.v14','cad_sha256':cad,'units':'mm','reference_class':REFERENCE,
            'bounds_mm':ref['bounds'],'observation_method':'prior_native_cad_observation','provenance':{'file_key':'native_reference','sha256':digest(case/'reference.json')},'features':[f]})
        write(case/'requirements.json',{'schema':'genlaw.requirements.v1','cad_sha256':cad,'units':'mm','items':[
            {'id':'C1-'+k,'feature_id':'C1','kind':k,'nominal':v,'acceptance':None}
            for k,v in {**nominals(f),'chamfer_angle_degrees':45,'surface_roughness':None}.items()]})
        common={'schema':'genlaw.numeric-acceptance.v1','units':'mm','scope':'software_regression',
            'basis':'Frozen before NC: 0.1 mm software allowance with two 0.025 mm cells as numerical guard; finite measurements, no manufacturing acceptance or certified angle uncertainty.'}
        rules={k:{**common,'method':m,'max_deviation_mm':.1} for k,m in [
            ('chamfer_sections','sectioned_original_chamfer_boundary'),('chamfer_width','sampled_extrapolated_chamfer_width'),('retained_planes','sampled_original_top_bottom')]}
        rules['retained_thickness']={**common,'method':'sampled_original_thickness','min_mm':1.4,'max_mm':1.6}
        write(case/'software-policy.json',{'schema':'genlaw.software-geometry-policy.v14','cad_sha256':cad,'production_approved':False,'features':{'C1':rules}})
        resources=json.loads((base/'resources-software-reference.json').read_text());resources['id']=out.name+'-'+spec['id']+'-RESOURCES'
        resources['stock']['bounds_mm']=spec['stock_bounds_mm'];resources['cam_backend']=FORMAL
        resources['tools']=[{'id':'T1','kind':'chamfer_frustum','center_cutting':False,'diameter_mm':spec['tool_diameter_mm'],
            'tip_diameter_mm':spec['tip_diameter_mm'],'included_angle_degrees':90,'cutting_length_mm':.8,'length_mm':20,
            'basis':'Explicit finite 90 degree frustum with a flat tip; software reference only, shank and holder excluded.'}]
        preset=resources['cutting_presets'][0]
        for key in ['step_down_mm','breakthrough_mm','layer_transition']:preset.pop(key)
        preset.update(operation='chamfer_milling',clearing_pattern='ClosedChamfer',compensation='CAM_frustum_offset_G40',
            extra_depth_mm={'value':spec['extra_depth_mm'],'basis':'Explicit software reference keeps the flat tip outside original vertical walls.'})
        resources['simulation']={'resolution_mm':spec['resolution_mm'],'basis':'Frozen before NC under unchanged local grid budget.'}
        write(case/'resources-software-reference.json',resources);paper(case/'process-card.pdf',spec,card)
        entry=json.loads((base/'input.json').read_text());entry.update(schema='genlaw.preparation-input.v14',capability=CAPABILITY,id=out.name+'-'+spec['id'])
        entry['scope']['declared_preconditions']['OP05.1']='来料为原坐标中的矩形实体，外形和厚度已具备；只选择上口四边倒角。'
        for value in entry['files'].values():value['sha256']=digest(case/value['path'])
        write(case/'input.json',entry);result=prepare(case/'input.json');write(case/'preparation-check.json',result)
        if result['status']!='ready_for_declared_scope':raise ValueError(json.dumps(result,ensure_ascii=False))
        write(case/'manifest.json',{'id':entry['id'],'production_approved':False,'files':{p.name:{'bytes':p.stat().st_size,'sha256':digest(p)} for p in sorted(case.iterdir())}})
    write(out/'manifest.json',{'id':out.name,'files':{str(p.relative_to(out)):{'bytes':p.stat().st_size,'sha256':digest(p)} for p in sorted(out.rglob('*')) if p.is_file()}})
    with zipfile.ZipFile(out.with_suffix('.zip'),'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(out.rglob('*')):
            if path.is_file():archive.write(path,str(path.relative_to(out)))
    print(json.dumps({'status':'archived','cases':len(specs['cases']),'output':str(out)}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    build(parser.parse_args().output)
