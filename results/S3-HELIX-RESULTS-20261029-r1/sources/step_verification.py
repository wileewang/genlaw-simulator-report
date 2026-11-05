"""Finite shoulder sections, floor probes and near-edge probes in original CAD coordinates."""
import numpy as np
from .preparation import number
from .step_geometry import floor_bounds,retained_profile
from .pocket_verification import floor_profile,evaluate as evaluate_floor


def shoulder_section(material,feature,bounds):
    from shapely.geometry import LineString
    axis='XY'.index(feature['side'][0]);other=1-axis;low=bounds['min'];high=bounds['max']
    components=list(material.geoms) if material.geom_type=='MultiPolygon' else [material]
    rows=[];valid=material.geom_type=='Polygon' and not material.is_empty and not material.interiors
    for fraction in np.linspace(.05,.95,9):
        fixed=low[other]+float(fraction)*(high[other]-low[other]);a=[0.,0.];b=[0.,0.]
        a[other]=b[other]=fixed;a[axis]=low[axis]-1;b[axis]=high[axis]+1
        section=material.intersection(LineString([a,b]));row={'transverse_coordinate_mm':fixed,'status':'ambiguous_section'}
        if valid and section.geom_type=='LineString' and not section.is_empty:
            positions=[p[axis] for p in section.coords]
            shoulder=max(positions) if feature['side'][1]=='+' else min(positions)
            row.update(status='shoulder_measured',coordinate_mm=shoulder,deviation_mm=abs(shoulder-feature['shoulder_coordinate_mm']))
        rows.append(row)
    complete=all(r['status']=='shoulder_measured' for r in rows)
    return {'material_components':len(components),'interior_loops':sum(len(p.interiors) for p in components if p.geom_type=='Polygon'),
      'complete':complete,'samples':rows,'max_deviation_mm':max(r['deviation_mm'] for r in rows) if complete else None}


def observations(vertices,faces,ref,resolution):
    from .verification import ray_hits
    from .measure_worker import section_height
    from mesh_metrics import Sections
    feature=ref['features'][0];bounds=ref['bounds'];low,high=bounds['min'],bounds['max'];top=high[2];floor=top-feature['depth_mm']
    levels=np.unique(np.concatenate((vertices[:,2].astype(float),[low[2],floor,top])))
    if len(levels)>4096:raise ValueError('Section indexing budget exceeded')
    sections=Sections(vertices,faces);contours=[];expected=retained_profile(feature,bounds)
    for fraction in np.linspace(.05,.95,11):
        requested=floor+float(fraction)*(top-floor);z=section_height(levels,requested);material=sections.at(z)
        row=shoulder_section(material,feature,bounds);row.update(requested_z_mm=requested,z_mm=z)
        row['retained_boundary_deviation_mm']=material.boundary.hausdorff_distance(expected.boundary) if material.geom_type in ('Polygon','MultiPolygon') and not material.is_empty else None
        contours.append(row)
    a,b=floor_bounds(feature,bounds);guard=2*resolution
    def probe(xy):return {'xy_mm':xy,'surface_z_mm':ray_hits(sections.triangles,xy,(low[2]-guard,top+guard))}
    center_probes=[probe([a[0]+float(u)*(b[0]-a[0]),a[1]+float(v)*(b[1]-a[1])]) for u in np.linspace(.15,.85,5) for v in np.linspace(.15,.85,5)]
    if min(b[i]-a[i] for i in range(2))<=4*resolution:raise ValueError('Floor too small for declared guarded probe pattern')
    edge_probes=[];axis='XY'.index(feature['side'][0]);coordinate=feature['shoulder_coordinate_mm']
    for direction in range(2):
        for end in [0,1]:
            value=(a if end==0 else b)[direction]
            if direction==axis and abs(value-coordinate)<1e-8:continue
            for fraction in np.linspace(.05,.95,7):
                xy=[0.,0.];xy[direction]=value+(resolution if end==0 else -resolution)
                other=1-direction;xy[other]=a[other]+float(fraction)*(b[other]-a[other])
                row=probe(xy);row['stock_edge']='XY'[direction]+('-' if end==0 else '+');edge_probes.append(row)
    if len(edge_probes)!=21:raise ValueError('Exactly three open stock edges must be inspected')
    measured=all(r['complete'] for r in contours)
    item={'sections':contours,'section_count':11,'shoulder_complete':measured,
      'max_shoulder_deviation_mm':max(r['max_deviation_mm'] for r in contours) if measured else None,
      'max_retained_boundary_deviation_mm':max(r['retained_boundary_deviation_mm'] for r in contours) if all(r['retained_boundary_deviation_mm'] is not None for r in contours) else None,
      'floor':floor_profile(center_probes,top,low[2],resolution),'open_edges':floor_profile(edge_probes,top,low[2],resolution),
      'resolution_mm':resolution,'numerical_guard_mm':guard,'guard_is_certified_uncertainty':False,'registration_applied':False}
    return {'method':'11 shoulder sections with 9 transverse lines each, 25 floor probes and 21 probes one grid cell inside the three open stock edges.',
      'features':{feature['id']:item},'unassigned_contours_detected':any(r['interior_loops'] or r['material_components']!=1 for r in contours)}


