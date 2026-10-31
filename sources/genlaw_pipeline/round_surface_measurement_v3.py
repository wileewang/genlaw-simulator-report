"""Bounded complete-material observation for an archived external-round probe."""
import argparse
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'web/tools')]
from mesh_metrics import require_bounded_worker,load_mesh,np
from genlaw_pipeline.indexed_sections import IndexedSections as Sections
from genlaw_pipeline.cell_windows import checked_file,file_binding
from genlaw_pipeline.round_surface_preparation import prepare
from genlaw_pipeline.round_surface_geometry import RoundReference
from genlaw_pipeline.round_surface_nc import check_nc
from genlaw_pipeline.small_partition_material import checked as checked_material
from genlaw_pipeline.sparse_material import checked_resources
from genlaw_pipeline.measure_worker import section_height
from genlaw_pipeline.reference_sections import integrate_variable
from genlaw_pipeline.region_verification import topology
from genlaw_pipeline.verification import ray_hits
from genlaw_pipeline.run import write,FREECAD

READ=lambda p:json.loads(Path(p).read_text())


def feature_observations(vertices,faces,reference,limits):
    from shapely.geometry import LineString
    target=RoundReference(reference);lo,hi=target.low,target.high;r=target.radius
    sections=Sections(vertices,faces);levels=np.unique(np.r_[vertices[:,2].astype(float),target.breaks])
    contours=[];radii=[]
    requests=[('round',hi[2]-r+r*float(u)) for u in np.linspace(.05,.95,11)]
    requests += [('retained_wall',lo[2]+u*(hi[2]-r-lo[2])) for u in [.2,.5,.8]]
    for kind,requested in requests:
        z=section_height(levels,requested) if min(abs(levels-requested))<1e-10 else requested
        actual=sections.at(z);expected=target.at(z)
        contours.append({'region':kind,'z_mm':z,'expected_topology':topology(expected),
            'observed_topology':topology(actual),'topology_matches':topology(actual)==topology(expected),
            'boundary_deviation_mm':float(actual.boundary.hausdorff_distance(expected.boundary)) if not actual.is_empty else None,
            'missing_material_area_mm2':expected.difference(actual).area,
            'excess_material_area_mm2':actual.difference(expected).area})
        if kind=='round':
            for u in [.1,.3,.5,.7,.9]:
                x=lo[0]+u*(hi[0]-lo[0]);line=actual.intersection(LineString([(x,lo[1]-1),(x,hi[1]+1)]))
                valid=line.geom_type=='LineString' and not line.is_empty
                values=[]
                if valid:
                    for y,center in [(line.bounds[1],lo[1]+r),(line.bounds[3],hi[1]-r)]:
                        values.append(math.hypot(y-center,z-(hi[2]-r)))
                radii.append({'x_mm':x,'z_mm':z,'complete':valid,'observed_radii_mm':values,
                    'maximum_radius_error_mm':max(abs(v-r) for v in values) if valid else None,
                    'center_from_original_CAD':True,'center_fitted':False})
    probes=[]
    for u in [.2,.35,.5,.65,.8]:
        for v in [.2,.35,.5,.65,.8]:
            xy=[lo[0]+u*(hi[0]-lo[0]),lo[1]+r+v*(hi[1]-lo[1]-2*r)]
            zs=ray_hits(sections.triangles,xy,(lo[2]-.1,hi[2]+.1));valid=len(zs)==2
            error=max(abs(zs[0]-lo[2]),abs(zs[1]-hi[2])) if valid else None
            probes.append({'xy_mm':xy,'surface_z_mm':zs,'plane_error_mm':error,
                'passed':valid and error<=limits['bounds_deviation_mm_max']})
    complete=all(v['boundary_deviation_mm'] is not None and v['topology_matches'] for v in contours)
    radius_complete=all(v['complete'] for v in radii)
    profile_error=max(v['boundary_deviation_mm'] for v in contours) if complete else None
    radius_error=max(v['maximum_radius_error_mm'] for v in radii) if radius_complete else None
    guard=limits['numerical_guard_mm']
    return {'method':'14 full original-coordinate sections, fixed CAD-center radii at 55 cross-lines, 25 central retained-plane rays',
        'contours':contours,'radius_samples':radii,'retained_plane_probes':probes,
        'maximum_boundary_deviation_mm':profile_error,'maximum_radius_deviation_mm':radius_error,
        'profile_pass':complete and profile_error+guard<=limits['profile_deviation_mm_max'],
        'radius_pass':radius_complete and radius_error+guard<=limits['radius_deviation_mm_max'],
        'retained_planes_pass':all(p['passed'] for p in probes),'numerical_guard_mm':guard,
        'guard_is_certified_uncertainty':False,'registration_applied':False}


