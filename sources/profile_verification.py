"""Original-coordinate full contours, retained planes and exterior stock probes."""
import numpy as np
from .profile_geometry import profile
from .preparation import number
from .region_verification import topology


def sample_points(feature,low,high,resolution):
    from shapely.geometry import Point
    region=profile(feature,1024);margin=4*resolution
    inside=[];outside={key:[] for key in [(0,0),(0,1),(1,0),(1,1)]}
    for u in np.linspace(.04,.96,21):
        for v in np.linspace(.04,.96,21):
            xy=[low[0]+float(u)*(high[0]-low[0]),low[1]+float(v)*(high[1]-low[1])];point=Point(xy)
            if region.contains(point) and region.boundary.distance(point)>margin:inside.append(xy)
            elif not region.covers(point) and region.distance(point)>margin:
                outside[(int(xy[0]>feature['center_xy_mm'][0]),int(xy[1]>feature['center_xy_mm'][1]))].append(xy)
    if len(inside)<25 or any(len(v)<5 for v in outside.values()):raise ValueError('Insufficient independent interior/exterior sample space at declared resolution')
    def choose(values,count):return [values[int(i)] for i in np.linspace(0,len(values)-1,count)]
    return choose(inside,25),[xy for values in outside.values() for xy in choose(values,5)]


def observations(vertices,faces,reference,resolution):
    from .verification import ray_hits
    from .measure_worker import section_height
    from mesh_metrics import Sections
    low,high=[reference['bounds'][k] for k in ['min','max']];f=reference['features'][0];guard=2*resolution
    expected=profile(f,2048);signature=topology(expected);sections=Sections(vertices,faces)
    levels=np.unique(np.concatenate((vertices[:,2].astype(float),[low[2],high[2]])))
    if len(levels)>4096:raise ValueError('Profile section indexing budget exceeded')
    contours=[]
    for fraction in np.linspace(.05,.95,11):
        requested=low[2]+float(fraction)*(high[2]-low[2]);z=section_height(levels,requested);actual=sections.at(z)
        observed=topology(actual)
        contours.append({'requested_z_mm':requested,'z_mm':z,'expected_topology':signature,'observed_topology':observed,
            'topology_matches':signature==observed,'boundary_deviation_mm':actual.boundary.hausdorff_distance(expected.boundary) if not actual.is_empty else None,
            'exterior_material_area_mm2':actual.difference(expected).area,'missing_material_area_mm2':expected.difference(actual).area})
    interior,exterior=sample_points(f,low,high,resolution);probes=[];waste=[]
    for xy in interior:
        zs=ray_hits(sections.triangles,xy,(low[2]-guard,high[2]+guard));valid=len(zs)==2
        probes.append({'xy_mm':xy,'surface_z_mm':zs,'status':'measured' if valid else 'ambiguous_intersections',
                       'thickness_mm':zs[-1]-zs[0] if valid else None,
                       'plane_deviation_mm':max(abs(zs[0]-low[2]),abs(zs[-1]-high[2])) if valid else None})
    for xy in exterior:
        zs=ray_hits(sections.triangles,xy,(low[2]-guard,high[2]+guard))
        lengths=[b-a for a,b in zip(zs[::2],zs[1::2])]
        state='clear' if not zs else 'ambiguous' if len(zs)%2 else 'blocked' if max(lengths)>guard else 'sub_guard_signal'
        waste.append({'xy_mm':xy,'surface_z_mm':zs,'observation':state,'maximum_material_interval_mm':max(lengths,default=0)})
    complete=all(p['status']=='measured' for p in probes);contours_complete=all(c['boundary_deviation_mm'] is not None for c in contours)
    thickness=[p['thickness_mm'] for p in probes] if complete else []
    item={'probes':probes,'probe_count':len(probes),'complete':complete,'sections':contours,'section_count':11,
          'contours_complete':contours_complete,'topology_matches':all(c['topology_matches'] for c in contours),
          'max_boundary_deviation_mm':max(c['boundary_deviation_mm'] for c in contours) if contours_complete else None,
          'thickness_band_mm':[min(thickness),max(thickness)] if complete else None,
          'max_plane_deviation_mm':max([p['plane_deviation_mm'] for p in probes]+[abs(float(vertices[:,2].min())-low[2]),abs(float(vertices[:,2].max())-high[2])]) if complete else None,
          'exterior_probes':waste,'exterior_probe_count':len(waste),'exterior_all_clear':all(p['observation']=='clear' for p in waste),
          'resolution_mm':resolution,'numerical_guard_mm':guard,'guard_is_certified_uncertainty':False,'registration_applied':False,
          'disconnected_exterior_material_discarded':False}
    return {'method':'11 complete material sections, 25 independent retained-solid rays and 5 exterior rays per stock corner; all actual mesh components remain.',
            'features':{f['id']:item},'unassigned_contours_detected':not item['topology_matches']}


