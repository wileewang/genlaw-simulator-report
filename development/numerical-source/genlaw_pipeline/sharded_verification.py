"""Original-CAD face, capsule and hole observations across complete surface shards."""
import argparse
import bisect
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'web/tools'))
from mesh_metrics import np,require_bounded_worker
from shapely.geometry import Polygon,box
from genlaw_pipeline.cell_windows import checked_file,file_binding
from genlaw_pipeline.sharded_surface import triangles
from genlaw_pipeline.sharded_measure import checked_surface,ShardedSections
from genlaw_pipeline.measure_worker import section_height
from genlaw_pipeline.sequence_verification import retained_probes,evaluate
from genlaw_pipeline.verification import fit_ring,match_rings,ray_hits,summarize_rays
from genlaw_pipeline.programming import checked_plan as original_plan,verify_binding
from genlaw_pipeline.facing_geometry import FACING
from genlaw_pipeline.profile_geometry import profile
from genlaw_pipeline.profile_sequence_verification import exterior_probes

RAY_WINDOWS_PER_WORKER=8
LOAD=lambda p:json.loads(p.read_text())


def write(path,data):
    with path.open('x') as f:json.dump(data,f,ensure_ascii=False,indent=2);f.write('\n')


def holes(reference):
    return [] if reference['reference_class']==FACING else reference['features']


def face_probes(reference,resolution):
    if reference['reference_class']!=FACING:return retained_probes(reference,resolution)
    low,high=[reference['bounds'][k] for k in ('min','max')]
    return [[low[0]+float(u)*(high[0]-low[0]),low[1]+float(v)*(high[1]-low[1])]
            for u in np.linspace(.05,.95,5) for v in np.linspace(.05,.95,5)]


