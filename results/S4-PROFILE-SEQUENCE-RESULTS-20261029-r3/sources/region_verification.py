"""Original-coordinate contour topology, floor depth, stock openings and islands."""
import math
import numpy as np
from .preparation import number
from .region_geometry import profile,sample_loop
from .pocket_verification import floor_profile,evaluate as evaluate_floor
from .step_verification import evaluate as evaluate_edge


def topology(geometry):
    if geometry.geom_type=='Polygon':pieces=[] if geometry.is_empty else [geometry]
    elif geometry.geom_type=='MultiPolygon':pieces=list(geometry.geoms)
    else:return None
    return {'material_components':len(pieces),'interior_loops_per_component':sorted(len(p.interiors) for p in pieces)}


def sample_inside(region,count,margin):
    """Deterministic, spatially distributed interior samples, never shifted to fit output."""
    from shapely.geometry import Point
    inset=region.buffer(-margin);x0,y0,x1,y1=region.bounds
    if inset.is_empty:raise ValueError('No interior for declared probe clearance')
    candidates=[[x0+(x1-x0)*i/18,y0+(y1-y0)*j/18] for i in range(1,18) for j in range(1,18)]
    candidates=[p for p in candidates if inset.contains(Point(*p))]
    if len(candidates)<count:raise ValueError('Profile too small or narrow for the declared sample pattern')
    chosen=[candidates.pop(0)]
    while len(chosen)<count:
        index=max(range(len(candidates)),key=lambda i:min(math.dist(candidates[i],p) for p in chosen));chosen.append(candidates.pop(index))
    return chosen


def open_edge_points(feature,bounds,resolution):
    from shapely.geometry import Point
    region=profile(feature,256);rows=[]
    for s in feature['profile']['outer']:
        if s['kind']!='line':continue
        a,b=s['start'],s['end']
        if not any(all(abs(p[k]-value)<1e-8 for p in [a,b]) for k in range(2) for value in (bounds['min'][k],bounds['max'][k])):continue
        length=math.dist(a,b);normal=[-(b[1]-a[1])/length,(b[0]-a[0])/length]
        for fraction in np.linspace(.05,.95,7):
            center=[a[k]+float(fraction)*(b[k]-a[k]) for k in range(2)]
            choices=[[center[k]+direction*resolution*normal[k] for k in range(2)] for direction in [-1,1]]
            inside=[p for p in choices if region.contains(Point(*p))]
            if len(inside)!=1:raise ValueError('Cannot uniquely place an original-CAD open-edge sample')
            rows.append(inside[0])
    if bool(rows)!=bool(feature['open_edge_groups']):raise ValueError('Declared and geometric stock openings disagree')
    return rows


def observations(vertices,faces,ref,resolution):
    from .verification import ray_hits
    from .measure_worker import section_height
    from mesh_metrics import Sections
    from shapely.geometry import Polygon,box
    f=ref['features'][0];bounds=ref['bounds'];low,high=bounds['min'],bounds['max'];top=high[2];bottom=top-f['depth_mm'];guard=2*resolution
    levels=np.unique(np.concatenate((vertices[:,2].astype(float),[low[2],bottom,top])))
    if len(levels)>4096:raise ValueError('Section indexing budget exceeded')
    sections=Sections(vertices,faces);clearing=profile(f);expected=box(*low[:2],*high[:2]).difference(clearing);signature=topology(expected);rows=[]
    for fraction in np.linspace(.05,.95,11):
        requested=bottom+float(fraction)*(top-bottom);z=section_height(levels,requested);material=sections.at(z);actual=topology(material)
        rows.append({'requested_z_mm':requested,'z_mm':z,'observed_topology':actual,'expected_topology':signature,
                     'topology_matches':actual==signature,'boundary_deviation_mm':material.boundary.hausdorff_distance(expected.boundary) if actual and not material.is_empty else None})
    def probes(points):return [{'xy_mm':p,'surface_z_mm':ray_hits(sections.triangles,p,(low[2]-guard,top+guard))} for p in points]
    floor=floor_profile(probes(sample_inside(clearing,25,guard)),top,low[2],resolution)
    opening_points=open_edge_points(f,bounds,resolution)
    edges=floor_profile(probes(opening_points),top,low[2],resolution) if opening_points else None
    islands=[]
    for i,loop in enumerate(f['profile']['islands']):
        hits=probes(sample_inside(Polygon(sample_loop(loop)),5,guard));complete=True
        for hit in hits:
            values=hit['surface_z_mm'];valid=len(values)==2
            hit.update(status='top_measured' if valid else 'ambiguous_intersections',top_deviation_mm=abs(values[-1]-top) if valid else None)
            complete=complete and valid
        islands.append({'index':i,'probes':hits,'complete':complete,'max_top_deviation_mm':max(h['top_deviation_mm'] for h in hits) if complete else None})
    item={'sections':rows,'section_count':11,'contours_measurable':all(r['boundary_deviation_mm'] is not None for r in rows),
          'topology_matches':all(r['topology_matches'] for r in rows),'max_boundary_deviation_mm':max(r['boundary_deviation_mm'] for r in rows) if all(r['boundary_deviation_mm'] is not None for r in rows) else None,
          'floor':floor,'open_edges':edges,'islands':islands,'island_probe_count':sum(len(i['probes']) for i in islands),
          'resolution_mm':resolution,'numerical_guard_mm':guard,'guard_is_certified_uncertainty':False,'registration_applied':False}
    return {'method':'11 complete retained-material contour sections, 25 distributed original-CAD floor probes, 7 points per open boundary segment and 5 points per protected island.',
            'features':{f['id']:item},'unassigned_contours_detected':not item['topology_matches']}


