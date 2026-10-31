"""Carry explicitly declared dimension checks from preparation to actual material.

The versioned optional declaration is absent from legacy jobs. This first
integration qualifies original-frame capsule jobs, with the unchanged observer.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'web/tools')]
from genlaw_pipeline.preparation import InputError, load_json, digest
from genlaw_pipeline.dimension_contract import POLICY, compile_requirements
from genlaw_pipeline.coordinate_setup import enabled as framed
from genlaw_pipeline.cell_windows import file_binding

DECLARATION = 'genlaw.declared-dimensions.v1'
PLAN = 'genlaw.declared-dimension-plan.v1'
FILE_KEY = 'dimension_requirements'


def attach(preparation, manifest, plan):
    declaration = manifest.get('inspection')
    if declaration is None and FILE_KEY not in manifest.get('files', {}):
        return plan
    field = 'inspection'
    if (not isinstance(declaration, dict)
            or set(declaration) != {'schema', 'file_key', 'scope'}
            or declaration.get('schema') != DECLARATION
            or declaration.get('file_key') != FILE_KEY or declaration.get('scope') != 'final_material'):
        raise InputError('unsupported_capability', field, '尺寸附件必须显式声明版本、文件键和最终材料检验范围，不能忽略附件。')
    if plan is None or any(i['severity'] == 'blocking' for i in preparation.issues):
        return plan
    if plan['schema'] not in ('genlaw.execution-plan.v10', 'genlaw.execution-plan.v11') or framed(plan):
        raise InputError('unsupported_capability', field, '本尺寸整链入口先验证原坐标的 X/Y 圆头外形及通孔；其他组合与偏置尚未取得资格。')
    if FILE_KEY not in preparation.files:
        raise InputError('missing_information', 'files/'+FILE_KEY, '已声明尺寸检验，但未提供尺寸要求文件。')
    binding = preparation.files[FILE_KEY]
    path = preparation.root/binding['path']; request = load_json(path)
    geometry = [op['geometry'] for op in plan['operations']
                if op['geometry']['kind'] in ('capsule_outline', 'through_hole')]
    reference = {'cad_sha256': plan['cad_ref']['sha256'], 'features': geometry}
    try:
        compile_requirements(request, reference)
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise InputError('conflicting_information', field+'/requirements', str(error)) from error
    ids = {r['id'] for r in preparation.result['requirement_readiness']}
    documents = {}
    extra = {'support_span': set(), 'end_radius': {'side'}, 'center_pitch': {'hole_ids'},
             'end_offset': {'side', 'hole_id'}}
    for item in request['items']:
        key = field+'/'+item['id']; kind = item['kind']
        allowed = {'id', 'kind', 'profile_id', 'axis', 'nominal_mm', 'acceptance', 'source'} | extra[kind]
        if set(item) != allowed:
            raise InputError('invalid_input', key, '尺寸字段缺失或包含当前无法解释的附加约束。')
        if item['id'] in ids:
            raise InputError('conflicting_information', key, '尺寸要求编号与已有要求重复。')
        ids.add(item['id'])
        preparation.source(item['source'], key+'/source')
        document = item['source'].get('document_id')
        if document in preparation.documents:
            file_key = preparation.documents[document]
            if file_key in preparation.files: documents[document] = dict(preparation.files[file_key])
        rule = item['acceptance']
        if rule is not None:
            if set(rule) != {'schema','units','scope','basis','method','min_mm','max_mm'}:
                raise InputError('unsupported_capability', key+'/acceptance', '未知尺寸判据字段不能忽略。')
            if not rule['min_mm'] <= item['nominal_mm'] <= rule['max_mm']:
                raise InputError('conflicting_information', key+'/acceptance', '尺寸名义值不在所声明的允许范围内。')
            cells = POLICY['radius_guard_cells'] if kind == 'end_radius' else POLICY['span_guard_cells'] if kind == 'support_span' else POLICY['two_anchor_distance_guard_cells']
            guard = cells * plan['simulation']['resolution_mm']
            if rule['max_mm']-rule['min_mm'] < 2*guard-1e-10:
                raise InputError('unsupported_capability', key+'/resolution', '当前网格保护带无法落入该公差；需先验证更高精度的检验能力。')
        else:
            preparation.issue('missing_information', key+'/acceptance', '该尺寸判据待补，最终结果将保留未判定状态。', severity='acceptance')
        preparation.result['requirement_readiness'].append({'id':item['id'], 'acceptance_defined':rule is not None,
            'verification_status':'not_verified', 'inspection':'declared_planar_dimension'})
    plan['declared_inspection'] = {'schema':PLAN, 'requirements_ref':dict(binding),
        'cad_sha256':plan['cad_ref']['sha256'], 'source_documents':documents,
        'requirement_ids':[r['id'] for r in request['items']], 'scope':'final_material',
        'observer_policy':POLICY, 'resolution_mm':plan['simulation']['resolution_mm'],
        'manufacturing_approved':False}
    return plan


def exact_surface_coverage(mesh, vertices, faces, audit):
    """Prove exact triangle-set coverage; never alter or rewrite the source STL.

    The existing loader identifies coincident faces for sectional measurement.
    Every input face must retain an identical, same-winding representative.
    """
    from mesh_metrics import np, STL_DTYPE
    raw=np.memmap(mesh,dtype=STL_DTYPE,mode='r',offset=84,shape=(audit['raw_triangles'],))
    points=np.ascontiguousarray(raw['points'].reshape(-1,3));points[points==0]=0
    _,first,inverse=np.unique(points.view('V12').reshape(-1),return_index=True,return_inverse=True)
    if not np.array_equal(points[first],vertices):raise ValueError('Input vertices changed during loading')
    original=inverse.reshape(-1,3).astype('<u4')
    def key(tri):return np.ascontiguousarray(np.sort(tri,axis=1)).view('V12').reshape(-1)
    def parity(tri):return (tri[:,0]>tri[:,1])^(tri[:,0]>tri[:,2])^(tri[:,1]>tri[:,2])
    unique,index,groups=np.unique(key(original),return_index=True,return_inverse=True)
    retained,indices=np.unique(key(faces),return_index=True)
    if (len(retained)!=len(faces) or not np.array_equal(unique,retained)
            or not np.array_equal(parity(original),parity(original)[index][groups])
            or not np.array_equal(parity(original)[index],parity(faces)[indices])):
        raise ValueError('Duplicate normalization loses a face or changes its orientation')
    if len(original)-len(faces)!=audit['exact_duplicate_faces_removed']:raise ValueError('Duplicate count changed')
    return {'raw_triangles':len(original),'unique_triangles':len(faces),
        'exact_same_winding_duplicates':audit['exact_duplicate_faces_removed'],
        'every_original_face_exactly_represented':True,'coordinates_changed':False,
        'source_stl_modified':False,'components_discarded':False}


def verify(run, output):
    from mesh_metrics import require_bounded_worker, load_mesh, Sections, np
    from genlaw_pipeline.programming import checked_plan, check_nc, verify_binding
    from genlaw_pipeline.dimension_inspection import inspection_heights
    from genlaw_pipeline.planar_dimensions import compile_requirements as observer_compile, observe_section, evaluate_dimensions
    from genlaw_pipeline.run import write
    require_bounded_worker()
    run = run.resolve(); output = output.resolve()
    for path in (run,output): path.relative_to(ROOT)
    source = load_json(run/'request.json'); input_path = Path(source['input'])
    plan = checked_plan(input_path,run/'preparation-result.json')
    declaration = plan.get('declared_inspection')
    if not declaration or declaration['schema'] != PLAN or framed(plan):
        raise ValueError('No qualified dimension declaration in the frozen execution plan')
    req_path = input_path.parent/declaration['requirements_ref']['path']
    verify_binding(req_path,declaration['requirements_ref']['sha256']); request = load_json(req_path)
    reference = load_json(run/'native-geometry.json'); cam = load_json(run/'cam-result.json')
    actual = load_json(run/'simulation-result.json'); observed = load_json(run/'feature-verification.json')
    metrics = load_json(run/'metrics.json'); project = load_json(run/'simulation.camotics')
    if (cam['status'] != 'generated_and_checked' or cam['native_cad_binding'] != reference
            or reference['cad_sha256'] != declaration['cad_sha256'] or not reference['valid_single_solid']
            or not reference['original_cad_reparsed'] or reference['symmetric_difference_mm3'] > 1e-8):
        raise ValueError('Additional dimensions require independent original native CAD binding')
    observer_compile(request,reference)
    nc = run/'program.nc'; mesh = run/'simulated.stl'; config = run/'simulation.camotics'
    verify_binding(nc,cam['nc_sha256']); check_nc(nc.read_text(),plan)
    verify_binding(mesh,actual['mesh_sha256']); verify_binding(config,actual['project_sha256'])
    if (actual['status'] != 'executed' or actual['nc_sha256_before'] != cam['nc_sha256']
            or actual['nc_sha256_after'] != cam['nc_sha256'] or actual['nc_text_modified']
            or observed['test_only_control'] or observed['mesh_sha256'] != actual['mesh_sha256']
            or observed['nc_sha256'] != cam['nc_sha256'] or observed['project_sha256'] != actual['project_sha256']
            or observed['native_geometry_sha256'] != digest(run/'native-geometry.json')
            or observed['cad_sha256'] != reference['cad_sha256'] or metrics['reference_cad'] != reference
            or metrics['native_geometry_sha256'] != digest(run/'native-geometry.json')
            or metrics['actual_mesh']['source_sha256'] != actual['mesh_sha256']
            or metrics['registration'] != 'none; same CAD/G54 coordinates'
            or project['files'] != ['program.nc'] or project['workpiece']['bounds'] != plan['stock']['bounds_mm']
            or project['resolution'] != declaration['resolution_mm']):
        raise ValueError('Declared dimensions and original checks do not use the same normal NC material')
    resource = load_json(run/'camotics-resources.json')
    if (not resource['success'] or resource['status'] != 'completed' or resource['wall_limit_seconds'] > 60
            or len(resource['cpu_affinity']) != 1 or resource['peak_observed_group_rss_bytes'] > 2*1024**3
            or resource['command'][-2:] != [str(config),str(mesh)]):
        raise ValueError('Missing bounded actual NC execution evidence')
    vertices,faces,audit = load_mesh(mesh)
    coverage=exact_surface_coverage(mesh,vertices,faces,audit)
    levels = np.unique(np.r_[vertices[:,2].astype(float),reference['bounds']['min'][2],reference['bounds']['max'][2]])
    heights = inspection_heights(levels,reference); provider = Sections(vertices,faces)
    sections = [observe_section(provider.at(h['z_mm']),reference,declaration['resolution_mm']) for h in heights]
    result = evaluate_dimensions(request,reference,sections,declaration['resolution_mm'])
    result.update(schema='genlaw.declared-dimension-verification.v1',status='evaluated',
        input=file_binding(input_path),preparation=file_binding(run/'preparation-result.json'),
        requirements_source=file_binding(req_path),execution_plan=file_binding(run/'execution-plan.json'),
        original_reference=file_binding(run/'native-geometry.json'),nc=file_binding(nc),material=file_binding(mesh),
        simulation=file_binding(run/'simulation-result.json'),original_feature_report=file_binding(run/'feature-verification.json'),
        original_metrics=file_binding(run/'metrics.json'),original_iou_percent=metrics['iou_percent'],
        source_documents=declaration['source_documents'],native_resources=file_binding(run/'camotics-resources.json'),
        requirements_bound_before_native_execution=True,additional_material_execution=False,
        mesh_loading_audit=audit,original_surface_coverage=coverage,
        section_observations=[{'height':h,'observation':s} for h,s in zip(heights,sections)],
        source_code=[file_binding(ROOT/p) for p in ['genlaw_pipeline/declared_dimensions.py',
            'genlaw_pipeline/dimension_contract.py',
            'genlaw_pipeline/planar_dimensions.py','genlaw_pipeline/dimension_inspection.py',
            'genlaw_pipeline/preparation.py','genlaw_pipeline/verification.py','genlaw_pipeline/measure_worker.py',
            'web/tools/mesh_metrics.py']],
        original_geometry_and_process_checks_replaced=False,
        limits=['Only declared dimensions are inspected; original contour, thickness and process checks remain separate.',
                'Finite sections and numerical guards are not certified uncertainty or manufacturing approval.'])
    # Recheck the input/plan after observation, without changing their sources.
    checked_plan(input_path,run/'preparation-result.json')
    write(output,result)
    print(json.dumps({'dimensions':len(result['requirements']),'dimension_passed':result['dimension_inspection_passed']}),flush=True)


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();verify(args.run,args.output)