def inspect(run,out):
    require_bounded_worker();out.mkdir(parents=True,exist_ok=False)
    preparation=READ(run/'preparation-result.json');request=READ(run/'request.json')
    if prepare(checked_file(preparation['input']))!=preparation:raise ValueError('Round inputs changed')
    for binding in request['execution_sources']+request['preserved_sources']:checked_file(binding)
    checked_resources(run/'freecad-resources.json',[str(FREECAD),'freecadcmd',str(ROOT/'tools/round_surface_cam_worker.py')])
    cam=READ(run/'cam-result.json');nc=checked_file(cam['NC'])
    if check_nc(nc.read_text(),preparation)!=cam['nc_check']:raise ValueError('Native NC check changed')
    material_path=run/'material-partitioned/result.json'
    native,mesh=checked_material(material_path)
    if (native['grid']!=preparation['grid'] or checked_file(native['inputs']['project'])!=run/'simulation.camotics'
            or READ(run/'simulation.camotics')!=preparation['simulation_project']):
        raise ValueError('Require complete original project and global grid')
    vertices,faces,inspection=load_mesh(mesh)
    if inspection['exact_duplicate_faces_removed']:raise ValueError('Do not discard duplicate native facets')
    ref=READ(checked_file(cam['native_geometry']));target=RoundReference(ref)
    if (ref['status']!='verified' or not ref['valid_single_solid'] or not ref['original_CAD_reparsed']
            or ref['CAD']!=preparation['input_bindings']['CAD'] or ref['symmetric_difference_mm3']>1e-8
            or abs(target.analytic_volume-ref['volume_mm3'])>1e-8):raise ValueError('Unverified independent native CAD reference')
    levels=np.unique(np.r_[vertices[:,2].astype(float),target.breaks])
    limits=preparation['specification']['software_acceptance']
    observations=feature_observations(vertices,faces,ref,limits)
    bound_error=max(abs(inspection['bounds'][k][i]-ref['bounds'][k][i]) for k in ['min','max'] for i in range(3))
    for name,array in [('vertices',vertices),('faces',faces),('levels',levels)]:np.save(out/(name+'.npy'),array,allow_pickle=False)
    write(out/'inspection.json',{'schema':'genlaw.round-surface-inspection.v3','mesh':file_binding(mesh),
        'native_result':file_binding(material_path),'preparation':file_binding(run/'preparation-result.json'),
        'reference':ref,'mesh_inspection':inspection,'acceptance':limits,'feature_observations':observations,
        'maximum_bounds_error_mm':bound_error,'volume_deviation_mm3':abs(inspection['volume_mm3']-target.analytic_volume),
        'level_count':len(levels),'total_interval_limit':4095,'quadrature_intervals_per_worker':16,
        'integral_plan_allowed':len(levels)<=4096,
        'parts':[[i,min(i+16,len(levels)-1)] for i in range(0,len(levels)-1,16)] if len(levels)<=4096 else [],
        'arrays':{n:file_binding(out/(n+'.npy')) for n in ['vertices','faces','levels']},
        'source_code':[file_binding(Path(__file__)),file_binding(ROOT/'genlaw_pipeline/round_surface_geometry.py'),
            file_binding(ROOT/'genlaw_pipeline/reference_sections.py'),file_binding(ROOT/'web/tools/mesh_metrics.py'),file_binding(ROOT/'genlaw_pipeline/indexed_sections.py'),file_binding(ROOT/'genlaw_pipeline/small_partition_material.py')],
        'IoU_not_yet_measured':True,'production_approved':False,'whole_process_card_complete':False})
    print(json.dumps({'levels':len(levels),'triangles':inspection['triangles'],'volume_error':abs(inspection['volume_mm3']-target.analytic_volume),
        'maximum_profile_error':observations['maximum_boundary_deviation_mm'],'feature_passes':{k:v for k,v in observations.items() if k.endswith('_pass')}}),flush=True)


