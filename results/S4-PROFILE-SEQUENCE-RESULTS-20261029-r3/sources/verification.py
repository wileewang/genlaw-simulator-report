"""Numerical checks for one straight Z hole; no production/GD&T qualification."""
import argparse
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'web/tools'))
from genlaw_pipeline.preparation import digest,load_json,number
from genlaw_pipeline.programming import checked_plan,verify_binding
from genlaw_pipeline.drilling_geometry import SINGLE, MULTI, DRILLED, operations, reference_holes
from genlaw_pipeline.measure_worker import section_height
from mesh_metrics import require_bounded_worker,load_mesh,Sections,np
from shapely.geometry import Polygon,Point


def fit_ring(ring):
    """Uniform perimeter weighting; retain the entire polygon's radial extremes."""
    if not ring.is_ring or ring.length<=0:raise ValueError('Need a closed hole contour')
    points=np.array([ring.interpolate(i/256,normalized=True).coords[0][:2] for i in range(256)])
    origin=points.mean(axis=0);local=points-origin
    matrix=np.column_stack((2*local,np.ones(len(local))))
    solution,_,rank,_=np.linalg.lstsq(matrix,np.sum(local*local,axis=1),rcond=None)
    if rank!=3:raise ValueError('Circle fit is not determined')
    center=solution[:2]+origin
    radii=np.linalg.norm(points-center,axis=1)
    vertices=np.asarray(ring.coords)[:,:2]
    low=ring.distance(Point(*center));high=float(np.linalg.norm(vertices-center,axis=1).max())
    return {'fit_center_xy_mm':center.tolist(),'fit_diameter_mm':2*float(radii.mean()),
            'radial_diameter_band_mm':[2*low,2*high],
            'area_equivalent_diameter_mm':2*math.sqrt(Polygon(ring).area/math.pi),
            'radial_rms_mm':float(np.sqrt(np.mean((radii-radii.mean())**2))),
            'perimeter_fit_samples':256}


def ray_hits(triangles,xy,z_bounds):
    """Vertical line/triangle intersections across the full stock thickness."""
    a=triangles[:,0];u=triangles[:,1]-a;v=triangles[:,2]-a
    det=u[:,0]*v[:,1]-u[:,1]*v[:,0]
    valid=np.abs(det)>1e-14
    # Vertical walls have zero projected area. Probe locations are strictly
    # inside the expected hole; wall-edge coincidence is not a supported gauge.
    a,u,v,det=a[valid],u[valid],v[valid],det[valid]
    dx,dy=xy[0]-a[:,0],xy[1]-a[:,1]
    s=(dx*v[:,1]-dy*v[:,0])/det;t=(u[:,0]*dy-u[:,1]*dx)/det
    inside=(s>=-1e-10)&(t>=-1e-10)&(s+t<=1+1e-10)
    z=a[:,2]+s*u[:,2]+t*v[:,2]
    z=z[inside&(z>=z_bounds[0])&(z<=z_bounds[1])]
    return np.unique(np.round(z,8)).tolist()


def summarize_rays(hits,guard):
    """Preserve tiny/ambiguous intersections rather than erasing them."""
    probes=[]
    for source in hits:
        item=dict(source);z=item['surface_z_mm']
        spans=[b-a for a,b in zip(z[::2],z[1::2])] if len(z)%2==0 else None
        status=('clear' if not z else 'ambiguous_intersections' if spans is None else
                'blocked' if max(spans)>guard else 'residue_below_grid_guard')
        item.update(material_spans_mm=spans,classification=status);probes.append(item)
    clear=sum(x['classification']=='clear' for x in probes)
    blocked=sum(x['classification']=='blocked' for x in probes)
    return {'method':'axial_probe_clear','probe_count':len(probes),'clear_count':clear,'blocked_count':blocked,
            'indeterminate_count':len(probes)-clear-blocked,'probes':probes,
            'observation':'axial_rays_clear' if clear==len(probes) else 'blocked_material_detected' if blocked else 'indeterminate_at_mesh_resolution'}


