"""Chamfer measurement over typed exact-planar complete native material.

The qualified native numerical method and current-input contract are retained.
This adapter only selects the validated complete-surface representation.

No historical measurement plan or saved numerical result is an input. Normal
NC must pass the ordinary programming contract. Frozen rejected NC is accepted
only by the separate test-control planner and cannot yield a normal acceptance.
"""
import argparse
import bisect
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'web/tools')]
from mesh_metrics import np,require_bounded_worker,controls
from genlaw_pipeline.cell_windows import checked_file,file_binding
from genlaw_pipeline.planar_sections import checked_surface
from genlaw_pipeline.planar_sections import PlanarSections,schedule,interval_groups
from genlaw_pipeline.chamfer_shards import target
from genlaw_pipeline.chamfer_measurement import frozen_policy
from genlaw_pipeline.programming import checked_plan as checked_execution,verify_binding,check_nc,simulation_project
from genlaw_pipeline.window_controls import checked_control
from genlaw_pipeline.measure_worker import section_height,combine_volumes
from genlaw_pipeline.reference_sections import integrate_variable
from genlaw_pipeline.compact_feature_observations import section_observation,assemble_observations
from genlaw_pipeline.sharded_surface import triangles
from genlaw_pipeline.verification import ray_hits
from genlaw_pipeline.run import write

LOAD=lambda p:json.loads(p.read_text())
SOURCES=['genlaw_pipeline/planar_material.py','genlaw_pipeline/native_planar.py','genlaw_pipeline/planar_sections.py','genlaw_pipeline/planar_directory.py',
    'genlaw_pipeline/planar_directory_entry.py','genlaw_pipeline/bucket_directory.py',
    'genlaw_pipeline/planar_chamfer_measurement.py','genlaw_pipeline/native_sections.py','genlaw_pipeline/native_stream_metadata.py',
    'genlaw_pipeline/sparse_sections.py','genlaw_pipeline/sparse_stream_surface.py',
    'genlaw_pipeline/native_stream.py','genlaw_pipeline/span_histogram.py',
    'genlaw_pipeline/native_material.py','genlaw_pipeline/native_material_entry.py',
    'genlaw_pipeline/packed_sparse_plan.py','genlaw_pipeline/surface_records.py',
    'genlaw_pipeline/sparse_material.py','genlaw_pipeline/material_blocks.py','genlaw_pipeline/cell_windows.py',
    'genlaw_pipeline/compact_sections.py','genlaw_pipeline/compact_feature_observations.py',
    'genlaw_pipeline/chamfer_shards.py','genlaw_pipeline/chamfer_geometry.py',
    'genlaw_pipeline/chamfer_verification.py','genlaw_pipeline/chamfer_measurement.py',
    'genlaw_pipeline/programming.py','genlaw_pipeline/preparation.py','genlaw_pipeline/milling_preparation.py',
    'genlaw_pipeline/milling_features.py',
    'genlaw_pipeline/chamfer_nc.py','genlaw_pipeline/window_controls.py','genlaw_pipeline/sharded_surface.py',
    'genlaw_pipeline/sharded_measure.py','genlaw_pipeline/verification.py','genlaw_pipeline/region_verification.py',
    'genlaw_pipeline/reference_sections.py','genlaw_pipeline/measure_worker.py','web/tools/mesh_metrics.py',
    'genlaw_pipeline/planar_chamfer_entry.py']


def source_bindings(): return [file_binding(ROOT/name) for name in SOURCES]


