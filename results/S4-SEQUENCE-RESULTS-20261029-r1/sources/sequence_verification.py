"""Observe facing and holes together without treating designed holes as defects."""
import math
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'web/tools'))
from .facing_verification import evaluate as evaluate_face


def retained_probes(reference,resolution):
    """Choose positions from original geometry alone, never from the result mesh."""
    low,high=[reference['bounds'][k] for k in ['min','max']];holes=reference['features'];guard=2*resolution
    candidates=[]
    for u in np.linspace(.05,.95,9):
        for v in np.linspace(.05,.95,9):
            xy=[low[0]+float(u)*(high[0]-low[0]),low[1]+float(v)*(high[1]-low[1])]
            if all(math.dist(xy,f['center_xy_mm'])>f['diameter_mm']/2+guard for f in holes):candidates.append(xy)
    if len(candidates)<25:raise ValueError('Retained-face probe budget: fewer than 25 independent original-geometry positions')
    selected=[candidates.pop(0)]
    while len(selected)<25:
        i=max(range(len(candidates)),key=lambda n:min(math.dist(candidates[n],p) for p in selected))
        selected.append(candidates.pop(i))
    return selected


def observations(vertices,faces,reference,resolution):
    from .verification import pattern_observations,ray_hits,summarize_rays
    from .measure_worker import section_height
    from mesh_metrics import Sections
    from shapely.geometry import box,Polygon
    low,high=[reference['bounds'][k] for k in ['min','max']];stock_top=reference['incoming_stock_bounds']['max'][2]
    guard=2*resolution;sections=Sections(vertices,faces)
    holes=pattern_observations(vertices,faces,reference,resolution)
    # Through rays must include all incoming material above the finished top.
    for f in reference['features']:
        item=holes['features'][f['id']];rays=item['through']['probes']
        hits=[{'xy_mm':p['xy_mm'],'surface_z_mm':ray_hits(sections.triangles,p['xy_mm'],(low[2]-guard,stock_top+guard))} for p in rays]
        item['through']=summarize_rays(hits,guard)
    levels=np.unique(np.concatenate((vertices[:,2].astype(float),[low[2],high[2],stock_top])))
    if len(levels)>4096:raise ValueError('Section indexing budget exceeded')
    expected=box(*low[:2],*high[:2]);contours=[]
    for fraction in np.linspace(.05,.95,11):
        requested=low[2]+float(fraction)*(high[2]-low[2]);z=section_height(levels,requested);actual=sections.at(z)
        pieces=list(actual.geoms) if actual.geom_type=='MultiPolygon' else [actual]
        valid=len(pieces)==1 and not actual.is_empty and pieces[0].geom_type=='Polygon'
        outer=Polygon(pieces[0].exterior) if valid else None
        contours.append({'requested_z_mm':requested,'z_mm':z,'retained_body_count':len(pieces),
            'topology_matches':valid,'holes_checked_separately':True,
            'boundary_deviation_mm':outer.boundary.hausdorff_distance(expected.boundary) if valid else None})
    probes=[]
    for xy in retained_probes(reference,resolution):
        zs=ray_hits(sections.triangles,xy,(low[2]-guard,stock_top+guard));valid=len(zs)==2
        probes.append({'xy_mm':xy,'surface_z_mm':zs,'status':'measured' if valid else 'ambiguous_intersections',
            'top_z_mm':zs[-1] if valid else None,'thickness_mm':zs[-1]-zs[0] if valid else None,
            'bottom_deviation_mm':abs(zs[0]-low[2]) if valid else None})
    complete=all(p['status']=='measured' for p in probes);contours_complete=all(c['boundary_deviation_mm'] is not None for c in contours)
    tops=[p['top_z_mm'] for p in probes] if complete else [];thicknesses=[p['thickness_mm'] for p in probes] if complete else []
    face={'probes':probes,'probe_count':len(probes),'complete':complete,'sections':contours,'section_count':11,
        'probe_selection':'25 deterministic spread positions in original retained top, excluding original holes plus two-cell guard; independent of actual mesh.',
        'contours_complete':contours_complete,'topology_matches':all(c['topology_matches'] for c in contours),
        'max_boundary_deviation_mm':max(c['boundary_deviation_mm'] for c in contours) if contours_complete else None,
        'top_z_band_mm':[min(tops),max(tops)] if complete else None,'thickness_band_mm':[min(thicknesses),max(thicknesses)] if complete else None,
        'top_flatness_range_mm':max(tops)-min(tops) if complete else None,
        'max_bottom_deviation_mm':max([p['bottom_deviation_mm'] for p in probes]+[abs(float(vertices[:,2].min())-low[2])]) if complete else None,
        'mesh_max_z_mm':float(vertices[:,2].max()),'resolution_mm':resolution,'numerical_guard_mm':guard,
        'guard_is_certified_uncertainty':False,'registration_applied':False}
    return {'method':'Original retained-face probes / outer contours and separately associated hole contours; full incoming-height through rays.',
        'features':{reference['facing_feature']['id']:face,**holes['features']},'section_feature_counts':holes['section_feature_counts'],
        'unassigned_contours_detected':holes['unassigned_contours_detected'] or not face['topology_matches']}


