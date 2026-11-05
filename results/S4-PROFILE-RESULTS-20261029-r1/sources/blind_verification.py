"""Measure cylindrical hole sections and the actual blind-hole floor."""
import math
import numpy as np
from .drilling_geometry import blind_levels
from .preparation import number
from .reference_sections import DrilledReference


def floor_profile(hits,feature,top,bottom,resolution):
    """Retain missing floors and ambiguous ray crossings; fit only valid floors."""
    guard=2*resolution;radius=feature['diameter_mm']/2
    shoulder,tip=blind_levels(feature,top);rows=[]
    for hit in hits:
        row=dict(hit);z=row['surface_z_mm']
        row['expected_floor_z_mm']=tip+(shoulder-tip)*row['radius_mm']/radius
        if not z:row['status']='open_through'
        elif len(z)!=2 or abs(z[0]-bottom)>guard:row['status']='ambiguous_intersections'
        elif z[1]-z[0]<=guard:row['status']='thin_floor_below_grid_guard'
        else:
            row.update(status='floor_measured',floor_z_mm=z[1],remaining_floor_mm=z[1]-z[0],
                       deviation_mm=z[1]-row['expected_floor_z_mm'])
        rows.append(row)
    complete=all(r['status']=='floor_measured' for r in rows)
    result={'method':'25 axial floor probes at radius 0, 0.25R, 0.5R and 0.75R',
            'probes':rows,'probe_count':len(rows),'complete':complete,
            'open_through_count':sum(r['status']=='open_through' for r in rows),
            'ambiguous_count':sum(r['status'] in ('ambiguous_intersections','thin_floor_below_grid_guard') for r in rows),
            'max_deviation_mm':None,'minimum_remaining_floor_mm':None,'cylindrical_depth_mm':None}
    if complete:
        radii=np.array([r['radius_mm'] for r in rows]);z=np.array([r['floor_z_mm'] for r in rows])
        matrix=np.column_stack([np.ones(len(rows)),radii]);fit,_,rank,_=np.linalg.lstsq(matrix,z,rcond=None)
        if rank!=2:raise ValueError('Blind floor fit is underdetermined')
        intercept,slope=map(float,fit)
        result.update(max_deviation_mm=max(abs(r['deviation_mm']) for r in rows),
            minimum_remaining_floor_mm=min(r['remaining_floor_mm'] for r in rows),
            cylindrical_depth_mm=top-(intercept+slope*radius),tip_depth_mm=top-intercept,
            fitted_bottom_angle_degrees=math.degrees(2*math.atan(1/slope)) if slope>1e-10 else None,
            floor_fit_max_residual_mm=float(np.max(np.abs(z-matrix@fit))),
            floor_fit='z = intercept + slope * radial_distance; inferred without imposing the target cone angle')
    return result


