"""Inspect faced thickness and top location in the unchanged original CAD frame."""
import numpy as np
from .preparation import number
from .region_verification import topology


def observations(vertices,faces,reference,resolution):
    from .verification import ray_hits
    from .measure_worker import section_height
    from mesh_metrics import Sections
    from shapely.geometry import box
    low,high=[reference['bounds'][k] for k in ['min','max']]
    stock_top=reference['incoming_stock_bounds']['max'][2];guard=2*resolution
    levels=np.unique(np.concatenate((vertices[:,2].astype(float),[low[2],high[2],stock_top])))
    if len(levels)>4096:raise ValueError('Section indexing budget exceeded')
    sections=Sections(vertices,faces);expected=box(*low[:2],*high[:2]);signature=topology(expected);contours=[]
    for fraction in np.linspace(.05,.95,11):
        requested=low[2]+float(fraction)*(high[2]-low[2]);z=section_height(levels,requested);actual=sections.at(z)
        observed=topology(actual)
        contours.append({'requested_z_mm':requested,'z_mm':z,'expected_topology':signature,'observed_topology':observed,
            'topology_matches':signature==observed,'boundary_deviation_mm':actual.boundary.hausdorff_distance(expected.boundary) if observed and not actual.is_empty else None})
    probes=[]
    for u in np.linspace(.05,.95,5):
        for v in np.linspace(.05,.95,5):
            xy=[low[0]+float(u)*(high[0]-low[0]),low[1]+float(v)*(high[1]-low[1])]
            zs=ray_hits(sections.triangles,xy,(low[2]-guard,stock_top+guard));valid=len(zs)==2
            probes.append({'xy_mm':xy,'surface_z_mm':zs,'status':'measured' if valid else 'ambiguous_intersections',
                           'top_z_mm':zs[-1] if valid else None,'thickness_mm':zs[-1]-zs[0] if valid else None,
                           'bottom_deviation_mm':abs(zs[0]-low[2]) if valid else None})
    complete=all(p['status']=='measured' for p in probes);contours_complete=all(c['boundary_deviation_mm'] is not None for c in contours)
    tops=[p['top_z_mm'] for p in probes] if complete else []
    thicknesses=[p['thickness_mm'] for p in probes] if complete else []
    item={'probes':probes,'probe_count':25,'complete':complete,'sections':contours,'section_count':11,
          'contours_complete':contours_complete,'topology_matches':all(c['topology_matches'] for c in contours),
          'max_boundary_deviation_mm':max(c['boundary_deviation_mm'] for c in contours) if contours_complete else None,
          'top_z_band_mm':[min(tops),max(tops)] if complete else None,
          'thickness_band_mm':[min(thicknesses),max(thicknesses)] if complete else None,
          'top_flatness_range_mm':max(tops)-min(tops) if complete else None,
          'max_bottom_deviation_mm':max([p['bottom_deviation_mm'] for p in probes]+[abs(float(vertices[:,2].min())-low[2])]) if complete else None,
          'mesh_max_z_mm':float(vertices[:,2].max()),'resolution_mm':resolution,'numerical_guard_mm':guard,
          'guard_is_certified_uncertainty':False,'registration_applied':False}
    return {'method':'25 original-footprint axial probes, 11 retained-body sections and global mesh maximum Z; incoming stock and target CAD are separate.',
            'features':{reference['features'][0]['id']:item},'unassigned_contours_detected':not item['topology_matches']}


def evaluate(kind,obs,rule):
    methods={'top_height':'sampled_top_height_and_mesh_max','target_thickness':'sampled_remaining_thickness',
             'top_geometry':'sampled_top_plane','outline_xy':'sampled_original_box_boundary'}
    if kind not in methods:return {'status':'unsupported_measurement','reason':'No roughness, material physics or general GD&T evaluator.'}
    if rule is None:return {'status':'pending_criteria','reason':'No approved numerical criterion supplied.'}
    try:
        if (rule.get('schema')!='genlaw.numeric-acceptance.v1' or rule.get('units')!='mm'
            or rule.get('scope') not in ('software_regression','engineering_requirement')
            or not isinstance(rule.get('basis'),str) or not rule['basis'].strip() or rule.get('method')!=methods[kind]):
            raise ValueError('Need an explicit supported method, units, scope and basis')
        common={'schema','units','scope','basis','method'};guard=obs['numerical_guard_mm']
        if kind in ('top_height','target_thickness'):
            if set(rule)-common-{'min_mm','max_mm'}:raise ValueError('Unsupported interval criterion')
            low=number(rule.get('min_mm'),'min_mm',kind=='target_thickness');high=number(rule.get('max_mm'),'max_mm',kind=='target_thickness')
            if low>=high:raise ValueError('Reversed limits')
            if not obs['complete']:return {'status':'not_measurable','reason':'Not two closed-body intersections at every original probe.'}
            a,b=obs['top_z_band_mm'] if kind=='top_height' else obs['thickness_band_mm']
            if kind=='top_height':b=max(b,obs['mesh_max_z_mm'])
            status='numeric_pass' if a>=low+guard and b<=high-guard else 'numeric_fail' if a<low-guard or b>high+guard else 'indeterminate'
        else:
            field='max_flatness_mm' if kind=='top_geometry' else 'max_deviation_mm'
            if set(rule)-common-{field}:raise ValueError('Unsupported geometric criterion')
            limit=number(rule.get(field),field,True)
            if not obs['complete']:return {'status':'not_measurable','reason':'Top or bottom probe intersections are ambiguous.'}
            if kind=='outline_xy' and not obs['topology_matches']:status='numeric_fail'
            elif kind=='outline_xy' and not obs['contours_complete']:return {'status':'not_measurable','reason':'A retained-body contour could not be measured.'}
            else:
                delta=obs['top_flatness_range_mm'] if kind=='top_geometry' else max(obs['max_boundary_deviation_mm'],obs['max_bottom_deviation_mm'])
                status='numeric_pass' if delta+guard<=limit else 'numeric_fail' if delta-guard>limit else 'indeterminate'
        return {'status':status,'criterion_scope':rule['scope'],'criterion':rule,'production_approved':False,
                'reason':'Original-coordinate finite geometric observations with a two-cell guard; not continuous GD&T or production acceptance.'}
    except (ValueError,TypeError,AttributeError) as error:return {'status':'unsupported_criterion','reason':str(error)}