def hole_observations(vertices,faces,reference,resolution):
    center=reference['hole_center_xy'];radius=reference['hole_radius_mm']
    lo,hi=reference['bounds']['min'][2],reference['bounds']['max'][2]
    levels=np.unique(np.concatenate((vertices[:,2].astype(float),[lo,hi])))
    if len(levels)>192:raise ValueError('Section count exceeds local budget')
    sections=Sections(vertices,faces);samples=[]
    for fraction in np.linspace(.05,.95,11):
        requested=lo+(hi-lo)*float(fraction);z=section_height(levels,requested)
        material=sections.at(z)
        pieces=list(material.geoms) if material.geom_type=='MultiPolygon' else [material]
        rings=[r for p in pieces for r in p.interiors]
        sample={'requested_z_mm':requested,'z_mm':z,'interior_loops':len(rings)}
        if len(rings)==1:
            sample.update(fit_ring(rings[0]))
            sample['center_deviation_mm']=math.dist(sample['fit_center_xy_mm'],center)
        samples.append(sample)
    probes=[list(center)]+[[center[0]+radius*.25*math.cos(a),center[1]+radius*.25*math.sin(a)]
                           for a in np.arange(8)*math.pi/4]
    # Include the discretized top/bottom surfaces instead of cropping away a
    # thin residual floor. These are ray observations, not a continuum proof.
    hits=[{'xy_mm':p,'surface_z_mm':ray_hits(sections.triangles,p,(lo-2*resolution,hi+2*resolution))} for p in probes]
    complete=all(s['interior_loops']==1 for s in samples)
    return {'method':'11 sections / perimeter-weighted circle fit / 9 full-height axial rays',
            'sections':samples,'section_count':len(samples),'all_sections_have_one_hole':complete,
            'diameter':{'radial_band_mm':[min(s['radial_diameter_band_mm'][0] for s in samples),
                                         max(s['radial_diameter_band_mm'][1] for s in samples)],
                        'fit_range_mm':[min(s['fit_diameter_mm'] for s in samples),max(s['fit_diameter_mm'] for s in samples)]} if complete else None,
            'position':{'max_center_deviation_mm':max(s['center_deviation_mm'] for s in samples),
                        'centers_xy_mm':[s['fit_center_xy_mm'] for s in samples]} if complete else None,
            'through':summarize_rays(hits,2*resolution),
            'resolution_mm':resolution,'numerical_guard_mm':2*resolution,
            'guard_is_certified_uncertainty':False,'registration_applied':False}


def match_rings(rings, features, resolution):
    """Associate observed contours without reusing a neighbour for a missing hole.

    A bounded neighbourhood allows a displaced hole to be measured, while
    multiple candidates or competing assignments remain explicitly ambiguous.
    """
    centers=[(Polygon(r).centroid.x,Polygon(r).centroid.y) for r in rings]
    candidates={}
    for f in features:
        neighbours=[math.dist(f['center_xy_mm'],g['center_xy_mm']) for g in features if g['id']!=f['id']]
        limit=min([f['diameter_mm']/2+2*resolution]+[d*.45 for d in neighbours])
        candidates[f['id']]=[i for i,c in enumerate(centers) if math.dist(c,f['center_xy_mm'])<=limit]
    proposed={fid:indices[0] for fid,indices in candidates.items() if len(indices)==1}
    return {fid:index for fid,index in proposed.items() if list(proposed.values()).count(index)==1}


