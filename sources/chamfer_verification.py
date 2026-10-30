"""Full original-coordinate sections, four finite width estimates and retained planes."""
import math
import numpy as np
from .chamfer_geometry import ChamferReference
from .region_verification import topology
from .preparation import number


def observations(vertices,faces,reference,resolution):
    from .verification import ray_hits
    from .measure_worker import section_height
    from mesh_metrics import Sections
    from shapely.geometry import LineString
    target=ChamferReference(reference);lo,hi=target.low,target.high;f=reference['features'][0]
    sections=Sections(vertices,faces);guard=2*resolution
    levels=np.unique(np.concatenate((vertices[:,2].astype(float),target.breaks)))
    if len(levels)>4096:raise ValueError('Chamfer section indexing budget exceeded')
    contours=[];midlines=[]
    requests=[('bevel',hi[2]-target.width*float(v)) for v in np.linspace(.05,.95,11)]
    requests += [('retained_wall',lo[2]+float(v)*(hi[2]-target.width-lo[2])) for v in [.2,.5,.8]]
    for kind,requested in requests:
        z=section_height(levels,requested) if float(np.min(abs(levels-requested)))<1e-10 else requested
        actual=sections.at(z);expected=target.at(z)
        contours.append({'region':kind,'requested_z_mm':requested,'z_mm':z,
            'expected_topology':topology(expected),'observed_topology':topology(actual),
            'topology_matches':topology(actual)==topology(expected),
            'boundary_deviation_mm':actual.boundary.hausdorff_distance(expected.boundary) if not actual.is_empty else None,
            'missing_material_area_mm2':expected.difference(actual).area,'excess_material_area_mm2':actual.difference(expected).area})
        if kind!='bevel':continue
        xline=actual.intersection(LineString([(lo[0]-1,(lo[1]+hi[1])/2),(hi[0]+1,(lo[1]+hi[1])/2)]))
        yline=actual.intersection(LineString([((lo[0]+hi[0])/2,lo[1]-1),((lo[0]+hi[0])/2,hi[1]+1)]))
        valid=all(g.geom_type=='LineString' and not g.is_empty for g in [xline,yline])
        midlines.append({'z_mm':z,'status':'measured' if valid else 'ambiguous_midline',
            'insets_mm':{'xmin':xline.bounds[0]-lo[0],'xmax':hi[0]-xline.bounds[2],
                         'ymin':yline.bounds[1]-lo[1],'ymax':hi[1]-yline.bounds[3]} if valid else None})
    widths=[];width_complete=all(r['status']=='measured' for r in midlines)
    if width_complete:
        zs=np.array([r['z_mm']-hi[2] for r in midlines])
        for edge in ['xmin','xmax','ymin','ymax']:
            values=np.array([r['insets_mm'][edge] for r in midlines]);slope,intercept=np.polyfit(zs,values,1)
            widths.append({'edge':edge,'extrapolated_top_width_mm':float(intercept),'nominal_width_mm':target.width,
                'width_error_mm':abs(float(intercept)-target.width),'max_fit_residual_mm':float(max(abs(values-(slope*zs+intercept)))),
                'estimated_angle_degrees':math.degrees(math.atan2(1,float(slope))),
                'angle_manufacturing_acceptance':False})
    probes=[];margin=target.width+4*resolution
    if any(hi[i]-lo[i]<=2*margin for i in range(2)):raise ValueError('Insufficient retained-top probe space')
    for x in np.linspace(lo[0]+margin,hi[0]-margin,5):
        for y in np.linspace(lo[1]+margin,hi[1]-margin,5):
            xy=[float(x),float(y)];zs=ray_hits(sections.triangles,xy,(lo[2]-guard,hi[2]+guard));valid=len(zs)==2
            probes.append({'xy_mm':xy,'surface_z_mm':zs,'status':'measured' if valid else 'ambiguous_intersections',
                'thickness_mm':zs[-1]-zs[0] if valid else None,
                'plane_deviation_mm':max(abs(zs[0]-lo[2]),abs(zs[-1]-hi[2])) if valid else None})
    complete=all(p['status']=='measured' for p in probes);contours_complete=all(r['boundary_deviation_mm'] is not None for r in contours)
    thickness=[p['thickness_mm'] for p in probes] if complete else []
    item={'sections':contours,'section_count':len(contours),'bevel_section_count':11,
        'contours_complete':contours_complete,'topology_matches':all(r['topology_matches'] for r in contours),
        'max_boundary_deviation_mm':max(r['boundary_deviation_mm'] for r in contours) if contours_complete else None,
        'midlines':midlines,'widths':widths,'width_complete':width_complete,
        'max_width_deviation_mm':max(max(r['width_error_mm'],r['max_fit_residual_mm']) for r in widths) if width_complete else None,
        'probes':probes,'probe_count':25,'complete':complete,'thickness_band_mm':[min(thickness),max(thickness)] if complete else None,
        'max_plane_deviation_mm':max([p['plane_deviation_mm'] for p in probes]+[abs(float(vertices[:,2].min())-lo[2]),abs(float(vertices[:,2].max())-hi[2])]) if complete else None,
        'resolution_mm':resolution,'numerical_guard_mm':guard,'guard_is_certified_uncertainty':False,
        'registration_applied':False,'disconnected_material_discarded':False}
    return {'method':'11 complete bevel sections, 3 retained-wall sections, four midline linear width/angle estimates and 25 retained-solid rays.',
        'features':{f['id']:item},'unassigned_contours_detected':not item['topology_matches']}


def evaluate(kind,obs,rule):
    from .profile_verification import evaluate as retained_evaluate
    if kind in ('retained_thickness','retained_planes'):return retained_evaluate(kind,obs,rule)
    methods={'chamfer_sections':'sectioned_original_chamfer_boundary','chamfer_width':'sampled_extrapolated_chamfer_width'}
    if kind not in methods:return {'status':'unsupported_measurement','reason':'No certified angle, roughness, physical cutting or GD&T evaluator.'}
    if rule is None:return {'status':'pending_criteria','reason':'No approved numerical criterion supplied.'}
    try:
        if (set(rule)-{'schema','units','scope','basis','method','max_deviation_mm'} or rule.get('schema')!='genlaw.numeric-acceptance.v1'
            or rule.get('units')!='mm' or rule.get('scope') not in ('software_regression','engineering_requirement')
            or not isinstance(rule.get('basis'),str) or not rule['basis'].strip() or rule.get('method')!=methods[kind]):
            raise ValueError('Explicit supported method, units, scope and basis required')
        limit=number(rule.get('max_deviation_mm'),'max_deviation_mm',True);guard=obs['numerical_guard_mm']
        if not obs['topology_matches']:status='numeric_fail'
        else:
            measured=obs['contours_complete'] if kind=='chamfer_sections' else obs['width_complete']
            if not measured:return {'status':'not_measurable','reason':'Missing original bevel sections or complete midline observations'}
            delta=obs['max_boundary_deviation_mm'] if kind=='chamfer_sections' else obs['max_width_deviation_mm']
            status='numeric_pass' if delta+guard<=limit else 'numeric_fail' if delta-guard>limit else 'indeterminate'
        return {'status':status,'criterion_scope':rule['scope'],'criterion':rule,'production_approved':False,
            'reason':'Finite original-coordinate section/width observations with two grid cells as numerical guard; no certified angle acceptance.'}
    except (ValueError,TypeError,AttributeError) as error:return {'status':'unsupported_criterion','reason':str(error)}