def make_plan(measure_plan,policy_path,output,checkpoint_context=None,control_binding=None):
    require_bounded_worker()
    measure=LOAD(measure_plan);checked_file(measure['source_code']);surface=checked_surface(checked_file(measure['surface']))
    reference_path=checked_file(measure['reference']);reference=LOAD(reference_path);policy=LOAD(policy_path)
    version=reference.get('schema','').rsplit('.',1)[-1]
    if (version not in ('v9','v11') or reference['schema']!='genlaw.native-binding.'+version or 'facing_feature' not in reference
            or policy.get('schema')!='genlaw.software-geometry-policy.'+version or policy['cad_sha256']!=reference['cad_sha256']):
        raise ValueError('Sharded observations require bound original ordered-milling CAD and policy')
    source=reference_path.parent
    cell=LOAD(checked_file(surface['plan']));res=cell['grid']['resolution_mm']
    context=None
    control=None
    if checkpoint_context and control_binding:raise ValueError('A negative final control cannot claim a normal checkpoint')
    if control_binding:
        from genlaw_pipeline.window_controls import checked_control
        control=checked_control(control_binding,source)
        if (file_binding(policy_path)!=file_binding(control['policy']) or reference!=control['reference']
                or cell['binding']['project']!=file_binding(control['source']/'simulation.camotics')
                or cell['binding']['nc_files']!=[file_binding(control['source']/'program.nc')]
                or surface['end_time_seconds'] is not None):
            raise ValueError('Negative geometry surface differs from its explicit original NC control')
        prepared_source=file_binding(source/'preparation-result.json');native_cam=file_binding(source/'cam-result.json')
    elif checkpoint_context:
        from genlaw_pipeline.checkpoint_context import checked_context
        context=checked_context(checkpoint_context);files=context['files']
        if (file_binding(reference_path)!=files['reference'] or file_binding(policy_path)!=files['policy']
                or cell['binding']['project']!=files['project'] or cell['binding']['nc_files']!=[files['nc']]
                or res!=context['resolution_mm'] or surface['end_time_seconds'] is not None):
            raise ValueError('Surface/reference differs from the exact native checkpoint')
        prepared_source=files['parent_preparation'];native_cam=files['parent_cam']
    else:
        request=LOAD(source/'request.json')
        original=original_plan(request['input'],source/'preparation-result.json');cam=LOAD(source/'cam-result.json')
        verify_binding(source/'program.nc',cam['nc_sha256'])
        if (reference['cad_sha256']!=original['cad_ref']['sha256'] or cam['native_cad_binding']!=reference
                or cell['binding']['project']!=file_binding(source/'simulation.camotics')):
            raise ValueError('Surface/reference differs from the original prepared CAD and native CAM')
        prepared_source=file_binding(source/'preparation-result.json');native_cam=file_binding(source/'cam-result.json')
    low,high=[reference['bounds'][k] for k in ('min','max')]
    levels=np.unique(np.concatenate((surface['z_levels_mm'],[low[2],high[2],reference['incoming_stock_bounds']['max'][2]])))
    heights=[]
    for fraction in np.linspace(.05,.95,11):
        requested=low[2]+float(fraction)*(high[2]-low[2]);z=section_height(levels,requested)
        heights.append({'requested_z_mm':requested,'z_mm':z})
    probes=[]
    for xy in face_probes(reference,res):probes.append({'feature_id':reference['facing_feature']['id'],'xy_mm':xy,'role':'retained_face'})
    for feature in holes(reference):
        x,y=feature['center_xy_mm'];r=feature['diameter_mm']/2
        positions=[[x,y]]+[[x+r*.25*math.cos(a),y+r*.25*math.sin(a)] for a in np.arange(8)*math.pi/4]
        probes.extend({'feature_id':feature['id'],'xy_mm':xy,'role':'through'} for xy in positions)
    if 'profile_feature' in reference:
        outline=reference['profile_feature']
        probes.extend({'feature_id':outline['id'],'xy_mm':xy,'role':'exterior_clearance'}
            for xy in exterior_probes(outline,low,high,res))
    for i,p in enumerate(probes):p['id']=i
    write(output,{'schema':'genlaw.sharded-feature-plan.v1','measure_plan':file_binding(measure_plan),
        'surface':measure['surface'],'reference':measure['reference'],'policy':file_binding(policy_path),
        'heights':heights,'probes':probes,'resolution_mm':res,
        'ray_z_bounds_mm':[low[2]-2*res,reference['incoming_stock_bounds']['max'][2]+2*res],
        'ray_groups':[[i,min(i+RAY_WINDOWS_PER_WORKER,len(surface['windows']))] for i in range(0,len(surface['windows']),RAY_WINDOWS_PER_WORKER)],
        'source_code':file_binding(Path(__file__)),'original_evaluator':file_binding(ROOT/'genlaw_pipeline/sequence_verification.py'),
        'original_hole_observer':file_binding(ROOT/'genlaw_pipeline/verification.py'),
        'prepared_source':prepared_source,'native_cam':native_cam,
        **({'checkpoint_context':file_binding(checkpoint_context)} if context else {}),
        **({'test_control':control['binding'],'test_control_validator':control['validator'],
            'normal_guard_rejection':control['normal_guard_rejection']} if control else {}),
        'face_only_observer':file_binding(ROOT/'genlaw_pipeline/facing_verification.py'),
        **({'profile_observer':file_binding(ROOT/'genlaw_pipeline/profile_sequence_verification.py'),
            'profile_geometry':file_binding(ROOT/'genlaw_pipeline/profile_geometry.py')} if 'profile_feature' in reference else {}),
        'probe_selection':'Unchanged original-CAD deterministic 25 retained-face and 9 per-hole probes, plus 20 exterior rays when profiled; selected independently of actual surface.'})


def checked(path):
    plan=LOAD(path)
    if plan.get('schema')!='genlaw.sharded-feature-plan.v1':raise ValueError('Unsupported feature plan')
    for key in ('source_code','original_evaluator','original_hole_observer','measure_plan','reference','policy','surface','prepared_source','native_cam'):checked_file(plan[key])
    checked_file(plan['face_only_observer'])
    if 'checkpoint_context' in plan:
        from genlaw_pipeline.checkpoint_context import checked_context
        checked_context(checked_file(plan['checkpoint_context']))
    if 'test_control' in plan:
        from genlaw_pipeline.window_controls import checked_control
        checked_file(plan['test_control_validator'])
        checked_control(checked_file(plan['test_control']),checked_file(plan['reference']).parent)
    checked_file(LOAD(checked_file(plan['measure_plan']))['source_code'])
    surface=checked_surface(checked_file(plan['surface']));reference=LOAD(checked_file(plan['reference']))
    if 'profile_feature' in reference:
        checked_file(plan['profile_observer']);checked_file(plan['profile_geometry'])
    return plan,surface,reference


