"""Exercise archived input and native NC rejection, without running CAM or material."""
import argparse
import copy
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from genlaw_pipeline.cell_windows import file_binding
from genlaw_pipeline.selected_chamfer_preparation_v2 import checked_archive, prepare
from genlaw_pipeline.selected_chamfer_nc import check_nc
from genlaw_pipeline.run import write
from step2gcode_demo.validation.local_resource_limits import require_bounded_worker

READ = lambda p: json.loads(Path(p).read_text())


def check(inputs, runs, out):
    require_bounded_worker(); out.mkdir(parents=True, exist_ok=False)
    archive = file_binding(inputs/'manifest.json'); cases = READ(inputs/'specifications.json')['cases']
    normals = []; rejections = []
    for spec in cases:
        entry = inputs/spec['id']/'input.json'; plan, admission = checked_archive(entry, archive)
        if runs:
            case = runs/spec['id']; saved = READ(case/'preparation-result.json'); result = READ(case/'cam-result.json')
            if plan != saved or check_nc((case/'program.nc').read_text(), plan) != result['nc_check']:
                raise ValueError('Normal archived preparation/NC changed')
        normals.append({'id': spec['id'], 'input': file_binding(entry), 'admission': admission})
    entry = inputs/'one-edge/input.json'; original = READ(entry)
    def reject(name, callback):
        try: callback()
        except ValueError as error: rejections.append({'id': name, 'rejection': str(error)})
        else: raise AssertionError('Bad input or NC was accepted: '+name)
    mutations = [
        ('unselected-to-selected', ('specification', 'selected_sides'), ['ymin', 'ymax']),
        ('unknown-edge', ('specification', 'selected_sides'), ['zmin']),
        ('duplicate-edge', ('specification', 'selected_sides'), ['ymin', 'ymin']),
        ('adjacent-edges-unqualified', ('specification', 'selected_sides'), ['xmin', 'ymin']),
        ('boolean-width', ('specification', 'width_mm'), True),
        ('negative-width', ('specification', 'width_mm'), -.4),
        ('zero-width', ('specification', 'width_mm'), 0),
        ('changed-width', ('specification', 'width_mm'), .5),
        ('changed-stock', ('specification', 'stock', 'max'), [5, 4, 0]),
        ('changed-resolution', ('specification', 'resolution_mm'), .05),
        ('insufficient-cutting-height', ('specification', 'tool', 'cutting_length_mm'), .4),
        ('wrong-tool-angle', ('specification', 'tool', 'included_angle_degrees'), 60),
        ('unsafe-rapid-plane', ('specification', 'cam', 'safe_height_mm'), -.1),
        ('tool-reach-exceeded', ('specification', 'cam', 'extra_depth_mm'), .5),
        ('zero-feed', ('specification', 'cam', 'feed_mm_min'), 0),
        ('weakened-acceptance', ('specification', 'acceptance', 'iou_percent_min'), 90),
        ('foreign-card', ('process_plan',), READ(inputs/'opposite-long/input.json')['process_plan']),
        ('foreign-CAD', ('CAD',), READ(inputs/'opposite-long/input.json')['CAD']),
        ('unsupported-production-claim', ('production_approved',), True)]
    for name, keys, value in mutations:
        changed = copy.deepcopy(original); cursor = changed
        for key in keys[:-1]: cursor = cursor[key]
        cursor[keys[-1]] = value; dest = out/(name+'.json'); write(dest, changed)
        reject(name, lambda p=dest: prepare(p))
    # The PDF association is guarded by membership, not falsely claimed semantic OCR.
    changed = copy.deepcopy(original); changed['paper_card'] = READ(inputs/'opposite-long/input.json')['paper_card']
    dest = out/'foreign-paper.json'; write(dest, changed)
    reject('foreign-paper-archive', lambda: checked_archive(dest, archive))
    dest = out/'unreviewed-copy.json'; write(dest, original)
    reject('unreviewed-copy-archive', lambda: checked_archive(dest, archive))
    nc_rows = []
    if runs:
        plan, _ = checked_archive(entry, archive); nc = (runs/'one-edge/program.nc').read_text()
        checks = {'wrong-units': nc.replace('G21', 'G20', 1),
            'missing-operation': re.sub(r'\(begin operation: SelectedChamferEdges\).*?\(finish operation: SelectedChamferEdges\)', '', nc, flags=re.S),
            'undeclared-tool-length': nc.replace('M6 T1', 'M6 T1\nG43 H1', 1),
            'wrong-spindle': nc.replace('S3000', 'S3001', 1)}
        # Native fixed-depth cuts carry explicit F and Z words. Perturb only those
        # words; unchanged normal evidence remains on disk.
        for name, axis, old, new in [('wrong-horizontal-feed', 'F', 120., 121.),
            ('too-deep', 'Z', plan['final_tip_z_mm'], plan['final_tip_z_mm']-.2),
            ('too-shallow', 'Z', plan['final_tip_z_mm'], plan['final_tip_z_mm']+.2),
            ('wrong-side-position', 'Y', -.3, .3)]:
            def substitute(match):
                return axis+f'{new:.5f}' if abs(float(match[1])-old) < 1e-7 else match[0]
            checks[name] = re.sub(r'\b'+axis+r'([-+]?\d+(?:\.\d+)?)', substitute, nc)
        for name, changed_nc in checks.items():
            if changed_nc == nc: raise ValueError('Counterexample made no change: '+name)
            before = len(rejections); reject('NC-'+name, lambda value=changed_nc: check_nc(value, plan))
            nc_rows.append(rejections.pop(before))
    report = {'schema': 'genlaw.selected-chamfer-input-checks.v1', 'status': 'passed',
        'archive': archive, 'normal_inputs': normals, 'input_rejections': rejections,
        'native_NC_rejections': nc_rows, 'new_CAM_or_simulation_runs': 0,
        'source_code': file_binding(Path(__file__)), 'arbitrary_production_PDF_parsed': False,
        'production_approved': False}
    write(out/'result.json', report)
    print(json.dumps({'normal_inputs': len(normals), 'input_rejections': len(rejections), 'NC_rejections': len(nc_rows)}), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--inputs', type=Path, required=True)
    p.add_argument('--runs', type=Path); p.add_argument('--output', type=Path, required=True); a = p.parse_args()
    check(a.inputs.resolve(), a.runs.resolve() if a.runs else None, a.output.resolve())
