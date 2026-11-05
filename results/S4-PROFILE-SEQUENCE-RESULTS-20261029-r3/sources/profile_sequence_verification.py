"""Combined contours and retained-face checks, with actual full-height waste rays."""
from .sequence_verification import observations as sequence_observations
import numpy as np


def exterior_probes(outline,low,high,resolution):
    """Five probes per original corner, independent of overall aspect ratio."""
    from shapely.geometry import Point
    from .profile_geometry import profile
    region=profile(outline,2048);radius=outline['width_mm']/2;result=[]
    for sx,sy in [(0,0),(0,1),(1,0),(1,1)]:
        candidates=[]
        for u in np.linspace(.04,.96,21):
            for v in np.linspace(.04,.96,21):
                xy=[(low[0]+float(u)*radius if not sx else high[0]-float(u)*radius),
                    (low[1]+float(v)*radius if not sy else high[1]-float(v)*radius)]
                point=Point(xy)
                if not region.covers(point) and region.distance(point)>4*resolution:candidates.append(xy)
        if len(candidates)<5:raise ValueError('Insufficient independent original corner probe space')
        result.extend(candidates[int(i)] for i in np.linspace(0,len(candidates)-1,5))
    return result


def observations(vertices, faces, reference, resolution):
    from .verification import ray_hits
    from mesh_metrics import Sections
    result=sequence_observations(vertices,faces,reference,resolution)
    face=result['features'][reference['facing_feature']['id']]
    outline=reference['profile_feature'];guard=2*resolution
    low,high=[reference['bounds'][k] for k in ['min','max']]
    stock_top=reference['incoming_stock_bounds']['max'][2]
    exterior=exterior_probes(outline,low,high,resolution)
    triangles=Sections(vertices,faces).triangles;waste=[]
    for xy in exterior:
        zs=ray_hits(triangles,xy,(low[2]-guard,stock_top+guard))
        lengths=[b-a for a,b in zip(zs[::2],zs[1::2])]
        state='clear' if not zs else 'ambiguous' if len(zs)%2 else 'blocked' if max(lengths)>guard else 'sub_guard_signal'
        waste.append({'xy_mm':xy,'surface_z_mm':zs,'observation':state,'maximum_material_interval_mm':max(lengths,default=0)})
    fields=['probes','probe_count','complete','sections','section_count','contours_complete','topology_matches',
            'max_boundary_deviation_mm','thickness_band_mm','resolution_mm','numerical_guard_mm',
            'guard_is_certified_uncertainty','registration_applied']
    item={k:face[k] for k in fields}
    item.update(max_plane_deviation_mm=max(face['max_bottom_deviation_mm'],
        *[abs(z-high[2]) for z in face['top_z_band_mm']],abs(face['mesh_max_z_mm']-high[2])) if face['complete'] else None,
        exterior_probes=waste,exterior_probe_count=len(waste),exterior_all_clear=all(p['observation']=='clear' for p in waste),
        disconnected_exterior_material_discarded=False,
        probe_selection='Original capsule retained solid, excluding all intended holes; never adapted to simulated material.')
    result['features'][outline['id']]=item
    result['method']='Original capsule outer boundary and holes checked separately; 25 retained-solid rays and 20 full incoming-height exterior rays.'
    return result