def pattern_observations(vertices,faces,reference,resolution):
    features=reference_holes(reference)
    lo,hi=reference['bounds']['min'][2],reference['bounds']['max'][2]
    levels=np.unique(np.concatenate((vertices[:,2].astype(float),[lo,hi])))
    # Only eleven observation slices are evaluated, irrespective of the number
    # of mesh vertex heights. Full-volume integration has its own job budget.
    if len(levels)>4096:raise ValueError('Section indexing budget exceeded')
    sections=Sections(vertices,faces);samples={f['id']:[] for f in features};counts=[]
    for fraction in np.linspace(.05,.95,11):
        requested=lo+(hi-lo)*float(fraction);z=section_height(levels,requested)
        material=sections.at(z);pieces=list(material.geoms) if material.geom_type=='MultiPolygon' else [material]
        rings=[r for p in pieces for r in p.interiors];matched=match_rings(rings,features,resolution)
        counts.append({'z_mm':z,'expected_holes':len(features),'observed_holes':len(rings),
                       'matched_features':list(matched),'unassigned_contours':len(rings)-len(matched)})
        for f in features:
            row={'requested_z_mm':requested,'z_mm':z,'interior_loops':len(rings),'matched':f['id'] in matched}
            if row['matched']:
                row.update(fit_ring(rings[matched[f['id']]]))
                row['center_deviation_mm']=math.dist(row['fit_center_xy_mm'],f['center_xy_mm'])
            samples[f['id']].append(row)
    results={}
    for f in features:
        center=f['center_xy_mm'];radius=f['diameter_mm']/2;rows=samples[f['id']]
        complete=all(s['matched'] for s in rows)
        probes=[list(center)]+[[center[0]+radius*.25*math.cos(a),center[1]+radius*.25*math.sin(a)]
                               for a in np.arange(8)*math.pi/4]
        hits=[{'xy_mm':p,'surface_z_mm':ray_hits(sections.triangles,p,(lo-2*resolution,hi+2*resolution))} for p in probes]
        results[f['id']]={'sections':rows,'section_count':len(rows),'all_sections_have_matched_hole':complete,
            'diameter':{'radial_band_mm':[min(s['radial_diameter_band_mm'][0] for s in rows),
                                         max(s['radial_diameter_band_mm'][1] for s in rows)],
                        'fit_range_mm':[min(s['fit_diameter_mm'] for s in rows),max(s['fit_diameter_mm'] for s in rows)]} if complete else None,
            'position':{'max_center_deviation_mm':max(s['center_deviation_mm'] for s in rows),
                        'centers_xy_mm':[s['fit_center_xy_mm'] for s in rows]} if complete else None,
            'through':summarize_rays(hits,2*resolution),'resolution_mm':resolution,'numerical_guard_mm':2*resolution,
            'guard_is_certified_uncertainty':False,'registration_applied':False}
    return {'method':'11 shared sections / one-to-one local contour association / 9 axial rays per hole',
            'features':results,'section_feature_counts':counts,'expected_feature_count':len(features),
            'unassigned_contours_detected':any(c['unassigned_contours'] for c in counts)}