def drilled_observations(vertices,faces,reference,resolution):
    from .verification import fit_ring,ray_hits,summarize_rays,match_rings
    from .measure_worker import section_height
    from mesh_metrics import Sections
    features=reference['features'];lo,hi=reference['bounds']['min'][2],reference['bounds']['max'][2]
    levels=np.unique(np.concatenate((vertices[:,2].astype(float),DrilledReference(reference,16).breaks)))
    if len(levels)>4096:raise ValueError('Section indexing budget exceeded')
    sections=Sections(vertices,faces);results={};counts=[]
    for feature in features:
        blind=feature['kind']=='blind_hole';radius=feature['diameter_mm']/2;center=feature['center_xy_mm']
        lower=hi-feature['cylindrical_depth_mm'] if blind else lo;rows=[]
        for fraction in np.linspace(.05,.95,11):
            requested=lower+(hi-lower)*float(fraction);z=section_height(levels,requested)
            material=sections.at(z);pieces=list(material.geoms) if material.geom_type=='MultiPolygon' else [material]
            rings=[r for p in pieces for r in p.interiors];matched=match_rings(rings,features,resolution)
            counts.append({'feature_id':feature['id'],'z_mm':z,'observed_holes':len(rings),
                           'matched_features':list(matched),'unassigned_contours':len(rings)-len(matched)})
            row={'requested_z_mm':requested,'z_mm':z,'matched':feature['id'] in matched,'interior_loops':len(rings)}
            if row['matched']:
                row.update(fit_ring(rings[matched[feature['id']]]));row['center_deviation_mm']=math.dist(row['fit_center_xy_mm'],center)
            rows.append(row)
        complete=all(s['matched'] for s in rows)
        obs={'hole_kind':feature['kind'],'sections':rows,'section_count':len(rows),'all_sections_have_matched_hole':complete,
             'diameter':{'radial_band_mm':[min(s['radial_diameter_band_mm'][0] for s in rows),max(s['radial_diameter_band_mm'][1] for s in rows)]} if complete else None,
             'position':{'max_center_deviation_mm':max(s['center_deviation_mm'] for s in rows)} if complete else None,
             'resolution_mm':resolution,'numerical_guard_mm':2*resolution,'guard_is_certified_uncertainty':False,'registration_applied':False}
        through_probes=[(0,0)]+[(radius*.25*math.cos(a),radius*.25*math.sin(a)) for a in np.arange(8)*math.pi/4]
        through_hits=[{'xy_mm':[center[0]+x,center[1]+y],
                       'surface_z_mm':ray_hits(sections.triangles,[center[0]+x,center[1]+y],(lo-2*resolution,hi+2*resolution))} for x,y in through_probes]
        obs['through']=summarize_rays(through_hits,2*resolution)
        if blind:
            measured_center=np.mean([s['fit_center_xy_mm'] for s in rows],axis=0).tolist() if complete else center
            offsets=[(0.,0.,0.)]+[(radius*f*math.cos(a),radius*f*math.sin(a),radius*f) for f in [.25,.5,.75] for a in np.arange(8)*math.pi/4]
            hits=[{'xy_mm':[measured_center[0]+x,measured_center[1]+y],'radius_mm':r,
                   'surface_z_mm':ray_hits(sections.triangles,[measured_center[0]+x,measured_center[1]+y],(lo-2*resolution,hi+2*resolution))} for x,y,r in offsets]
            obs['floor']=floor_profile(hits,feature,hi,lo,resolution)
            obs['floor']['probe_center_xy_mm']=measured_center
            obs['floor']['probe_center_source']='observed cylindrical sections' if complete else 'nominal CAD center; cylindrical sections incomplete'
        results[feature['id']]=obs
    return {'method':'11 sections per cylindrical portion; through: 9 axial rays; blind: 25 actual floor probes',
            'features':results,'section_feature_counts':counts,'expected_feature_count':len(features),
            'unassigned_contours_detected':any(c['unassigned_contours'] for c in counts)}


def evaluate_blind(kind,observation,rule):
    if rule is None:return {'status':'pending_criteria','reason':'No approved numerical criterion supplied.'}
    if observation.get('hole_kind')!='blind_hole':return {'status':'unsupported_measurement','reason':'Blind depth/bottom rule requires a blind-hole feature.'}
    try:
        if (rule.get('schema')!='genlaw.numeric-acceptance.v1' or rule.get('units')!='mm'
            or rule.get('scope') not in ('software_regression','engineering_requirement')
            or not isinstance(rule.get('basis'),str) or not rule['basis'].strip()):
            raise ValueError('Need version, units, scope and explicit criterion basis')
        floor=observation['floor'];guard=observation['numerical_guard_mm']
        common={'schema','units','scope','basis','method'}
        if kind=='cylindrical_depth':
            if rule.get('method')!='sampled_floor_fit_depth' or set(rule)-common-{'min_mm','max_mm'}:raise ValueError('Unsupported depth criterion')
            lower=number(rule.get('min_mm'),'min_mm',True);upper=number(rule.get('max_mm'),'max_mm',True)
            if lower>=upper:raise ValueError('Reversed depth limits')
            if not floor['complete']:return {'status':'not_measurable','reason':'A closed floor with unambiguous crossings was not observed at all probes.'}
            depth=floor['cylindrical_depth_mm'];guard+=floor['floor_fit_max_residual_mm']
            status='numeric_pass' if lower+guard<=depth<=upper-guard else 'numeric_fail' if depth<lower-guard or depth>upper+guard else 'indeterminate'
        else:
            if rule.get('method')!='sampled_conical_floor' or set(rule)-common-{'max_deviation_mm','min_remaining_floor_mm'}:raise ValueError('Unsupported floor criterion')
            limit=number(rule.get('max_deviation_mm'),'max_deviation_mm',True)
            minimum=number(rule.get('min_remaining_floor_mm'),'min_remaining_floor_mm',True)
            if floor['open_through_count']:status='numeric_fail'
            elif not floor['complete']:status='indeterminate'
            else:
                deviation=floor['max_deviation_mm'];remaining=floor['minimum_remaining_floor_mm']
                status=('numeric_fail' if deviation-guard>limit or remaining+guard<minimum else
                        'numeric_pass' if deviation+guard<=limit and remaining-guard>=minimum else 'indeterminate')
        return {'status':status,'criterion_scope':rule['scope'],'criterion':rule,'production_approved':False,
                'reason':'Sampled actual floor geometry, with a numerical grid guard; not a production certificate.'}
    except (ValueError,TypeError,AttributeError) as error:return {'status':'unsupported_criterion','reason':str(error)}
