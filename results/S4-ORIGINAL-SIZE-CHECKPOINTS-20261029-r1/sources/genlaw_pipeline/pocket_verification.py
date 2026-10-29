"""Compare original pocket profile and sampled flat floor with the actual mesh."""
import numpy as np
from .pocket_geometry import profile
from .preparation import number


def floor_profile(hits,top,lo,resolution):
    guard=2*resolution;probes=[]
    for hit in hits:
        row=dict(hit);zs=row['surface_z_mm']
        if not zs:row['status']='open_through'
        elif len(zs)!=2 or abs(zs[0]-lo)>guard:row['status']='ambiguous_intersections'
        elif zs[1]-zs[0]<=guard:row['status']='thin_floor_below_grid_guard'
        else:row.update(status='floor_measured',floor_z_mm=zs[1],depth_mm=top-zs[1],remaining_floor_mm=zs[1]-zs[0])
        probes.append(row)
    complete=bool(probes) and all(p['status']=='floor_measured' for p in probes)
    result={'probes':probes,'probe_count':len(probes),'complete':complete,'open_through_count':sum(p['status']=='open_through' for p in probes),
            'depth_band_mm':None,'flatness_range_mm':None,'minimum_remaining_floor_mm':None}
    if complete:
        depths=[p['depth_mm'] for p in probes]
        result.update(depth_band_mm=[min(depths),max(depths)],flatness_range_mm=max(depths)-min(depths),minimum_remaining_floor_mm=min(p['remaining_floor_mm'] for p in probes))
    return result


def observations(vertices,faces,ref,resolution):
    from .verification import ray_hits
    from .measure_worker import section_height
    from mesh_metrics import Sections
    from shapely.geometry import Polygon,Point
    feature=ref['features'][0];lo,top=ref['bounds']['min'][2],ref['bounds']['max'][2];floor=top-feature['depth_mm']
    target=profile(feature,2048);levels=np.unique(np.concatenate((vertices[:,2].astype(float),[lo,floor,top])))
    if len(levels)>4096:raise ValueError('Section indexing budget exceeded')
    sections=Sections(vertices,faces);rows=[]
    for fraction in np.linspace(.05,.95,11):
        requested=floor+(top-floor)*float(fraction);z=section_height(levels,requested);material=sections.at(z)
        pieces=list(material.geoms) if material.geom_type=='MultiPolygon' else [material];rings=[r for p in pieces for r in p.interiors]
        row={'requested_z_mm':requested,'z_mm':z,'interior_loops':len(rings),'material_components':len(pieces),'matched':len(rings)==1 and len(pieces)==1}
        if row['matched']:
            contour=Polygon(rings[0]);row.update(boundary_distance_mm=contour.boundary.hausdorff_distance(target.boundary),
              observed_bounds_xy_mm=list(contour.bounds),area_mm2=contour.area,center_xy_mm=list(contour.centroid.coords[0]),
              excess_void_area_mm2=contour.difference(target).area,remaining_material_area_mm2=target.difference(contour).area)
        rows.append(row)
    x,y=feature['center_xy_mm'];w,h=feature['width_mm'],feature['height_mm'];guard=2*resolution;probes=[]
    for u in np.linspace(-.35,.35,5):
        for v in np.linspace(-.35,.35,5):
            xy=[x+float(u)*w,y+float(v)*h]
            if not target.contains(Point(xy)):continue
            probes.append({'xy_mm':xy,'surface_z_mm':ray_hits(sections.triangles,xy,(lo-guard,top+guard))})
    if len(probes)<9:raise ValueError('Insufficient original-profile floor probes')
    bottom=floor_profile(probes,top,lo,resolution)
    item={'sections':rows,'section_count':11,'profile_complete':all(r['matched'] for r in rows),
      'max_profile_deviation_mm':max(r['boundary_distance_mm'] for r in rows) if all(r['matched'] for r in rows) else None,
      'floor':bottom,'resolution_mm':resolution,'numerical_guard_mm':guard,'guard_is_certified_uncertainty':False,'registration_applied':False}
    return {'method':'11 original-coordinate planar contours and up to 25 axial probes inside the original floor profile','features':{feature['id']:item},
      'unassigned_contours_detected':any(r['interior_loops']>1 or r['material_components']>1 for r in rows)}


def evaluate(kind,obs,rule):
    if kind not in ('profile_xy','pocket_depth','floor_geometry'):return {'status':'unsupported_measurement','reason':'No roughness, material physics or general GD&T evaluator.'}
    if rule is None:return {'status':'pending_criteria','reason':'No approved numerical criterion supplied.'}
    try:
        if rule.get('schema')!='genlaw.numeric-acceptance.v1' or rule.get('units')!='mm' or rule.get('scope') not in ('software_regression','engineering_requirement') or not isinstance(rule.get('basis'),str) or not rule['basis'].strip():raise ValueError('Need explicit version, method, units, scope and basis')
        common={'schema','units','scope','basis','method'};guard=obs['numerical_guard_mm'];floor=obs['floor']
        if kind=='profile_xy':
            if rule.get('method')!='sampled_boundary_distance' or set(rule)-common-{'max_deviation_mm'}:raise ValueError('Unsupported profile criterion')
            limit=number(rule.get('max_deviation_mm'),'max_deviation_mm',True)
            if not obs['profile_complete']:return {'status':'not_measurable','reason':'Not one matched pocket contour at every sampled depth.'}
            delta=obs['max_profile_deviation_mm'];status='numeric_pass' if delta+guard<=limit else 'numeric_fail' if delta-guard>limit else 'indeterminate'
        elif kind=='pocket_depth':
            if rule.get('method')!='sampled_floor_depth' or set(rule)-common-{'min_mm','max_mm'}:raise ValueError('Unsupported depth criterion')
            low=number(rule.get('min_mm'),'min_mm',True);high=number(rule.get('max_mm'),'max_mm',True)
            if low>=high:raise ValueError('Reversed depth limits')
            if not floor['complete']:return {'status':'not_measurable','reason':'Not an unambiguous closed floor at all probes.'}
            a,b=floor['depth_band_mm'];status='numeric_pass' if a>=low+guard and b<=high-guard else 'numeric_fail' if a<low-guard or b>high+guard else 'indeterminate'
        else:
            if rule.get('method')!='sampled_flat_floor' or set(rule)-common-{'max_flatness_mm','min_remaining_floor_mm'}:raise ValueError('Unsupported flat-floor criterion')
            flat=number(rule.get('max_flatness_mm'),'max_flatness_mm',True);minimum=number(rule.get('min_remaining_floor_mm'),'min_remaining_floor_mm',True)
            if floor['open_through_count']:status='numeric_fail'
            elif not floor['complete']:status='indeterminate'
            else:
                delta=floor['flatness_range_mm'];remaining=floor['minimum_remaining_floor_mm']
                status='numeric_pass' if delta+guard<=flat and remaining-guard>=minimum else 'numeric_fail' if delta-guard>flat or remaining+guard<minimum else 'indeterminate'
        return {'status':status,'criterion_scope':rule['scope'],'criterion':rule,'production_approved':False,
          'reason':'Finite contour/floor observations with a numerical grid guard; not a continuous or certified manufacturing inspection.'}
    except (ValueError,TypeError,AttributeError) as error:return {'status':'unsupported_criterion','reason':str(error)}