def evaluate(kind,observation,rule):
    """Keep missing criteria, unsupported measurements and marginal cases distinct."""
    if kind in ('cylindrical_depth','conical_bottom'):
        from genlaw_pipeline.blind_verification import evaluate_blind
        return evaluate_blind(kind,observation,rule)
    if kind=='through' and observation.get('hole_kind')=='blind_hole':
        return {'status':'unsupported_measurement','reason':'Use a conical-bottom criterion for a blind hole; clear-through rays are not its acceptance method.'}
    if kind not in ('diameter','position_xy','through'):
        return {'status':'unsupported_measurement','reason':'No roughness/material or general GD&T evaluator.'}
    if rule is None:return {'status':'pending_criteria','reason':'No approved numerical criterion supplied.'}
    try:
        if (rule.get('schema')!='genlaw.numeric-acceptance.v1' or rule.get('units')!='mm'
            or rule.get('scope') not in ('software_regression','engineering_requirement')
            or not isinstance(rule.get('basis'),str) or not rule['basis'].strip()):
            raise ValueError('Need version, units, scope and explicit criterion basis')
        method={'diameter':'sampled_radial_diameter_band','position_xy':'sampled_center_radial_deviation','through':'axial_probe_clear'}[kind]
        if rule.get('method')!=method:raise ValueError('Criterion method not supported for this requirement')
        guard=observation['numerical_guard_mm']
        if kind=='through':
            if set(rule)-{'schema','units','scope','basis','method','require_clear_probes'}:
                raise ValueError('Unknown axial-probe criterion field')
            if rule.get('require_clear_probes')!=9 or isinstance(rule.get('require_clear_probes'),bool):
                raise ValueError('This method requires all 9 probes')
            status=('numeric_pass' if observation['through']['clear_count']==9 else
                    'numeric_fail' if observation['through']['blocked_count'] else 'indeterminate')
        elif kind=='diameter':
            if set(rule)-{'schema','units','scope','basis','method','min_mm','max_mm'}:raise ValueError('Unknown diameter criterion field')
            lower=number(rule.get('min_mm'),'min_mm',True);upper=number(rule.get('max_mm'),'max_mm',True)
            if lower>=upper:raise ValueError('Reversed diameter limits')
            if observation['diameter'] is None:return {'status':'not_measurable','reason':'Not one closed hole at every sampled section.'}
            a,b=observation['diameter']['radial_band_mm']
            status='numeric_fail' if b<lower-guard or a>upper+guard or a<lower-guard or b>upper+guard else 'numeric_pass' if a>=lower+guard and b<=upper-guard else 'indeterminate'
        else:
            if set(rule)-{'schema','units','scope','basis','method','max_deviation_mm'}:raise ValueError('Unknown position criterion field')
            limit=number(rule.get('max_deviation_mm'),'max_deviation_mm',True)
            if observation['position'] is None:return {'status':'not_measurable','reason':'Not one closed hole at every sampled section.'}
            deviation=observation['position']['max_center_deviation_mm']
            status='numeric_pass' if deviation+guard<=limit else 'numeric_fail' if deviation-guard>limit else 'indeterminate'
        return {'status':status,'criterion_scope':rule['scope'],'criterion':rule,'production_approved':False,
                'reason':'Numerical test under the declared method; finite sampling and mesh resolution are not a production certificate.'}
    except (ValueError,TypeError,AttributeError) as error:
        return {'status':'unsupported_criterion','reason':str(error)}