def current_inputs(surface_path,reference_path,policy_path,control_path=None):
    """Recheck the current preparation and bind the material to the original NC."""
    surface,stream,catalog=checked_surface(surface_path)
    reference=LOAD(reference_path); target(reference)
    source=reference_path.resolve().parent; source.relative_to(ROOT)
    request_path=source/'request.json'; request=LOAD(request_path)
    input_path=Path(request['input']).resolve(); input_path.relative_to(ROOT)
    preparation_path=source/'preparation-result.json'
    if Path(request['preparation']).resolve()!=preparation_path or Path(request['output']).resolve()!=source:
        raise ValueError('Native CAM request does not identify the current prepared source')
    execution=checked_execution(input_path,preparation_path)
    cam_path=source/'cam-result.json'; cam=LOAD(cam_path); policy=LOAD(policy_path)
    if (execution['schema']!='genlaw.execution-plan.v14' or cam.get('status')!='generated_and_checked'
            or cam['native_cad_binding']!=reference or execution['cad_ref']['sha256']!=reference['cad_sha256']
            or cam.get('execution_plan_sha256')!=LOAD(preparation_path)['execution_plan_sha256']
            or policy.get('schema')!='genlaw.software-geometry-policy.v14'
            or policy['cad_sha256']!=reference['cad_sha256']
            or set(policy['features'])!={reference['features'][0]['id']}):
        raise ValueError('Current CAD, CAM, preparation or chamfer policy differs')
    frozen=frozen_policy(input_path,execution)
    if file_binding(policy_path)!=frozen['policy']:
        raise ValueError('Measurement must use the current frozen card/CAD policy')
    parent_nc=source/'program.nc'; verify_binding(parent_nc,cam['nc_sha256'])
    check_nc(parent_nc.read_text(),execution)
    control=checked_control(control_path,source) if control_path is not None else None
    material_source=control['source'] if control else source
    material_inputs=stream['inputs']; project=checked_file(material_inputs['project'])
    actual_nc=material_source/'program.nc'
    if (file_binding(project)!=file_binding(material_source/'simulation.camotics')
            or material_inputs['nc_files']!=[file_binding(actual_nc)]
            or LOAD(project)!=simulation_project(execution)):
        raise ValueError('Native material must use the current original NC, stock, tools and resolution')
    bindings={'request':file_binding(request_path),'input':file_binding(input_path),
        'preparation':file_binding(preparation_path),'native_cam':file_binding(cam_path),
        'reference':file_binding(reference_path),'policy':file_binding(policy_path),
        'input_manifest':frozen['manifest'],'parent_nc':file_binding(parent_nc),
        'actual_nc':file_binding(actual_nc),'project':file_binding(project),
        'test_only_guard_bypass':control is not None}
    if control:
        bindings.update(test_control=control['binding'],normal_guard_rejection=control['normal_guard_rejection'])
    return surface,stream,catalog,reference,bindings,stream['grid']['resolution_mm']


def sampling(surface,reference,resolution,native_count):
    """The existing geometric sample locations, independent of prior reports."""
    obj=target(reference); lo,hi=obj.low,obj.high
    levels=np.unique(np.concatenate((surface['z_levels_mm'],obj.breaks))).astype('<f8')
    if not 2<=len(levels)<=4096: raise ValueError('Invalid or over-budget complete height levels')
    requests=[('bevel',hi[2]-obj.width*float(v)) for v in np.linspace(.05,.95,11)]
    requests += [('retained_wall',lo[2]+float(v)*(hi[2]-obj.width-lo[2])) for v in [.2,.5,.8]]
    heights=[]
    for region,requested in requests:
        z=section_height(levels,requested) if float(np.min(abs(levels-requested)))<1e-10 else requested
        heights.append({'region':region,'requested_z_mm':requested,'z_mm':z})
    margin=obj.width+4*resolution
    if any(hi[i]-lo[i]<=2*margin for i in range(2)): raise ValueError('Insufficient retained-solid probe space')
    positions=[[float(x),float(y)] for x in np.linspace(lo[0]+margin,hi[0]-margin,5)
               for y in np.linspace(lo[1]+margin,hi[1]-margin,5)]
    return {'levels_mm':levels.tolist(),'levels_sha256':hashlib.sha256(levels.tobytes()).hexdigest(),
        'heights':heights,'probes':[{'id':i,'xy_mm':xy} for i,xy in enumerate(positions)],
        'ray_groups':[[i,min(i+8,native_count)] for i in range(0,native_count,8)],
        'ray_z_bounds_mm':[lo[2]-2*resolution,hi[2]+2*resolution],'resolution_mm':resolution}