def evaluate(kind,obs,rule):
    if kind in ('cut_depth','floor_geometry'):return evaluate_floor('pocket_depth' if kind=='cut_depth' else kind,obs,rule)
    if kind=='open_edge_clearance':
        if obs['open_edges'] is None:return {'status':'unsupported_measurement','reason':'Original CAD has no open stock edge.'}
        return evaluate_edge('edge_clearance',obs,rule)
    if kind not in ('contour_xy','island_retention'):return {'status':'unsupported_measurement','reason':'No roughness, material physics or general GD&T evaluator.'}
    if rule is None:return {'status':'pending_criteria','reason':'No approved numerical criterion supplied.'}
    try:
        if rule.get('schema')!='genlaw.numeric-acceptance.v1' or rule.get('units')!='mm' or rule.get('scope') not in ('software_regression','engineering_requirement') or not isinstance(rule.get('basis'),str) or not rule['basis'].strip():raise ValueError('Need explicit version, method, units, scope and basis')
        common={'schema','units','scope','basis','method'};guard=obs['numerical_guard_mm']
        if kind=='contour_xy':
            if rule.get('method')!='sampled_retained_contours' or set(rule)-common-{'max_deviation_mm'}:raise ValueError('Unsupported contour criterion')
            limit=number(rule.get('max_deviation_mm'),'max_deviation_mm',True)
            if not obs['contours_measurable']:return {'status':'not_measurable','reason':'Retained contour could not be measured at every selected section.'}
            delta=obs['max_boundary_deviation_mm']
            status='numeric_fail' if not obs['topology_matches'] else 'numeric_pass' if delta+guard<=limit else 'numeric_fail' if delta-guard>limit else 'indeterminate'
        else:
            if rule.get('method')!='sampled_preserved_island_top' or set(rule)-common-{'max_top_deviation_mm'}:raise ValueError('Unsupported island criterion')
            limit=number(rule.get('max_top_deviation_mm'),'max_top_deviation_mm',True)
            if not obs['islands']:return {'status':'unsupported_measurement','reason':'Original CAD has no protected island.'}
            if not all(i['complete'] for i in obs['islands']):return {'status':'not_measurable','reason':'Island top intersections are ambiguous.'}
            delta=max(i['max_top_deviation_mm'] for i in obs['islands']);status='numeric_pass' if delta+guard<=limit else 'numeric_fail' if delta-guard>limit else 'indeterminate'
        return {'status':status,'criterion_scope':rule['scope'],'criterion':rule,'production_approved':False,
                'reason':'Original-coordinate finite sections/probes with a two-cell guard; not continuous GD&T or manufacturing approval.'}
    except (ValueError,TypeError,AttributeError) as error:return {'status':'unsupported_criterion','reason':str(error)}
