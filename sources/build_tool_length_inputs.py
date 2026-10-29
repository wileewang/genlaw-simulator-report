"""Archive independent original CAD, paper cards and actual/compensated lengths before CAM."""
import argparse
import copy
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from genlaw_pipeline.preparation import digest,prepare
from genlaw_pipeline.run import write
from genlaw_pipeline.coordinate_setup import validate_setup,setup_requirements
from genlaw_pipeline.tool_length_setup import SCHEMA
from tools.build_sequence_inputs import paper

SOURCE=ROOT/'validation/feature-inputs/S4-POCKET-SEQUENCE-20261030-r1'
LOAD=lambda p:json.loads(p.read_text())
CASES=[
    ('single-tool','baseline','单刀静态刀长补偿',[10,20,30],[0,0,0],'G54',[30.4]),
    ('cad-origin','floor-hole','非零工件零点与刀长补偿',[-10,-20,-30],[4,3,-.5],'G55',[30.4]),
    ('shorter-tool','tool-change','换成较短的刀具',[10,20,30],[4,3,-.5],'G55',[30.4,30.0]),
    ('longer-tool','tool-change','换成较长的刀具',[10,20,30],[4,3,-.5],'G55',[30.4,30.8]),
]


def setup_for(p,d,slot,lengths):
    return {'schema':SCHEMA,'units':'mm','height_parameter_frame':'original_cad',
        'material_origin_machine_mm':p,'work_origin_cad_mm':d,
        'work_offset_origin_machine_mm':[a+b for a,b in zip(p,d)],'work_offset':slot,
        'rotation_degrees':[0,0,0],'fixture_mode':'unmodelled_explicitly_excluded',
        'basis':'Explicit synthetic absolute gauge reference, original CAD and material placement fixed; no fitted alignment.',
        'tool_length_compensation':{'mode':'static_Z_absolute_gauge',
            'actual_lengths_mm':[{'tool_number':i,'gauge_to_tip_mm':v} for i,v in enumerate(lengths,1)],
            'registers_mm':[{'h_register':i,'offset_mm':v} for i,v in enumerate(lengths,1)],
            'tool_bindings':[{'tool_number':i,'h_register':i} for i in range(1,len(lengths)+1)],
            'minimum_clearance_above_stock_mm':1.0,
            'basis':'Declared actual mounted lengths and independently initialized controller table; nominal equality in the absolute gauge reference is checked, never inferred.'},
        'startup':{'position_mm':[0,0,0],'position_frame':'machine','tool':'unloaded','spindle':'stopped',
            'retract_before_tool_selection_z_mm':round(p[2]+max(lengths)+3,5),
            'basis':'Unloaded G53 gauge retract clears original stock for the longest mounted tool before the first M6.'}}


def build(out):
    from local_resource_limits import require_bounded_worker
    require_bounded_worker();out=out.resolve();out.relative_to(ROOT);out.mkdir(parents=True,exist_ok=False)
    revision=out.name.rsplit('-',1)[-1]; old_specs={s['id']:s for s in LOAD(SOURCE/'specifications.json')['cases']}
    frozen=LOAD(SOURCE/'manifest.json')['files'];specs=[]
    for key,parent,title,p,d,slot,lengths in CASES:
        src=SOURCE/parent;dest=out/key;dest.mkdir();entry=LOAD(src/'input.json')
        for item in entry['files'].values():
            if digest(src/item['path'])!=item['sha256'] or item['sha256']!=frozen[parent+'/'+item['path']]['sha256']:
                raise ValueError('Original frozen source changed')
        replaced={'process-plan.json','resources-software-reference.json','process-card.pdf'}
        immutable={item['path'] for item in entry['files'].values()}-replaced
        immutable.update(['target.FCStd','software-policy.json'])
        for name in sorted(immutable):
            if digest(src/name)!=frozen[parent+'/'+name]['sha256']:raise ValueError('Original CAD/criteria changed')
            shutil.copy2(src/name,dest/name)
        card=LOAD(src/'process-plan.json');res=LOAD(src/'resources-software-reference.json')
        card['id']='S4-TOOL-LENGTH-'+key+'-CARD-'+revision;res['id']='S4-TOOL-LENGTH-'+key+'-RESOURCES-'+revision
        res['setup']=setup_for(p,d,slot,lengths);validate_setup(res['setup'],res['stock']['bounds_mm'])
        step=card['operations'][1]['steps'][0];step['constraints']={'coordinate_setup':setup_requirements(res['setup'])}
        values='；'.join(f'T{i} 实装 L={v:g} mm、独立 H{i}={v:g} mm' for i,v in enumerate(lengths,1))
        step['text']=(f'材料原点机床坐标 {p} mm；工件零点原 CAD 坐标 {d} mm；{slot} 寄存器为 {res["setup"]["work_offset_origin_machine_mm"]} mm。'
                      f'{values}。换刀后原生 G43 Hn 加载补偿；前后刀尖距原来料至少 1 mm。尺寸及高度仍用原 CAD，XYZ 无旋转；夹具待审核。')
        write(dest/'process-plan.json',card);write(dest/'resources-software-reference.json',res)
        spec=copy.deepcopy(old_specs[parent]);spec.update(id=key,name=title,source_case=parent,
            coordinate_setup=copy.deepcopy(res['setup']),card_origin_note='复用已存档 CAD · 刀长补偿测试 · 工艺师傅审核待定')
        paper(dest/'process-card.pdf',spec,card,res)
        entry['id']='S4-TOOL-LENGTH-'+key+'-'+revision
        entry['scope']['declared_preconditions']['OP10.1']='按本卡独立原 CAD / 机床零点、实际装刀长度和 H 表配置；真实夹具仍未验证。'
        for value in entry['files'].values():value['sha256']=digest(dest/value['path'])
        write(dest/'input.json',entry)
        ready=prepare(dest/'input.json')
        if ready['status']!='ready_for_declared_scope':raise ValueError(ready['issues'])
        write(dest/'archive.json',{'source_input_sha256':digest(src/'input.json'),
            'original_cad_and_criteria_reused_unchanged':{name:digest(dest/name) for name in sorted(immutable)},
            'frozen_before_nc':True,'production_approved':False})
        specs.append(spec)
    write(out/'specifications.json',{'schema':'genlaw.tool-length-fixtures.v1','cases':specs,
        'created_before_nc':True,'source_manifest_sha256':digest(SOURCE/'manifest.json'),
        'not_yet_supported':['dynamic_tool_compensation','rotations','fixtures','multiple_setups','five_axes'],
        'production_approved':False})
    write(out/'manifest.json',{'id':out.name,'source_sha256':digest(Path(__file__)),
        'paper_writer_sha256':digest(ROOT/'tools/build_sequence_inputs.py'),
        'files':{str(p.relative_to(out)):{'bytes':p.stat().st_size,'sha256':digest(p)} for p in sorted(out.rglob('*')) if p.is_file()}})
    with zipfile.ZipFile(out.with_suffix('.zip'),'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(out.rglob('*')):
            if path.is_file():archive.write(path,str(path.relative_to(out)))
    print(json.dumps({'status':'archived_before_cam','cases':len(specs),'output':str(out)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    build(parser.parse_args().output)
