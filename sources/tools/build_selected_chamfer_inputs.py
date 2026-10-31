"""Freeze paper cards, CAD, resource choices and criteria for selected-edge milling."""
import argparse
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from genlaw_pipeline.admission_wait import admitted_bounded
from genlaw_pipeline.cell_windows import file_binding, checked_file
from genlaw_pipeline.freecad_deburr import FORMAL
from genlaw_pipeline.run import FREECAD, write, workspace_environment
from genlaw_pipeline.selected_chamfer_geometry import ACCEPTANCE


def specifications():
    cases = []
    for key, name, sides, width, lo in [
        ('one-edge', '单条长边倒角', ['ymin'], .4, [0, 0, -2]),
        ('opposite-long', '两条相对长边倒角', ['ymin', 'ymax'], .4, [0, 0, -2]),
        ('opposite-short', '两条相对短边倒角', ['xmin', 'xmax'], .4, [0, 0, -2]),
        ('shifted-wider', '非零坐标与加宽倒角', ['ymin', 'ymax'], .6, [-3, 4, 1])]:
        # X is the 6 mm long direction; full original 6 x 4 x 2 mm stock is retained.
        hi = [lo[0]+6, lo[1]+4, lo[2]+2]
        cases.append({'id': key, 'name': name, 'revision': 'r1',
            'stock': {'min': lo, 'max': hi}, 'material_label': '6061-T6 software reference',
            'width_mm': width, 'angle_degrees': 45, 'selected_sides': sides, 'resolution_mm': .025,
            'tool': {'id': 'T1', 'kind': 'finite_frustum', 'diameter_mm': 2.,
                'tip_diameter_mm': .4, 'included_angle_degrees': 90.,
                'cutting_length_mm': .8, 'length_mm': 20.},
            'cam': {'native_operation': 'Deburr', 'backend': FORMAL,
                'extra_depth_mm': .1, 'path_tolerance_mm': .001,
                'safe_height_mm': hi[2]+2, 'clearance_height_mm': hi[2]+3,
                'spindle_rpm': 3000, 'feed_mm_min': 120, 'plunge_mm_min': 60},
            'acceptance': ACCEPTANCE})
    return {'schema': 'genlaw.selected-chamfer-specifications.v1', 'cases': cases,
        'development_dimension': 'part_features',
        'selection': 'Exercise explicit one/two parallel edge selection, preserving every unselected edge. Synthetic standalone coupons; no customer drawing identity assumed.',
        'source_requirement_motivation': 'Existing cards contain two specified C3 edges, which cannot be expanded to all edges without a drawing binding.',
        'real_C3_part_size_tested': False, 'whole_real_card_complete': False, 'manufacturing_approval': False}