def load_inspection(out):
    value=READ(out/'inspection.json')
    for binding in [value['mesh'],value['native_result'],value['preparation'],*value['arrays'].values(),*value['source_code']]:checked_file(binding)
    if value['schema']!='genlaw.round-surface-inspection.v3' or not value['integral_plan_allowed']:
        raise ValueError('No complete bounded interval plan')
    return value


def part(out,index):
    require_bounded_worker();value=load_inspection(out)
    if type(index) is not int or not 0<=index<len(value['parts']):raise ValueError('Unknown interval group')
    arrays={k:np.load(checked_file(v),mmap_mode='r',allow_pickle=False) for k,v in value['arrays'].items()}
    a,b=value['parts'][index];target=RoundReference(value['reference']);sections=Sections(arrays['vertices'],arrays['faces'])
    results=[integrate_variable(sections,arrays['levels'][a:b+1],target,n) for n in value['acceptance']['quadrature_orders']]
    write(out/f'part-{index:04d}.json',{'inspection':file_binding(out/'inspection.json'),'interval':[a,b],
        'quadrature':results,'source_code':file_binding(Path(__file__))})
    print(json.dumps({'part':index,'interval':[a,b],'section_calls':sections.calls}),flush=True)


def finish(out):
    require_bounded_worker();value=load_inspection(out);sums=[];bindings=[]
    for order in value['acceptance']['quadrature_orders']:
        totals={k:0. for k in ['actual_volume_mm3','target_volume_mm3','intersection_mm3']}
        for i,interval in enumerate(value['parts']):
            p=out/f'part-{i:04d}.json';v=READ(p)
            if (v['inspection']!=file_binding(out/'inspection.json') or v['interval']!=interval
                    or v['source_code']!=file_binding(Path(__file__))):raise ValueError('Foreign or changed quadrature partition')
            rows=[r for r in v['quadrature'] if r['quadrature_order']==order]
            if len(rows)!=1:raise ValueError('Missing quadrature order')
            for k in totals:totals[k]+=rows[0][k]
            if order==value['acceptance']['quadrature_orders'][0]:bindings.append(file_binding(p))
        totals['union_mm3']=totals['actual_volume_mm3']+totals['target_volume_mm3']-totals['intersection_mm3']
        totals['iou']=totals['intersection_mm3']/totals['union_mm3'];totals['order']=order;sums.append(totals)
    coarse,fine=sums;limits=value['acceptance'];target=RoundReference(value['reference'])
    errors={'quadrature_iou_difference':abs(coarse['iou']-fine['iou']),
        'target_integral_error_mm3':abs(fine['target_volume_mm3']-target.analytic_volume),
        'mesh_integral_error_mm3':abs(fine['actual_volume_mm3']-value['mesh_inspection']['volume_mm3'])}
    stable=all(errors[k]<=limits[k+'_max'] for k in errors)
    f=value['feature_observations'];passed=(stable and fine['iou']*100>=limits['iou_percent_min']
        and value['volume_deviation_mm3']<=limits['volume_deviation_mm3_max']
        and value['maximum_bounds_error_mm']<=limits['bounds_deviation_mm_max']
        and all(f[k] for k in ['profile_pass','radius_pass','retained_planes_pass']))
    report={'schema':'genlaw.round-surface-metrics.v3','inspection':file_binding(out/'inspection.json'),'parts':bindings,
        'quadrature':sums,'numerical_errors':errors,'numerical_stability_pass':stable,
        'iou_percent':fine['iou']*100,'geometry_pass':passed,'complete_material_measured':True,
        'registration_applied':False,'material_discarded':False,'production_approved':False,
        'whole_real_card_complete':False,'feature_cohort_qualified':False}
    write(out/'metrics.json',report);print(json.dumps({k:report[k] for k in ['iou_percent','geometry_pass','numerical_stability_pass']}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['inspect','part','finish']);p.add_argument('--run',type=Path)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--index',type=int);a=p.parse_args();out=a.output.resolve()
    if a.mode=='inspect':inspect(a.run.resolve(),out)
    elif a.mode=='part':part(out,a.index)
    else:finish(out)
