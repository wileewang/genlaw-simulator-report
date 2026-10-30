"""Select the frozen static tool-length engine only with its native evidence."""
import json
from pathlib import Path
from .cell_windows import checked_file, candidate_engine
from .preparation import digest
from .tool_length_setup import enabled, validate_resources

ROOT=Path(__file__).resolve().parents[1]
SELECTION=ROOT/'engines/camotics/tool-length-engine.json'


def engine_for_plan(plan):
    if not enabled(plan):raise ValueError('Explicit tool-length setup required')
    validate_resources(plan['setup'],plan['stock'],plan['tools'],plan['operations'])
    selection=json.loads(SELECTION.read_text())
    if selection.get('schema')!='genlaw.tool-length-engine-selection.v1':raise ValueError('Unknown tool-length engine selection')
    path=checked_file(selection['engine']); evidence=json.loads(checked_file(selection['qualification']).read_text())
    binary,_,manifest=candidate_engine(path)
    if (evidence['status']!='declared_native_probe_scope_verified' or evidence['engine']!=selection['engine']
            or checked_file(evidence['engine_binary'])!=binary or evidence['motion_check_count']!=153
            or evidence['normal_geometry_case_count']!=10 or evidence['geometry_defect_case_count']!=4
            or len(evidence['native_rejections'])!=13 or len(evidence['legacy_exact_payload_regressions'])!=7):
        raise ValueError('Missing bound native tool-length evidence')
    for name in ['tool_length_capability','work_coordinate_capability','material_frame_capability']:
        capability=manifest[name]
        checked_file({'path':capability['patch_ref'],'sha256':capability['patch_sha256']})
    cap=manifest['tool_length_capability']
    if (cap['schema']!='genlaw.camotics-tool-length.v1' or not cap['opt_in']
            or cap['cutting_shape_modified'] or cap['m6_reloads_compensation']
            or cap['nc_error_accepts_partial_surface']):
        raise ValueError('Unexpected native tool-length contract')
    return {'binary':binary,'manifest':manifest,'manifest_path':path,'manifest_sha256':digest(path),
            'selection_sha256':digest(SELECTION),
            'tool_length_probe_qualification_sha256':selection['qualification']['sha256']}