def make_plan(surface_path,reference_path,policy_path,output,control_path=None):
    require_bounded_worker()
    surface,stream,_,reference,bindings,resolution=current_inputs(surface_path,reference_path,policy_path,control_path)
    spec=sampling(surface,reference,resolution,len(stream['chunks']))
    settings=schedule(surface_path,spec['levels_mm'])
    write(output,{'schema':'genlaw.planar-chamfer-measure-plan.v1','surface':file_binding(surface_path),
        'inputs':bindings,'reference':bindings['reference'],'policy':bindings['policy'],
        'sampling':spec,'schedule':settings,'sources':source_bindings(),
        'historical_measurement_required':False,'registration':'none','artificial_caps':False,
        'production_approved':False,'whole_process_card_complete':False})
    print(json.dumps({'native_measurement_planned':True,'groups':len(settings['groups']),
        'feature_sections':len(spec['heights']),'ray_groups':len(spec['ray_groups']),
        'test_only_guard_bypass':bindings['test_only_guard_bypass']}),flush=True)


def checked(path):
    plan=LOAD(path)
    if (plan.get('schema')!='genlaw.planar-chamfer-measure-plan.v1' or plan.get('sources')!=source_bindings()
            or plan.get('historical_measurement_required') is not False or plan.get('registration')!='none'
            or plan.get('artificial_caps') is not False or plan.get('production_approved') is not False
            or plan.get('whole_process_card_complete') is not False):
        raise ValueError('Unsupported or changed native multi-file chamfer measurement plan')
    inputs=plan['inputs']; control=checked_file(inputs['test_control']) if inputs.get('test_only_guard_bypass') is True else None
    surface_path=checked_file(plan['surface'])
    surface,stream,catalog,reference,current,resolution=current_inputs(surface_path,
        checked_file(plan['reference']),checked_file(plan['policy']),control)
    spec=sampling(surface,reference,resolution,len(stream['chunks']))
    settings=plan['schedule']; counts=settings['active_triangle_counts_by_span']
    intervals=len(spec['levels_mm'])-1
    if (inputs!=current or plan['sampling']!=spec or settings['levels_mm']!=spec['levels_mm']
            or settings['maximum_active_triangles']!=500000 or settings['maximum_section_segments']!=32768
            or settings['maximum_intervals_per_worker']!=16 or settings['original_intervals_removed']!=0
            or settings['native_coordinates_modified'] is not False
            or len(counts)!=min(16,intervals) or any(len(row)!=intervals for row in counts)
            or any(type(n) is not int for row in counts for n in row)
            or settings['groups']!=interval_groups(np.asarray(counts,dtype='<i8'))):
        raise ValueError('Current inputs, original samples or measurement limits changed')
    return plan,surface_path,surface,stream,catalog,reference


def part(plan_path,index,output):
    require_bounded_worker(); plan,surface_path,_,_,_,reference=checked(plan_path)
    groups=plan['schedule']['groups']
    if type(index) is not int or not 0<=index<len(groups): raise ValueError('Invalid native integral group')
    start,end=groups[index]['interval']; levels=plan['sampling']['levels_mm'][start:end+1]
    material=PlanarSections(surface_path,levels[0],levels[-1])
    if len(material.triangles)!=groups[index]['active_triangles']:
        raise ValueError('Planned occupancy differs from actual native triangles')
    coarse=integrate_variable(material,levels,target(reference),2)
    refined=integrate_variable(material,levels,target(reference),3)
    write(output,{'schema':'genlaw.planar-chamfer-integral.v1','plan':file_binding(plan_path),
        'index':index,'interval':[start,end],'coarse':coarse,'refined':refined,
        'active_triangles':len(material.triangles),'section_calls':material.calls,
        'maximum_raw_segments':material.maximum_observed_segments,
        'maximum_compacted_segments':material.maximum_compacted_segments,'source_code':file_binding(Path(__file__))})


