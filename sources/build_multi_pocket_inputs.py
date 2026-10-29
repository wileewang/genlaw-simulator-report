"""Freeze four paper-card / original-CAD inputs before any multi-pocket NC."""
import argparse
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from genlaw_pipeline.multi_pocket_geometry import CAPABILITY
from genlaw_pipeline.run import write
from tools.build_sequence_inputs import build


def specifications():
    cases = []
    for key, name, depths, with_holes, order in [
        ('equal-depth', '双型腔：相同深度，分别绑定底面', [.4, .4], False, ['P1','K1','K2']),
        ('different-depth', '双型腔：不同深度，分别检查剩余底厚', [.4, .7], False, ['P1','K1','K2']),
        ('floor-holes', '双型腔各带腔底孔：换刀后加工两孔', [.4, .7], True, ['P1','K1','K2','H1','H2']),
        ('interleaved-card', '另一张明确卡片：孔、型腔交错并多次换刀', [.4, .7], True, ['P1','H1','K2','H2','K1']),
    ]:
        cases.append({'id':key, 'name':name, 'stock_bounds_mm':{'min':[0,0,-1.5],'max':[8,6,0]},
            'target_bounds_mm':{'min':[0,0,-1.5],'max':[8,6,-.5]}, 'resolution_mm':.025,
            'pockets':[{'id':f'K{i+1}','kind':'rounded_rectangular_pocket','axis':[0,0,1],
                'center_xy_mm':[x,3],'width_mm':2.4,'height_mm':3,'corner_radius_mm':.6,'depth_mm':depths[i]}
                for i,x in enumerate([2,6])],
            'holes':[{'id':f'H{i+1}','kind':'through_hole','axis':[0,0,1],
                'center_xy_mm':[x,3],'diameter_mm':1} for i,x in enumerate([2,6])] if with_holes else [],
            'order':order,'two_tools':with_holes,'tool_diameter_mm':1,'second_tool_diameter_mm':.5})
    return {'schema':'genlaw.multi-pocket-fixtures.v1','stage':'s4-multi-pocket','capability':CAPABILITY,'cases':cases,
        'selection':'Two disjoint pockets: same-height face identity, independent depths, floor holes, and a separate explicitly interleaved card. All are original single-solid synthetic CAD; no automatic order optimization.',
        'production_approved':False,'real_customer_card_passed':False,
        'not_yet_supported':['intersecting_pockets','islands','partial_pocket_wall_holes','fixtures','material_physics','multiple_setups','five_axes']}


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--specification-file',type=Path,required=True)
    a=p.parse_args();a.specification_file.resolve().relative_to(ROOT)
    a.specification_file.parent.mkdir(parents=True,exist_ok=True)
    write(a.specification_file,specifications());build(a.output,specification_file=a.specification_file)