def ray_group(plan_path,index,output):
    require_bounded_worker();plan,surface,ref=checked(plan_path)
    if type(index) is not int or not 0<=index<len(plan['ray_groups']):raise ValueError('Invalid ray-group index')
    start,end=plan['ray_groups'][index]
    if not 0<end-start<=RAY_WINDOWS_PER_WORKER:raise ValueError('Ray group exceeds bounded window count')
    hits={p['id']:[] for p in plan['probes']}
    for row in surface['windows'][start:end]:
        result=LOAD(checked_file(row['result']));audit=LOAD(checked_file(row['audit']));bounds=audit['bounds']
        if bounds is None:continue
        selected=[p for p in plan['probes'] if all(bounds['min'][k]-1e-8<=p['xy_mm'][k]<=bounds['max'][k]+1e-8 for k in range(2))]
        if not selected:continue
        tri=triangles(checked_file(result['surface'])).astype(np.float64)
        for p in selected:hits[p['id']].extend(ray_hits(tri,p['xy_mm'],plan['ray_z_bounds_mm']))
    write(output,{'schema':'genlaw.sharded-ray-part.v1','plan':file_binding(plan_path),'index':index,'window_interval':[start,end],
        'hits':[{'id':p['id'],'surface_z_mm':sorted(set(hits[p['id']]))} for p in plan['probes']],
        'source_code':file_binding(Path(__file__))})


def section(plan_path,index,output):
    require_bounded_worker();plan,surface,ref=checked(plan_path)
    if type(index) is not int or not 0<=index<len(plan['heights']):raise ValueError('Invalid feature-section index')
    height=plan['heights'][index];z=height['z_mm'];measure=LOAD(checked_file(plan['measure_plan']))
    levels=measure['levels_mm'];i=bisect.bisect_right(levels,z)-1
    if not 0<=i<len(levels)-1:raise ValueError('Observation plane is outside original material height intervals')
    provider=ShardedSections(measure,levels[i],levels[i+1]);actual=provider.at(z)
    pieces=list(actual.geoms) if actual.geom_type=='MultiPolygon' else [actual]
    rings=[r for piece in pieces if piece.geom_type=='Polygon' for r in piece.interiors]
    hole_features=holes(ref)
    matched=match_rings(rings,hole_features,plan['resolution_mm']);hole_rows={}
    for f in hole_features:
        row={**height,'interior_loops':len(rings),'matched':f['id'] in matched}
        if row['matched']:
            row.update(fit_ring(rings[matched[f['id']]]))
            row['center_deviation_mm']=math.dist(row['fit_center_xy_mm'],f['center_xy_mm'])
        hole_rows[f['id']]=row
    valid=len(pieces)==1 and not actual.is_empty and pieces[0].geom_type=='Polygon'
    expected=profile(ref['profile_feature'],2048) if 'profile_feature' in ref else box(*ref['bounds']['min'][:2],*ref['bounds']['max'][:2])
    outer=Polygon(pieces[0].exterior) if valid else None
    face={**height,'retained_body_count':len(pieces),'topology_matches':valid,'holes_checked_separately':True,
        'boundary_deviation_mm':outer.boundary.hausdorff_distance(expected.boundary) if valid else None}
    write(output,{'schema':'genlaw.sharded-feature-section.v1','plan':file_binding(plan_path),'index':index,
        'holes':hole_rows,'face':face,'counts':{'z_mm':z,'expected_holes':len(hole_features),'observed_holes':len(rings),
            'matched_features':list(matched),'unassigned_contours':len(rings)-len(matched)},
        'source_code':file_binding(Path(__file__))})


