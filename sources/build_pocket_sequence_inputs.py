"""Four archived paper-card / CAD inputs for layered pocket-and-hole composition."""
import argparse
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from genlaw_pipeline.pocket_sequence_geometry import CAPABILITY
from genlaw_pipeline.run import write
from tools.build_sequence_inputs import build


def specifications():
    cases = []
    for key, name, centers, order, two in [
        ('baseline', '面铣 → 圆角型腔 → 腔外孔', [(6.5,3)], ['P1','K1','H1'], False),
        ('floor-hole', '面铣 → 型腔 → 腔底孔及腔外孔', [(3,3),(6.5,3)], ['P1','K1','H1','H2'], False),
        ('tool-change', 'φ1 面铣 / 型腔 → 换 φ0.5 铣双孔', [(3,3),(6.5,3)], ['P1','K1','H1','H2'], True),
        ('holes-first', '另一张明确卡片：面铣 → 双孔 → 型腔', [(3,3),(6.5,3)], ['P1','H1','H2','K1'], False),
    ]:
        cases.append({'id':key, 'name':name, 'stock_bounds_mm':{'min':[0,0,-1.5], 'max':[8,6,0]},
            'target_bounds_mm':{'min':[0,0,-1.5], 'max':[8,6,-.5]}, 'resolution_mm':.025,
            'pocket':{'id':'K1', 'kind':'rounded_rectangular_pocket', 'axis':[0,0,1], 'center_xy_mm':[3,3],
                      'width_mm':4, 'height_mm':3, 'corner_radius_mm':.6, 'depth_mm':.5},
            'holes':[{'id':f'H{i+1}', 'kind':'through_hole', 'axis':[0,0,1], 'center_xy_mm':list(xy), 'diameter_mm':1.5}
                     for i, xy in enumerate(centers)], 'order':order, 'two_tools':two,
            'tool_diameter_mm':1, 'second_tool_diameter_mm':.5})
    return {'schema':'genlaw.pocket-sequence-fixtures.v1', 'stage':'s4-pocket-sequence', 'capability':CAPABILITY,
        'cases':cases, 'selection':'Synthetic original CAD: disjoint cavity/outside hole, then floor-hole interaction, explicit tool change, and a separate holes-first card. No automatic reordering.',
        'production_approved':False, 'real_customer_card_passed':False,
        'not_yet_supported':['partial_pocket_wall_holes', 'multiple_pockets', 'islands', 'nonzero_work_offsets',
                             'nonzero_tool_length_offsets', 'fixtures', 'material_physics', 'multiple_setups', 'five_axes']}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True); p.add_argument('--specification-file', type=Path, required=True)
    a = p.parse_args()
    if not a.specification_file.resolve().is_relative_to(ROOT): raise ValueError('Keep all work in project')
    a.specification_file.parent.mkdir(parents=True, exist_ok=True)
    write(a.specification_file, specifications()); build(a.output, specification_file=a.specification_file)
