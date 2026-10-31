"""Reuse unchanged pre-NC CAD/paper while explicitly versioning the CAM backend."""
import argparse
import copy
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from genlaw_pipeline.cell_windows import checked_file, file_binding
from genlaw_pipeline.freecad_selected_deburr import FORMAL, checked_formal_backend
from genlaw_pipeline.run import write
from step2gcode_demo.validation.local_resource_limits import require_bounded_worker

READ = lambda p: json.loads(Path(p).read_text())


def build(previous, out):
    require_bounded_worker(); out.relative_to(ROOT); out.mkdir(parents=True, exist_ok=False)
    prior = READ(previous/'manifest.json')
    if prior['schema'] != 'genlaw.selected-chamfer-input-archive.v1' or prior['all_inputs_saved_before_NC'] is not True:
        raise ValueError('Require the original independent input archive')
    for binding in prior['files']: checked_file(binding)
    checked_formal_backend(FORMAL)
    specs = copy.deepcopy(READ(previous/'specifications.json')); records = []
    for spec in specs['cases']:
        old, case = previous/spec['id'], out/spec['id']; case.mkdir()
        old_spec = copy.deepcopy(spec); spec['cam']['backend'] = dict(FORMAL)
        for filename in ['target.brep', 'target.step', 'target.FCStd', 'process-card.pdf']:
            shutil.copyfile(old/filename, case/filename)
            if file_binding(old/filename)['sha256'] != file_binding(case/filename)['sha256']:
                raise ValueError('Original CAD or paper bytes changed')
        cad = file_binding(case/'target.brep'); ref = READ(old/'reference.json'); ref['cad'] = cad
        write(case/'reference.json', ref)
        card = READ(old/'process-plan.json'); card['CAD'] = cad; write(case/'process-plan.json', card)
        entry = READ(old/'input.json'); entry.update(specification=spec, CAD=cad,
            CAD_reference=file_binding(case/'reference.json'), process_plan=file_binding(case/'process-plan.json'),
            paper_card=file_binding(case/'process-card.pdf'))
        write(case/'input.json', entry)
        comparison = copy.deepcopy(spec); comparison['cam']['backend'] = old_spec['cam']['backend']
        if comparison != old_spec: raise ValueError('Unexpected geometry, resource or criterion change')
        records.append({'id': spec['id'], 'original_CAD': file_binding(old/'target.brep'), 'CAD': cad,
            'original_paper': file_binding(old/'process-card.pdf'), 'paper': file_binding(case/'process-card.pdf'),
            'CAD_and_paper_byte_identical': True, 'only_execution_setting_changed': 'cam.backend'})
    write(out/'specifications.json', specs)
    write(out/'reused-original-inputs.json', {'previous_archive': file_binding(previous/'manifest.json'),
        'new_backend': FORMAL, 'cases': records, 'native_CAD_regenerated': False, 'paper_regenerated': False,
        'acceptance_changed': False, 'manufacturing_approval': False})
    for source in [Path(__file__), ROOT/'genlaw_pipeline/freecad_selected_deburr.py',
                   ROOT/'engines/freecad/deburr-selected-edge-side-r1/Deburr.py']:
        dest = out/'sources'/source.relative_to(ROOT); dest.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, dest)
    files = [file_binding(p) for p in sorted(out.rglob('*')) if p.is_file()]
    write(out/'manifest.json', {'schema': 'genlaw.selected-chamfer-input-archive.v2', 'files': files,
        'all_inputs_saved_before_NC': True, 'paper_cards': 4, 'independent_native_CADs': 4,
        'original_CAD_and_paper_reused': True, 'manufacturing_approval': False})


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--previous', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True); a = p.parse_args(); build(a.previous.resolve(), a.output.resolve())
