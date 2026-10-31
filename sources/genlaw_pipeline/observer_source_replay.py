"""Read saved observer evidence with an explicitly resolved historical source.

This is a regression reader, not a production plan upgrade. The historical hole
observer may differ only in the body of the unused full-workflow ``verify``
dispatcher. All module statements, other functions and signatures must match.
The original plan and its recorded source binding remain unchanged.
"""
import ast
import copy
import json
from pathlib import Path

from .cell_windows import checked_file, file_binding
from .sharded_measure import checked_surface

ROOT = Path(__file__).resolve().parents[1]
LOAD = lambda p: json.loads(p.read_text())


def require_dispatch_only_change(original, current):
    """Ignore only the uncalled dispatch body; keep its defaults/decorators."""
    def signature(source):
        tree = ast.parse(source)
        matches = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'verify']
        if len(matches) != 1:
            raise ValueError('Expected one historical verify dispatcher')
        node = matches[0]
        if node.decorator_list:
            raise ValueError('A dispatcher decorator could change replay behavior')
        node.body = [ast.Pass()]
        return ast.dump(tree, include_attributes=False)
    if signature(original) != signature(current):
        raise ValueError('Historical observer differs outside the unused dispatch body')


def checked_saved_plan(plan_path, historical_source):
    """Resolve the one historical source without rewriting old provenance."""
    plan = LOAD(plan_path)
    if (plan.get('schema') != 'genlaw.sharded-feature-plan.v1'
            or plan['source_code'] != file_binding(ROOT / 'genlaw_pipeline/sharded_verification.py')):
        raise ValueError('Unsupported saved observer plan')
    for key in ('source_code', 'original_evaluator', 'measure_plan', 'reference', 'policy',
                'surface', 'prepared_source', 'native_cam', 'face_only_observer'):
        checked_file(plan[key])
    expected = plan['original_hole_observer']
    historical = checked_file(historical_source)
    current = ROOT / 'genlaw_pipeline/verification.py'
    if (expected['path'] != 'genlaw_pipeline/verification.py'
            or historical_source['sha256'] != expected['sha256']):
        raise ValueError('Historical hole observer does not match the original plan')
    require_dispatch_only_change(historical.read_text(), current.read_text())
    if 'checkpoint_context' in plan:
        from .checkpoint_context import checked_context
        checked_context(checked_file(plan['checkpoint_context']))
    if 'test_control' in plan:
        from .window_controls import checked_control
        checked_file(plan['test_control_validator'])
        checked_control(checked_file(plan['test_control']), checked_file(plan['reference']).parent)
    measure = LOAD(checked_file(plan['measure_plan']))
    checked_file(measure['source_code'])
    if measure['surface'] != plan['surface'] or measure['reference'] != plan['reference']:
        raise ValueError('Saved observer and material-integral plans differ')
    surface = checked_surface(checked_file(plan['surface']))
    reference = LOAD(checked_file(plan['reference']))
    if 'profile_feature' in reference:
        checked_file(plan['profile_observer']); checked_file(plan['profile_geometry'])
    replay = {'original_source_binding': copy.deepcopy(expected),
        'resolved_historical_source': historical_source,
        'current_source': file_binding(current), 'only_uninvoked_dispatch_body_changed': True,
        'original_plan_modified': False, 'production_plan_upgrade': False,
        'source_code': file_binding(Path(__file__))}
    return plan, surface, reference, replay
