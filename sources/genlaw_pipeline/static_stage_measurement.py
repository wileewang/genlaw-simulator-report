"""Measure complete accumulated material against pre-NC CAD in its original frame."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'web/tools'))
from mesh_metrics import require_bounded_worker, load_mesh, Sections, np
from genlaw_pipeline.cell_windows import checked_file, file_binding
from genlaw_pipeline.indexed_facing import IDENTITY, close_bounds, transform_bounds
from genlaw_pipeline.static_stages import prepare_chain, read
from genlaw_pipeline.sparse_material import checked_resources
from genlaw_pipeline.facing_geometry import FACING, FacingReference
from genlaw_pipeline.chamfer_geometry import REFERENCE, ChamferReference
from genlaw_pipeline.reference_sections import integrate_variable
from genlaw_pipeline.chamfer_verification import observations, evaluate
from genlaw_pipeline.verification import ray_hits
from genlaw_pipeline.run import write, FREECAD


class PlacedReference:
    """Only the predeclared rigid frame transform; no fit or registration."""
    def __init__(self,reference,pose):
        if reference['reference_class']==FACING:self.local=FacingReference(reference)
        elif reference['reference_class']==REFERENCE:self.local=ChamferReference(reference)
        else:raise ValueError('No registered reference-section adapter')
        self.pose=pose;self.analytic_volume=self.local.analytic_volume
        self.breaks=sorted(pose[8]*z+pose[11] for z in self.local.breaks)

    def at(self,z):
        from shapely.affinity import affine_transform
        poly=self.local.at(self.pose[8]*(z-self.pose[11]))
        return affine_transform(poly,[self.pose[0],self.pose[1],self.pose[3],self.pose[4],self.pose[9],self.pose[10]])


def measure(request_path,out):
    require_bounded_worker();req=read(request_path)
    if req.get('schema')!='genlaw.static-stage-measurement-request.v1':raise ValueError('Unsupported stage measurement request')
    chain=read(checked_file(req['preparation']))
    if chain!=prepare_chain(checked_file(chain['input'])):raise ValueError('Stage inputs changed')
    for binding in req['bound_artifacts']+req['source_code']:checked_file(binding)
    build=read(checked_file(req['build']))
    if build['schema']!='genlaw.static-stage-stream-build.v1':raise ValueError('Wrong native stage build')
    for binding in [build['binary'],*build['source_code'],*build['reused_objects']]:checked_file(binding)
    cam_request_path=checked_file(req['cam_request']);cam_request=read(cam_request_path);case=cam_request_path.parent
    if Path(cam_request['input']).resolve()!=checked_file(chain['input']) or Path(cam_request['output']).resolve()!=case:
        raise ValueError('CAM resource request is not bound to this chain')
    expected_cam=[str(FREECAD),'freecadcmd',str(ROOT/'tools/static_stages_cam_worker.py')]
    if req['cam_command']!=expected_cam:raise ValueError('Unexpected CAM command')
    checked_resources(checked_file(req['cam_resources']),expected_cam)
    cad=read(checked_file(req['CAD_chain']))
    if (cad['schema']!='genlaw.static-stage-cad-binding.v1' or cad['preparation']!=req['preparation']
            or not cad['CAD_reparsed_before_CAM'] or cad['final_cad']!=chain['final_cad']
            or not 0<=cad['final_target_transform_difference_mm3']<=1e-8
            or len(cad['stages'])!=len(chain['stages'])):raise ValueError('Unverified native CAD continuity')
    for i,row in enumerate(cad['stages']):
        plan=chain['stage_plans'][i];checked_file(row['original_CAD']);checked_file(row['reference'])
        if (row['id']!=chain['stages'][i]['id'] or row['original_CAD']['sha256']!=plan['cad_ref']['sha256']
                or row['reference']!=chain['stage_references'][i]
                or not 0<=row['nominal_incoming_difference_mm3']<=1e-8 or not 0<=row['added_material_mm3']<=1e-8
                or not close_bounds(row['target_bounds_in_material'],chain['stage_target_bounds_in_material'][i])):
            raise ValueError('Native predecessor CAD continuity failed')
    checked_file(cad['final_cad'])
    index=req['reference_stage_index'];kind=req['case_kind'];last=len(chain['stages'])-1
    if type(index) is not int or not 0<=index<=last:raise ValueError('Invalid reference stage')
    normal=kind=='normal'
    if normal:indices=list(range(index+1))
    elif index!=last:raise ValueError('Controls must compare with final original CAD')
    elif kind=='omitted-chamfer':indices=list(range(last))
    elif kind=='wrong-chamfer-pose':indices=list(range(last+1))
    elif kind=='omitted-second-facing' and last==2:indices=[0,2]
    else:raise ValueError('Unsupported explicit negative control')
    native_request_path=checked_file(req['stage_request']);native_request=read(native_request_path)
    expected_stages=[]
    for i in indices:
        stage=chain['stages'][i];dest=case/stage['id'];cam=read(dest/'cam-result.json')
        if (cam['status']!='generated_and_checked' or cam['nc_sha256']!=file_binding(dest/'program.nc')['sha256']
                or read(dest/'simulation.camotics')!=chain['simulation_projects'][i]):
            raise ValueError('Native program/project differs from generated original')
        pose=IDENTITY if kind=='wrong-chamfer-pose' and i==last else stage['to_material']
        expected_stages.append({'id':stage['id'],'project':str(dest/'simulation.camotics'),'to_material':pose})
    if native_request!={'schema':'genlaw.native-static-stage-request.v1','stages':expected_stages}:
        raise ValueError('Executed native stages differ from explicit measurement scope')
    manifest_path=checked_file(req['native_manifest']);native=read(manifest_path)
    expected_cmd=[str(checked_file(build['binary'])),'--cell-window',','.join(map(str,chain['window'])),
                  '--facets-per-file','500000',str(native_request_path),str(manifest_path.parent)]
    if req['native_command']!=expected_cmd:raise ValueError('Unbound native command')
    checked_resources(checked_file(req['native_resources']),expected_cmd)
    expected={'schema':'genlaw.static-stage-stream-window.v1','status':'complete_window','nc_executed':True,
        'complete_nc':True,'surface_computed':True,'target_cad_used':False,'reduction':False,'threads':1,
        'sampling':'global-integer-lattice-v1','units':'mm','cell_interval':'half-open',
        'resolution_mm':chain['grid']['resolution_mm'],'offset_mm':chain['grid']['offset_mm'],'steps':chain['grid']['steps'],
        'cell_window':chain['window'],'programs_evaluated':len(indices),'stock_reset_between_stages':False,
        'continuous_rotary_motion':False,'visited_native_cells':chain['budget']['cell_count'],'whole_stock_surface_verified':False}
    if any(native.get(k)!=v for k,v in expected.items()):raise ValueError('Native stage receipt does not match execution')
    if (len(native['stages'])!=len(indices) or any(
        r['id']!=s['id'] or r['to_material']!=s['to_material'] or not 0<r['native_moves']<=200000
        for r,s in zip(native['stages'],expected_stages)) or sum(r['native_moves'] for r in native['stages'])!=native['native_moves']):
        raise ValueError('Native stage membership or path count is invalid')
    if len(native['shards'])!=1 or not 0<native['triangle_count']<=500000:
        raise ValueError('This observer requires one bounded complete native surface')
    shard=native['shards'][0];mesh=manifest_path.parent/'surface-0000.stl'
    if (shard['index']!=0 or shard['file']!=mesh.name or shard['triangles']!=native['triangle_count']
            or mesh.stat().st_size!=shard['bytes'] or shard['bytes']!=84+50*native['triangle_count']
            or {p.name for p in manifest_path.parent.iterdir()}!={'native.json',mesh.name}):
        raise ValueError('Native surface membership differs')
    vertices,faces,inspection=load_mesh(mesh)
    target_stage=chain['stages'][index];ref_cam=read(case/target_stage['id']/'cam-result.json')
    reference=ref_cam['native_cad_binding'];plan=chain['stage_plans'][index]
    if (reference['status']!='verified' or not reference['original_cad_reparsed']
            or not reference['valid_single_solid'] or reference['cad_sha256']!=plan['cad_ref']['sha256']
            or not 0<=reference['symmetric_difference_mm3']<=1e-8):
        raise ValueError('Stage reference lacks independent native CAD verification')
    target=PlacedReference(reference,target_stage['to_material']);limits=chain['acceptance']
    if (abs(target.analytic_volume-reference['volume_mm3'])>1e-8
            or (index==last and abs(target.analytic_volume-cad['final_volume_mm3'])>1e-8)):
        raise ValueError('Independent section target does not equal native CAD volume')
    levels=np.unique(np.concatenate((vertices[:,2].astype(np.float64),target.breaks)))
    if len(levels)>512:raise ValueError('Complete material measurement interval budget exceeded')
    sections=Sections(vertices,faces)
    coarse=integrate_variable(sections,levels,target,2);fine=integrate_variable(sections,levels,target,3)
    if (abs(coarse['iou']-fine['iou'])>=limits['quadrature_iou_difference_max']
            or abs(fine['actual_volume_mm3']-inspection['volume_mm3'])>=limits['mesh_vs_integrated_volume_mm3_max']
            or abs(fine['target_volume_mm3']-target.analytic_volume)>1e-8):
        raise ValueError('Whole-material independent integrals disagree')
    expected_bounds=chain['stage_target_bounds_in_material'][index]
    bound_error=max(abs(inspection['bounds'][k][i]-expected_bounds[k][i]) for k in ['min','max'] for i in range(3))
    # Applying the declared inverse frame to a copy is a coordinate conversion,
    # never registration, mesh fitting, or removal of disconnected material.
    pose=target_stage['to_material'];rotation=np.array(pose[:9],dtype=float).reshape(3,3)
    local_vertices=(vertices.astype(float)-np.array(pose[9:]))@rotation
    local_sections=Sections(local_vertices,faces);lo,hi=reference['bounds']['min'],reference['bounds']['max']
    feature_results={};feature_observations={}
    if reference['reference_class']==REFERENCE:
        feature_observations=observations(local_vertices,faces,reference,chain['grid']['resolution_mm'])
        fid=reference['features'][0]['id'];obs=feature_observations['features'][fid]
        common={'schema':'genlaw.numeric-acceptance.v1','units':'mm','scope':'software_regression',
                'basis':'Frozen 0.1 mm software allowance with two .025 mm cells; no manufacturing acceptance.'}
        rules={k:{**common,'method':m,'max_deviation_mm':limits['feature_deviation_mm_max']} for k,m in [
            ('chamfer_sections','sectioned_original_chamfer_boundary'),('chamfer_width','sampled_extrapolated_chamfer_width'),
            ('retained_planes','sampled_original_top_bottom')]}
        h=hi[2]-lo[2];rules['retained_thickness']={**common,'method':'sampled_original_thickness','min_mm':h-.1,'max_mm':h+.1}
        feature_results={k:evaluate(k,obs,v) for k,v in rules.items()}
        feature_pass=all(v['status']=='numeric_pass' for v in feature_results.values())
    else:
        probes=[]
        for u in [.2,.35,.5,.65,.8]:
            for v in [.2,.35,.5,.65,.8]:
                xy=[lo[0]+u*(hi[0]-lo[0]),lo[1]+v*(hi[1]-lo[1])]
                zs=ray_hits(local_sections.triangles,xy,(chain['original_stock']['bounds_mm']['min'][2]-.1,
                                                       chain['original_stock']['bounds_mm']['max'][2]+.1))
                valid=len(zs)==2;err=max(abs(zs[0]-lo[2]),abs(zs[1]-hi[2])) if valid else None
                probes.append({'xy_mm':xy,'surface_z_mm':zs,'plane_error_mm':err,'passed':valid and err<=limits['bounds_error_mm_max']})
        feature_observations={'retained_plane_probes':probes};feature_pass=all(p['passed'] for p in probes)
    geometry_pass=(fine['iou']>=limits['normal_iou_min'] and feature_pass
                   and abs(inspection['volume_mm3']-target.analytic_volume)<=limits['volume_error_mm3_max']
                   and bound_error<=limits['bounds_error_mm_max'])
    expectation=geometry_pass if normal else (not geometry_pass and fine['iou']<limits['control_iou_max'])
    report={'schema':'genlaw.static-stage-metrics.v1','request':file_binding(request_path),'case_kind':kind,
        'reference_stage_index':index,'executed_stage_ids':[r['id'] for r in expected_stages],
        'mesh':file_binding(mesh),'mesh_inspection':inspection,
        'reference_CAD':chain['final_cad'] if index==last else cad['stages'][index]['original_CAD'],
        'reference_to_material':target_stage['to_material'],'reference_bounds_in_original_material_frame':expected_bounds,
        'reference_was_checked_against_native_CAD':True,'reference_is_reconstructed_from_NC':False,
        'iou':fine['iou'],'iou_percent':fine['iou']*100,'volumes':fine,'coarse_quadrature':coarse,
        'expected_volume_mm3':target.analytic_volume,'maximum_bounds_error_mm':bound_error,
        'feature_observations':feature_observations,'feature_verification':feature_results,
        'geometry_pass':geometry_pass,'regression_expectation_met':expectation,'negative_control':not normal,
        'full_native_material_measured':True,'registration_applied':False,'disconnected_material_discarded':False,
        'manufacturing_angle_acceptance':False,'production_approved':False,'whole_process_card_complete':False}
    for binding in req['bound_artifacts']+req['source_code']:checked_file(binding)
    write(out,report)
    if not expectation:raise ValueError('Static-stage geometry did not meet its frozen expectation')
    print(json.dumps({'stage':target_stage['id'],'kind':kind,'iou_percent':report['iou_percent'],
                      'geometry_pass':geometry_pass}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--request',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();measure(a.request.resolve(),a.output.resolve())
