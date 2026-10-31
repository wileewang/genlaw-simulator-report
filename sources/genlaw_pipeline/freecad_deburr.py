"""Load a version-bound native Deburr correction only when explicitly requested."""
import importlib.util
import json
from pathlib import Path
import sys
from .preparation import digest

ROOT=Path(__file__).resolve().parents[1]
FORMAL={'path':'engines/freecad/deburr-linear-height-r2/manifest.json',
        'sha256':'d9d124ffe9eeafd311c68463971dc118ba47b578219dda9137c0b90739d19008'}


def checked_formal_backend(reference):
    if reference!=FORMAL:raise ValueError('Require the explicitly versioned Deburr backend')
    manifest=ROOT/reference['path']
    if digest(manifest)!=reference['sha256']:raise ValueError('Deburr backend manifest changed')
    data=json.loads(manifest.read_text())
    for path,expected in [(data['upstream_path'],data['upstream_sha256']),
                          (data['module_path'],data['module_sha256']),(data['patch_path'],data['patch_sha256'])]+[(b['path'],b['sha256']) for b in data['dependency_bindings']]:
        source=(ROOT/path).resolve()
        if not source.is_relative_to(ROOT) or digest(source)!=expected:raise ValueError('Changed Deburr backend source or dependency')
    return dict(reference)


def load_overlay(reference):
    import Path.Op.Deburr as original
    import Path.Op as package
    manifest=(ROOT/reference['path']).resolve()
    if not manifest.is_relative_to(ROOT) or digest(manifest)!=reference['sha256']:
        raise ValueError('Changed or external Deburr overlay manifest')
    data=json.loads(manifest.read_text())
    for binding in data.get('dependency_bindings',[]):
        path=(ROOT/binding['path']).resolve()
        if not path.is_relative_to(ROOT) or digest(path)!=binding['sha256']:raise ValueError('Changed Deburr dependency')
    if data['schema']!='genlaw.freecad-operation-overlay.v1' or digest(Path(original.__file__))!=data['upstream_sha256']:
        raise ValueError('Deburr overlay does not match the installed upstream operation')
    module_path=(ROOT/data['module_path']).resolve();patch=(ROOT/data['patch_path']).resolve()
    if not module_path.is_relative_to(ROOT) or not patch.is_relative_to(ROOT):raise ValueError('Overlay paths outside workspace')
    if digest(module_path)!=data['module_sha256'] or digest(patch)!=data['patch_sha256']:
        raise ValueError('Changed Deburr source or patch')
    spec=importlib.util.spec_from_file_location('Path.Op.Deburr',module_path)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module
    spec.loader.exec_module(module);package.Deburr=module
    return module,{'manifest':reference,'module_sha256':data['module_sha256'],'upstream_sha256':data['upstream_sha256']}
