"""Freeze four paper cards, existing original CAD and explicit setup resources."""
import argparse
import copy
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from genlaw_pipeline.preparation import digest,prepare
from genlaw_pipeline.run import write
from genlaw_pipeline.coordinate_setup import SCHEMA,validate_setup,setup_requirements
from tools.build_sequence_inputs import paper

SOURCE=ROOT/'validation/feature-inputs/S4-POCKET-SEQUENCE-20261030-r1'
LOAD=lambda p:json.loads(p.read_text())
CASES=[
    ('g54-positive','baseline','G54 正向装夹平移',[10,20,30],[0,0,0],'G54'),
    ('g54-negative','baseline','G54 负向装夹平移',[-10,-20,-30],[0,0,0],'G54'),
    ('cad-origin','floor-hole','原 CAD 中指定非零工件零点',[10,20,30],[4,3,-.5],'G54'),
    ('g55-tool-change','tool-change','G55 非零零点及换刀',[10,20,30],[4,3,-.5],'G55'),
]


def setup_for(p,d,slot):
    return {'schema':SCHEMA,'units':'mm','height_parameter_frame':'original_cad',
        'material_origin_machine_mm':p,'work_origin_cad_mm':d,
        'work_offset_origin_machine_mm':[a+b for a,b in zip(p,d)],'work_offset':slot,
        'rotation_degrees':[0,0,0],'tool_length_offset_mm':0,
        'fixture_mode':'unmodelled_explicitly_excluded',
        'basis':'Explicit synthetic XYZ translation: original CAD and independently declared physical material placement remain fixed; no fitted registration.',
        'startup':{'position_mm':[0,0,0],'position_frame':'machine','tool':'unloaded','spindle':'stopped',
                   'retract_before_tool_selection_z_mm':p[2]+3,
                   'basis':'Initial unloaded G53 machine retract reaches the original-CAD Z3 clearance before first M6.'}}


def build(out):
    from local_resource_limits import require_bounded_worker
    require_bounded_worker()
    out=out.resolve();out.relative_to(ROOT);out.mkdir(parents=True,exist_ok=False);revision=out.name.rsplit('-',1)[-1]
    old_specs={r['id']:r for r in LOAD(SOURCE/'specifications.json')['cases']}
    source_manifest=LOAD(SOURCE/'manifest.json')['files'];specs=[]
    for key,parent,title,p,d,slot in CASES:
        src=SOURCE/parent;dest=out/key;dest.mkdir();entry=LOAD(src/'input.json')
        for item in entry['files'].values():
            path=src/item['path']
            if digest(path)!=item['sha256'] or item['sha256']!=source_manifest[parent+'/'+item['path']]['sha256']:
                raise ValueError('Original archived input changed')
        replace={'process-plan.json','resources-software-reference.json','process-card.pdf'}
        immutable={v['path'] for v in entry['files'].values()}-replace
        immutable.update(['target.FCStd','software-policy.json'])
        for name in sorted(immutable):
            if digest(src/name)!=source_manifest[parent+'/'+name]['sha256']:raise ValueError('Original CAD/policy changed')
            shutil.copy2(src/name,dest/name)
        card=LOAD(src/'process-plan.json');res=LOAD(src/'resources-software-reference.json')
        card['id']='S4-WORK-COORDINATE-'+key+'-CARD-'+revision;res['id']='S4-WORK-COORDINATE-'+key+'-RESOURCES-'+revision
        res['setup']=setup_for(p,d,slot);validate_setup(res['setup'],res['stock']['bounds_mm'])
        step=card['operations'][1]['steps'][0]
        step['constraints']={'coordinate_setup':setup_requirements(res['setup'])}
        step['text']=(f'XYZ 参考装夹：材料原点在机床中为 {p} mm；工件零点在原 CAD 中为 {d} mm；'
                      f'{slot} 控制器原点为 {res["setup"]["work_offset_origin_machine_mm"]} mm。'
                      '所有尺寸及高度标注沿用原 CAD；程序坐标减去工件零点。无旋转、无刀长补偿，夹具待审核。')
        write(dest/'process-plan.json',card);write(dest/'resources-software-reference.json',res)
        spec=copy.deepcopy(old_specs[parent]);spec.update(id=key,name=title,source_case=parent,
            coordinate_setup=copy.deepcopy(res['setup']),card_origin_note='复用已归档原 CAD / 工步；新增显式坐标配置，生产审核待定')
        paper(dest/'process-card.pdf',spec,card,res)
        entry['id']='S4-WORK-COORDINATE-'+key+'-'+revision
        entry['scope']['declared_preconditions']['OP10.1']='采用本卡明确的原 CAD 零点、独立装夹位置及控制器寄存器；实际夹具仍未验证。'
        for value in entry['files'].values():value['sha256']=digest(dest/value['path'])
        write(dest/'input.json',entry)
        ready=prepare(dest/'input.json')
        if ready['status']!='ready_for_declared_scope':raise ValueError(ready['issues'])
        write(dest/'archive.json',{'source_input_sha256':digest(src/'input.json'),
            'original_cad_and_criteria_reused_unchanged':{name:digest(dest/name) for name in sorted(immutable)},
            'frozen_before_nc':True,'production_approved':False})
        specs.append(spec)
    write(out/'specifications.json',{'schema':'genlaw.work-coordinate-fixtures.v1','cases':specs,
        'created_before_nc':True,'source_manifest_sha256':digest(SOURCE/'manifest.json'),
        'not_yet_supported':['tool_length_compensation','rotations','fixtures','multiple_setups','five_axes'],
        'production_approved':False})
    write(out/'manifest.json',{'id':out.name,'source_sha256':digest(Path(__file__)),
        'paper_writer_sha256':digest(ROOT/'tools/build_sequence_inputs.py'),
        'files':{str(p.relative_to(out)):{'bytes':p.stat().st_size,'sha256':digest(p)}
                 for p in sorted(out.rglob('*')) if p.is_file()}})
    with zipfile.ZipFile(out.with_suffix('.zip'),'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(out.rglob('*')):
            if p.is_file():archive.write(p,str(p.relative_to(out)))
    print(json.dumps({'status':'archived_before_cam','cases':len(specs),'output':str(out)}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True,type=Path)
    build(p.parse_args().output)
