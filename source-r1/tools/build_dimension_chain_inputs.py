"""Freeze existing paper/CAD fixtures with a separately identified inspection sheet."""
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.preparation import digest,prepare
from genlaw_pipeline.cell_windows import file_binding
from genlaw_pipeline.run import write
from tools.run_dimension_suite import requirements
from genlaw_pipeline.dimension_contract import features

OUT=ROOT/'validation/feature-inputs/S4-DIMENSION-CHAIN-20261030-r2'
SOURCES={
    'profile-x':'S4-PROFILE-SUITE-20261029-r1/baseline',
    'profile-y':'S4-PROFILE-SUITE-20261029-r1/vertical',
    'two-holes':'S4-PROFILE-SEQUENCE-SUITE-20261029-r1/baseline',
    'three-holes':'S4-PROFILE-SEQUENCE-SUITE-20261029-r1/three-holes',
}
LOAD=lambda p:json.loads(p.read_text())


def paper(path,key,request,reference,card_id):
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.pagesizes import A4
    pdfmetrics.registerFont(TTFont('DimLatin','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
    pdfmetrics.registerFont(TTFont('DimCJK','/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf'))
    c=canvas.Canvas(str(path),pagesize=A4);c.setTitle(key+' / 工艺卡尺寸检验附件')
    def text(x,y,value,size=10):
        for ch in str(value):
            font='DimLatin' if ord(ch) in pdfmetrics.getFont('DimLatin').face.charToGlyph else 'DimCJK'
            c.setFont(font,size);c.drawString(x,y,ch);x+=pdfmetrics.stringWidth(ch,font,size)
    text(42,796,'GenLaw 尺寸检验附件',18);text(42,770,'验证用例：'+key)
    text(42,746,'配套原工艺卡：'+card_id,9)
    text(42,723,'原 CAD 与工序保持；仅增加最终材料尺寸检查。工艺师傅签审：待定。',10)
    profile,holes=features(reference);lo=reference['bounds']['min'];hi=reference['bounds']['max']
    width,height=hi[0]-lo[0],hi[1]-lo[1];scale=min(380/width,82/height)
    x=(595-width*scale)/2;y=608
    c.setFillColorRGB(.84,.90,.95);c.setStrokeColorRGB(.23,.35,.49)
    c.roundRect(x,y,width*scale,height*scale,min(width,height)*scale/2,fill=1)
    c.setFillColorRGB(1,1,1)
    for hole in holes:
        hx=x+(hole['center_xy_mm'][0]-lo[0])*scale;hy=y+(hole['center_xy_mm'][1]-lo[1])*scale
        c.circle(hx,hy,hole['diameter_mm']*scale/2,fill=1)
        c.setFillColorRGB(0,0,0);text(hx-7,hy-3,hole['id'],9);c.setFillColorRGB(1,1,1)
    c.setStrokeColorRGB(.23,.35,.49);c.setFillColorRGB(0,0,0)
    c.line(55,620,98,620);c.line(55,620,55,665)
    c.line(98,620,93,623);c.line(98,620,93,617)
    c.line(55,665,52,660);c.line(55,665,58,660)
    text(101,617,'+X',9);text(47,674,'+Y',9)
    c.setFillColorRGB(0,0,0);text(42,587,'名义 CAD 顶视示意；按原 X/Y 坐标测量，不移动或对齐实际结果。',9)
    positions=[42,188,332,448,553];head=555;rowheight=30
    for i,title in enumerate(['要求 ID / 基准','名义尺寸 mm','允许范围 mm','类型']):text(positions[i]+5,head-19,title,9)
    total=(len(request['items'])+1)*rowheight
    for xx in positions:c.line(xx,head,xx,head-total)
    for i in range(len(request['items'])+2):c.line(42,head-i*rowheight,553,head-i*rowheight)
    for i,item in enumerate(request['items']):
        yy=head-(i+1)*rowheight-19;rule=item['acceptance']
        axis=item['axis'];kind=item['kind'];side='低' if item.get('side')=='low' else '高'
        label=(('总长' if item['id']=='length' else '总宽')+'（'+axis+'）' if kind=='support_span' else
            side+axis+'端半径' if kind=='end_radius' else
            side+axis+'端→'+item['hole_id'] if kind=='end_offset' else
            '→'.join(item['hole_ids'])+'（'+axis+'投影）')
        text(47,yy,label,8);text(193,yy,item['nominal_mm'],9)
        text(337,yy,f'{rule["min_mm"]:g} ～ {rule["max_mm"]:g}',9);text(453,yy,'软件判据',9)
    yy=head-total-26
    for line in [
        '所有长度以 mm 表示；孔距为指定 X/Y 轴的投影距离，边距使用明确的端部。',
        '本附件是合成用例的软件检查条件，不替代真实工艺卡制造公差。',
        '仿真网格 0.025 mm；半径保护带 0.05 mm，长度及距离保护带 0.10 mm。',
        '在原 CAD 的 11 个内部截面检查；保护带不是认证测量不确定度。',
        '原工艺卡中的厚度、外形、孔、顺序与中间状态检查继续执行。',
        '夹具/刀柄、材料物理、粗糙度、整卡与生产批准仍未验证。',
        '录入来源：本页依次为 length、width、low-radius、high-radius，',
        '如有孔，续 high-end-offset、H1-H2-pitch、H2-H3-pitch。CAD SHA-256：',request['cad_sha256']]:
        text(42,yy,line,8 if len(line)>70 else 9);yy-=18
    text(42,46,'第 1 / 1 页 · 与原工艺卡和 CAD 一起在本次 NC 生成前存档',9)
    c.save()


def build():
    from mesh_metrics import require_bounded_worker
    require_bounded_worker();OUT.mkdir(parents=True,exist_ok=False);cases=[]
    for key,suffix in SOURCES.items():
        original=ROOT/'validation/runs'/suffix
        input_path=Path(LOAD(original/'request.json')['input']);manifest=LOAD(input_path)
        prior=prepare(input_path)
        if prior['status']!='ready_for_declared_scope':raise ValueError('Original input no longer prepares')
        dest=OUT/key;dest.mkdir()
        for binding in manifest['files'].values():
            src=input_path.parent/binding['path']
            if digest(src)!=binding['sha256']:raise ValueError('Original input changed')
            target=dest/binding['path'];target.parent.mkdir(parents=True,exist_ok=True)
            if not target.exists():shutil.copy2(src,target)
        policy=input_path.parent/'software-policy.json';shutil.copy2(policy,dest/policy.name)
        reference=LOAD(original/'native-geometry.json');request=requirements(reference,False)
        for field in ('criteria_added_after_original_material_execution','criteria_frozen_before_added_measurement'):
            request.pop(field,None)
        request['scope_note']='Synthetic software inspection sheet, bound before this fresh job; original geometry is an existing regression fixture.'
        for item in request['items']:
            item['source']={'document_id':'DIMENSIONS','page':1,'location':'检验表 '+item['id']}
        write(dest/'dimension-requirements.json',request)
        card=LOAD(dest/'process-plan.json');paper(dest/'dimension-inspection.pdf',key,request,reference,card['id'])
        for field,name in [('dimension_requirements','dimension-requirements.json'),('inspection_sheet','dimension-inspection.pdf')]:
            manifest['files'][field]={'path':name,'sha256':digest(dest/name)}
        manifest['source_documents']['DIMENSIONS']='inspection_sheet'
        manifest['id']='S4-DIMENSION-CHAIN-'+key+'-r2'
        manifest['inspection']={'schema':'genlaw.declared-dimensions.v1','file_key':'dimension_requirements','scope':'final_material'}
        write(dest/'input.json',manifest)
        prepared=prepare(dest/'input.json')
        if prepared['status']!='ready_for_declared_scope':raise ValueError(str(prepared['issues']))
        write(dest/'preparation-preview.json',prepared)
        cases.append({'id':key,'input':file_binding(dest/'input.json'),'original_input':file_binding(input_path),
            'original_native_cad':file_binding(original/'native-geometry.json'),'source_run':str(original.relative_to(ROOT)),
            'dimension_count':len(request['items']),'cad_geometry_reused':True,'planned_new_native_job':True})
    write(OUT/'cases.json',{'schema':'genlaw.dimension-chain-inputs.v1','cases':cases,
        'built_before_new_native_jobs':True,'production_approved':False,'whole_process_card_complete':False})
    files={str(p.relative_to(OUT)):{'sha256':digest(p),'bytes':p.stat().st_size} for p in sorted(OUT.rglob('*')) if p.is_file()}
    write(OUT/'manifest.json',{'schema':'genlaw.input-archive.v1','files':files,'source_code':file_binding(Path(__file__))})
    print(json.dumps({'status':'frozen','cases':len(cases),'files':len(files)}),flush=True)


if __name__=='__main__':build()
