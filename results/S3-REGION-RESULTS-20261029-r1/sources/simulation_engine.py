"""Bind the selected CAMotics executable to a reviewed build and regression."""
import json
import os
from pathlib import Path

from .preparation import digest

ROOT = Path(__file__).resolve().parents[1]
SELECTION = ROOT / 'engines/camotics/selected-engine.json'


def workspace_path(relative):
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError('Engine paths must remain inside the workspace')
    return path


def configured_binary():
    """Read the configured path without starting or requiring a native runtime."""
    selection = json.loads(SELECTION.read_text())
    manifest_path = workspace_path(selection['manifest'])
    if digest(manifest_path) != selection['manifest_sha256']:
        raise ValueError('Engine manifest changed; revalidate before execution')
    return workspace_path(json.loads(manifest_path.read_text())['binary'])


def selected_engine():
    selection = json.loads(SELECTION.read_text())
    if selection['schema'] != 'genlaw.engine-selection.v1':
        raise ValueError('Unsupported engine selection')
    manifest_path = workspace_path(selection['manifest'])
    if digest(manifest_path) != selection['manifest_sha256']:
        raise ValueError('Engine manifest changed; revalidate before execution')
    manifest = json.loads(manifest_path.read_text())
    binary = workspace_path(manifest['binary'])
    if digest(binary) != manifest['binary_sha256']:
        raise ValueError('Engine binary changed; revalidate before execution')
    for library in manifest['linked_libraries']:
        path = Path(library['path'])
        if library['inside_workspace'] and digest(path) != library['sha256']:
            raise ValueError('Engine dependency changed; revalidate before execution')
    if not manifest['complete_nc_material_removal_engine'] or not manifest['patched_vertical_conic_sweep']:
        raise ValueError('Selected engine has not integrated the vertical sweep correction')
    regression = workspace_path(selection['regression'])
    if digest(regression) != selection['regression_sha256']:
        raise ValueError('Engine regression binding changed')
    result = json.loads(regression.read_text())
    if (result['status'] != 'regression_passed' or result['engine_manifest_sha256'] != digest(manifest_path)
            or len(result['normal_cases']) != 4 or len(result['controls']) != 2
            or not all(x['regression_expectation_met'] for x in result['normal_cases'] + result['controls'])):
        raise ValueError('Selected engine requires the four-case and negative-control regression')
    if manifest.get('patched_horizontal_cylinder_sweep'):
        if not selection.get('horizontal_regression'):
            raise ValueError('Horizontal cylinder correction requires an unchanged-NC complete-engine control')
        control_path=workspace_path(selection['horizontal_regression'])
        if digest(control_path)!=selection.get('horizontal_regression_sha256'):
            raise ValueError('Horizontal cylinder regression binding changed')
        control=json.loads(control_path.read_text())
        if (control.get('status')!='passed' or not control.get('all_software_checks_passed')
                or not control.get('original_nc_and_configuration_unchanged')
                or control.get('engine_manifest_sha256')!=digest(manifest_path)
                or control.get('engine_binary_sha256')!=manifest['binary_sha256']):
            raise ValueError('Horizontal cylinder correction requires an unchanged-NC complete-engine control')
    return {'binary': binary, 'manifest': manifest, 'manifest_path': manifest_path,
            'manifest_sha256': digest(manifest_path), 'selection_sha256': digest(SELECTION)}


def workspace_environment():
    env = dict(os.environ)
    for key, name in [('TMPDIR', 'tmp'), ('XDG_CACHE_HOME', 'cache'), ('XDG_CONFIG_HOME', 'config')]:
        path = ROOT / '.runtime' / name
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    return env


def simulation_environment():
    engine = selected_engine()
    env = workspace_environment()
    env.pop('LD_LIBRARY_PATH', None)
    if engine['manifest']['library_directories']:
        env['LD_LIBRARY_PATH'] = ':'.join(str(workspace_path(p)) for p in engine['manifest']['library_directories'])
    return env
