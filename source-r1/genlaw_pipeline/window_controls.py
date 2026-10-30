"""Explicit geometric regression of a frozen rejected NC; never normal execution."""
import json
from pathlib import Path
from .cell_windows import file_binding
from .programming import checked_plan, check_nc, simulation_project, verify_binding

ROOT=Path(__file__).resolve().parents[1]
LOAD=lambda p:json.loads(p.read_text())


def checked_control(binding_path,parent):
    binding_path,parent=binding_path.resolve(),parent.resolve()
    binding_path.relative_to(ROOT);parent.relative_to(ROOT)
    binding=LOAD(binding_path);source=binding_path.parent
    if (binding.get('schema')!='genlaw.verification-control.v1' or binding.get('test_only_guard_bypass') is not True
            or binding.get('production_approved') is not False or binding.get('parent_case')!=parent.name
            or any(binding.get(k)!=v for k,v in [('nc_file','program.nc'),('mesh_file','simulated.stl'),('project_file','simulation.camotics')])):
        raise ValueError('Need an explicit frozen test-only NC control and its original parent')
    request=LOAD(parent/'request.json');plan=checked_plan(request['input'],parent/'preparation-result.json')
    cam=LOAD(parent/'cam-result.json');reference=LOAD(parent/'native-geometry.json')
    if cam['native_cad_binding']!=reference or reference['cad_sha256']!=plan['cad_ref']['sha256']:
        raise ValueError('Control original CAD differs from the native parent')
    verify_binding(parent/'program.nc',cam['nc_sha256'])
    verify_binding(parent/'program.nc',binding['parent_nc_sha256'])
    for name,key in [('program.nc','nc_sha256'),('simulation.camotics','project_sha256'),('simulated.stl','mesh_sha256')]:
        verify_binding(source/name,binding[key])
    if LOAD(source/'simulation.camotics')!=simulation_project(plan):
        raise ValueError('Control must preserve the original stock, tools and resolution')
    try:check_nc((source/'program.nc').read_text(),plan)
    except ValueError as error:
        rejection=str(error)
    else:raise ValueError('Control NC was not rejected by the normal entry')
    if rejection!=binding['normal_guard_rejection']:
        raise ValueError('Control normal-entry rejection changed')
    policy=Path(request['input']).parent/'software-policy.json'
    manifest=LOAD(policy.parent/'manifest.json')
    verify_binding(policy,manifest['files']['software-policy.json']['sha256'])
    return {'source':source,'parent':parent,'plan':plan,'reference':reference,'policy':policy,
        'normal_guard_rejection':rejection,'binding':file_binding(binding_path),
        'validator':file_binding(Path(__file__))}