def verify(run,output,control=None,software_policy=None):
    require_bounded_worker()
    request=load_json(run/'request.json');input_path=Path(request['input'])
    plan=checked_plan(input_path,run/'preparation-result.json')
    requirements_path=input_path.parent/plan['requirements_ref']['path'];requirements=load_json(requirements_path)
    ref=load_json(run/'native-geometry.json');cam=load_json(run/'cam-result.json')
    if cam['status']!='generated_and_checked' or cam['native_cad_binding']!=ref or ref['cad_sha256']!=plan['cad_ref']['sha256']:
        raise ValueError('Native CAD binding is missing or changed')
    verify_binding(run/'program.nc',cam['nc_sha256'])
    if control:
        binding=load_json(control)
        if binding.get('test_only_guard_bypass') is not True or binding['parent_nc_sha256']!=cam['nc_sha256']:
            raise ValueError('Unbound test-only control')
        root=control.parent
        if any(Path(binding[k]).name!=binding[k] for k in ['nc_file','mesh_file','project_file']):raise ValueError('Control artifacts must be local')
        actual=root/binding['mesh_file'];project=root/binding['project_file'];nc=root/binding['nc_file']
        for path,key in [(actual,'mesh_sha256'),(project,'project_sha256'),(nc,'nc_sha256')]:verify_binding(path,binding[key])
    else:
        binding=load_json(run/'simulation-result.json');actual=run/'simulated.stl';project=run/'simulation.camotics';nc=run/'program.nc'
        if binding['nc_sha256_before']!=binding['nc_sha256_after'] or binding['nc_sha256_after']!=cam['nc_sha256']:raise ValueError('Simulation NC binding mismatch')
        verify_binding(actual,binding['mesh_sha256']);verify_binding(project,binding['project_sha256'])
    config=load_json(project)
    if config['workpiece']['bounds']!=plan['stock']['bounds_mm'] or config['files']!=[nc.name]:raise ValueError('Simulation frame/NC mismatch')
    resolution=number(config['resolution'],'resolution',True)
    vertices,faces,inspection=load_mesh(actual)
    planned={op['feature_id']:op for op in operations(plan)}
    pocket=plan['schema']=='genlaw.execution-plan.v4'
    step=plan['schema']=='genlaw.execution-plan.v5'
    region=plan['schema']=='genlaw.execution-plan.v6'
    facing=plan['schema']=='genlaw.execution-plan.v7'
    profiled_sequence=plan['schema']=='genlaw.execution-plan.v11'
    sequence=plan['schema'] in ('genlaw.execution-plan.v9','genlaw.execution-plan.v11')
    external=plan['schema']=='genlaw.execution-plan.v10'
    evaluator=evaluate
    drilled=plan['schema']=='genlaw.execution-plan.v3'
    multiple=plan['schema']!='genlaw.execution-plan.v1'
    process_conformance=None
    if sequence:
        from genlaw_pipeline.sequence_verification import observations as sequence_observations,evaluate as evaluator
        from genlaw_pipeline.sequence_nc import check_nc as check_sequence_nc
        from genlaw_pipeline.sequence_checkpoints import verify_checkpoint_bindings
        from genlaw_pipeline.programming import simulation_project
        reference_class=MULTI
        if profiled_sequence:
            from genlaw_pipeline.profile_sequence_geometry import REFERENCE as reference_class
            from genlaw_pipeline.profile_sequence_verification import observations as sequence_observations
        if (ref['reference_class']!=reference_class or ref['feature_bindings']!=[op['geometry'] for op in operations(plan)]
            or ref['bounds']!=plan['target_bounds_mm'] or ref['incoming_stock_bounds']!=plan['stock']['bounds_mm']):
            raise ValueError('Original combined feature or incoming stock binding changed')
        if config!=simulation_project(plan):raise ValueError('Sequence simulation must retain the explicit complete tool table and original stock')
        observations=sequence_observations(vertices,faces,ref,resolution);by_feature=observations['features']
        try:check_sequence_nc(nc.read_text(),plan)
        except ValueError as error:
            if not control:raise
            process_conformance={'status':'rejected','reason':str(error),'modified_control_nc':True,'checkpoints':'not_applicable_to_modified_control_nc'}
        else:
            if control:raise ValueError('Test control was not rejected by normal sequence guard')
            checkpoint=verify_checkpoint_bindings(run,cam)
            process_conformance={'status':'matched_card_and_measured_checkpoints','material_checkpoint_count':len(checkpoint['checkpoints']),
                'material_checkpoints_sha256':digest(run/'material-checkpoints.json'),'manufacturing_acceptance':False}
    elif external:
        from genlaw_pipeline.profile_geometry import REFERENCE as CAPSULE
        from genlaw_pipeline.profile_verification import observations as profile_observations,evaluate as evaluator
        from genlaw_pipeline.programming import simulation_project,check_nc
        if (ref['reference_class']!=CAPSULE or ref['features']!=[op['geometry'] for op in operations(plan)]
            or ref['bounds']!=plan['target_bounds_mm'] or ref['incoming_stock_bounds']!=plan['stock']['bounds_mm']):
            raise ValueError('Original capsule or incoming stock binding changed')
        if config!=simulation_project(plan):raise ValueError('Capsule simulation must retain original stock and complete tool configuration')
        if not control:check_nc(nc.read_text(),plan)
        observations=profile_observations(vertices,faces,ref,resolution);by_feature=observations['features']
    elif facing:
        from genlaw_pipeline.facing_geometry import FACING
        from genlaw_pipeline.facing_verification import observations as facing_observations,evaluate as evaluator
        if ref['reference_class']!=FACING or {f['id'] for f in ref['features']}!=set(planned) or ref['bounds']!=plan['target_bounds_mm'] or ref['incoming_stock_bounds']!=plan['stock']['bounds_mm']:
            raise ValueError('Original target or incoming stock binding changed')
        observations=facing_observations(vertices,faces,ref,resolution);by_feature=observations['features']
    elif region:
        from genlaw_pipeline.region_geometry import REGION
        from genlaw_pipeline.region_verification import observations as region_observations,evaluate as evaluator
        if ref['reference_class']!=REGION or {f['id'] for f in ref['features']}!=set(planned):raise ValueError('Native planar feature set changed')
        observations=region_observations(vertices,faces,ref,resolution);by_feature=observations['features']
    elif step:
        from genlaw_pipeline.step_geometry import STEP
        from genlaw_pipeline.step_verification import observations as step_observations,evaluate as evaluator
        if ref['reference_class']!=STEP or {f['id'] for f in ref['features']}!=set(planned):raise ValueError('Native step feature set changed')
        observations=step_observations(vertices,faces,ref,resolution);by_feature=observations['features']
    elif pocket:
        from genlaw_pipeline.pocket_geometry import POCKET
        from genlaw_pipeline.pocket_verification import observations as pocket_observations,evaluate as evaluator
        if ref['reference_class']!=POCKET or {f['id'] for f in ref['features']}!=set(planned):raise ValueError('Native pocket feature set changed')
        observations=pocket_observations(vertices,faces,ref,resolution);by_feature=observations['features']
    elif multiple:
        if ref['reference_class']!=(DRILLED if drilled else MULTI) or {f['id'] for f in ref['features']}!=set(planned):
            raise ValueError('Native feature set changed')
        if drilled:
            from genlaw_pipeline.blind_verification import drilled_observations
            observations=drilled_observations(vertices,faces,ref,resolution)
        else:observations=pattern_observations(vertices,faces,ref,resolution)
        by_feature=observations['features']
    else:
        observations=hole_observations(vertices,faces,ref,resolution)
        by_feature={plan['operation']['feature_id']:observations}
    rows=[]
    for req in requirements['items']:
        op=planned.get(req['feature_id'])
        if op is None:raise ValueError('Requirement/CAD feature mismatch')
        if pocket or step or region or facing or sequence or external:
            from genlaw_pipeline.milling_features import CONTRACTS,required_nominals
            if sequence:
                from genlaw_pipeline.sequence_geometry import KINDS
                expected=required_nominals(CONTRACTS[KINDS[op['geometry']['kind']]],op['geometry'])
                if profiled_sequence:
                    from genlaw_pipeline.profile_sequence_geometry import feature_nominals
                    expected=feature_nominals(op['geometry'])
            else:expected=required_nominals(CONTRACTS[plan['capability']],op['geometry'])
        else:expected={'diameter':op['diameter_mm'],'position_xy':op['hole_center_xy_mm'],'through':op['through']}
        if drilled and not op['through']:expected.update(cylindrical_depth=op['cylindrical_depth_mm'],conical_bottom=op['bottom_angle_degrees'])
        if req['kind'] in expected and req['nominal']!=expected[req['kind']]:raise ValueError('Requirement/CAD feature mismatch')
        rows.append({'id':req['id'],'feature_id':req['feature_id'],'kind':req['kind'],'nominal':req['nominal'],
                     **evaluator(req['kind'],by_feature[req['feature_id']],req['acceptance'])})
    software=[]
    if software_policy:
        policy=load_json(software_policy)
        version='v11' if profiled_sequence else 'v10' if external else 'v9' if sequence else 'v8' if plan['schema']=='genlaw.execution-plan.v8' else 'v7' if facing else 'v6' if region else 'v5' if step else 'v4' if pocket else 'v3' if drilled else 'v2' if multiple else 'v1'
        if policy['schema']!='genlaw.software-geometry-policy.'+version or policy['cad_sha256']!=ref['cad_sha256']:raise ValueError('Software policy CAD binding mismatch')
        policies=policy['features'] if multiple else {plan['operation']['feature_id']:policy['rules']}
        if set(policies)!=set(planned):raise ValueError('Software policy does not cover the exact feature set')
        for fid,rules in policies.items():
            if external:
                from genlaw_pipeline.profile_geometry import nominals
                if set(rules)!=set(nominals(planned[fid]['geometry'])):raise ValueError('Profile software policy must cover all declared geometric checks')
            if sequence:
                from genlaw_pipeline.milling_features import CONTRACTS,required_nominals
                from genlaw_pipeline.sequence_geometry import KINDS
                required=feature_nominals(planned[fid]['geometry']) if profiled_sequence else required_nominals(CONTRACTS[KINDS[planned[fid]['geometry']['kind']]],planned[fid]['geometry'])
                if set(rules)!=set(required):
                    raise ValueError('Sequence software policy must cover every declared geometric check')
            for kind,rule in rules.items():
                if rule.get('scope')!='software_regression':raise ValueError('Regression policy must not claim engineering approval')
                software.append({'feature_id':fid,'kind':kind,**evaluator(kind,by_feature[fid],rule)})
    result={'schema':'genlaw.feature-verification.v2' if multiple else 'genlaw.feature-verification.v1','status':'measured',
            'feature_ids':list(planned),
            'cad_sha256':ref['cad_sha256'],'native_geometry_sha256':digest(run/'native-geometry.json'),
            'nc_sha256':digest(nc),'mesh_sha256':digest(actual),'project_sha256':digest(project),
            'requirements_sha256':digest(requirements_path),'software_policy_sha256':digest(software_policy) if software_policy else None,
            'source_code_sha256':digest(Path(__file__)),'test_only_control':bool(control),
            'mesh':inspection,'observations':observations,'requirements':rows,'software_regression':software,
            'production_approved':False,'full_tolerance_acceptance':False,'whole_process_card_complete':False,
            'limitations':['Disjoint straight Z through-holes only; no datum-based GD&T.','Diameter/position use 11 interior sections, not every surface point.',
                           'Through observation tests 9 axial rays, not all possible paths or a full-diameter gauge.',
                           'Two grid cells are a numerical guard, not a certified uncertainty bound.','No roughness, fixtures, holder or material physics.']}
    if not multiple:result['feature_id']=plan['operation']['feature_id']
    if sequence:
        result['schema']='genlaw.feature-verification.v9';result['process_conformance']=process_conformance
        result['additional_source_code_sha256']={name:digest(ROOT/'genlaw_pipeline'/name) for name in
            ['sequence_verification.py','sequence_geometry.py','sequence_nc.py','sequence_checkpoints.py','facing_verification.py','milling_features.py']}
        result['limitations']=['One facing operation followed by disjoint Z through holes, same setup and explicit endmill tool numbers.',
            '25 original retained-face probes, 11 sections and 9 full incoming-height rays per hole are finite samples, not continuous GD&T.',
            'Good final geometry alone does not establish card order or correct tool use; these have separate NC and material-prefix checks.',
            'Two grid cells are a numerical guard, not certified uncertainty.','No nonzero WCS/TLO, roughness, fixtures, holder or material physics.']
    if external:
        result['schema']='genlaw.feature-verification.v10'
        result['additional_source_code_sha256']={name:digest(ROOT/'genlaw_pipeline'/name) for name in ['profile_verification.py','profile_geometry.py','profile_nc.py','milling_features.py']}
        result['limitations']=['One Z-extruded capsule with straight sides and two semicircular ends; same-bounds rectangular stock.',
            '11 complete sections, 25 retained-body probes and 20 exterior probes are finite observations, not continuous GD&T.',
            'No disconnected waste is discarded and no result registration is applied.',
            'Two grid cells are a numerical guard, not certified uncertainty.','No fixture retention, holder, roughness, physical cutting or production approval.']
    if profiled_sequence:
        result['schema']='genlaw.feature-verification.v11'
        result['additional_source_code_sha256'].update({name:digest(ROOT/'genlaw_pipeline'/name) for name in
            ['profile_sequence_geometry.py','profile_sequence_verification.py','profile_geometry.py','profile_verification.py','profile_nc.py']})
        result['limitations'][0]='One facing operation, one capsule outside profile and disjoint through holes in explicit card order, same coordinate system and stock.'
        result['limitations'].append('Facing checks the retained top/thickness; the separate profile checks the outside contour. No fixture retention or panel separation model.')
    if plan['schema']=='genlaw.execution-plan.v8':
        result['schema']='genlaw.feature-verification.v8'
        result['additional_source_code_sha256']={'drilling_geometry.py':digest(ROOT/'genlaw_pipeline/drilling_geometry.py')}
        result['limitations'].append('Milled-hole geometry is measured independently of the native helical NC; no physical cutting or fixture approval.')
    if drilled:
        result['schema']='genlaw.feature-verification.v3'
        result['additional_source_code_sha256']={name:digest(ROOT/'genlaw_pipeline'/name) for name in ['blind_verification.py','reference_sections.py','drilling_geometry.py']}
        result['limitations'][0]='Disjoint Z through-holes and explicitly dimensioned conical-bottom blind holes only.'
        result['limitations'].append('Blind depth is inferred from a fitted floor profile; bottom checks use 25 finite probes, not a continuous surface certification.')
    if pocket:
        result['schema']='genlaw.feature-verification.v4'
        result['additional_source_code_sha256']={name:digest(ROOT/'genlaw_pipeline'/name) for name in ['pocket_verification.py','pocket_geometry.py','milling_features.py']}
        result['limitations']=['One closed Z-aligned rounded rectangular pocket with flat floor.','11 contours and 25 floor probes are finite samples, not continuous GD&T.','Two grid cells are a numerical guard, not certified uncertainty.','No roughness, fixtures, holder or material physics.']
    if step:
        result['schema']='genlaw.feature-verification.v5'
        result['additional_source_code_sha256']={name:digest(ROOT/'genlaw_pipeline'/name) for name in ['step_verification.py','step_geometry.py','pocket_verification.py','milling_features.py']}
        result['limitations']=['One Z-aligned flat step open at three stock edges, with a single retained shoulder.',
          '11 sections, 25 interior probes and 21 near-edge probes are finite samples, not continuous GD&T.',
          'Near-edge probes are one declared grid cell inside the stock, not an all-surface edge-clearance proof.',
          'Two grid cells are a numerical guard, not certified uncertainty.','No roughness, fixtures, holder or material physics.']
    if region:
        result['schema']='genlaw.feature-verification.v6'
        result['additional_source_code_sha256']={name:digest(ROOT/'genlaw_pipeline'/name) for name in ['region_verification.py','region_geometry.py','step_verification.py','step_geometry.py','pocket_verification.py','milling_features.py']}
        result['limitations']=['One constant-depth flat floor with line/circular boundaries, vertical walls, optional islands and explicit stock openings.',
          '11 contours, 25 interior probes, 7 probes per open segment and 5 per island are finite samples, not continuous GD&T.',
          'Two grid cells are a numerical guard, not certified uncertainty.','No roughness, fixtures, holder, material physics, splines or arbitrary 3D surfaces.']
    if facing:
        result['schema']='genlaw.feature-verification.v7'
        result['additional_source_code_sha256']={name:digest(ROOT/'genlaw_pipeline'/name) for name in ['facing_verification.py','facing_geometry.py','region_verification.py','milling_features.py']}
        result['limitations']=['One axis-aligned box faced from explicitly thicker incoming stock; XY footprint and bottom remain unchanged.',
          '25 thickness/top probes, 11 body sections and global maximum Z do not certify continuous flatness or general GD&T.',
          'Two grid cells are a numerical guard, not certified uncertainty.','No roughness, fixtures, holder or material physics.']
    for path,key in [(actual,'mesh_sha256'),(project,'project_sha256'),(nc,'nc_sha256')]:verify_binding(path,result[key])
    checked_plan(input_path,run/'preparation-result.json')
    with output.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps({'status':'measured','requirements':[x['status'] for x in rows],
                      'software':[x['status'] for x in software],
                      **({'milled_features':list(by_feature)} if pocket or step or region or facing or sequence or external else {'through':{fid:obs['through']['observation'] for fid,obs in by_feature.items()}})}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--control',type=Path);parser.add_argument('--software-policy',type=Path)
    args=parser.parse_args();verify(args.run,args.output,args.control,args.software_policy)