def finish(plan_path,rays,sections,output):
    require_bounded_worker();plan,surface,ref=checked(plan_path);policy=LOAD(checked_file(plan['policy']))
    if len(rays)!=len(plan['ray_groups']) or len(sections)!=len(plan['heights']):raise ValueError('Missing feature measurement partitions')
    results={p['id']:[] for p in plan['probes']};section_rows=[]
    for i,path in enumerate(rays):
        row=LOAD(path)
        if (row.get('schema')!='genlaw.sharded-ray-part.v1' or row['index']!=i or row['plan']!=file_binding(plan_path)
                or row['window_interval']!=plan['ray_groups'][i] or [p['id'] for p in row['hits']]!=list(results)
                or row['source_code']!=plan['source_code']):raise ValueError('Missing, foreign or overlapping ray evidence')
        for p in row['hits']:results[p['id']].extend(p['surface_z_mm'])
    for i,path in enumerate(sections):
        row=LOAD(path)
        if (row.get('schema')!='genlaw.sharded-feature-section.v1' or row['index']!=i or row['plan']!=file_binding(plan_path)
                or row['source_code']!=plan['source_code']):raise ValueError('Missing, foreign or duplicate feature sections')
        section_rows.append(row)
    all_hits=[{**p,'surface_z_mm':sorted(set(results[p['id']]))} for p in plan['probes']]
    guard=2*plan['resolution_mm'];features={};low=ref['bounds']['min']
    for f in holes(ref):
        rows=[r['holes'][f['id']] for r in section_rows];complete=all(row['matched'] for row in rows)
        hits=[{'xy_mm':p['xy_mm'],'surface_z_mm':p['surface_z_mm']} for p in all_hits if p['feature_id']==f['id']]
        features[f['id']]={'sections':rows,'section_count':len(rows),'all_sections_have_matched_hole':complete,
            'diameter':{'radial_band_mm':[min(s['radial_diameter_band_mm'][0] for s in rows),max(s['radial_diameter_band_mm'][1] for s in rows)],
                        'fit_range_mm':[min(s['fit_diameter_mm'] for s in rows),max(s['fit_diameter_mm'] for s in rows)]} if complete else None,
            'position':{'max_center_deviation_mm':max(s['center_deviation_mm'] for s in rows),
                        'centers_xy_mm':[s['fit_center_xy_mm'] for s in rows]} if complete else None,
            'through':summarize_rays(hits,guard),'resolution_mm':plan['resolution_mm'],'numerical_guard_mm':guard,
            'guard_is_certified_uncertainty':False,'registration_applied':False}
    probes=[]
    for p in all_hits:
        if p['role']!='retained_face':continue
        zs=p['surface_z_mm'];valid=len(zs)==2
        probes.append({'xy_mm':p['xy_mm'],'surface_z_mm':zs,'status':'measured' if valid else 'ambiguous_intersections',
            'top_z_mm':zs[-1] if valid else None,'thickness_mm':zs[-1]-zs[0] if valid else None,
            'bottom_deviation_mm':abs(zs[0]-low[2]) if valid else None})
    contours=[r['face'] for r in section_rows];complete=all(p['status']=='measured' for p in probes)
    contours_complete=all(c['boundary_deviation_mm'] is not None for c in contours)
    tops=[p['top_z_mm'] for p in probes] if complete else [];thicknesses=[p['thickness_mm'] for p in probes] if complete else []
    face={'probes':probes,'probe_count':len(probes),'complete':complete,'sections':contours,'section_count':11,
        'contours_complete':contours_complete,'topology_matches':all(c['topology_matches'] for c in contours),
        'max_boundary_deviation_mm':max(c['boundary_deviation_mm'] for c in contours) if contours_complete else None,
        'top_z_band_mm':[min(tops),max(tops)] if complete else None,'thickness_band_mm':[min(thicknesses),max(thicknesses)] if complete else None,
        'top_flatness_range_mm':max(tops)-min(tops) if complete else None,
        'max_bottom_deviation_mm':max([p['bottom_deviation_mm'] for p in probes]+[abs(surface['bounds']['min'][2]-low[2])]) if complete else None,
        'mesh_max_z_mm':surface['bounds']['max'][2],'resolution_mm':plan['resolution_mm'],'numerical_guard_mm':guard,
        'guard_is_certified_uncertainty':False,'registration_applied':False}
    features[ref['facing_feature']['id']]=face
    if 'profile_feature' in ref:
        waste=[]
        for p in all_hits:
            if p['role']!='exterior_clearance':continue
            zs=p['surface_z_mm'];lengths=[b-a for a,b in zip(zs[::2],zs[1::2])]
            state='clear' if not zs else 'ambiguous' if len(zs)%2 else 'blocked' if max(lengths)>guard else 'sub_guard_signal'
            waste.append({'xy_mm':p['xy_mm'],'surface_z_mm':zs,'observation':state,'maximum_material_interval_mm':max(lengths,default=0)})
        if len(waste)!=20:raise ValueError('Missing original capsule exterior probes')
        fields=['probes','probe_count','complete','sections','section_count','contours_complete','topology_matches',
                'max_boundary_deviation_mm','thickness_band_mm','resolution_mm','numerical_guard_mm',
                'guard_is_certified_uncertainty','registration_applied']
        item={k:face[k] for k in fields};high=ref['bounds']['max']
        item.update(max_plane_deviation_mm=max(face['max_bottom_deviation_mm'],
            *[abs(z-high[2]) for z in face['top_z_band_mm']],abs(face['mesh_max_z_mm']-high[2])) if face['complete'] else None,
            exterior_probes=waste,exterior_probe_count=len(waste),exterior_all_clear=all(p['observation']=='clear' for p in waste),
            disconnected_exterior_material_discarded=False,
            probe_selection='Original capsule retained solid, excluding all intended holes; never adapted to simulated material.')
        features[ref['profile_feature']['id']]=item
    if set(features)!=set(policy['features']):raise ValueError('Original feature policy does not cover the exact measured set')
    checks=[{'feature_id':fid,'kind':kind,**evaluate(kind,features[fid],rule)} for fid,rules in policy['features'].items() for kind,rule in rules.items()]
    unexpected=any(r['counts']['unassigned_contours'] for r in section_rows) or not face['topology_matches']
    write(output,{'schema':'genlaw.sharded-feature-verification.v1','status':'measured','plan':file_binding(plan_path),
        'software_regression':checks,'observations':{'features':features,'unassigned_contours_detected':unexpected,
            'section_feature_counts':[r['counts'] for r in section_rows]},
        'all_software_geometry_checks_passed':all(r['status']=='numeric_pass' for r in checks) and not unexpected,
        'ray_parts':[file_binding(p) for p in rays],'section_parts':[file_binding(p) for p in sections],
        'source_code':file_binding(Path(__file__)),'production_approved':False,'whole_process_card_complete':False,
        **({'test_only_guard_bypass':True,'control_binding':plan['test_control'],
            'normal_guard_rejection':plan['normal_guard_rejection']} if 'test_control' in plan else {}),
        'limits':['Finite original-CAD probes and sections, not manufacturing acceptance.',
            'This report covers one bound cumulative NC prefix; final geometry is reported separately.'
            if 'checkpoint_context' in plan else 'Actual cumulative-prefix validation is separate.']})


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    start=sub.add_parser('plan');start.add_argument('--checkpoint-context',type=Path);start.add_argument('--control-binding',type=Path)
    for n in ('measure-plan','policy','output'):start.add_argument('--'+n,required=True,type=Path)
    for mode in ('rays','section'):
        s=sub.add_parser(mode);s.add_argument('--plan',required=True,type=Path);s.add_argument('--index',required=True,type=int);s.add_argument('--output',required=True,type=Path)
    end=sub.add_parser('finish')
    for n in ('plan','directory','output'):end.add_argument('--'+n,required=True,type=Path)
    a=p.parse_args()
    if a.action=='plan':make_plan(a.measure_plan,a.policy,a.output,a.checkpoint_context,a.control_binding)
    elif a.action=='rays':ray_group(a.plan,a.index,a.output)
    elif a.action=='section':section(a.plan,a.index,a.output)
    else:finish(a.plan,sorted(a.directory.glob('rays-[0-9][0-9][0-9].json')),sorted(a.directory.glob('section-[0-9][0-9][0-9].json')),a.output)


if __name__=='__main__':main()
