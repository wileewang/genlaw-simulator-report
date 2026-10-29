"""Layer-aware contour, retained-plane and full-stock through-hole observations."""
import math
from pathlib import Path
import sys
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'web/tools'))
from .pocket_sequence_geometry import REFERENCE, hole_wall_top
from .pocket_geometry import profile


def spread_probes(region, bounds, count=25):
    """Select from original expected material only, before looking at any mesh."""
    from shapely.geometry import Point
    lo,hi=bounds
    candidates=[[lo[0]+float(u)*(hi[0]-lo[0]),lo[1]+float(v)*(hi[1]-lo[1])]
                for u in np.linspace(.05,.95,13) for v in np.linspace(.05,.95,13)]
    candidates=[xy for xy in candidates if region.contains(Point(xy))]
    if len(candidates)<count:raise ValueError('Too few independent retained-plane probe positions')
    selected=[candidates.pop(0)]
    while len(selected)<count:
        i=max(range(len(candidates)),key=lambda n:min(math.dist(candidates[n],xy) for xy in selected))
        selected.append(candidates.pop(i))
    return selected


def observations(vertices,faces,reference,resolution):
    from mesh_metrics import Sections
    from shapely.geometry import box,Point,Polygon
    from .verification import ray_hits,summarize_rays,fit_ring,match_rings
    from .measure_worker import section_height
    from .pocket_verification import floor_profile
    if reference['reference_class']!=REFERENCE:raise ValueError('Need original layered reference')
    low,high=[reference['bounds'][k] for k in ('min','max')]
    holes=reference['features'];pocket=reference.get('pocket_feature')
    guard=2*resolution;stock_top=reference['incoming_stock_bounds']['max'][2]
    floor=high[2]-pocket['depth_mm'] if pocket else high[2]
    rectangle=box(*low[:2],*high[:2]);void=profile(pocket,2048) if pocket else Polygon()
    levels=np.unique(np.concatenate((vertices[:,2].astype(float),[low[2],floor,high[2],stock_top])))
    if len(levels)>4096:raise ValueError('Section indexing budget exceeded')
    sections=Sections(vertices,faces);cache={}
    def sample(requested):
        z=section_height(levels,requested)
        if z not in cache:
            material=sections.at(z);pieces=list(material.geoms) if material.geom_type=='MultiPolygon' else [material]
            valid=not material.is_empty and all(p.geom_type=='Polygon' for p in pieces)
            rings=[r for p in pieces for r in p.interiors] if valid else []
            active=[h for h in holes if z<hole_wall_top(reference,h)]
            if pocket and z>floor:
                active=active+[{'id':pocket['id'],'center_xy_mm':pocket['center_xy_mm'],
                               'diameter_mm':min(pocket['width_mm'],pocket['height_mm'])}]
            matched=match_rings(rings,active,resolution)
            cache[z]={'material':material,'pieces':pieces,'rings':rings,'matched':matched,'valid':valid,
                'counts':{'z_mm':z,'expected_voids':len(active),'observed_voids':len(rings),
                          'matched_features':list(matched),'unassigned_contours':len(rings)-len(matched),
                          'missing_features':[f['id'] for f in active if f['id'] not in matched],
                          'material_components':len(pieces) if valid else 0}}
        return z,cache[z]
    def heights(bottom,top):
        return [bottom+float(f)*(top-bottom) for f in np.linspace(.05,.95,11)]
    def rays(points):
        return [{'xy_mm':xy,'surface_z_mm':ray_hits(sections.triangles,xy,(low[2]-guard,stock_top+guard))} for xy in points]
    results={}
    for hole in holes:
        wall_top=hole_wall_top(reference,hole);rows=[];center=hole['center_xy_mm'];radius=hole['diameter_mm']/2
        for requested in heights(low[2],wall_top):
            z,s=sample(requested);matched=hole['id'] in s['matched']
            row={'requested_z_mm':requested,'z_mm':z,'matched':matched,'interior_loops':len(s['rings'])}
            if matched:
                row.update(fit_ring(s['rings'][s['matched'][hole['id']]]))
                row['center_deviation_mm']=math.dist(row['fit_center_xy_mm'],center)
            rows.append(row)
        complete=all(r['matched'] for r in rows)
        points=[list(center)]+[[center[0]+radius*.25*math.cos(a),center[1]+radius*.25*math.sin(a)] for a in np.arange(8)*math.pi/4]
        results[hole['id']]={'sections':rows,'section_count':len(rows),'all_sections_have_matched_hole':complete,
            'original_wall_z_bounds_mm':[low[2],wall_top],
            'diameter':{'radial_band_mm':[min(r['radial_diameter_band_mm'][0] for r in rows),max(r['radial_diameter_band_mm'][1] for r in rows)],
                        'fit_range_mm':[min(r['fit_diameter_mm'] for r in rows),max(r['fit_diameter_mm'] for r in rows)]} if complete else None,
            'position':{'max_center_deviation_mm':max(r['center_deviation_mm'] for r in rows),'centers_xy_mm':[r['fit_center_xy_mm'] for r in rows]} if complete else None,
            'through':summarize_rays(rays(points),guard),'resolution_mm':resolution,'numerical_guard_mm':guard,
            'guard_is_certified_uncertainty':False,'registration_applied':False}
    excluded=void.buffer(guard) if pocket else Polygon()
    for h in holes:excluded=excluded.union(Point(h['center_xy_mm']).buffer(h['diameter_mm']/2+guard,quad_segs=256))
    land=rectangle.buffer(-guard).difference(excluded)
    probes=[]
    for row in rays(spread_probes(land,(low[:2],high[:2]))):
        zs=row['surface_z_mm'];valid=len(zs)==2
        probes.append({**row,'status':'measured' if valid else 'ambiguous_intersections',
            'top_z_mm':zs[-1] if valid else None,'thickness_mm':zs[-1]-zs[0] if valid else None,
            'bottom_deviation_mm':abs(zs[0]-low[2]) if valid else None})
    contours=[]
    for requested in heights(low[2],high[2]):
        z,s=sample(requested);valid=s['valid'] and len(s['pieces'])==1
        outer=Polygon(s['pieces'][0].exterior) if valid else None
        contours.append({'requested_z_mm':requested,'z_mm':z,'retained_body_count':len(s['pieces']),
            'topology_matches':valid,'holes_checked_separately':True,
            'boundary_deviation_mm':outer.boundary.hausdorff_distance(rectangle.boundary) if valid else None})
    complete=all(p['status']=='measured' for p in probes);contours_complete=all(r['boundary_deviation_mm'] is not None for r in contours)
    tops=[p['top_z_mm'] for p in probes] if complete else [];thicknesses=[p['thickness_mm'] for p in probes] if complete else []
    results[reference['facing_feature']['id']]={'probes':probes,'probe_count':len(probes),'complete':complete,
        'sections':contours,'section_count':len(contours),'contours_complete':contours_complete,
        'probe_selection':'25 deterministic spread positions on original retained top, excluding the pocket and holes with a two-cell guard; no result-driven selection.',
        'topology_matches':all(r['topology_matches'] for r in contours),
        'max_boundary_deviation_mm':max(r['boundary_deviation_mm'] for r in contours) if contours_complete else None,
        'top_z_band_mm':[min(tops),max(tops)] if complete else None,
        'thickness_band_mm':[min(thicknesses),max(thicknesses)] if complete else None,
        'top_flatness_range_mm':max(tops)-min(tops) if complete else None,
        'max_bottom_deviation_mm':max([p['bottom_deviation_mm'] for p in probes]+[abs(float(vertices[:,2].min())-low[2])]) if complete else None,
        'mesh_max_z_mm':float(vertices[:,2].max()),'resolution_mm':resolution,'numerical_guard_mm':guard,
        'guard_is_certified_uncertainty':False,'registration_applied':False}
    if pocket:
        rows=[]
        for requested in heights(floor,high[2]):
            z,s=sample(requested);matched=pocket['id'] in s['matched']
            row={'requested_z_mm':requested,'z_mm':z,'matched':matched,'interior_loops':len(s['rings']),'material_components':len(s['pieces'])}
            if matched:
                contour=Polygon(s['rings'][s['matched'][pocket['id']]])
                row.update(boundary_distance_mm=contour.boundary.hausdorff_distance(void.boundary),
                    observed_bounds_xy_mm=list(contour.bounds),area_mm2=contour.area,
                    excess_void_area_mm2=contour.difference(void).area,remaining_material_area_mm2=void.difference(contour).area)
            rows.append(row)
        retained_floor=void.buffer(-guard)
        for h in holes:retained_floor=retained_floor.difference(Point(h['center_xy_mm']).buffer(h['diameter_mm']/2+guard,quad_segs=256))
        x,y=pocket['center_xy_mm'];w,h=pocket['width_mm'],pocket['height_mm']
        floor_points=spread_probes(retained_floor,([x-w/2,y-h/2],[x+w/2,y+h/2]))
        bottom=floor_profile(rays(floor_points),high[2],low[2],resolution)
        bottom['probe_selection']='25 deterministic original retained-floor probes, excluding declared holes plus two cells; no result-driven selection.'
        complete=all(r['matched'] for r in rows)
        results[pocket['id']]={'sections':rows,'section_count':len(rows),'profile_complete':complete,
            'max_profile_deviation_mm':max(r['boundary_distance_mm'] for r in rows) if complete else None,
            'floor':bottom,'resolution_mm':resolution,'numerical_guard_mm':guard,'guard_is_certified_uncertainty':False,'registration_applied':False}
    counts=[cache[z]['counts'] for z in sorted(cache)]
    return {'method':'Original layer-aware contour association, independent retained-top/floor probes, full incoming-height through rays.',
        'features':results,'section_feature_counts':counts,
        'unassigned_contours_detected':any(r['unassigned_contours'] or r['missing_features'] or r['material_components']!=1 for r in counts)}