def evaluate(kind,obs,rule):
    if 'top_z_band_mm' in obs:return evaluate_face(kind,obs,rule)
    from .verification import evaluate as evaluate_hole
    return evaluate_hole(kind,obs,rule)


def inspect_checkpoint(parent,case,output):
    """A measured prefix, source-bound to the same job and original stock."""
    import json
    from .preparation import load_json,digest
    from .programming import checked_plan,verify_binding,simulation_project
    from .native_planar_cam import write
    from .facing_geometry import FACING
    from .facing_verification import observations as face_observations
    from mesh_metrics import load_mesh,require_bounded_worker
    require_bounded_worker()
    request=load_json(parent/'request.json');plan=checked_plan(request['input'],parent/'preparation-result.json')
    cam=load_json(parent/'cam-result.json');proof=load_json(case/'prefix-binding.json')
    if not any(r['id']==case.name and r['prefix_binding_sha256']==digest(case/'prefix-binding.json') for r in cam['checkpoint_prefixes']):
        raise ValueError('Checkpoint not bound to native CAM job')
    verify_binding(parent/'program.nc',proof['full_nc_sha256']);verify_binding(case/'program.nc',proof['prefix_nc_sha256'])
    verify_binding(case/'native-geometry.json',proof['expected_native_geometry_sha256'])
    config=load_json(case/'simulation.camotics');binding=load_json(case/'simulation-result.json')
    if config!=simulation_project(plan):raise ValueError('Checkpoint must use the same original stock, tools and resolution')
    for name,key in [('simulated.stl','mesh_sha256'),('program.nc','nc_sha256_after'),('simulation.camotics','project_sha256')]:verify_binding(case/name,binding[key])
    ref=load_json(case/'native-geometry.json');vertices,faces,mesh=load_mesh(case/'simulated.stl')
    obs=(face_observations if ref['reference_class']==FACING else observations)(vertices,faces,ref,config['resolution'])
    result={'schema':'genlaw.material-checkpoint-observation.v1','status':'measured','completed_features':ref['completed_features'],
        'observations':obs,'mesh':mesh,'mesh_sha256':digest(case/'simulated.stl'),'nc_sha256':digest(case/'program.nc'),
        'native_geometry_sha256':digest(case/'native-geometry.json'),'project_sha256':digest(case/'simulation.camotics'),
        'prefix_binding_sha256':digest(case/'prefix-binding.json'),'source_code_sha256':digest(Path(__file__)),
        'original_stock_used':True,'expected_cad_used_as_stock':False,'manufacturing_acceptance':False}
    write(output,result)


if __name__=='__main__':
    import argparse
    from pathlib import Path
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--parent',type=Path,required=True);p.add_argument('--case',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();inspect_checkpoint(a.parent,a.case,a.output)