def section(plan_path,index,output):
    require_bounded_worker(); plan,surface_path,_,_,_,reference=checked(plan_path)
    spec=plan['sampling']
    if type(index) is not int or not 0<=index<len(spec['heights']): raise ValueError('Invalid feature section')
    height=spec['heights'][index]; z=height['z_mm']; levels=spec['levels_mm']; slot=bisect.bisect_right(levels,z)-1
    if not 0<=slot<len(levels)-1: raise ValueError('Original feature section outside complete material heights')
    material=PlanarSections(surface_path,levels[slot],levels[slot+1])
    observed=section_observation('chamfer',spec,reference,height,material.at(z))
    write(output,{'schema':'genlaw.planar-chamfer-section.v1','plan':file_binding(plan_path),
        'index':index,**observed,'section_statistics':material.last_stats,'source_code':file_binding(Path(__file__))})


def rays(plan_path,index,output):
    require_bounded_worker(); plan,_,_,stream,catalog,_=checked(plan_path); spec=plan['sampling']
    if type(index) is not int or not 0<=index<len(spec['ray_groups']): raise ValueError('Invalid native ray group')
    start,end=spec['ray_groups'][index]; hits={p['id']:[] for p in spec['probes']}
    for entry,leaf in zip(stream['chunks'][start:end],catalog['chunks'][start:end]):
        bounds=LOAD(checked_file(leaf['audit']))['statistics']['bounds']
        if bounds is None: continue
        selected=[p for p in spec['probes'] if all(bounds['min'][axis]-1e-8<=p['xy_mm'][axis]<=bounds['max'][axis]+1e-8 for axis in range(2))]
        if not selected: continue
        native=triangles(checked_file(entry['surface'])).astype('<f8')
        for probe in selected: hits[probe['id']].extend(ray_hits(native,probe['xy_mm'],spec['ray_z_bounds_mm']))
    write(output,{'schema':'genlaw.planar-chamfer-rays.v1','plan':file_binding(plan_path),'index':index,
        'interval':[start,end],'hits':[{'id':p['id'],'surface_z_mm':sorted(set(hits[p['id']]))} for p in spec['probes']],
        'source_code':file_binding(Path(__file__))})


def checked_rows(paths,plan_path,schema,count):
    if len(paths)!=count: raise ValueError('Missing native measurement workers')
    rows=[]
    for index,path in enumerate(paths):
        row=LOAD(path)
        if (row.get('schema')!=schema or row.get('plan')!=file_binding(plan_path)
                or type(row.get('index')) is not int or row['index']!=index
                or row.get('source_code')!=file_binding(Path(__file__))):
            raise ValueError('Missing, duplicate, reordered or foreign native measurement result')
        rows.append(row)
    return rows


def finish(plan_path,paths,output):
    require_bounded_worker(); plan,_,surface,_,_,reference=checked(plan_path)
    groups=plan['schedule']['groups']; rows=checked_rows(paths,plan_path,'genlaw.planar-chamfer-integral.v1',len(groups))
    if any(row['interval']!=group['interval'] or row['active_triangles']!=group['active_triangles'] for row,group in zip(rows,groups)):
        raise ValueError('Original complete integral partition changed')
    coarse=combine_volumes([r['coarse'] for r in rows],2); refined=combine_volumes([r['refined'] for r in rows],3)
    delta=abs(coarse['iou']-refined['iou'])
    if (delta>1e-5 or abs(refined['actual_volume_mm3']-surface['volume_mm3'])>1e-4
            or abs(refined['target_volume_mm3']-reference['volume_mm3'])>1e-5):
        raise ValueError('Sparse integration failed unchanged stability or CAD-volume acceptance')
    write(output,{'schema':'genlaw.planar-chamfer-iou.v1','status':'measured_numerical_estimate',
        'iou':refined['iou'],'iou_percent':100*refined['iou'],'volumes':refined,
        'missing_material_mm3':refined['target_volume_mm3']-refined['intersection_mm3'],
        'excess_material_mm3':refined['actual_volume_mm3']-refined['intersection_mm3'],
        'absolute_iou_refinement_difference':delta,'plan':file_binding(plan_path),
        'surface':plan['surface'],'reference':plan['reference'],'parts':[file_binding(p) for p in paths],
        'all_original_height_intervals_preserved':True,'per_window_iou_averaged':False,
        'registration':'none; original coordinates','controls':controls(),
        'source_code':file_binding(Path(__file__)),'full_tolerance_acceptance':False,
        'production_approved':False,'whole_process_card_complete':False})