def evaluate(kind,obs,rule):
    if 'floor' in obs:
        from .pocket_verification import evaluate as evaluator
    elif 'top_z_band_mm' in obs:
        from .facing_verification import evaluate as evaluator
    else:
        from .verification import evaluate as evaluator
    return evaluator(kind,obs,rule)


def inspect_checkpoint(parent,case,output):
    """Bind measured cumulative NC to its native job and independent pre-NC CAD."""
    from .preparation import load_json,digest
    from .programming import checked_plan,verify_binding,simulation_project
    from .sequence_nc import check_prefix
    from .native_planar_cam import write
    from mesh_metrics import load_mesh,require_bounded_worker
    require_bounded_worker()
    request=load_json(parent/'request.json');plan=checked_plan(request['input'],parent/'preparation-result.json')
    if plan['schema']!='genlaw.execution-plan.v12':raise ValueError('Need layered sequence execution plan')
    cam=load_json(parent/'cam-result.json');proof=load_json(case/'prefix-binding.json')
    if not any(r['id']==case.name and r['prefix_binding_sha256']==digest(case/'prefix-binding.json') for r in cam['checkpoint_prefixes']):
        raise ValueError('Checkpoint not bound to native job')
    verify_binding(parent/'program.nc',proof['full_nc_sha256']);verify_binding(case/'program.nc',proof['prefix_nc_sha256'])
    verify_binding(case/'native-geometry.json',proof['expected_native_geometry_sha256'])
    count=int(case.name.rsplit('-',1)[1]);observed=check_prefix((case/'program.nc').read_text(),(parent/'program.nc').read_text(),plan,count)
    if any(proof.get(k)!=v for k,v in observed.items()):raise ValueError('Checkpoint NC proof changed')
    config=load_json(case/'simulation.camotics');binding=load_json(case/'simulation-result.json')
    if config!=simulation_project(plan):raise ValueError('Checkpoint original stock / tool table / resolution changed')
    for name,key in [('simulated.stl','mesh_sha256'),('program.nc','nc_sha256_after'),('simulation.camotics','project_sha256')]:verify_binding(case/name,binding[key])
    ref=load_json(case/'native-geometry.json');features=[op['geometry'] for op in plan['operations'][:count]]
    root=Path(request['input']).parent;oracles=load_json(root/plan['expected_states_ref']['path']);oracle=oracles['states'][count-1]
    if (ref['feature_bindings']!=features or ref['completed_features']!=[f['id'] for f in features]
        or ref['features']!=[f for f in features if f['kind']=='through_hole']
        or ref.get('pocket_feature')!=next((f for f in features if f['kind']=='rounded_rectangular_pocket'),None)
        or ref['facing_feature']!=features[0] or ref['original_final_cad_sha256']!=plan['cad_ref']['sha256']
        or ref['oracle_ref']!=oracle['cad'] or ref['cad_sha256']!=oracle['cad']['sha256']
        or ref['bounds']!=oracle['bounds_mm'] or ref['incoming_stock_bounds']!=plan['stock']['bounds_mm']
        or abs(ref['volume_mm3']-oracle['volume_mm3'])>1e-8):raise ValueError('Original checkpoint CAD / feature binding changed')
    verify_binding(root/oracle['cad']['path'],oracle['cad']['sha256'])
    vertices,faces,mesh=load_mesh(case/'simulated.stl');obs=observations(vertices,faces,ref,config['resolution'])
    write(output,{'schema':'genlaw.material-checkpoint-observation.v1','status':'measured','completed_features':ref['completed_features'],
        'observations':obs,'mesh':mesh,'mesh_sha256':digest(case/'simulated.stl'),'nc_sha256':digest(case/'program.nc'),
        'native_geometry_sha256':digest(case/'native-geometry.json'),'project_sha256':digest(case/'simulation.camotics'),
        'prefix_binding_sha256':digest(case/'prefix-binding.json'),'source_code_sha256':digest(Path(__file__)),
        'original_stock_used':True,'expected_cad_used_as_stock':False,'manufacturing_acceptance':False})


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parent',type=Path,required=True);p.add_argument('--case',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();inspect_checkpoint(a.parent,a.case,a.output)