def paper(path, spec):
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.pagesizes import A4
    for name, filename in [('SelectedLatin', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
                           ('SelectedCJK', '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf')]:
        if name not in pdfmetrics.getRegisteredFontNames(): pdfmetrics.registerFont(TTFont(name, filename))
    def font(ch): return 'SelectedLatin' if ord(ch) in pdfmetrics.getFont('SelectedLatin').face.charToGlyph else 'SelectedCJK'
    def width(text, size): return sum(pdfmetrics.stringWidth(ch, font(ch), size) for ch in text)
    c = canvas.Canvas(str(path), pagesize=A4); c.setTitle('SELECTED-'+spec['id']+'-r1')
    def text(x, y, value, size=10):
        for ch in value:
            c.setFont(font(ch), size); c.drawString(x, y, ch); x += pdfmetrics.stringWidth(ch, font(ch), size)
    def lines(y, value):
        line = ''
        for token in re.findall(r'[A-Za-z0-9_.+°/×()%-]+|.', value):
            if line and width(line+token, 10) > 495:
                text(45, y, line); y -= 17; line = ''
            line += token
        if line: text(45, y, line); y -= 17
        if y < 65: raise ValueError('Paper card overflow')
        return y
    lo, hi = spec['stock']['min'], spec['stock']['max']; sides = spec['selected_sides']; w = spec['width_mm']
    names = {'xmin': 'X 最小端', 'xmax': 'X 最大端', 'ymin': 'Y 最小侧', 'ymax': 'Y 最大侧'}
    def header(page):
        text(45, 796, 'GenLaw 指定边倒角验证工艺卡', 17)
        text(45, 766, 'SELECTED-'+spec['id']+'-r1 / '+spec['name'])
        text(45, 43, f'第 {page} / 2 页 · 合成软件样件 · 编制 / 审核 / 生产批准：待定', 9)
    header(1)
    y = lines(725, f'零件与原毛坯：6 × 4 × 2 mm 单实体矩形块；材料标签 6061-T6。上侧指定 {len(sides)} 条完整直边加工 C{w:g}，45°；其余边保留锐边。')
    y = lines(y-10, '指定边：'+'、'.join(names[s] for s in sides)+'。CAD 坐标下界 '+str(lo)+'，上界 '+str(hi)+' mm。')
    # Top view includes all four original edges so omitted and selected edges are explicit.
    x, y0, scale = 140, 380, 45
    c.setLineWidth(1); c.setStrokeColorRGB(.4, .4, .4); c.rect(x, y0, 6*scale, 4*scale)
    c.setLineWidth(5); c.setStrokeColorRGB(.05, .35, .75)
    coords = {'xmin': (0, 0, 0, 4), 'xmax': (6, 0, 6, 4), 'ymin': (0, 0, 6, 0), 'ymax': (0, 4, 6, 4)}
    for side in sides:
        a, b, d, e = coords[side]; c.line(x+a*scale, y0+b*scale, x+d*scale, y0+e*scale)
    c.setStrokeColorRGB(0, 0, 0); c.setLineWidth(1)
    text(x+90, y0-25, 'Y 最小侧'); text(x+90, y0+4*scale+15, 'Y 最大侧')
    text(x-70, y0+80, 'X 最小端'); text(x+6*scale+10, y0+80, 'X 最大端')
    lines(325, '俯视示意：蓝线为需倒角的上边；灰线为必须保留的其他上边。模型方向和位置以随卡独立 CAD 为准。')
    lines(255, 'OP05 核对毛坯和 CAD 版本 → OP10 装夹对刀 → OP20 指定边倒角 → OP30 对照完整 CAD 检验。')
    lines(195, '本卡参考真实工艺卡“指定若干边倒角”的表达，尺寸和资源为独立验证试件。未替代真实卡片 C3，也未补填缺失的配合尺寸或图号。')
    c.showPage(); header(2); y = 725
    rows = [
        'OP10：通用三轴 XYZ、固定 Z 刀轴、原 CAD 坐标，G54 零偏置，mm，G17/G90/G40/G49。起始主轴停止且无刀，先抬至安全清空高度再装 T1。夹具与刀柄实体未加入本样件。',
        f'OP20：T1 90° 有限截锥倒角刀，外径 2 mm、刀尖直径 0.4 mm、有效刃高 0.8 mm、总长 20 mm；额外下刀 0.1 mm，最终刀尖 Z{hi[2]-w-.1:g}。S3000，水平 F120，下刀 F60 mm/min。',
        f'安全高度 Z{hi[2]+2:g}，清空高度 Z{hi[2]+3:g}。由 FreeCAD 原生 Deburr 选择对应 CAD 倒角面生成路径，自带后处理输出 NC。原始 NC 不手工改写。',
        '逐条执行指定边；不得自动连成整圈，不得漏边或增加其他边。转移到下一条边前抬刀。路径容差 0.001 mm，仿真网格 0.025 mm，原始整块毛坯不可裁小。',
        'OP30 软件验收：完整剩料 IoU≥99%，外包框偏差≤0.025 mm，绝对体积差≤0.03 mm³；完整截面、各指定边宽度和未选边 / 顶底面偏差≤0.1 mm，另留 0.05 mm 数值保护带。',
        '完整剩料截面积分采用 2 / 3 阶对照，IoU 差≤1e-6；目标解析体积误差≤1e-8 mm³，网格体积与积分差≤1e-4 mm³。不拟合对齐、不删除散落或多余材料。',
        '反例预定：漏加工一条指定边、错加工到未选边或扩大为整圈。即使全局 IoU 较高，也必须按指定边和保留边检验拒绝。',
        '未验收：实际机床控制器、真实装夹与刀柄碰撞、材料物理、粗糙度和制造尺寸 / 角度公差。工艺师傅须另行确认卡片、刀具参数及验收值。']
    for row in rows: y = lines(y, row)-17
    c.save()


def finalize(out):
    from genlaw_pipeline.selected_chamfer_geometry import SelectedChamferReference
    specs = json.loads((out/'specifications.json').read_text())
    for spec in specs['cases']:
        case = out/spec['id']; ref = json.loads((case/'reference.json').read_text()); checked_file(ref['cad'])
        if abs(SelectedChamferReference(ref).analytic_volume-ref['volume_mm3']) > 1e-8:
            raise ValueError('Unbound original CAD')
        write(case/'process-plan.json', {'schema': 'genlaw.selected-chamfer-card.v1',
            'id': 'SELECTED-'+spec['id']+'-r1', 'CAD': ref['cad'],
            'operations': [{'id': 'OP05', 'type': 'incoming_inspection'},
                {'id': 'OP10', 'type': 'setup', 'frame': 'original_CAD', 'rotary_motion': False},
                {'id': 'OP20', 'type': 'selected_edge_chamfer', 'width_mm': spec['width_mm'],
                    'angle_degrees': 45, 'selected_sides': spec['selected_sides'], 'tool_id': 'T1'},
                {'id': 'OP30', 'type': 'geometry_inspection'}],
            'manufacturing_approval': False, 'whole_real_card_complete': False})
        paper(case/'process-card.pdf', spec)
        write(case/'input.json', {'schema': 'genlaw.selected-chamfer-input.v1', 'specification': spec,
            'CAD': ref['cad'], 'CAD_reference': file_binding(case/'reference.json'),
            'process_plan': file_binding(case/'process-plan.json'), 'paper_card': file_binding(case/'process-card.pdf'),
            'scope': 'experimental_software_geometry', 'material_physics_verified': False,
            'production_approved': False})
    files = [file_binding(p) for p in sorted(out.rglob('*')) if p.is_file()
        and p.suffix in ('.json', '.pdf', '.brep', '.step', '.FCStd', '.py')
        and p.name != 'manifest.json' and '-resources' not in p.name and '-capacity' not in p.name]
    write(out/'manifest.json', {'schema': 'genlaw.selected-chamfer-input-archive.v1',
        'files': files, 'all_inputs_saved_before_NC': True, 'paper_cards': 4,
        'independent_native_CADs': 4, 'manufacturing_approval': False})


def build(out):
    out = out.resolve(); out.relative_to(ROOT); out.mkdir(parents=True, exist_ok=False)
    write(out/'specifications.json', specifications())
    for p in [Path(__file__), ROOT/'tools/build_selected_chamfer_cad.py',
              ROOT/'genlaw_pipeline/selected_chamfer_geometry.py']:
        dest = out/'sources'/p.relative_to(ROOT); dest.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(p, dest)
    env = workspace_environment(); env.update(QT_QPA_PLATFORM='offscreen', GENLAW_SELECTED_CHAMFER_CAD=str(out))
    admitted_bounded(out, 'generate-CAD', [str(FREECAD), 'freecadcmd', str(ROOT/'tools/build_selected_chamfer_cad.py')], env)
    if not (out/'cad-generation.json').is_file(): raise ValueError('Native CAD builder did not complete')
    admitted_bounded(out, 'archive-paper', [sys.executable, Path(__file__), 'finalize', '--output', out])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['build', 'finalize']); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.mode == 'build': build(args.output)
    else:
        from local_resource_limits import require_bounded_worker
        require_bounded_worker(); finalize(args.output.resolve())