def verify(plan_path,sections,ray_paths,output):
    require_bounded_worker(); plan,_,surface,_,_,reference=checked(plan_path); spec=plan['sampling']
    sr=checked_rows(sections,plan_path,'genlaw.planar-chamfer-section.v1',len(spec['heights']))
    rr=checked_rows(ray_paths,plan_path,'genlaw.planar-chamfer-rays.v1',len(spec['ray_groups']))
    result=assemble_observations('chamfer',spec,surface,reference,LOAD(checked_file(plan['policy'])),sr,rr)
    write(output,{'schema':'genlaw.planar-chamfer-verification.v1','status':'measured',**result,
        'plan':file_binding(plan_path),'section_parts':[file_binding(p) for p in sections],
        'ray_parts':[file_binding(p) for p in ray_paths],'source_code':file_binding(Path(__file__)),
        'test_only_guard_bypass':plan['inputs']['test_only_guard_bypass'],
        **({k:plan['inputs'][k] for k in ['test_control','normal_guard_rejection']} if plan['inputs']['test_only_guard_bypass'] else {}),
        'full_tolerance_acceptance':False,'production_approved':False,'whole_process_card_complete':False})


def result_status(plan_path,metrics_path,verification_path):
    """Require all current feature criteria; explicit test controls cannot pass."""
    require_bounded_worker(); plan,_,surface,_,_,reference=checked(plan_path)
    metrics=LOAD(metrics_path); report=LOAD(verification_path); binding=file_binding(plan_path)
    if (plan.get('schema')!='genlaw.planar-chamfer-measure-plan.v1'
            or metrics.get('schema')!='genlaw.planar-chamfer-iou.v1'
            or report.get('schema')!='genlaw.planar-chamfer-verification.v1'
            or metrics.get('status')!='measured_numerical_estimate' or report.get('status')!='measured'
            or metrics.get('plan')!=binding or report.get('plan')!=binding
            or metrics.get('surface')!=plan['surface'] or metrics.get('reference')!=plan['reference']
            or metrics.get('source_code')!=file_binding(Path(__file__)) or report.get('source_code')!=file_binding(Path(__file__))
            or plan['inputs'].get('test_only_guard_bypass') is not False
            or report.get('test_only_guard_bypass') is not False or metrics.get('per_window_iou_averaged') is not False
            or metrics.get('all_original_height_intervals_preserved') is not True
            or any(row.get(k) is not False for row in [metrics,report]
                   for k in ['production_approved','whole_process_card_complete','full_tolerance_acceptance'])):
        raise ValueError('Normal acceptance requires matching current material and no test-control bypass')
    iou=metrics.get('iou')
    if type(iou) not in (int,float) or not math.isfinite(iou) or not 0<=iou<=1 or metrics.get('iou_percent')!=100*iou:
        raise ValueError('Invalid complete material IoU')
    policy=LOAD(checked_file(plan['policy']))
    sections=checked_rows([checked_file(b) for b in report['section_parts']],plan_path,
        'genlaw.planar-chamfer-section.v1',len(plan['sampling']['heights']))
    rays=checked_rows([checked_file(b) for b in report['ray_parts']],plan_path,
        'genlaw.planar-chamfer-rays.v1',len(plan['sampling']['ray_groups']))
    actual=assemble_observations('chamfer',plan['sampling'],surface,reference,policy,sections,rays)
    if any(report.get(k)!=value for k,value in actual.items()):
        raise ValueError('Feature report differs from its bound actual observations')
    parts=checked_rows([checked_file(b) for b in metrics['parts']],plan_path,
        'genlaw.planar-chamfer-integral.v1',len(plan['schedule']['groups']))
    if any(row['interval']!=group['interval'] or row['active_triangles']!=group['active_triangles']
           for row,group in zip(parts,plan['schedule']['groups'])):
        raise ValueError('Final IoU partitions differ from the complete schedule')
    refined=combine_volumes([row['refined'] for row in parts],3)
    coarse=combine_volumes([row['coarse'] for row in parts],2)
    delta=abs(coarse['iou']-refined['iou'])
    if (metrics['volumes']!=refined or iou!=refined['iou']
            or metrics['absolute_iou_refinement_difference']!=delta or delta>1e-5
            or abs(refined['actual_volume_mm3']-surface['volume_mm3'])>1e-4
            or abs(refined['target_volume_mm3']-reference['volume_mm3'])>1e-5
            or metrics['missing_material_mm3']!=refined['target_volume_mm3']-refined['intersection_mm3']
            or metrics['excess_material_mm3']!=refined['actual_volume_mm3']-refined['intersection_mm3']):
        raise ValueError('Final IoU differs from its actual volumes or original CAD')
    expected=[(fid,kind,rule) for fid,rules in policy['features'].items() for kind,rule in rules.items()]
    rows=report.get('software_regression',[])
    if not expected or [(r.get('feature_id'),r.get('kind'),r.get('criterion')) for r in rows]!=expected:
        raise ValueError('Missing or changed current feature acceptance rules')
    passed=all(r['status']=='numeric_pass' for r in rows)
    if report.get('all_software_geometry_checks_passed') is not passed:
        raise ValueError('Feature acceptance summary differs from individual criteria')
    return {'status':'declared_geometry_verified' if passed else 'declared_geometry_rejected',
        'all_software_geometry_checks_passed':passed,
        'feature_acceptance':'passed_declared_software_checks' if passed else 'failed_or_indeterminate_software_checks'}