def evaluate(kind,obs,rule):
    methods={'outer_profile_xy':'sectioned_original_capsule_boundary','retained_thickness':'sampled_original_thickness',
             'retained_planes':'sampled_original_top_bottom','exterior_clearance':'sampled_full_depth_exterior_clearance'}
    if kind not in methods:return {'status':'unsupported_measurement','reason':'No physical cutting, roughness or general GD&T evaluator.'}
    if rule is None:return {'status':'pending_criteria','reason':'No approved numerical criterion supplied.'}
    try:
        if (rule.get('schema')!='genlaw.numeric-acceptance.v1' or rule.get('units')!='mm'
            or rule.get('scope') not in ('software_regression','engineering_requirement')
            or not isinstance(rule.get('basis'),str) or not rule['basis'].strip() or rule.get('method')!=methods[kind]):
            raise ValueError('Explicit supported method, units, scope and basis required')
        common={'schema','units','scope','basis','method'};guard=obs['numerical_guard_mm']
        if kind=='exterior_clearance':
            if set(rule)-common-{'must_be_clear'} or rule.get('must_be_clear') is not True:raise ValueError('Exterior stock must be completely clear at the declared probes')
            states=[p['observation'] for p in obs['exterior_probes']]
            if len(states)!=20:return {'status':'not_measurable','reason':'Missing original exterior probes'}
            status='numeric_pass' if all(s=='clear' for s in states) else 'numeric_fail' if 'blocked' in states else 'not_measurable' if 'ambiguous' in states else 'indeterminate'
        elif kind=='retained_thickness':
            if set(rule)-common-{'min_mm','max_mm'}:raise ValueError('Unsupported thickness criterion')
            low=number(rule.get('min_mm'),'min_mm',True);high=number(rule.get('max_mm'),'max_mm',True)
            if low>=high:raise ValueError('Reversed thickness limits')
            if not obs['complete']:return {'status':'not_measurable','reason':'Not two retained-body intersections at each original probe'}
            a,b=obs['thickness_band_mm'];status='numeric_pass' if a>=low+guard and b<=high-guard else 'numeric_fail' if a<low-guard or b>high+guard else 'indeterminate'
        else:
            if set(rule)-common-{'max_deviation_mm'}:raise ValueError('Unsupported geometric criterion')
            limit=number(rule.get('max_deviation_mm'),'max_deviation_mm',True)
            if kind=='outer_profile_xy' and not obs['topology_matches']:status='numeric_fail'
            else:
                measured=obs['contours_complete'] if kind=='outer_profile_xy' else obs['complete']
                if not measured:return {'status':'not_measurable','reason':'Original retained-body contour or plane cannot be measured'}
                delta=obs['max_boundary_deviation_mm'] if kind=='outer_profile_xy' else obs['max_plane_deviation_mm']
                status='numeric_pass' if delta+guard<=limit else 'numeric_fail' if delta-guard>limit else 'indeterminate'
        return {'status':status,'criterion_scope':rule['scope'],'criterion':rule,'production_approved':False,
                'reason':'Independent original-CAD finite measurements with a two-cell guard; no discarded waste or output registration.'}
    except (ValueError,TypeError,AttributeError) as error:return {'status':'unsupported_criterion','reason':str(error)}
