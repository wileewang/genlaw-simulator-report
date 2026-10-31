"""Opt-in selected-face direction correction; the installed/default engine is unchanged."""
import json
from pathlib import Path
from .cell_windows import checked_file
from .freecad_deburr import FORMAL as PARENT, checked_formal_backend as checked_parent

FORMAL = {'path': 'engines/freecad/deburr-selected-edge-side-r1/manifest.json',
          'sha256': 'a5d392b2b0fc4e5f28f91e1f5835d7ff278016066f0c1e6b9d422bb34cba3e30'}


def checked_formal_backend(reference):
    if reference != FORMAL: raise ValueError('Require the explicit selected-face native backend')
    checked_parent(PARENT); manifest = json.loads(checked_file(reference).read_text())
    if manifest['parent_backend'] != PARENT: raise ValueError('Changed Deburr parent backend')
    checked_file(manifest['parent_source'])
    for name in ['upstream', 'module', 'patch']:
        checked_file({'path': manifest[name+'_path'], 'sha256': manifest[name+'_sha256']})
    for binding in manifest['dependency_bindings']: checked_file(binding)
    return dict(reference)