def evaluate(kind,obs,rule):
    if kind in ('step_depth','floor_geometry'):return evaluate_floor('pocket_depth' if kind=='step_depth' else kind,obs,rule)
    if kind not in ('shoulder_position','edge_clearance'):return {'status':'unsupported_measurement','reason':'No roughness, material physics or general GD&T evaluator.'}
    if rule is None:return {'status':'pending_criteria','reason':'No approved numerical criterion supplied.'}
    try:
        if rule.get('schema')!='genlaw.numeric-acceptance.v1' or rule.get('units')!='mm' or rule.get('scope') not in ('software_regression','engineering_requirement') or not isinstance(rule.get('basis'),str) or not rule['basis'].strip():raise ValueError('Need explicit version, method, units, scope and basis')
        common={'schema','units','scope','basis','method'};guard=obs['numerical_guard_mm']
        if kind=='shoulder_position':
            if rule.get('method')!='sampled_shoulder_position' or set(rule)-common-{'max_deviation_mm'}:raise ValueError('Unsupported shoulder criterion')
            limit=number(rule.get('max_deviation_mm'),'max_deviation_mm',True)
            if not obs['shoulder_complete']:return {'status':'not_measurable','reason':'No unambiguous open-step shoulder at every sampled section.'}
            delta=obs['max_shoulder_deviation_mm'];status='numeric_pass' if delta+guard<=limit else 'numeric_fail' if delta-guard>limit else 'indeterminate'
        else:
            if rule.get('method')!='sampled_open_edge_depth' or set(rule)-common-{'min_mm','max_mm'}:raise ValueError('Unsupported open-edge criterion')
            low=number(rule.get('min_mm'),'min_mm',True);high=number(rule.get('max_mm'),'max_mm',True)
            if low>=high:raise ValueError('Reversed depth limits')
            edge=obs['open_edges']
            if edge['open_through_count']:status='numeric_fail'
            elif not edge['complete']:return {'status':'not_measurable','reason':'Ambiguous remaining material near an open stock edge.'}
            else:
                a,b=edge['depth_band_mm'];status='numeric_pass' if a>=low+guard and b<=high-guard else 'numeric_fail' if a<low-guard or b>high+guard else 'indeterminate'
        return {'status':status,'criterion_scope':rule['scope'],'criterion':rule,'production_approved':False,
          'reason':'Finite original-coordinate samples with a two-grid-cell guard; not continuous GD&T or manufacturing approval.'}
    except (ValueError,TypeError,AttributeError) as error:return {'status':'unsupported_criterion','reason':str(error)}