def sections(plan_path,indices,out):
    require_bounded_worker();plan,*_=checked(plan_path)
    if (not indices or len(indices)>16 or indices!=sorted(set(indices))
            or any(type(i) is not int or not 0<=i<len(plan['sampling']['heights']) for i in indices)):
        raise ValueError('Invalid bounded feature batch')
    paths=[]
    for i in indices:
        path=out/f'section-{i:03d}.json';section(plan_path,i,path);paths.append(file_binding(path))
    write(out/'sections-batch.json',{'plan':file_binding(plan_path),'indices':indices,'sections':paths,
        'source_code':file_binding(Path(__file__))})

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); sub=parser.add_subparsers(dest='action',required=True)
    for action in ['plan','plan-control']:
        p=sub.add_parser(action)
        for name in ['surface','reference','policy','output']: p.add_argument('--'+name,type=Path,required=True)
        if action=='plan-control': p.add_argument('--control',type=Path,required=True)
    p=sub.add_parser('sections');p.add_argument('--plan',type=Path,required=True);p.add_argument('--indices',type=int,nargs='+',required=True);p.add_argument('--output',type=Path,required=True)
    for action in ['part','section','rays']:
        p=sub.add_parser(action); p.add_argument('--plan',type=Path,required=True)
        p.add_argument('--index',type=int,required=True); p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('finish'); p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--parts',type=Path,nargs='+',required=True); p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('verify'); p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--sections',type=Path,nargs='+',required=True); p.add_argument('--rays',type=Path,nargs='+',required=True)
    p.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.action in ['plan','plan-control']:
        make_plan(args.surface,args.reference,args.policy,args.output,getattr(args,'control',None))
    elif args.action=='sections': sections(args.plan,args.indices,args.output)
    elif args.action=='part': part(args.plan,args.index,args.output)
    elif args.action=='section': section(args.plan,args.index,args.output)
    elif args.action=='rays': rays(args.plan,args.index,args.output)
    elif args.action=='finish': finish(args.plan,args.parts,args.output)
    else: verify(args.plan,args.sections,args.rays,args.output)
